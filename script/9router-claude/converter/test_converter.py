"""Testes unitários do conversor (rodam com stdlib: python3 test_converter.py)."""
import sys

from app.converter import (
    anthropic_to_openai_request,
    openai_to_anthropic_response,
)
from app.routing import (
    InvalidUpstreamBase,
    browser_origin_rejected,
    resolve_upstream_base,
)


def test_resolve_upstream_base_prefers_gateway_header():
    assert resolve_upstream_base("http://127.0.0.1:20338/v1", "http://env") == "http://127.0.0.1:20338/v1"
    assert resolve_upstream_base("http://127.0.0.1:20339/v1/", "http://env") == "http://127.0.0.1:20339/v1"


def test_resolve_upstream_base_falls_back_to_env():
    assert resolve_upstream_base(None, "http://127.0.0.1:20338/v1/") == "http://127.0.0.1:20338/v1"
    assert resolve_upstream_base("", "http://127.0.0.1:20338/v1") == "http://127.0.0.1:20338/v1"


def test_resolve_upstream_base_rejects_bad_scheme():
    for bad in ("ftp://x/v1", "file:///etc/passwd", "javascript:alert(1)", "sem-esquema"):
        try:
            resolve_upstream_base(bad, "http://env")
        except InvalidUpstreamBase:
            continue
        raise AssertionError(f"{bad!r} deveria ser rejeitado")


def test_origin_rejected_only_when_present():
    assert browser_origin_rejected("https://evil.example") is True
    assert browser_origin_rejected(None) is False
    assert browser_origin_rejected("") is False


def test_request_passes_model_through():
    # pass-through: o que o cliente manda chega igual no payload OpenAI
    for wanted in ("sec/gemini/gemini-3.8-flash", "gpt-4o", "claude-haiku-5-5", "qualquer-coisa"):
        req = anthropic_to_openai_request({
            "model": wanted,
            "max_tokens": 10,
            "messages": [{"role": "user", "content": "oi"}],
        })
        assert req["model"] == wanted, f"{wanted} -> {req['model']}"
    # sem model: fallback do próprio conversor (modo direto)
    req = anthropic_to_openai_request({"max_tokens": 10, "messages": [{"role": "user", "content": "oi"}]})
    assert req["model"] == "claude-sonnet-5"


def test_http_rejects_origin_and_validates_base():
    try:
        from fastapi.testclient import TestClient
        from app.main import app
    except Exception as exc:  # stdlib puro: pula o teste HTTP
        print(f"  (skip de teste HTTP: {exc})")
        return
    client = TestClient(app)
    # Origin sempre 403 (navegador não fala com este serviço)
    resp = client.get("/health", headers={"Origin": "https://evil.example"})
    assert resp.status_code == 403, resp.status_code
    assert resp.json()["error"]["type"] == "permission_error"
    # base do header respeitada e valida
    resp = client.get("/health")
    assert resp.status_code == 200, resp.status_code
    # header malformado => 400 em formato Anthropic, sem tocar na rede
    payload = {"model": "claude-sonnet-5", "max_tokens": 1, "messages": [{"role": "user", "content": "oi"}]}
    resp = client.post("/v1/messages", json=payload,
                       headers={"X-9Router-Upstream-Base": "file:///etc/passwd"})
    assert resp.status_code == 400, (resp.status_code, resp.text)
    body = resp.json()
    assert body["type"] == "error" and body["error"]["type"] == "invalid_request_error", body


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
