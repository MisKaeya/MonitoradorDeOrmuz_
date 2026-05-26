"""models.py — Estruturas de dados do sistema."""
import uuid, time
from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class Requisicao:
    id:             str   = field(default_factory=lambda: str(uuid.uuid4())[:8])
    area_id:        str   = ""
    area_nome:      str   = ""
    descricao:      str   = ""
    critica:        bool  = False
    timestamp:      float = field(default_factory=time.time)
    clock_lamport:  int   = 0      # ← relógio de Lamport no momento do envio
    drone_id:       Optional[str] = None
    torre_id:       Optional[str] = None
    status:         str   = "pendente"
    rota:           list  = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "Requisicao":
        campos = Requisicao.__dataclass_fields__.keys()
        return Requisicao(**{k: v for k, v in d.items() if k in campos})

    def prioridade(self) -> tuple:
        """
        Chave de ordenação da fila de prioridade:
          1º critério: criticidade (0=crítica tem prioridade sobre 1=normal)
          2º critério: clock de Lamport (menor = evento anterior causalmente)
          3º critério: wall time (desempate em casos sem causalidade)
        """
        return (0 if self.critica else 1, self.clock_lamport, self.timestamp)


@dataclass
class Drone:
    id:                  str
    nome:                str
    torre_base:          str
    torre_origem:        Optional[str] = None
    disponivel:          bool  = True
    requisicao_atual:    Optional[str] = None
    ultimo_heartbeat:    float = field(default_factory=time.time)
    missoes_concluidas:  int   = 0

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "Drone":
        campos = Drone.__dataclass_fields__.keys()
        return Drone(**{k: v for k, v in d.items() if k in campos})
