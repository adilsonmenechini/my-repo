"""Testes do ProbeEngine: probe paralelo, cache TTL e quarentena por instância."""

from __future__ import annotations

import json
import sys
import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from helpers import quiet

from server_pool.models import Instance
from server_pool.probe import (
    PROBE_PROMPT,
    PROBE_TTL_SECONDS,
    ProbeEngine,
    classify_model_probe,
    probe_payload,
)

OK_BODY = '{"choices": [], "usage": {"completion_tokens": 3}}'
ERR_BODY = '{"error": {"message": "boom"}}'
THINKING_BODY = '{"usage": {"output_tokens_details": {"reasoning_tokens": 506}}}'
POUCO_BODY = '{"usage": {"output_tokens_details": {"reasoning_tokens": 42}}}'


def instance(host: str = "localhost:20128") -> Instance:
    return Instance(name="test", host=host, password="123456")


class FakeTransport:
    """Substitui requests.post: devolve (status, corpo) por modelo.

    `delay` segura a resposta para expor o paralelismo; `in_flight` /
    `max_in_flight` medem quantas sondagens estavam abertas ao mesmo tempo.
    """

    def __init__(
        self,
        statuses: dict[str, int],
        bodies: dict[str, str] | None = None,
        delay: float = 0.0,
        explode: set[str] | None = None,
    ) -> None:
        self.statuses = statuses
        self.bodies = bodies or {}
        self.delay = delay
        self.explode = explode or set()
        self.calls: list[str] = []
        self.in_flight = 0
        self.max_in_flight = 0
        self._lock = threading.Lock()

    def __call__(self, url, json=None, headers=None, timeout=None):
        model = json["model"]
        with self._lock:
            self.calls.append(model)
            self.in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            if self.delay:
                time.sleep(self.delay)
            if model in self.explode:
                raise OSError("conexão recusada")
            status = self.statuses[model]
            body = self.bodies.get(
                model, OK_BODY if status == 200 else ERR_BODY
            )
            return SimpleNamespace(status_code=status, text=body)
        finally:
            with self._lock:
                self.in_flight -= 1


class EngineTestCase(unittest.TestCase):
    """Base: aponta os dois arquivos de estado para um tmp, nunca pro repo."""

    def setUp(self) -> None:
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.state_file = Path(tmp.name) / "model-state.json"
        self.failures_file = Path(tmp.name) / "model-failures.json"

    def engine(
        self,
        transport: FakeTransport,
        host: str = "localhost:20128",
        workers: int = 6,
        ttl: int = PROBE_TTL_SECONDS,
        clock=None,
    ) -> ProbeEngine:
        return ProbeEngine(
            instance=instance(host),
            api_key="sk-test",
            transport=transport,
            workers=workers,
            ttl=ttl,
            state_path=self.state_file,
            failures_path=self.failures_file,
            clock=clock,
        )

    def read_state(self) -> dict:
        if not self.state_file.exists():
            return {}
        return json.loads(self.state_file.read_text(encoding="utf-8"))

    def write_state(self, data: dict) -> None:
        self.state_file.write_text(json.dumps(data), encoding="utf-8")

    def write_failures(self, data: dict) -> None:
        self.failures_file.write_text(json.dumps(data), encoding="utf-8")

    def read_failures(self) -> dict:
        if not self.failures_file.exists():
            return {}
        return json.loads(self.failures_file.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------
# classificação
# --------------------------------------------------------------------------


class ProbePayloadTest(unittest.TestCase):
    """O limiar só significa algo se o probe pedir thinking forte."""

    def test_payload_pede_effort_high(self) -> None:
        payload = probe_payload("oc/a-free", "What is 2+2?", 2000)

        self.assertEqual(payload["reasoning_effort"], "high")

    def test_prompt_canonico_e_o_dificil(self) -> None:
        """Valor do prompt: o fácil dava 0-4 dos dois lados do corte."""
        self.assertEqual(
            PROBE_PROMPT,
            "A bakery sells croissants for $3 each. You buy 7, pay with a $50 "
            "note and get change. Then you return 2 croissants. How much "
            "money do you have spent in the end? Reason step by step "
            "before answering.",
        )


class ClassifyLimiarTest(unittest.TestCase):
    """Fronteira exata entre o combo do opus e o do sonnet."""

    def test_49_tokens_e_no_thinking(self) -> None:
        text = (
            '{"usage": {"completion_tokens_details": {"reasoning_tokens": 49}}}'
        )

        self.assertEqual(classify_model_probe(200, text), ("no_thinking", 49))

    def test_50_tokens_e_thinking(self) -> None:
        text = (
            '{"usage": {"completion_tokens_details": {"reasoning_tokens": 50}}}'
        )

        self.assertEqual(classify_model_probe(200, text), ("thinking", 50))


class ClassifyTest(EngineTestCase):
    def test_200_vivo_e_classifica_thinking(self) -> None:
        t = FakeTransport(
            {"oc/a-free": 200}, {"oc/a-free": THINKING_BODY}
        )
        result = self.engine(t).check(["oc/a-free"], force=True, memory=False)

        self.assertTrue(result["oc/a-free"].alive)
        self.assertEqual(result["oc/a-free"].kind, "thinking")
        self.assertEqual(result["oc/a-free"].reasoning_tokens, 506)
        self.assertFalse(result["oc/a-free"].from_cache)

    def test_200_pouco_reasoning_e_no_thinking(self) -> None:
        """42 tokens é pensar pouco: pensa, mas não passa do limiar do opus."""
        t = FakeTransport({"oc/a-free": 200}, {"oc/a-free": POUCO_BODY})
        result = self.engine(t).check(["oc/a-free"], force=True, memory=False)

        self.assertEqual(result["oc/a-free"].kind, "no_thinking")
        self.assertEqual(result["oc/a-free"].reasoning_tokens, 42)

    def test_200_sem_reasoning_e_no_thinking(self) -> None:
        t = FakeTransport({"oc/a-free": 200})
        result = self.engine(t).check(["oc/a-free"], force=True, memory=False)

        self.assertEqual(result["oc/a-free"].kind, "no_thinking")
        self.assertTrue(result["oc/a-free"].alive)

    def test_200_com_erro_no_corpo_e_morto(self) -> None:
        """O upstream devolve 200 com {"type":"error"} quando recusa o turno."""
        t = FakeTransport(
            {"oc/a-free": 200},
            {"oc/a-free": '{"type": "error", "error": {"message": "boom"}}'},
        )
        result = self.engine(t).check(["oc/a-free"], force=True, memory=False)

        self.assertFalse(result["oc/a-free"].alive)

    def test_200_sem_usage_e_morto(self) -> None:
        """200 sem `usage` não é completion: é resposta vazia do upstream."""
        t = FakeTransport(
            {"oc/a-free": 200}, {"oc/a-free": '{"type": "response.completed"}'}
        )
        result = self.engine(t).check(["oc/a-free"], force=True, memory=False)

        self.assertFalse(result["oc/a-free"].alive)

    def test_200_sem_usage_e_morto_fora_de_strict(self) -> None:
        """No watch também: sem usage não há evidência de que funcionou."""
        t = FakeTransport(
            {"oc/a-free": 200}, {"oc/a-free": '{"type": "response.completed"}'}
        )
        result = self.engine(t).check(
            ["oc/a-free"], force=True, memory=True, strict=False
        )

        self.assertFalse(result["oc/a-free"].alive)

    def test_403_e_morto_com_strict(self) -> None:
        t = FakeTransport({"oc/a-free": 403})
        result = self.engine(t).check(["oc/a-free"], force=True, memory=False)

        self.assertFalse(result["oc/a-free"].alive)
        self.assertEqual(result["oc/a-free"].kind, "stable")
        self.assertEqual(result["oc/a-free"].status, 403)

    def test_nao_strict_mantem_transitorio_vivo(self) -> None:
        """Um 500 isolado não pode derrubar o modelo do combo no watch."""
        t = FakeTransport({"oc/a-free": 500})
        result = self.engine(t).check(
            ["oc/a-free"], force=True, memory=True, strict=False
        )

        self.assertTrue(result["oc/a-free"].alive)

    def test_nao_strict_remove_quarentenado(self) -> None:
        t = FakeTransport({"oc/a-free": 403})
        result = self.engine(t).check(
            ["oc/a-free"], force=True, memory=True, strict=False
        )

        self.assertFalse(result["oc/a-free"].alive)

    def test_excecao_de_rede_nao_grava_memoria(self) -> None:
        t = FakeTransport({}, explode={"oc/a-free"})
        result = self.engine(t).check(
            ["oc/a-free"], force=True, memory=True
        )

        self.assertFalse(result["oc/a-free"].alive)
        self.assertEqual(self.read_failures(), {})

    def test_excecao_de_rede_nao_derruba_fora_de_strict(self) -> None:
        """Sem resposta não há evidência: no watch o modelo segue no combo."""
        t = FakeTransport({}, explode={"oc/a-free"})
        result = self.engine(t).check(
            ["oc/a-free"], force=True, memory=True, strict=False
        )

        self.assertTrue(result["oc/a-free"].alive)


# --------------------------------------------------------------------------
# paralelismo
# --------------------------------------------------------------------------


class ConcurrencyTest(EngineTestCase):
    def test_sonda_em_paralelo(self) -> None:
        models = [f"m{i}-free" for i in range(6)]
        t = FakeTransport({m: 200 for m in models}, delay=0.05)

        result = self.engine(t, workers=6).check(models, force=True, memory=False)

        self.assertEqual(len(result), 6)
        self.assertGreater(t.max_in_flight, 1)

    def test_nao_ultrapassa_o_limite_de_workers(self) -> None:
        models = [f"m{i}-free" for i in range(8)]
        t = FakeTransport({m: 200 for m in models}, delay=0.02)

        self.engine(t, workers=2).check(models, force=True, memory=False)

        self.assertLessEqual(t.max_in_flight, 2)


# --------------------------------------------------------------------------
# cache TTL
# --------------------------------------------------------------------------


class CacheTest(EngineTestCase):
    def test_cache_fresco_pula_o_probe(self) -> None:
        t = FakeTransport({"oc/a-free": 200})
        engine = self.engine(t, ttl=1800)
        engine.check(["oc/a-free"], force=False, memory=False)

        result = engine.check(["oc/a-free"], force=False, memory=False)

        self.assertEqual(t.calls, ["oc/a-free"])
        self.assertTrue(result["oc/a-free"].from_cache)
        self.assertTrue(result["oc/a-free"].alive)

    def test_cache_expirado_sonda_de_novo(self) -> None:
        now = [10_000_000.0]
        t = FakeTransport({"oc/a-free": 200})
        engine = self.engine(t, ttl=1800, clock=lambda: now[0])
        engine.check(["oc/a-free"], force=False, memory=False)

        now[0] += 1801
        engine.check(["oc/a-free"], force=False, memory=False)

        self.assertEqual(t.calls, ["oc/a-free", "oc/a-free"])

    def test_force_ignora_o_cache(self) -> None:
        t = FakeTransport({"oc/a-free": 200})
        engine = self.engine(t, ttl=1800)
        engine.check(["oc/a-free"], force=False, memory=False)

        engine.check(["oc/a-free"], force=True, memory=False)

        self.assertEqual(t.calls, ["oc/a-free", "oc/a-free"])

    def test_force_nao_grava_o_cache(self) -> None:
        t = FakeTransport({"oc/a-free": 200})
        self.engine(t, ttl=1800).check(
            ["oc/a-free"], force=True, memory=False
        )

        self.assertFalse(self.state_file.exists())

    def test_transitorio_nao_entra_no_cache(self) -> None:
        """500 não vale 30min: sem cache, o próximo ciclo re-sonda."""
        t = FakeTransport({"oc/a-free": 500})
        engine = self.engine(t, ttl=1800)

        engine.check(["oc/a-free"], force=False, memory=True)

        self.assertNotIn("a-free", self.read_state().get("localhost:20128", {}))

        engine.check(["oc/a-free"], force=False, memory=True)
        self.assertEqual(len(t.calls), 2)

    def test_excecao_de_rede_nao_entra_no_cache(self) -> None:
        """Erro de rede também é transitório: não vira 'vivo' por 30min."""
        t = FakeTransport({}, explode={"oc/a-free"})
        engine = self.engine(t, ttl=1800)

        engine.check(["oc/a-free"], force=False, memory=False)

        self.assertNotIn("a-free", self.read_state().get("localhost:20128", {}))

    def test_falha_estavel_continua_entrando_no_cache(self) -> None:
        """403 é evidência definitiva: dá para guardar e não re-sondar."""
        t = FakeTransport({"oc/a-free": 403})
        engine = self.engine(t, ttl=1800)

        engine.check(["oc/a-free"], force=False, memory=True)

        cached = self.read_state().get("localhost:20128", {}).get("a-free")
        self.assertIsNotNone(cached)
        self.assertFalse(cached["alive"])


# --------------------------------------------------------------------------
# quarentena e estado por instância
# --------------------------------------------------------------------------


class FailureMemoryTest(EngineTestCase):
    def test_falha_estavel_entra_em_quarentena(self) -> None:
        t = FakeTransport({"oc/a-free": 403})
        self.engine(t).check(["oc/a-free"], force=True, memory=True)

        entry = self.read_failures()["localhost:20128"]["a-free"]
        self.assertEqual(entry["kind"], "stable")

    def test_quarentena_fresca_pula_o_probe(self) -> None:
        self.write_failures(
            {
                "localhost:20128": {
                    "a-free": {"kind": "stable", "status": 403, "at": time.time()}
                }
            }
        )
        t = FakeTransport({"oc/a-free": 200})
        result = self.engine(t).check(["oc/a-free"], force=True, memory=True)

        self.assertEqual(t.calls, [])
        self.assertFalse(result["oc/a-free"].alive)

    def test_sucesso_limpa_a_quarentena(self) -> None:
        ttl = 6 * 3600
        self.write_failures(
            {
                "localhost:20128": {
                    "a-free": {
                        "kind": "stable",
                        "status": 403,
                        "at": time.time() - ttl - 1,
                    }
                }
            }
        )
        t = FakeTransport({"oc/a-free": 200})
        self.engine(t).check(["oc/a-free"], force=True, memory=True)

        self.assertEqual(self.read_failures(), {"localhost:20128": {}})

    def test_estado_nao_vaza_entre_instancias(self) -> None:
        """Cada slave tem cota e combos próprios: o estado é por instância."""
        self.write_failures(
            {
                "rs001": {
                    "a-free": {"kind": "stable", "status": 429, "at": time.time()}
                }
            }
        )
        t = FakeTransport({"oc/a-free": 200})
        result = self.engine(t, host="rs002:20131").check(
            ["oc/a-free"], force=True, memory=True
        )

        self.assertEqual(t.calls, ["oc/a-free"])
        self.assertTrue(result["oc/a-free"].alive)

    def test_cache_nao_vaza_entre_instancias(self) -> None:
        self.write_state(
            {
                "rs001": {
                    "a-free": {"alive": True, "kind": "thinking", "status": 200,
                               "reasoning_tokens": 0, "checked_at": time.time()}
                }
            }
        )
        t = FakeTransport({"oc/a-free": 200})
        self.engine(t, host="rs002:20131").check(
            ["oc/a-free"], force=False, memory=True
        )

        self.assertEqual(t.calls, ["oc/a-free"])

    def test_arquivo_de_estado_corrompido_nao_quebra(self) -> None:
        self.state_file.write_text("{quebrado", encoding="utf-8")
        t = FakeTransport({"oc/a-free": 200})
        with quiet():
            result = self.engine(t).check(
                ["oc/a-free"], force=False, memory=False
            )

        self.assertTrue(result["oc/a-free"].alive)

    def test_modo_sem_memoria_nao_toca_os_arquivos(self) -> None:
        t = FakeTransport({"oc/a-free": 403})
        self.engine(t).check(["oc/a-free"], force=True, memory=False)

        self.assertFalse(self.state_file.exists())
        self.assertFalse(self.failures_file.exists())


if __name__ == "__main__":
    unittest.main()
