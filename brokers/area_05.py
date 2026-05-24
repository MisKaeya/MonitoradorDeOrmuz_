"""Broker area-05 — Setor Echo — Zona Costeira"""
import os, sys
sys.path.insert(0, "/app/shared")
os.environ.setdefault("AREA_ID",        "area-05")
os.environ.setdefault("AREA_NOME",      "Setor Echo — Zona Costeira")
os.environ.setdefault("AREA_PORT",      "7005")
os.environ.setdefault("AREA_HTTP_PORT", "7105")
''' Abre o arquivo do nucle do sistem e le oc odigo de lá como uma string e 
usa o exec para executar dinamicamente dentro do memo contexto de memória'''
with open("/app/brokers/broker_base.py") as f:
    exec(f.read())
