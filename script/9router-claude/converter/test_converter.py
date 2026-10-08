"""Testes unitários do conversor (rodam com stdlib: python3 test_converter.py)."""
import sys

from app.converter import (
    MODEL_ALIAS_MAP,
    anthropic_to_openai_request,
    openai_to_anthropic_response,
    resolve_upstream_model,
)


def test_identity_alias_map():
    for alias, target in MODEL_ALIAS_MAP.items():
        assert alias == target, f"{alias} -> {target} deveria ser identidade"


def test_resolve_upstream_model():
    assert resolve_upstream_model("claude-sonnet-5") == "claude-sonnet-5"
    assert resolve_upstream_model("claude-opus-5") == "claude-opus-5"
    assert resolve_upstream_model("claude-haiku-4-5-20251001") == "claude-haiku-4-5-20251001"
    # IDs compostos do 9router passam adiante
    assert resolve_upstream_model("sec/gemini/gemini-3.8-flash") == "sec/gemini/gemini-3.8-flash"
    # nomes legados caem no fallback anthropic
    assert resolve_upstream_model("claude-3-5-sonnet-20241022") == "claude-sonnet-5"
    assert resolve_upstream_model("qualquer-coisa") == "claude-sonnet-5"


def test_request_conversion_shape():
    req = anthropic_to_openai_request({
        "model": "claude-sonnet-5",
        "max_tokens": 100,
        "system": "voce e util",
        "messages": [{"role": "user", "content": "oi"}],
    })
    assert req["model"] == "claude-sonnet-5"
    assert req["max_tokens"] == 100
    assert req["stream"] is False
    # system vira mensagem system; a pergunta fica em user
    assert req["messages"][0] == {"role": "system", "content": "voce e util"}
    assert req["messages"][1] == {"role": "user", "content": "oi"}


def test_response_conversion_is_anthropic():
    resp = openai_to_anthropic_response({
        "choices": [{
            "message": {"role": "assistant", "content": "ola!"},
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": 10, "completion_tokens": 3},
    }, "claude-sonnet-5")
    assert resp["type"] == "message"
    assert resp["role"] == "assistant"
    assert resp["model"] == "claude-sonnet-5"
    assert resp["content"][0] == {"type": "text", "text": "ola!"}
    assert resp["stop_reason"] == "end_turn"
    assert resp["usage"]["input_tokens"] == 10


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL {fn.__name__}: {e}")
    sys.exit(1 if failed else 0)
