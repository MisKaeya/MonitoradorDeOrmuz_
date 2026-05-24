"""config.py — Configuração central do sistema Estreito de Ormuz v3 (TCP + Lamport)"""
import os

#  IPs das máquinas
MAQUINA_TORRES_IP  = os.environ.get("MAQUINA_TORRES_IP",  "192.168.1.10")
MAQUINA_BROKERS_IP = os.environ.get("MAQUINA_BROKERS_IP", "192.168.1.20")
MAQUINA_WEB_IP     = os.environ.get("MAQUINA_WEB_IP",     "192.168.1.30")

#  Torres
TORRES = {
    "torre-1": {"host": MAQUINA_TORRES_IP, "porta": 6001, "nome": "Torre Alpha"},
    "torre-2": {"host": MAQUINA_TORRES_IP, "porta": 6002, "nome": "Torre Bravo"},
    "torre-3": {"host": MAQUINA_TORRES_IP, "porta": 6003, "nome": "Torre Charlie"},
}

#  Brokers / Áreas
BROKERS = {
    "area-01": {"host": MAQUINA_BROKERS_IP, "porta": 7001, "nome": "Setor Alfa — Canal Norte"},
    "area-02": {"host": MAQUINA_BROKERS_IP, "porta": 7002, "nome": "Setor Bravo — Entrada Sul"},
    "area-03": {"host": MAQUINA_BROKERS_IP, "porta": 7003, "nome": "Setor Charlie — Rota Central"},
    "area-04": {"host": MAQUINA_BROKERS_IP, "porta": 7004, "nome": "Setor Delta — Corredor Leste"},
    "area-05": {"host": MAQUINA_BROKERS_IP, "porta": 7005, "nome": "Setor Echo — Zona Costeira"},
    "area-06": {"host": MAQUINA_BROKERS_IP, "porta": 7006, "nome": "Setor Foxtrot — Rota Comercial"},
    "area-07": {"host": MAQUINA_BROKERS_IP, "porta": 7007, "nome": "Setor Golf — Passagem Estreita"},
    "area-08": {"host": MAQUINA_BROKERS_IP, "porta": 7008, "nome": "Setor Hotel — Águas Profundas"},
    "area-09": {"host": MAQUINA_BROKERS_IP, "porta": 7009, "nome": "Setor India — Zona de Exclusão"},
    "area-10": {"host": MAQUINA_BROKERS_IP, "porta": 7010, "nome": "Setor Juliet — Ponto de Ancoragem"},
}

#  Web 
WEB_PORTA        = int(os.environ.get("WEB_PORTA", 8080))
# Porta HTTP interna de cada torre (só para a web consultar estado)
TORRE_HTTP_PORTA = {"torre-1": 6101, "torre-2": 6102, "torre-3": 6103}
# Porta HTTP interna de cada área (só para a web)
AREA_HTTP_PORTA  = {f"area-{i:02d}": 7100 + i for i in range(1, 11)}

# Comportamento
INTERVALO_OCORRENCIA_MIN = 8
INTERVALO_OCORRENCIA_MAX = 20
HEARTBEAT_DRONE_TIMEOUT  = 15
TIMEOUT_TCP              = 4    # segundos para tentativa de conexão TCP
BUFFER_SIZE              = 4096

# Ocorrências 
OCORRENCIAS = [
    {"descricao": "Embarcação civil à deriva",                      "critica": True},
    {"descricao": "Suspeita de bloqueio parcial de rota",           "critica": True},
    {"descricao": "Colisão iminente detectada por radar costeiro",  "critica": True},
    {"descricao": "Detecção de objeto não identificado submerso",   "critica": True},
    {"descricao": "Embarcação sem combustível reportada",           "critica": True},
    {"descricao": "Sinal de socorro — embarcação pequena",          "critica": True},
    {"descricao": "Falha de sinalização marítima",                  "critica": False},
    {"descricao": "Congestionamento em corredor marítimo",          "critica": False},
    {"descricao": "Embarcação sem transponder ativo",               "critica": False},
    {"descricao": "Anomalia em boia de sinalização",                "critica": False},
    {"descricao": "Necessidade de inspeção visual de rota",         "critica": False},
    {"descricao": "Replanejamento de tráfego por risco ambiental",  "critica": False},
    {"descricao": "Vazamento de óleo detectado por sensor naval",   "critica": False},
    {"descricao": "Embarcação suspeita sem identificação",          "critica": False},
    {"descricao": "Verificação de rota comercial programada",       "critica": False},
]

#  Coordenadas simuladas (Estreito de Ormuz)
COORDENADAS_AREAS = {
    "area-01": {"lat": 26.56, "lon": 56.25, "nome": "Canal Norte"},
    "area-02": {"lat": 26.38, "lon": 56.60, "nome": "Entrada Sul"},
    "area-03": {"lat": 26.50, "lon": 56.90, "nome": "Rota Central"},
    "area-04": {"lat": 26.62, "lon": 57.20, "nome": "Corredor Leste"},
    "area-05": {"lat": 26.30, "lon": 57.50, "nome": "Zona Costeira"},
    "area-06": {"lat": 26.70, "lon": 57.80, "nome": "Rota Comercial"},
    "area-07": {"lat": 26.45, "lon": 58.10, "nome": "Passagem Estreita"},
    "area-08": {"lat": 26.20, "lon": 58.40, "nome": "Águas Profundas"},
    "area-09": {"lat": 26.80, "lon": 58.70, "nome": "Zona de Exclusão"},
    "area-10": {"lat": 26.55, "lon": 59.00, "nome": "Ponto de Ancoragem"},
}

COORDENADAS_TORRES = {
    "torre-1": {"lat": 26.90, "lon": 56.50, "nome": "Torre Alpha"},
    "torre-2": {"lat": 26.90, "lon": 57.50, "nome": "Torre Bravo"},
    "torre-3": {"lat": 26.90, "lon": 58.50, "nome": "Torre Charlie"},
}
