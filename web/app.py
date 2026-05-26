"""
INTERFACE WEB — Dashboard + CRUD
Consulta as torres e áreas via HTTP (porta secundária).
Comunicação pesada (TCP + Lamport) fica entre os componentes operacionais.
"""
import os, sys, time, threading, requests, json, socket
from flask import Flask, render_template, jsonify, request
from flask_socketio import SocketIO
from flask_cors import CORS

sys.path.insert(0, "/app/shared")
from config import (TORRES, BROKERS, WEB_PORTA, TORRE_HTTP_PORTA,
                    AREA_HTTP_PORTA, COORDENADAS_AREAS, COORDENADAS_TORRES, OCORRENCIAS)

app = Flask(__name__)
app.config["SECRET_KEY"] = "ormuz-tcp-2024"
CORS(app)
sio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

TO = 2   # timeout HTTP para polling

_areas_extra  = {}
_torres_extra = {}
CONTAINERS_TORRES = {tid: tid for tid in TORRES}
CONTAINERS_AREAS = {aid: aid for aid in BROKERS}

# ── Coleta ────────────────────────────────────────────────────────────────────
def _get_torre(torre_id: str, info: dict) -> dict:
    http_port = TORRE_HTTP_PORTA.get(torre_id, info["porta"] + 100)
    try:
        r = requests.get(f"http://{info['host']}:{http_port}/estado", timeout=TO)
        if r.ok:
            d = r.json(); d["online"] = True; return d
    except Exception:
        pass
    return {"torre_id": torre_id, "torre_nome": info["nome"], "online": False,
            "drones": [], "missoes": [], "fila_tamanho": 0,
            "requisicoes_ativas": 0, "clock_lamport": 0}


def _get_area(area_id: str, info: dict) -> dict:
    http_port = AREA_HTTP_PORTA.get(area_id, info["porta"] + 100)
    try:
        r = requests.get(f"http://{info['host']}:{http_port}/status", timeout=TO)
        if r.ok:
            d = r.json(); d["online"] = True; return d
    except Exception:
        pass
    return {"area_id": area_id, "area_nome": info["nome"], "online": False,
            "total_req": 0, "pausada": False, "clock_lamport": 0}


def _estado_global() -> dict:
    torres_all = {**TORRES, **_torres_extra}
    areas_all  = {**BROKERS, **_areas_extra}
    torres_estado, drones_todos, missoes_todas, hist, redist = [], [], [], [], []

    for tid, info in torres_all.items():
        t = _get_torre(tid, info)
        torres_estado.append(t)
        drones_todos.extend(t.get("drones", []))
        missoes_todas.extend(t.get("missoes", []))
        hist.extend(t.get("historico", []))
        redist.extend(t.get("redistribuicoes", []))

    hist.sort(key=lambda x: (-x.get("clock_lamport", 0), -x.get("timestamp", 0)))
    redist.sort(key=lambda x: (-x.get("clock_lamport", 0), -x.get("timestamp", 0)))

    return {
        "torres":             torres_estado,
        "drones":             drones_todos,
        "missoes":            missoes_todas,
        "areas":              [_get_area(aid, info) for aid, info in areas_all.items()],
        "historico":          hist[:30],
        "redistribuicoes":    redist[:30],
        "coordenadas_areas":  COORDENADAS_AREAS,
        "coordenadas_torres": COORDENADAS_TORRES,
        "timestamp":          time.time(),
    }


def _torres_config():
    return {**TORRES, **_torres_extra}


def _areas_config():
    return {**BROKERS, **_areas_extra}


def _post_torre(torre_id: str, path: str, payload: dict | None = None):
    todas = _torres_config()
    info = todas.get(torre_id)
    if not info:
        return None
    http_port = TORRE_HTTP_PORTA.get(torre_id, info["porta"] + 100)
    return requests.post(f"http://{info['host']}:{http_port}{path}", json=payload or {}, timeout=TO)


def _docker_request(method: str, path: str) -> tuple[int, dict]:
    sock_path = "/var/run/docker.sock"
    if not os.path.exists(sock_path):
        return 503, {"erro": "socket Docker nao montado na interface web"}

    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(5)
        s.connect(sock_path)
        req = (
            f"{method} {path} HTTP/1.1\r\n"
            "Host: docker\r\n"
            "Content-Length: 0\r\n"
            "Connection: close\r\n\r\n"
        )
        s.sendall(req.encode("utf-8"))
        chunks = []
        while True:
            chunk = s.recv(65536)
            if not chunk:
                break
            chunks.append(chunk)

    raw = b"".join(chunks)
    head, _, body = raw.partition(b"\r\n\r\n")
    status_line = head.splitlines()[0].decode("utf-8", errors="replace")
    status = int(status_line.split()[1])
    if not body:
        return status, {"ok": 200 <= status < 300}
    try:
        return status, json.loads(body.decode("utf-8"))
    except json.JSONDecodeError:
        return status, {"ok": 200 <= status < 300, "raw": body.decode("utf-8", errors="replace")}


def _container_action(container_name: str, action: str) -> tuple[dict, int]:
    if action == "start":
        status, data = _docker_request("POST", f"/containers/{container_name}/start")
        if status in (204, 304):
            return {"ok": True, "container": container_name, "acao": "start"}, 200
    elif action == "stop":
        status, data = _docker_request("POST", f"/containers/{container_name}/stop?t=2")
        if status in (204, 304):
            return {"ok": True, "container": container_name, "acao": "stop"}, 200
    else:
        return {"erro": "acao invalida"}, 400
    return {"erro": data.get("message") or data.get("erro") or f"Docker HTTP {status}"}, status


def _pusher():
    while True:
        time.sleep(3)
        try:
            sio.emit("estado", _estado_global())
        except Exception:
            pass

threading.Thread(target=_pusher, daemon=True).start()

# ════════════════════════════════════════════════════════════════════════════
# ROTAS
# ════════════════════════════════════════════════════════════════════════════

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/estado")
def api_estado():
    return jsonify(_estado_global())


# ── Torres ────────────────────────────────────────────────────────────────────
@app.route("/api/torres", methods=["GET"])
def api_torres():
    return jsonify([_get_torre(tid, info) for tid, info in _torres_config().items()])


@app.route("/api/torres", methods=["POST"])
def api_add_torre():
    data = request.get_json()
    tid  = data.get("id", f"torre-{int(time.time())}")
    _torres_extra[tid] = {"host": data["host"], "porta": int(data["porta"]), "nome": data["nome"]}
    # Notifica torres existentes via HTTP
    for torre_id, info in TORRES.items():
        http_port = TORRE_HTTP_PORTA.get(torre_id, info["porta"] + 100)
        try:
            requests.post(
                f"http://{info['host']}:{http_port}/torres",
                json={**data, "id": tid},
                timeout=TO,
            )
        except Exception:
            pass
    return jsonify({"id": tid, "ok": True}), 201


@app.route("/api/torres/<torre_id>", methods=["DELETE"])
def api_del_torre(torre_id):
    _torres_extra.pop(torre_id, None)
    return jsonify({"removida": torre_id})


@app.route("/api/torres/<torre_id>/redistribuir", methods=["POST"])
def api_redistribuir_torre(torre_id):
    info_origem = _torres_config().get(torre_id)
    snapshot = None
    if info_origem:
        estado = _get_torre(torre_id, info_origem)
        if estado.get("online"):
            snapshot = estado

    resultados = []
    for destino, info in _torres_config().items():
        if destino == torre_id:
            continue
        try:
            payload = {"torre_id": torre_id}
            if snapshot:
                payload["snapshot"] = snapshot
            r = _post_torre(destino, "/redistribuir_torre", payload)
            if r is not None and r.ok:
                data = r.json()
                resultados.append({"destino": destino, **data})
                if data.get("ok"):
                    return jsonify({"ok": True, "resultados": resultados})
        except Exception as exc:
            resultados.append({"destino": destino, "ok": False, "erro": str(exc)})
    return jsonify({"ok": False, "erro": "nenhuma torre assumiu o snapshot", "resultados": resultados}), 409


@app.route("/api/torres/<torre_id>/derrubar", methods=["POST"])
def api_derrubar_torre(torre_id):
    container = CONTAINERS_TORRES.get(torre_id)
    if not container:
        return jsonify({"erro": "torre sem container conhecido"}), 404

    info_origem = _torres_config().get(torre_id)
    snapshot = _get_torre(torre_id, info_origem) if info_origem else None

    data, status = _container_action(container, "stop")
    if status >= 400:
        return jsonify(data), status

    resultados = []
    try:
        for destino in _torres_config().keys():
            if destino != torre_id:
                payload = {"torre_id": torre_id}
                if snapshot and snapshot.get("online"):
                    payload["snapshot"] = snapshot
                r = _post_torre(destino, "/redistribuir_torre", payload)
                if r is not None and r.ok:
                    resultados.append({"destino": destino, **r.json()})
    except Exception as exc:
        resultados.append({"ok": False, "erro": str(exc)})

    return jsonify({**data, "redistribuicao": resultados}), status


@app.route("/api/torres/<torre_id>/levantar", methods=["POST"])
def api_levantar_torre(torre_id):
    container = CONTAINERS_TORRES.get(torre_id)
    if not container:
        return jsonify({"erro": "torre sem container conhecido"}), 404
    data, status = _container_action(container, "start")
    return jsonify(data), status


# ── Drones ────────────────────────────────────────────────────────────────────
@app.route("/api/drones", methods=["GET"])
def api_drones():
    return jsonify(_estado_global()["drones"])


@app.route("/api/drones", methods=["POST"])
def api_add_drone():
    return _api_add_drone_payload(request.get_json())


def _api_add_drone_payload(data: dict):
    torre_alvo = data.get("torre_base", list(TORRES.keys())[0])
    todas      = _torres_config()
    info       = todas.get(torre_alvo) or list(todas.values())[0]
    http_port  = TORRE_HTTP_PORTA.get(torre_alvo, info["porta"] + 100)
    try:
        r = requests.post(f"http://{info['host']}:{http_port}/drones", json=data, timeout=TO)
        return jsonify(r.json()), r.status_code
    except Exception as e:
        return jsonify({"erro": str(e)}), 500


@app.route("/api/drones/<drone_id>", methods=["DELETE"])
def api_del_drone(drone_id):
    for tid, info in _torres_config().items():
        http_port = TORRE_HTTP_PORTA.get(tid, info["porta"] + 100)
        try:
            r = requests.post(
                f"http://{info['host']}:{http_port}/drones/{drone_id}/delete",
                json={}, timeout=TO,
            )
            if r.ok:
                return jsonify(r.json())
        except Exception:
            pass
    return jsonify({"erro": "não encontrado"}), 404


@app.route("/api/drones/<drone_id>/falha", methods=["POST"])
def api_falha_drone(drone_id):
    for tid in _torres_config().keys():
        try:
            r = _post_torre(tid, "/falha_drone", {"drone_id": drone_id})
            if r is not None and r.ok:
                data = r.json()
                if data.get("encontrado") or data.get("req_id"):
                    return jsonify(data), r.status_code
        except Exception:
            pass
    return jsonify({"erro": "drone não encontrado"}), 404


@app.route("/api/drones/<drone_id>/levantar", methods=["POST"])
def api_levantar_drone(drone_id):
    data = request.get_json(silent=True) or {}
    torre_alvo = data.get("torre_base") or data.get("torre_id") or list(TORRES.keys())[0]
    nome = data.get("nome") or drone_id
    return _api_add_drone_payload({"id": drone_id, "nome": nome, "torre_base": torre_alvo})


# ── Áreas ─────────────────────────────────────────────────────────────────────
@app.route("/api/areas", methods=["GET"])
def api_areas():
    return jsonify([_get_area(aid, info) for aid, info in _areas_config().items()])


@app.route("/api/areas", methods=["POST"])
def api_add_area():
    data   = request.get_json()
    area_id = data.get("id", f"area-{int(time.time())}")
    _areas_extra[area_id] = {
        "host":  data.get("host",  "127.0.0.1"),
        "porta": int(data.get("porta", 7099)),
        "nome":  data.get("nome",  area_id),
    }
    return jsonify({"id": area_id, "ok": True}), 201


@app.route("/api/areas/<area_id>", methods=["DELETE"])
def api_del_area(area_id):
    _areas_extra.pop(area_id, None)
    return jsonify({"removida": area_id})


@app.route("/api/areas/<area_id>/pausar", methods=["POST"])
def api_pausar(area_id):
    todas = _areas_config()
    info  = todas.get(area_id)
    if not info:
        return jsonify({"erro": "não encontrada"}), 404
    http_port = AREA_HTTP_PORTA.get(area_id, info["porta"] + 100)
    try:
        r = requests.post(f"http://{info['host']}:{http_port}/pausar", timeout=TO)
        return jsonify(r.json()), r.status_code
    except Exception as e:
        return jsonify({"erro": str(e)}), 500


@app.route("/api/areas/<area_id>/retomar", methods=["POST"])
def api_retomar(area_id):
    todas = _areas_config()
    info  = todas.get(area_id)
    if not info:
        return jsonify({"erro": "não encontrada"}), 404
    http_port = AREA_HTTP_PORTA.get(area_id, info["porta"] + 100)
    try:
        r = requests.post(f"http://{info['host']}:{http_port}/retomar", timeout=TO)
        return jsonify(r.json()), r.status_code
    except Exception as e:
        return jsonify({"erro": str(e)}), 500


@app.route("/api/areas/<area_id>/ocorrencia", methods=["POST"])
def api_ocorrencia(area_id):
    todas = _areas_config()
    info  = todas.get(area_id)
    if not info:
        return jsonify({"erro": "não encontrada"}), 404
    http_port = AREA_HTTP_PORTA.get(area_id, info["porta"] + 100)
    try:
        r = requests.post(
            f"http://{info['host']}:{http_port}/ocorrencia_manual",
            json=request.get_json(), timeout=TO,
        )
        return jsonify(r.json()), r.status_code
    except Exception as e:
        return jsonify({"erro": str(e)}), 500


@app.route("/api/areas/<area_id>/intervalo", methods=["POST"])
def api_intervalo(area_id):
    todas = _areas_config()
    info = todas.get(area_id)
    if not info:
        return jsonify({"erro": "não encontrada"}), 404
    http_port = AREA_HTTP_PORTA.get(area_id, info["porta"] + 100)
    try:
        r = requests.post(
            f"http://{info['host']}:{http_port}/intervalo",
            json=request.get_json(), timeout=TO,
        )
        return jsonify(r.json()), r.status_code
    except Exception as e:
        return jsonify({"erro": str(e)}), 500


@app.route("/api/areas/<area_id>/derrubar", methods=["POST"])
def api_derrubar_area(area_id):
    container = CONTAINERS_AREAS.get(area_id)
    if not container:
        return jsonify({"erro": "area sem container conhecido"}), 404
    data, status = _container_action(container, "stop")
    return jsonify(data), status


@app.route("/api/areas/<area_id>/levantar", methods=["POST"])
def api_levantar_area(area_id):
    container = CONTAINERS_AREAS.get(area_id)
    if not container:
        return jsonify({"erro": "area sem container conhecido"}), 404
    data, status = _container_action(container, "start")
    return jsonify(data), status


@app.route("/api/ocorrencias_tipos")
def api_tipos():
    return jsonify(OCORRENCIAS)


@app.route("/health")
def health():
    return jsonify({"ok": True})


if __name__ == "__main__":
    print(f"\n{'═'*60}")
    print(f"  🌊  INTERFACE WEB TCP+Lamport — porta {WEB_PORTA}")
    print(f"{'═'*60}\n")
    sio.run(app, host="0.0.0.0", port=WEB_PORTA, debug=False, allow_unsafe_werkzeug=True)
