import importlib.util
import sys
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "shared"))

spec = importlib.util.spec_from_file_location("torre_testavel", ROOT / "torres" / "torre.py")
torre = importlib.util.module_from_spec(spec)
spec.loader.exec_module(torre)

from models import Drone, Requisicao


class DispatchTest(unittest.TestCase):
    def setUp(self):
        with torre._lock:
            torre.drones.clear()
            torre.fila.clear()
            torre.missoes.clear()
            torre.historico.clear()
            torre.req_vistas.clear()
            torre.req_finalizadas.clear()
            torre.peers.clear()
            torre.peer_snapshots.clear()
            torre.peers_offline.clear()
            torre.redistribuicoes.clear()
            torre.drones["drone-a"] = Drone(id="drone-a", nome="Drone A", torre_base=torre.TORRE_ID)
        torre._sync_peers = lambda: None
        torre._executar_missao = lambda drone_id, req_id: None

    def test_requisicao_duplicada_concorrente_nao_gera_duplo_despacho(self):
        req = Requisicao(id="req-dup", area_id="area-01", area_nome="Area 1", critica=True)

        threads = [threading.Thread(target=torre._enfileirar, args=(req,)) for _ in range(50)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        torre._tentar_alocar()

        self.assertEqual(list(torre.missoes), ["req-dup"])
        self.assertEqual(torre.drones["drone-a"].requisicao_atual, "req-dup")
        self.assertEqual(len(torre.fila), 0)

    def test_criticidade_tem_prioridade_sobre_ordem_de_chegada(self):
        torre.drones.clear()
        normal = Requisicao(id="req-normal", area_id="area-01", critica=False, clock_lamport=1)
        critica = Requisicao(id="req-critica", area_id="area-02", critica=True, clock_lamport=2)

        torre._enfileirar(normal)
        torre._enfileirar(critica)
        with torre._lock:
            torre.drones["drone-a"] = Drone(id="drone-a", nome="Drone A", torre_base=torre.TORRE_ID)
        torre._tentar_alocar()

        self.assertIn("req-critica", torre.missoes)
        self.assertEqual([item[2].id for item in torre.fila], ["req-normal"])

    def test_falha_de_drone_replaneja_requisicao(self):
        req = Requisicao(id="req-falha", area_id="area-01", critica=False, clock_lamport=1)
        torre._enfileirar(req)
        torre._tentar_alocar()

        with torre._lock:
            torre.drones["drone-b"] = Drone(id="drone-b", nome="Drone B", torre_base=torre.TORRE_ID)
        torre._handle_falha_drone({"drone_id": "drone-a"})
        time.sleep(0.1)

        self.assertNotIn("drone-a", torre.drones)
        self.assertIn("req-falha", torre.missoes)
        self.assertEqual(torre.missoes["req-falha"].drone_id, "drone-b")

    def test_falha_de_torre_redistribui_snapshot(self):
        torre.drones.clear()
        req = Requisicao(
            id="req-peer",
            area_id="area-02",
            critica=False,
            clock_lamport=7,
            status="alocada",
            drone_id="peer-drone",
            torre_id="torre-2",
        )
        torre.peer_snapshots["torre-2"] = {
            "torre_id": "torre-2",
            "drones": [Drone(id="peer-drone", nome="Peer Drone", torre_base="torre-2", disponivel=False, requisicao_atual="req-peer").to_dict()],
            "missoes": [req.to_dict()],
            "fila": [],
            "req_ids_vistas": ["req-peer"],
            "req_ids_finalizadas": [],
        }

        resp = torre._assumir_snapshot_torre("torre-2", "teste")
        torre._tentar_alocar()

        self.assertTrue(resp["ok"])
        self.assertIn("peer-drone", torre.drones)
        self.assertEqual(torre.drones["peer-drone"].torre_base, torre.TORRE_ID)
        self.assertEqual(torre.drones["peer-drone"].torre_origem, "torre-2")
        self.assertIn("req-peer", torre.missoes)
        self.assertEqual(torre.missoes["req-peer"].status, "alocada")
        self.assertEqual(torre.redistribuicoes[0]["torre_origem"], "torre-2")
        self.assertEqual(torre.redistribuicoes[0]["drones_ids"], ["peer-drone"])


if __name__ == "__main__":
    unittest.main()
