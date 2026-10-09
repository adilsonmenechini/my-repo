"""apply_combo_plan: só mexe no combo que mudou (não abre janela de roteamento)."""

from __future__ import annotations

import unittest

from helpers import FakeClient, quiet

from server_pool.sync import apply_combo_plan


def combo(name: str, models: list[str], combo_id: str = "c1") -> dict:
    return {"id": combo_id, "name": name, "models": list(models)}


class ApplyComboPlanTest(unittest.TestCase):
    def apply(self, plan, server):
        client = FakeClient({}, server_combos=server)
        with quiet():
            apply_combo_plan(client, client.instance, plan, client.get_combos())
        return client

    def test_combo_inalterado_nao_e_tocado(self) -> None:
        """Sem diff, todo reload apaga e recria: o combo some por um instante."""
        client = self.apply(
            [("claude-sonnet-5", ["oc/ok-free"])],
            [combo("claude-sonnet-5", ["oc/ok-free"])],
        )

        self.assertEqual(client.created_combos, [])
        self.assertEqual(
            [(c["id"], c["models"]) for c in client.server_combos],
            [("c1", ["oc/ok-free"])],
        )

    def test_combo_alterado_e_recriado_com_o_novo_conjunto(self) -> None:
        client = self.apply(
            [("claude-sonnet-5", ["oc/ok-free"])],
            [combo("claude-sonnet-5", ["oc/ok-free", "oc/morto-free"])],
        )

        self.assertEqual(
            [c["models"] for c in client.created_combos],
            [["oc/ok-free"]],
        )
        self.assertEqual(len(client.server_combos), 1)

    def test_none_mantem_o_combo_atual(self) -> None:
        """`models=None` = último conjunto válido: não apaga, não recria."""
        client = self.apply(
            [("claude-sonnet-5", None)],
            [combo("claude-sonnet-5", ["oc/velho-free"])],
        )

        self.assertEqual(client.created_combos, [])
        self.assertEqual(client.server_combos[0]["models"], ["oc/velho-free"])

    def test_combo_novo_do_plano_e_criado(self) -> None:
        client = self.apply([("claude-opus-5", ["oc/a-free"])], [])

        self.assertEqual(
            [c["name"] for c in client.created_combos], ["claude-opus-5"]
        )

    def test_combo_fora_do_plano_nao_e_removido(self) -> None:
        """O plano só descreve combos gerenciados; o resto não é da nossa conta."""
        client = self.apply(
            [("claude-sonnet-5", ["oc/ok-free"])],
            [combo("combo-legado", ["oc/outro-free"], combo_id="c9")],
        )

        self.assertEqual(
            [c["name"] for c in client.server_combos], ["combo-legado"]
        )
        self.assertEqual(client.created_combos, [])


if __name__ == "__main__":
    unittest.main()
