"""Testes do watchdog: agendamento dos timers e revalidação de modelos."""

from __future__ import annotations

import inspect
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from helpers import FakeClient, quiet

from server_pool import watch as watch_mod
from server_pool.__main__ import main
from server_pool.probe import PROBE_PROMPT
from server_pool.watch import WatchState, revalidate_models

CONFIG = {
    "master": {"name": "master", "host": "localhost:20228", "password": "123456"},
    "defaults": {
        "models": ["oc/ok-free", "oc/morto-free"],
        "combos": [
            {"name": "claude-sonnet-5", "models": ["oc/ok-free", "oc/morto-free"]}
        ],
        "publish": ["claude-sonnet-5"],
    },
    "slaves": [
        {"name": "rs000", "host": "localhost:20129", "docker_host": "9router-slave-000:20129", "password": "123456"},
        {"name": "rs001", "host": "localhost:20130", "docker_host": "9router-slave-001:20130", "password": "123456"},
    ],
}


def clone_config() -> dict:
    return json.loads(json.dumps(CONFIG))


class RevalidateModelsTest(unittest.TestCase):
    """A revalidação tira do combo só o que não responde, e só no slave."""

    def setUp(self) -> None:
        # Os arquivos de memória do ProbeEngine vão para um tmp: quarentena
        # gravada por um teste não pode decidir o resultado do próximo.
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = Path(tmp.name)

        for name, filename in (
            ("PROBE_STATE_FILE", "model-state.json"),
            ("PROBE_FAILURES_FILE", "model-failures.json"),
        ):
            patcher = patch.object(watch_mod, name, base / filename)
            patcher.start()
            self.addCleanup(patcher.stop)

    def revalidate(
        self,
        statuses: dict[str, int],
        server_combos: list[dict] | None = None,
        slaves: list[str] | None = None,
    ) -> dict[str, FakeClient]:
        """Roda a revalidação e devolve o fake por host, para inspeção."""
        config = clone_config()
        if slaves is not None:
            config["slaves"] = [
                s for s in config["slaves"] if s["name"] in slaves
            ]

        per_host = {
            s["host"]: FakeClient(statuses, server_combos=server_combos or [])
            for s in config["slaves"]
        }

        def _factory(instance):
            return per_host[instance.host]

        self.config = config
        with (
            quiet(),
            patch.object(watch_mod, "RouterClient", side_effect=_factory),
        ):
            revalidate_models(config)

        self.clients = per_host
        return per_host

    # -- resultado por modelo ------------------------------------------------

    def test_modelo_morto_sai_do_combo_do_slave(self) -> None:
        clients = self.revalidate({"oc/ok-free": 200, "oc/morto-free": 404})

        for client in clients.values():
            self.assertEqual(
                [c["models"] for c in client.server_combos],
                [["oc/ok-free"]],
            )

    def test_config_json_nao_perde_modelo_por_caida(self) -> None:
        """config.json é a intenção; quem filtra é o servidor."""
        self.revalidate({"oc/ok-free": 200, "oc/morto-free": 404})

        self.assertEqual(
            self.config["defaults"]["combos"][0]["models"],
            ["oc/ok-free", "oc/morto-free"],
        )
        self.assertIn("oc/morto-free", self.config["defaults"]["models"])

    def test_modelo_que_volta_e_reinserido(self) -> None:
        server = [{"id": "c1", "name": "claude-sonnet-5", "models": ["oc/ok-free"]}]
        clients = self.revalidate(
            {"oc/ok-free": 200, "oc/morto-free": 200},
            server_combos=server,
        )

        for client in clients.values():
            self.assertEqual(
                [c["models"] for c in client.server_combos],
                [["oc/ok-free", "oc/morto-free"]],
            )

    def test_combo_sem_nenhum_modelo_vivo_mantem_o_atual(self) -> None:
        server = [{"id": "c1", "name": "claude-sonnet-5", "models": ["oc/ok-free"]}]
        clients = self.revalidate(
            {"oc/ok-free": 404, "oc/morto-free": 404},
            server_combos=server,
        )

        for client in clients.values():
            self.assertEqual(
                [c["models"] for c in client.server_combos],
                [["oc/ok-free"]],
            )
            self.assertEqual(client.created_combos, [])

    def test_combo_sem_vivos_e_sem_historico_nao_e_criado(self) -> None:
        """Nenhum vivo e nenhum combo no servidor: não se cria um combo que
        só serve falha durante toda a janela de quarentena (até 6h)."""
        clients = self.revalidate(
            {"oc/ok-free": 404, "oc/morto-free": 404},
            server_combos=[],
        )

        for client in clients.values():
            self.assertEqual(client.created_combos, [])
            self.assertEqual(client.server_combos, [])

    def test_combo_inalterado_nao_e_recriado(self) -> None:
        server = [
            {"id": "c1", "name": "claude-sonnet-5",
             "models": ["oc/ok-free", "oc/morto-free"]}
        ]
        clients = self.revalidate(
            {"oc/ok-free": 200, "oc/morto-free": 200},
            server_combos=server,
        )

        for client in clients.values():
            self.assertEqual(client.created_combos, [])

    def test_transitorio_isolado_nao_derruba_o_combo(self) -> None:
        """strict=False: um 500 sozinho não sacode o roteamento no watch."""
        server = [
            {"id": "c1", "name": "claude-sonnet-5",
             "models": ["oc/ok-free", "oc/morto-free"]}
        ]
        clients = self.revalidate(
            {"oc/ok-free": 200, "oc/morto-free": 500},
            server_combos=server,
        )

        for client in clients.values():
            self.assertEqual(client.created_combos, [])
            self.assertEqual(
                [c["models"] for c in client.server_combos],
                [["oc/ok-free", "oc/morto-free"]],
            )

    def test_revalidacao_usa_o_payload_canonico(self) -> None:
        """O watch sonda com o mesmo payload do sync e do fetch."""
        clients = self.revalidate(
            {"oc/ok-free": 200, "oc/morto-free": 200}
        )

        for client in clients.values():
            self.assertTrue(client.probe_calls)
            for call in client.probe_calls:
                payload = call["json"]
                self.assertEqual(payload["messages"][0]["content"], PROBE_PROMPT)
                self.assertEqual(payload["max_tokens"], 2000)

    # -- topologia de duas camadas (específico do pool) ----------------------

    def test_master_nunca_e_alcancado(self) -> None:
        """Critério de sucesso 6 do spec: o roteamento do master não muda.

        Se a revalidação construísse um client do master "porque é um login
        em vez de N", todos os testes de modelo acima continuariam passando.
        """
        config = clone_config()
        clients = {s["host"]: FakeClient({"oc/ok-free": 200, "oc/morto-free": 200})
                   for s in config["slaves"]}

        with (
            quiet(),
            patch.object(watch_mod, "RouterClient",
                         side_effect=lambda inst: clients[inst.host]),
            patch.object(watch_mod, "master_instance",
                         side_effect=AssertionError("master não pode ser alcançado")),
        ):
            revalidate_models(config)

        self.assertEqual(
            sorted(clients),
            ["localhost:20129", "localhost:20130"],
        )

    def test_login_falho_num_slave_nao_aborta_os_demais(self) -> None:
        """rs000 fora não pode cegar a revalidação do rs001."""
        config = clone_config()
        good = FakeClient({"oc/ok-free": 200, "oc/morto-free": 200})

        def _factory(instance):
            if instance.name == "rs000":
                return FakeClient({}, login_ok=False)
            return good

        with (
            quiet(),
            patch.object(watch_mod, "RouterClient", side_effect=_factory),
        ):
            revalidate_models(config)

        self.assertEqual(
            [c["models"] for c in good.server_combos],
            [["oc/ok-free", "oc/morto-free"]],
        )

    def test_sem_combos_no_config_nada_e_sondado(self) -> None:
        config = clone_config()
        config["defaults"]["combos"] = []
        client = FakeClient({})

        with (
            quiet(),
            patch.object(watch_mod, "RouterClient", return_value=client),
        ):
            revalidate_models(config)

        self.assertEqual(client.probes, [])

    def test_caches_vao_para_o_tmp_e_nao_para_a_raiz(self) -> None:
        """Os arquivos de quarentena não podem vazar para a árvore de trabalho."""
        self.revalidate({"oc/ok-free": 200, "oc/morto-free": 200})

        root = Path(__file__).resolve().parents[1]
        self.assertFalse((root / "model-state.json").exists())
        self.assertFalse((root / "model-failures.json").exists())


class DiscoverModelsTest(unittest.TestCase):
    """A descoberta usa o config que recebeu — não recarrega por conta própria."""

    def test_usa_o_config_recebido_em_vez_de_recarregar(self) -> None:
        """Regressão: recarregar dentro da função descarta o snapshot que o
        `run_tick` acabou de montar e traz de volta o `sys.exit`."""
        cfg = clone_config()
        seen: list[dict] = []

        def _noop_fetch(master_host: str = "", master_password: str = "") -> dict:
            seen.append({"host": master_host})
            return {"thinking": [], "no_thinking": []}

        with (
            quiet(),
            patch.object(
                watch_mod, "fetch_opencode_free_models", side_effect=_noop_fetch
            ),
            patch.object(
                watch_mod,
                "load_config",
                side_effect=AssertionError("não deveria recarregar o config"),
            ),
        ):
            watch_mod.discover_models(cfg)

        self.assertEqual(seen, [{"host": "localhost:20228"}])

    def test_master_host_e_password_vem_do_config(self) -> None:
        """O fetch do pool recebe host/senha, não o config inteiro."""
        cfg = clone_config()
        seen: dict[str, str] = {}

        def _noop_fetch(master_host: str = "", master_password: str = "") -> dict:
            seen["host"] = master_host
            seen["password"] = master_password
            return {"thinking": [], "no_thinking": []}

        with (
            quiet(),
            patch.object(watch_mod, "fetch_opencode_free_models", side_effect=_noop_fetch),
        ):
            watch_mod.discover_models(cfg)

        self.assertEqual(seen, {"host": "localhost:20228", "password": "123456"})


class RunTickTest(unittest.TestCase):
    """O ciclo não depende da descoberta para checar os modelos."""

    def tick(
        self,
        state: WatchState,
        fetch_interval: int = 600,
        model_interval: int = 900,
        now: float = 100_000.0,
    ) -> list[str]:
        calls: list[str] = []
        with (
            quiet(),
            patch.object(watch_mod, "discover_models", side_effect=lambda c: calls.append("discover")),
            patch.object(watch_mod, "revalidate_models", side_effect=lambda c: calls.append("revalidate")),
            patch.object(watch_mod, "load_config", return_value=clone_config()),
        ):
            watch_mod.run_tick(
                state,
                fetch_interval=fetch_interval,
                model_interval=model_interval,
                now=now,
            )
        return calls

    def test_revalidacao_roda_mesmo_sem_descoberta_nova(self) -> None:
        """Regressão: o watch antigo só validava se aparecesse modelo novo."""
        state = WatchState(last_fetch=99_500.0, last_model=0.0)

        self.assertEqual(self.tick(state), ["revalidate"])

    def test_descoberta_roda_quando_vence_o_intervalo(self) -> None:
        state = WatchState(last_fetch=0.0, last_model=0.0)

        self.assertEqual(self.tick(state), ["discover", "revalidate"])

    def test_nada_roda_fora_do_intervalo(self) -> None:
        state = WatchState(last_fetch=99_500.0, last_model=99_500.0)

        self.assertEqual(self.tick(state), [])

    def test_fetch_intervalo_zero_desativa_a_descoberta(self) -> None:
        state = WatchState(last_fetch=0.0, last_model=99_500.0)

        self.assertEqual(self.tick(state, fetch_interval=0), [])

    def test_model_intervalo_zero_desativa_a_revalidacao(self) -> None:
        state = WatchState(last_fetch=99_500.0, last_model=0.0)

        self.assertEqual(self.tick(state, model_interval=0), [])

    def test_falha_num_timer_nao_mata_o_loop(self) -> None:
        """Um 500 do servidor no meio de um ciclo não pode encerrar o
        watchdog: ele é o único processo olhando o pool."""
        state = WatchState(last_fetch=0.0, last_model=0.0)

        with (
            quiet(),
            patch.object(watch_mod, "discover_models", side_effect=RuntimeError("boom")),
            patch.object(watch_mod, "revalidate_models", side_effect=RuntimeError("boom")),
            patch.object(watch_mod, "load_config", return_value=clone_config()),
        ):
            watch_mod.run_tick(state, fetch_interval=600, model_interval=900, now=100_000.0)

        # Os dois timers continuam agendados para o próximo ciclo.
        self.assertEqual(state.last_fetch, 100_000.0)
        self.assertEqual(state.last_model, 100_000.0)

    def test_config_invalido_nao_encerra_o_watchdog(self) -> None:
        """`load_config` faz `sys.exit(1)` com JSON inválido. `SystemExit` não
        é `Exception`: escapava do try por timer e derrubava o processo."""
        state = WatchState(last_fetch=0.0, last_model=0.0)
        calls: list[str] = []

        with (
            quiet(),
            patch.object(watch_mod, "load_config", side_effect=SystemExit(1)),
            patch.object(watch_mod, "discover_models", side_effect=lambda c: calls.append("discover")),
            patch.object(watch_mod, "revalidate_models", side_effect=lambda c: calls.append("revalidate")),
        ):
            watch_mod.run_tick(state, fetch_interval=600, model_interval=900, now=100_000.0)

        # Não age sobre uma intenção ilegível, e não adianta o relógio.
        self.assertEqual(calls, [])
        self.assertEqual(state.last_fetch, 0.0)
        self.assertEqual(state.last_model, 0.0)

    def test_config_invalido_nao_persiste_o_snapshot_do_boot(self) -> None:
        """Um fallback com o config do boot seria repassado à descoberta, que
        grava o config — reverteria a edição do operador."""
        state = WatchState(last_fetch=0.0, last_model=0.0)

        with (
            quiet(),
            patch.object(watch_mod, "load_config", side_effect=SystemExit(1)),
            patch.object(
                watch_mod,
                "fetch_opencode_free_models",
                return_value={"thinking": ["oc/novo-free"], "no_thinking": []},
            ),
            patch.object(watch_mod, "update_config_with_models") as update,
            patch.object(watch_mod, "revalidate_models"),
        ):
            watch_mod.run_tick(state, fetch_interval=600, model_interval=900, now=100_000.0)

        update.assert_not_called()

    def test_revalidacao_agendada_para_cada_15_minutos(self) -> None:
        signature = inspect.signature(watch_mod.watch_pool)

        self.assertEqual(
            signature.parameters["model_interval"].default, 900
        )


class WatchCliTest(unittest.TestCase):
    """A CLI precisa expor o timer de revalidação (e batê-lo com o watchdog)."""

    def run_main(self, argv: list[str]) -> object:
        with (
            patch.object(sys, "argv", ["pool", *argv]),
            patch("server_pool.__main__.load_config", return_value={}),
            patch("server_pool.__main__.watch_pool") as watch,
        ):
            main()
        return watch

    def test_revalidacao_padrao_e_de_15_minutos(self) -> None:
        watch = self.run_main(["watch"])

        self.assertEqual(watch.call_args.kwargs["model_interval"], 900)

    def test_model_interval_customizado_chega_no_watchdog(self) -> None:
        watch = self.run_main(["watch", "--model-interval", "60"])

        self.assertEqual(watch.call_args.kwargs["model_interval"], 60)

    def test_health_e_discovery_continuam_com_os_padroes(self) -> None:
        watch = self.run_main(["watch"])

        self.assertEqual(watch.call_args.kwargs["interval"], 180)
        self.assertEqual(watch.call_args.kwargs["fetch_interval"], 600)

    def test_model_interval_zero_desliga_a_revalidacao(self) -> None:
        watch = self.run_main(["watch", "--model-interval", "0"])

        self.assertEqual(watch.call_args.kwargs["model_interval"], 0)

    def test_failures_continua_exposto(self) -> None:
        watch = self.run_main(["watch", "--failures", "5"])

        self.assertEqual(watch.call_args.kwargs["max_failures"], 5)


if __name__ == "__main__":
    unittest.main()
