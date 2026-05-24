"""
Torre/broker de drones - TCP com relogio de Lamport.

Cada torre e um broker operacional autonomo: mantem seus drones, fila local
ordenada, missoes ativas e conexoes P2P com as demais torres. Nao existe
servidor central. Requisicoes de setores/sensores chegam via TCP e sao
encaminhadas para uma torre proprietaria deterministica, calculada pelo id da
requisicao, evitando duplo despacho da mesma area/requisicao.
"""

import heapq
import json
import logging
import os
import socket
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from socketserver import BaseRequestHandler, ThreadingTCPServer

sys.path.insert(0, "/app/shared")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "shared"))
from config import COORDENADAS_TORRES, HEARTBEAT_DRONE_TIMEOUT, TIMEOUT_TCP, TORRES
from lamport import RelogioLamport, conectar_tcp, enviar_mensagem, receber_mensagem
from models import Drone, Requisicao

TORRE_ID = os.environ.get("TORRE_ID", "torre-1")
TORRE_NOME = os.environ.get("TORRE_NOME", "Torre Alpha")
TORRE_PORT = int(os.environ.get("TORRE_PORT", 6001))
TORRE_HTTP_PORT = int(os.environ.get("TORRE_HTTP_PORT", 6101))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")

R = "\033[91m"
Y = "\033[93m"
G = "\033[92m"
C = "\033[96m"
M = "\033[95m"
W = "\033[0m"
B = "\033[94m"

relogio = RelogioLamport(TORRE_ID)
_lock = threading.RLock()
drones: dict[str, Drone] = {}
fila: list[tuple[tuple, str, Requisicao]] = []
missoes: dict[str, Requisicao] = {}
historico: list[Requisicao] = []
req_vistas: set[str] = set()
req_finalizadas: set[str] = set()
peers: dict[str, dict] = {}


def log(msg, cor=C):
    print(f"{cor}[{TORRE_NOME} | L={relogio.valor:04d}] {msg}{W}", flush=True)


def _init_peers():
    for tid, info in TORRES.items():
        if tid != TORRE_ID:
            peers[tid] = dict(info)
    log(f"Peers: {list(peers.keys())}", C)


def _init_drones():
    raw = os.environ.get("DRONES_JSON", "[]")
    for d in json.loads(raw):
        drone = Drone(id=d["id"], nome=d["nome"], torre_base=TORRE_ID)
        drones[drone.id] = drone
        log(f"Drone pronto: {drone.nome} ({drone.id})", G)


def _torres_conhecidas() -> list[str]:
    return sorted({TORRE_ID, *peers.keys()})


def _owner_requisicao(req_id: str) -> str:
    """Particionamento deterministico: todos os brokers escolhem o mesmo dono."""
    ids = _torres_conhecidas()
    if not ids:
        return TORRE_ID
    return ids[sum(req_id.encode("utf-8")) % len(ids)]


def _owner_sequence(req_id: str) -> list[str]:
    ids = _torres_conhecidas()
    if not ids:
        return [TORRE_ID]
    idx = sum(req_id.encode("utf-8")) % len(ids)
    return ids[idx:] + ids[:idx]


def _enfileirar(req: Requisicao) -> bool:
    with _lock:
        if req.id in req_finalizadas or req.id in missoes:
            return False
        if any(item[2].id == req.id for item in fila):
            return False
        heapq.heappush(fila, (req.prioridade(), req.id, req))
        req_vistas.add(req.id)
    return True


class TorreHandler(BaseRequestHandler):
    def handle(self):
        buf = []
        msg = receber_mensagem(self.request, relogio, buf)
        if msg is None:
            return

        tipo = msg.get("tipo")
        payload = msg.get("payload", {})
        log(f"<- [{tipo}] de {msg.get('remetente', '?')} clock={msg.get('clock_lamport', 0)}", B)

        resp = self._despachar(tipo, payload, msg)
        if resp:
            try:
                enviar_mensagem(self.request, resp["tipo"], resp["payload"], relogio)
            except OSError:
                pass

    def _despachar(self, tipo: str, payload: dict, msg_original: dict) -> dict | None:
        if tipo in ("REQUISICAO", "SOLICITAR_DRONE"):
            return _handle_requisicao(payload, msg_original)
        if tipo == "CONFIRMAR_DESPACHO":
            return _handle_confirmar_despacho(payload)
        if tipo in ("LIBERAR_DRONE", "MISSAO_CONCLUIDA"):
            return _handle_liberar_drone(payload)
        if tipo == "FALHA_DRONE":
            return _handle_falha_drone(payload)
        if tipo == "SYNC_ESTADO":
            return _handle_sync(payload)
        if tipo == "CONSULTA_ESTADO":
            return {"tipo": "ESTADO", "payload": _montar_estado()}
        if tipo == "CADASTRAR_DRONE":
            return _handle_cadastrar_drone(payload)
        if tipo == "REMOVER_DRONE":
            return _handle_remover_drone(payload)
        if tipo == "CADASTRAR_TORRE":
            return _handle_cadastrar_torre(payload)
        if tipo == "HEARTBEAT_DRONE":
            return _handle_heartbeat(payload)
        return {"tipo": "ERRO", "payload": {"msg": f"tipo desconhecido: {tipo}"}}


def _handle_requisicao(payload: dict, msg: dict) -> dict:
    req = Requisicao.from_dict(payload)
    req.clock_lamport = msg.get("clock_lamport", req.clock_lamport or relogio.valor)
    req.status = "pendente"

    for owner in _owner_sequence(req.id):
        if owner == TORRE_ID:
            break
        if owner not in peers:
            continue
        resp = _enviar_tcp_e_receber(owner, "SOLICITAR_DRONE", req.to_dict(), tentativas=2)
        if resp and resp.get("tipo") == "ACK":
            return {
                "tipo": "ACK",
                "payload": {"status": "encaminhada", "req_id": req.id, "torre": owner},
            }
        log(f"Owner candidato {owner} indisponivel para req {req.id}", Y)

    with _lock:
        if req.id in req_vistas or req.id in req_finalizadas:
            return {"tipo": "ACK", "payload": {"status": "duplicada", "req_id": req.id}}

    _enfileirar(req)
    log(f"Req {req.id} enfileirada area={req.area_id} critica={req.critica}", C)
    threading.Thread(target=_tentar_alocar, daemon=True).start()
    threading.Thread(target=_sync_peers, daemon=True).start()
    return {
        "tipo": "ACK",
        "payload": {
            "status": "enfileirada",
            "req_id": req.id,
            "torre": TORRE_ID,
            "clock_lamport": relogio.valor,
        },
    }


def _tentar_alocar():
    alocacoes: list[tuple[Drone, Requisicao]] = []
    with _lock:
        while fila:
            drone = _melhor_drone()
            if drone is None:
                log(f"{len(fila)} req(s) na fila aguardando drone livre", Y)
                break

            _, _, req = heapq.heappop(fila)
            if req.id in req_finalizadas or req.id in missoes:
                continue

            drone.disponivel = False
            drone.requisicao_atual = req.id
            drone.ultimo_heartbeat = time.time()
            req.drone_id = drone.id
            req.torre_id = TORRE_ID
            req.status = "alocada"
            req.rota = _gerar_rota(req.area_id, drone.torre_base)
            missoes[req.id] = req
            alocacoes.append((drone, req))

    for drone, req in alocacoes:
        log(f"DESPACHANDO drone={drone.nome} req={req.id} rota={' -> '.join(req.rota)}", G)
        threading.Thread(target=_sync_peers, daemon=True).start()
        threading.Thread(target=_executar_missao, args=(drone.id, req.id), daemon=True).start()


def _executar_missao(drone_id: str, req_id: str):
    import random

    duracao = random.uniform(15, 35)
    fim = time.time() + duracao
    log(f"Drone {drone_id} em voo req={req_id} por aproximadamente {duracao:.0f}s", C)

    while time.time() < fim:
        time.sleep(min(3, max(0.1, fim - time.time())))
        with _lock:
            if drone_id not in drones or req_id not in missoes:
                return
            drones[drone_id].ultimo_heartbeat = time.time()

    _handle_liberar_drone({"drone_id": drone_id, "req_id": req_id})


def _melhor_drone() -> Drone | None:
    livres = [d for d in drones.values() if d.disponivel and d.requisicao_atual is None]
    if not livres:
        return None
    return min(livres, key=lambda d: (d.missoes_concluidas, d.id))


def _gerar_rota(area_id: str, torre_base: str) -> list[str]:
    import random
    from config import COORDENADAS_AREAS

    ca = COORDENADAS_AREAS.get(area_id, {"lat": 26.5, "lon": 57.0})
    ct = COORDENADAS_TORRES.get(torre_base, {"lat": 26.9, "lon": 57.0})
    wp_lat = round((ct["lat"] + ca["lat"]) / 2 + random.uniform(-0.04, 0.04), 4)
    wp_lon = round((ct["lon"] + ca["lon"]) / 2 + random.uniform(-0.04, 0.04), 4)
    return [f"{ct['lat']},{ct['lon']}", f"{wp_lat},{wp_lon}", f"{ca['lat']},{ca['lon']}"]


def _handle_confirmar_despacho(payload: dict) -> dict:
    drone_id = payload.get("drone_id")
    with _lock:
        req = next((m for m in missoes.values() if m.drone_id == drone_id), None)
    return {"tipo": "ACK", "payload": {"ok": req is not None, "drone_id": drone_id, "req_id": getattr(req, "id", None)}}


def _handle_liberar_drone(payload: dict) -> dict:
    req_id = payload.get("req_id")
    drone_id = payload.get("drone_id")

    with _lock:
        req = missoes.pop(req_id, None) if req_id else None
        if req:
            req.status = "concluida"
            req_finalizadas.add(req.id)
            historico.insert(0, req)
            del historico[60:]

        if drone_id in drones:
            drones[drone_id].disponivel = True
            drones[drone_id].requisicao_atual = None
            drones[drone_id].ultimo_heartbeat = time.time()
            drones[drone_id].missoes_concluidas += 1

    relogio.tick()
    log(f"Drone liberado drone={drone_id} req={req_id}", G)
    threading.Thread(target=_sync_peers, daemon=True).start()
    threading.Thread(target=_tentar_alocar, daemon=True).start()
    return {"tipo": "ACK", "payload": {"status": "liberado", "drone_id": drone_id, "req_id": req_id}}


def _handle_falha_drone(payload: dict) -> dict:
    drone_id = payload.get("drone_id")
    with _lock:
        drone = drones.pop(drone_id, None)
        req_id = drone.requisicao_atual if drone else payload.get("req_id")
        req = missoes.pop(req_id, None) if req_id else None
        if req:
            req.status = "replanejada"
            req.drone_id = None
            req.clock_lamport = relogio.tick()
            req.critica = True
            req_vistas.discard(req.id)
            _enfileirar(req)

    if req_id:
        log(f"Falha do drone {drone_id}; req {req_id} voltou para a fila", R)
    threading.Thread(target=_sync_peers, daemon=True).start()
    threading.Thread(target=_tentar_alocar, daemon=True).start()
    return {"tipo": "ACK", "payload": {"status": "falha_registrada", "drone_id": drone_id, "req_id": req_id}}


def _handle_sync(payload: dict) -> dict:
    with _lock:
        req_vistas.update(payload.get("req_ids_vistas", []))
        req_finalizadas.update(payload.get("req_ids_finalizadas", []))
    return {"tipo": "ACK_SYNC", "payload": {"ok": True}}


def _sync_peers():
    with _lock:
        snap = {
            "torre_id": TORRE_ID,
            "req_ids_vistas": list(req_vistas),
            "req_ids_finalizadas": list(req_finalizadas),
        }
    for tid in list(peers.keys()):
        _enviar_tcp(tid, "SYNC_ESTADO", snap, tentativas=1)


def _handle_heartbeat(payload: dict) -> dict:
    drone_id = payload.get("drone_id")
    with _lock:
        if drone_id in drones:
            drones[drone_id].ultimo_heartbeat = time.time()
            return {"tipo": "ACK", "payload": {"ok": True, "drone_id": drone_id}}
    return {"tipo": "ERRO", "payload": {"msg": "drone nao encontrado", "drone_id": drone_id}}


def _handle_cadastrar_drone(payload: dict) -> dict:
    drone = Drone(
        id=payload.get("id", f"drone-{uuid.uuid4().hex[:6]}"),
        nome=payload.get("nome", "Novo Drone"),
        torre_base=payload.get("torre_base", TORRE_ID),
    )
    with _lock:
        drones[drone.id] = drone
    log(f"Drone cadastrado: {drone.nome} ({drone.id})", G)
    threading.Thread(target=_tentar_alocar, daemon=True).start()
    return {"tipo": "ACK", "payload": drone.to_dict()}


def _handle_remover_drone(payload: dict) -> dict:
    drone_id = payload.get("drone_id")
    with _lock:
        d = drones.get(drone_id)
        if d and d.requisicao_atual:
            return _handle_falha_drone({"drone_id": drone_id})
        d = drones.pop(drone_id, None)
    if d:
        log(f"Drone removido: {d.nome}", Y)
        return {"tipo": "ACK", "payload": {"removido": drone_id}}
    return {"tipo": "ERRO", "payload": {"msg": "nao encontrado"}}


def _handle_cadastrar_torre(payload: dict) -> dict:
    tid = payload.get("id")
    with _lock:
        peers[tid] = {"host": payload["host"], "porta": int(payload["porta"]), "nome": payload["nome"]}
    log(f"Torre cadastrada: {payload['nome']}", G)
    return {"tipo": "ACK", "payload": {"ok": True, "torre_id": tid}}

''' Envia uma mensagem TCP para outra torre e aguarda resposta, com retries 
tentativas é =2 '''
def _enviar_tcp(torre_id: str, tipo: str, payload: dict, tentativas: int = 2) -> bool:
    return _enviar_tcp_e_receber(torre_id, tipo, payload, tentativas=tentativas) is not None

''' Envia uma mensagem TCP para outra torre e aguarda resposta, com retries, aqui a troca de mensagens esta
acontecendo de fato e a resposta é retornada para quem chamou a função '''
def _enviar_tcp_e_receber(torre_id: str, tipo: str, payload: dict, tentativas: int = 2) -> dict | None:
    info = peers.get(torre_id)
    if not info:
        return None

    for tentativa in range(1, tentativas + 1):
        s = conectar_tcp(info["host"], info["porta"], TIMEOUT_TCP)
        if not s:
            log(f"Peer {torre_id} inacessivel tentativa={tentativa}", Y)
            continue
        try:
            s.settimeout(TIMEOUT_TCP)
            enviar_mensagem(s, tipo, payload, relogio)
            buf = []
            resp = receber_mensagem(s, relogio, buf)
            if resp:
                return resp
        except (OSError, socket.timeout):
            log(f"Timeout ao falar com {torre_id} tentativa={tentativa}", Y)
        finally:
            s.close()
        time.sleep(0.2 * tentativa)
    return None


def _monitor_heartbeat():
    while True:
        time.sleep(5)
        agora = time.time()
        falhos = []
        with _lock:
            for drone in drones.values():
                if drone.requisicao_atual and (agora - drone.ultimo_heartbeat) > HEARTBEAT_DRONE_TIMEOUT:
                    falhos.append(drone.id)
        for drone_id in falhos:
            _handle_falha_drone({"drone_id": drone_id})


def _montar_estado() -> dict:
    with _lock:
        return {
            "torre_id": TORRE_ID,
            "torre_nome": TORRE_NOME,
            "clock_lamport": relogio.valor,
            "requisicoes_ativas": len(missoes),
            "fila_tamanho": len(fila),
            "fila": [item[2].to_dict() for item in sorted(fila)],
            "drones": [d.to_dict() for d in drones.values()],
            "missoes": [r.to_dict() for r in missoes.values()],
            "historico": [r.to_dict() for r in historico[:20]],
            "peers": list(peers.keys()),
            "timestamp": time.time(),
        }


class HTTPHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _json(self, data: dict, code: int = 200):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,DELETE,PUT")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        if self.path == "/estado":
            self._json(_montar_estado())
        elif self.path == "/health":
            self._json({"ok": True, "clock": relogio.valor})
        else:
            self._json({"erro": "nao encontrado"}, 404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length)) if length else {}

        if self.path == "/drones":
            resp = _handle_cadastrar_drone(body)
            self._json(resp["payload"], 201)
        elif self.path.startswith("/drones/") and self.path.endswith("/delete"):
            drone_id = self.path.split("/")[2]
            resp = _handle_remover_drone({"drone_id": drone_id})
            self._json(resp["payload"])
        elif self.path == "/torres":
            resp = _handle_cadastrar_torre(body)
            self._json(resp["payload"], 201)
        elif self.path == "/falha_drone":
            resp = _handle_falha_drone(body)
            self._json(resp["payload"])
        elif self.path == "/liberar_drone":
            resp = _handle_liberar_drone(body)
            self._json(resp["payload"])
        else:
            self._json({"erro": "rota nao encontrada"}, 404)


def _start_http():
    srv = HTTPServer(("0.0.0.0", TORRE_HTTP_PORT), HTTPHandler)
    log(f"HTTP (web) na porta {TORRE_HTTP_PORT}", C)
    srv.serve_forever()


class ReusableThreadingTCPServer(ThreadingTCPServer):
    allow_reuse_address = True


if __name__ == "__main__":
    print(f"\n{M}{'=' * 65}")
    print(f"  TORRE: {TORRE_NOME} | TCP:{TORRE_PORT} | HTTP:{TORRE_HTTP_PORT}")
    print("  Broker distribuido P2P com Lamport e fila de prioridade")
    print(f"{'=' * 65}{W}\n")

    _init_peers()
    _init_drones()

    threading.Thread(target=_monitor_heartbeat, daemon=True).start()
    threading.Thread(target=_start_http, daemon=True).start()

    srv = ReusableThreadingTCPServer(("0.0.0.0", TORRE_PORT), TorreHandler)
    log(f"TCP escutando na porta {TORRE_PORT}", G)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        log("Encerrando torre.", Y)
        srv.shutdown()
