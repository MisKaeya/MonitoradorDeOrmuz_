"""Broker de setor/sensor - gera ocorrencias e solicita drones via TCP.

O broker de setor nao decide sozinho qual drone sera usado. Ele consulta os
brokers operacionais de drones (torres), envia SOLICITAR_DRONE com timeout,
aguarda ACK e retransmite para outro broker quando a comunicacao direta falha.
"""

import json
import os
import random
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, "/app/shared")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "shared"))
from config import (
    COORDENADAS_AREAS,
    INTERVALO_OCORRENCIA_MAX,
    INTERVALO_OCORRENCIA_MIN,
    OCORRENCIAS,
    TIMEOUT_TCP,
    TORRES,
)
from lamport import RelogioLamport, conectar_tcp, enviar_mensagem, receber_mensagem
from models import Requisicao

AREA_ID = os.environ.get("AREA_ID", "area-01")
AREA_NOME = os.environ.get("AREA_NOME", "Setor Alfa - Canal Norte")
AREA_PORT = int(os.environ.get("AREA_PORT", 7001))
AREA_HTTP_PORT = int(os.environ.get("AREA_HTTP_PORT", 7101))

R = "\033[91m"
Y = "\033[93m"
G = "\033[92m"
C = "\033[96m"
W = "\033[0m"
B = "\033[94m"

relogio = RelogioLamport(AREA_ID)
_lock = threading.Lock()
enviadas: list[Requisicao] = []
pausada = False
intervalo = random.uniform(INTERVALO_OCORRENCIA_MIN, INTERVALO_OCORRENCIA_MAX)
torres_extras: dict = {}


def log(msg, cor=C):
    print(f"{cor}[{AREA_NOME} | L={relogio.valor:04d}] {msg}{W}", flush=True)


def _consultar_carga(torre_id: str, info: dict) -> int | None:
    s = conectar_tcp(info["host"], info["porta"], TIMEOUT_TCP)
    if not s:
        return None
    try:
        s.settimeout(TIMEOUT_TCP)
        enviar_mensagem(s, "CONSULTA_ESTADO", {}, relogio)
        resp = receber_mensagem(s, relogio, [])
        if resp and resp.get("tipo") == "ESTADO":
            payload = resp.get("payload", {})
            return payload.get("requisicoes_ativas", 0) + payload.get("fila_tamanho", 0)
    except (OSError, socket.timeout):
        return None
    finally:
        s.close()
    return None


def _torres_por_carga() -> list[tuple[str, dict, int]]:
    cargas = []
    for tid, info in {**TORRES, **torres_extras}.items():
        carga = _consultar_carga(tid, info)
        if carga is None:
            log(f"{info['nome']} inacessivel", Y)
            continue
        log(f"{info['nome']} carga={carga} clock={relogio.valor}", B)
        cargas.append((tid, info, carga))
    return sorted(cargas, key=lambda item: (item[2], item[0]))


def _enviar_requisicao(req: Requisicao) -> bool:
    torres_disponiveis = _torres_por_carga()
    if not torres_disponiveis:
        log("Nenhuma torre disponivel; requisicao mantida como falha local", R)
        return False
    ''' Atualiza o relógio de Lamport antes de enviar a requisição para garantir 
    ordenação causal '''
    req.clock_lamport = relogio.before_send()
    log(f"Ocorrencia req={req.id} critica={req.critica} area={req.area_id} clock={req.clock_lamport}", C)

    for torre_id, info, _ in torres_disponiveis:
        '''Tentativas caso não hopuver resposta ou ACK da torre, tenta outra torre disponível'''
        for tentativa in range(1, 3):
            s = conectar_tcp(info["host"], info["porta"], TIMEOUT_TCP)
            if not s:
                log(f"Falha TCP em {info['nome']} tentativa={tentativa}", Y)
                continue
            ''' Envia a requisição para a torre e aguarda ACK. Se falhar, 
            tenta outra torre disponível '''
            try:
                s.settimeout(TIMEOUT_TCP)
                enviar_mensagem(s, "SOLICITAR_DRONE", req.to_dict(), relogio)
                resp = receber_mensagem(s, relogio, [])
                if resp and resp.get("tipo") == "ACK":
                    payload = resp.get("payload", {})
                    status = payload.get("status", "ack")
                    with _lock:
                        req.status = status
                        req.torre_id = payload.get("torre", torre_id)
                        enviadas.insert(0, req)
                        del enviadas[100:]
                    log(f"ACK status={status} torre={req.torre_id}", G)
                    return True
                log(f"Sem ACK de {info['nome']} tentativa={tentativa}", Y)
            except (OSError, socket.timeout) as exc:
                log(f"Erro TCP em {info['nome']}: {exc}", R)
            finally:
                s.close()
        log(f"Encaminhando req={req.id} para proxima torre disponivel", Y)

    log(f"Todas as torres falharam para req={req.id}", R)
    return False

'''criação efetiva das ocorrencias de forma aleatoria e envio para as torres,
 respeitando o intervalo configurado e o estado de pausa'''
def _loop_ocorrencias():
    log(f"Iniciada; gerando ocorrencias a cada aproximadamente {intervalo:.0f}s", G)
    time.sleep(random.uniform(1, 6))
    while True:
        with _lock:
            esta_pausada = pausada
            ivl = intervalo

        if not esta_pausada:
            relogio.tick()
            evento = random.choice(OCORRENCIAS)
            req = Requisicao(
                area_id=AREA_ID,
                area_nome=AREA_NOME,
                descricao=evento["descricao"],
                critica=evento["critica"],
            )
            _enviar_requisicao(req)

        time.sleep(random.uniform(ivl * 0.7, ivl * 1.3))

''' Handler HTTP para expor status e permitir controle de pausa/intervalo via API '''
class HTTPHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _json(self, data, code=200):
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
        self.send_header("Access-Control-Allow-Methods", "GET,POST")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        if self.path == "/status":
            coord = COORDENADAS_AREAS.get(AREA_ID, {})
            with _lock:
                self._json(
                    {
                        "area_id": AREA_ID,
                        "area_nome": AREA_NOME,
                        "porta_tcp": AREA_PORT,
                        "pausada": pausada,
                        "intervalo": intervalo,
                        "clock_lamport": relogio.valor,
                        "total_req": len(enviadas),
                        "lat": coord.get("lat"),
                        "lon": coord.get("lon"),
                        "historico": [r.to_dict() for r in enviadas[:20]],
                    }
                )
        elif self.path == "/health":
            self._json({"ok": True})
        else:
            self._json({"erro": "nao encontrado"}, 404)

    def do_POST(self):
        global pausada, intervalo
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length)) if length else {}

        if self.path == "/pausar":
            with _lock:
                pausada = True
            self._json({"pausada": True})
        elif self.path == "/retomar":
            with _lock:
                pausada = False
            self._json({"pausada": False})
        elif self.path == "/intervalo":
            with _lock:
                intervalo = max(2.0, float(body.get("intervalo", 10)))
            self._json({"intervalo": intervalo})
        elif self.path == "/ocorrencia_manual":
            req = Requisicao(
                id=body.get("id") or body.get("req_id") or Requisicao().id,
                area_id=AREA_ID,
                area_nome=AREA_NOME,
                descricao=body.get("descricao", "Ocorrencia manual"),
                critica=body.get("critica", False),
            )
            threading.Thread(target=_enviar_requisicao, args=(req,), daemon=True).start()
            self._json({"req_id": req.id, "status": "disparada"})
        else:
            self._json({"erro": "rota nao encontrada"}, 404)


def _start_http():
    srv = HTTPServer(("0.0.0.0", AREA_HTTP_PORT), HTTPHandler)
    log(f"HTTP na porta {AREA_HTTP_PORT}", C)
    srv.serve_forever()


if __name__ == "__main__":
    print(f"\n{'=' * 65}")
    print(f"  AREA: {AREA_NOME}")
    print(f"  TCP:{AREA_PORT} | HTTP:{AREA_HTTP_PORT} | Lamport ativo")
    print(f"{'=' * 65}\n")

    threading.Thread(target=_start_http, daemon=True).start()
    threading.Thread(target=_loop_ocorrencias, daemon=True).start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        log("Area encerrada.", Y)
