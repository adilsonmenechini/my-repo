"""`sync --dry-run` não pode tocar em nada: nenhuma escrita em nenhum host.

O flag chegava até `sync_pool` via `__main__`, mas lá dentro controlava só o
preview impresso — `configure_slave` e `sync_master` eram chamados com
`dry_run=False` hardcoded e o bloco de export de API key nem checava. Na
prática "simular" rotacionava as keys dos slaves e recriava os provider
nodes do master.

Os testes observam comportamento, não implementação: um RouterClient falso
grava toda chamada e marca as de escrita. Dry-run limpo = lista de escritas
vazia.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from helpers import captured
from server_pool import sync as sync_mod

# Estado que o servidor já tem + o que uma escrita devolve. Espelha o run real:
# havia provider nodes, connections, custom models, combos e a key "pool-key"
# antes do sync, então o caminho destrutivo percorre REMOVER e depois CRIAR.
READS: dict[str, Any] = {
    "login": True,
    "get_provider_nodes": [{"id": "node-1", "name": "rs000"}],
    "get_providers": [{"id": "prov-1", "name": "rs000"}],
    "get_custom_models": [{"providerAlias": "node-1", "id": "big-pickle"}],
    "get_combos": [
        {"id": "combo-1", "name": "claude-sonnet-5", "models": ["rs000/claude-sonnet-5"]}
    ],
    "get_api_keys": [{"id": "key-1", "name": "pool-key", "key": "sk-existing"}],
    "create_api_key": {"id": "fake-key", "name": "pool-key", "key": "sk-fake"},
    # Sem `id` o `sync_master` aborta a criação da provider connection
    # ("node ID não disponível") e essa escrita passaria batida no teste.
    "create_provider_node": {"id": "fake-node", "name": "rs000"},
}

# Toda chamada que muda estado no servidor. Dry-run tem que deixar isto vazio.
WRITES = frozenset(
    {
        "create_api_key",
        "delete_api_key",
        "create_combo",
        "delete_combo",
        "create_provider_node",
        "delete_provider_node",
        "create_provider",
        "delete_provider",
        "create_custom_model",
        "delete_custom_model",
        "add_custom_model",
    }
)


class FakeResponse:
    def raise_for_status(self) -> None:
        return None


class FakeSession:
    """`client.session` — o sync faz um GET de saúde antes de criar combos."""

    def get(self, *args: Any, **kwargs: Any) -> FakeResponse:
        return FakeResponse()


class FakeRouterClient:
    """Grava cada chamada; as de escrita entram em `writes` como host.método."""

    def __init__(self, instance: Any) -> None:
        self.instance = instance
        self.calls: list[str] = []
        self.writes: list[str] = []

    def __getattr__(self, name: str) -> Any:
        if name == "session":
            return FakeSession()

        def call(*args: Any, **kwargs: Any) -> Any:
            self.calls.append(name)
            if name in WRITES:
                self.writes.append(f"{self.instance.host}.{name}")
            # {} genérico: o chamador costuma fazer `.get(...)` no resultado.
            return READS.get(name, {})

        return call


def config() -> dict[str, Any]:
    """Config mínimo que `sync_pool` consegue percorrer inteiro."""
    return {
        "master": {
            "name": "master",
            "host": "localhost:20228",
            "password": "123456",
        },
        "defaults": {
            "models": ["oc/big-pickle"],
            "combos": [
                {"name": "claude-sonnet-5", "models": ["oc/big-pickle"]}
            ],
            "publish": ["claude-sonnet-5"],
        },
        "slaves": [
            {
                "name": "rs000",
                "host": "localhost:20129",
                "docker_host": "9router-slave-000:20129",
                "password": "123456",
            }
        ],
    }


class DryRunNaoEscreveTest(unittest.TestCase):
    def run_sync_dry(self) -> tuple[list[FakeRouterClient], str]:
        """Roda `sync_pool(dry_run=True)` e devolve clientes + stdout."""
        clients: list[FakeRouterClient] = []

        def factory(instance: Any) -> FakeRouterClient:
            client = FakeRouterClient(instance)
            clients.append(client)
            return client

        with (
            patch.object(sync_mod, "RouterClient", side_effect=factory),
            patch.object(sync_mod, "wait_for_instance"),
            captured() as out,
        ):
            sync_mod.sync_pool(config(), dry_run=True)

        return clients, out.getvalue()

    def test_dry_run_nao_faz_nenhuma_escrita(self) -> None:
        """O essencial: nenhuma mutação em master ou slave."""
        clients, _ = self.run_sync_dry()

        escritas = [w for c in clients for w in c.writes]

        self.assertEqual(escritas, [])

    def test_dry_run_nao_toca_nos_slaves(self) -> None:
        """`configure_slave` tem que retornar antes de logar no slave."""
        clients, _ = self.run_sync_dry()

        hosts = [c.instance.host for c in clients]

        self.assertNotIn("localhost:20129", hosts)

    def test_dry_run_ainda_mostra_o_preview(self) -> None:
        """Não adianta virar mudo: o preview é a razão de existir do flag."""
        _, out = self.run_sync_dry()

        self.assertIn("DRY RUN", out)
        self.assertIn("MASTER (preview)", out)


if __name__ == "__main__":
    unittest.main()
