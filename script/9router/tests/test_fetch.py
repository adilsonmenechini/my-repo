"""Testes da descoberta de modelos no fetch do pool (auto-discovery).

A descoberta tem que sondar pelo mesmo caminho que sync e watch — o
`ProbeEngine`, em `/v1/chat/completions` — porque é o único formato cujo
`usage` traz `reasoning_tokens`. O loop próprio em `/v1/responses` nunca
via o contador: todo modelo caía no sonnet e o combo do opus não nascia.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from helpers import quiet

from server_pool import fetch as fetch_mod
from server_pool.probe import PROBE_PROMPT


def response(status_code: int, payload: dict, text: str = "") -> SimpleNamespace:
    return SimpleNamespace(
        status_code=status_code,
        json=lambda: payload,
        text=text or json.dumps(payload),
    )


def body_with(reasoning: int | None) -> str:
    """Corpo no formato real do chat/completions: `usage` com o contador."""
    usage: dict = {"prompt_tokens": 12, "completion_tokens": 34}
    if reasoning is not None:
        usage["completion_tokens_details"] = {"reasoning_tokens": reasoning}
    return json.dumps({"usage": usage})


class DiscoverTest(unittest.TestCase):
    """Um probe por modelo, classificado igual ao ProbeEngine."""

    def run_fetch(
        self,
        body: str,
        probe_status: int = 200,
    ) -> tuple[dict[str, list[str]], Mock]:
        session = SimpleNamespace(
            post=Mock(return_value=response(200, {"success": True})),
            get=Mock(
                side_effect=[
                    response(200, {"keys": [{"key": "sk-test"}]}),
                    response(200, {"data": [{"id": "novo-free"}]}),
                ]
            ),
        )
        probe = SimpleNamespace(status_code=probe_status, text=body)

        with (
            patch.object(fetch_mod.req, "Session", return_value=session),
            patch.object(fetch_mod.req, "post", return_value=probe) as post,
            patch.object(
                fetch_mod,
                "PROBE_FAILURES_FILE",
                Path(tempfile.mkdtemp()) / "model-failures.json",
                create=True,
            ),
            quiet(),
        ):
            result = fetch_mod.fetch_opencode_free_models()

        return result, post

    def test_sonda_via_chat_completions(self) -> None:
        """O `/v1/responses` não traz `reasoning_tokens` — sonda por ele e
        todo modelo vira sonnet, porque o regex nunca casa."""
        _, post = self.run_fetch(body_with(506))

        self.assertIn("/v1/chat/completions", post.call_args.args[0])

    def test_payload_carrega_o_prompt_canonico_nas_messages(self) -> None:
        """O prompt difícil é o que separa pensador de não-pensador; ele
        viaja em `messages` (chat) — `input` é do formato errado."""
        _, post = self.run_fetch(body_with(506))

        contents = [
            m.get("content")
            for m in (post.call_args.kwargs["json"].get("messages") or [])
        ]

        self.assertIn(PROBE_PROMPT, contents)

    def test_payload_pede_effort_high(self) -> None:
        """Sem `reasoning_effort: high` o mesmo modelo oscila e o corte em
        50 vira um chute — tem que pedir o mesmo esforço que o probe."""
        _, post = self.run_fetch(body_with(506))

        self.assertEqual(
            post.call_args.kwargs["json"].get("reasoning_effort"),
            "high",
        )

    def test_reasoning_forte_entra_em_thinking(self) -> None:
        result, _ = self.run_fetch(body_with(506))

        self.assertEqual(result["thinking"], ["oc/novo-free"])
        self.assertEqual(result["no_thinking"], [])

    def test_reasoning_pouco_vai_para_no_thinking(self) -> None:
        """42 tokens é pensar pouco: vive, mas combo do sonnet."""
        result, _ = self.run_fetch(body_with(42))

        self.assertEqual(result["thinking"], [])
        self.assertEqual(result["no_thinking"], ["oc/novo-free"])

    def test_modelo_morto_nao_entra_em_lugar_nenhum(self) -> None:
        """Sem evidência (429/500/erro) o modelo não pode ir pro sonnet:
        faria o combo do sonnet encher de coisas que nem responderam."""
        result, _ = self.run_fetch(
            '{"error": {"message": "FreeUsageLimitError"}}',
            probe_status=429,
        )

        self.assertEqual(result["thinking"], [])
        self.assertEqual(result["no_thinking"], [])


if __name__ == "__main__":
    unittest.main()
