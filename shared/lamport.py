"""
lamport.py — Relógio Lógico de Lamport + protocolo de framing TCP

Regras do relógio de Lamport:
  1. Evento local:     clock += 1
  2. Envio de msg:     clock += 1  →  envia clock junto na mensagem
  3. Recebimento:      clock = max(clock_local, clock_recebido) + 1

Isso garante: se evento A causou evento B, então clock(A) < clock(B).
Na fila de requisições, ordenamos por (clock_lamport, timestamp_wall)
para respeitar a causalidade mesmo entre máquinas com relógios diferentes.

Protocolo de framing TCP:
  Cada mensagem é um JSON terminado por '\n' (newline delimiter).
  Isso permite múltiplas mensagens numa mesma conexão TCP sem ambiguidade.
"""

import threading
import json
import socket
import time


class RelogioLamport:
    """
    Relógio lógico de Lamport thread-safe.
    Cada componente do sistema instancia um único relógio.
    """

    def __init__(self, dono: str):
        self.dono  = dono      # identificador do componente (ex: "torre-1")
        self._clock = 0
        self._lock  = threading.Lock()

    # Evento local (processamento interno) 
    def tick(self) -> int:
        with self._lock:
            self._clock += 1
            return self._clock

    # Antes de ENVIAR uma mensagem 
    def before_send(self) -> int:
        with self._lock:
            self._clock += 1
            return self._clock

    #  Ao RECEBER uma mensagem com clock remoto 
    def on_receive(self, clock_remoto: int) -> int:
        with self._lock:
            self._clock = max(self._clock, clock_remoto) + 1
            return self._clock

    @property
    def valor(self) -> int:
        with self._lock:
            return self._clock

    def __repr__(self):
        return f"Lamport({self.dono}, clock={self._clock})"



# PROTOCOLO TCP — framing por newline

def enviar_mensagem(sock: socket.socket, tipo: str, payload: dict, relogio: RelogioLamport) -> None:
    """
    Serializa e envia uma mensagem pelo socket TCP.
    Formato: JSON + '\\n'
    Inclui automaticamente o clock de Lamport.
    """
    clock = relogio.before_send()
    mensagem = {
        "tipo":          tipo,
        "payload":       payload,
        "clock_lamport": clock,
        "remetente":     relogio.dono,
        "wall_time":     time.time(),
    }
    dados = (json.dumps(mensagem) + "\n").encode("utf-8")
    sock.sendall(dados)


def receber_mensagem(sock: socket.socket, relogio: RelogioLamport, buffer: list) -> dict | None:
    """
    Lê uma mensagem completa do socket (delimitada por '\\n').
    Usa buffer acumulador para lidar com fragmentação TCP.
    Atualiza o relógio de Lamport ao receber.
    Retorna o dicionário da mensagem ou None em caso de erro/fechamento.
    """
    while True:
        # Verifica se já há uma mensagem completa no buffer
        dados_str = b"".join(buffer).decode("utf-8", errors="replace")
        if "\n" in dados_str:
            linha, resto = dados_str.split("\n", 1)
            buffer.clear()
            buffer.append(resto.encode("utf-8"))
            try:
                msg = json.loads(linha)
                # Atualiza relógio de Lamport
                clock_remoto = msg.get("clock_lamport", 0)
                relogio.on_receive(clock_remoto)
                return msg
            except json.JSONDecodeError:
                return None

        # Precisa de mais dados
        try:
            chunk = sock.recv(4096)
            if not chunk:
                return None   # conexão fechada
            buffer.append(chunk)
        except (ConnectionResetError, OSError):
            return None


def conectar_tcp(host: str, porta: int, timeout: float = 4.0) -> socket.socket | None:
    """Cria e retorna um socket TCP conectado, ou None em caso de falha."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect((host, porta))
        s.settimeout(None)   # modo bloqueante após conexão
        return s
    except (ConnectionRefusedError, TimeoutError, OSError):
        return None
