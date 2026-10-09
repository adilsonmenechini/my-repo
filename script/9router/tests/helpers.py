"""Testes compartilhados: silenciamento de saída e fakes do RouterClient."""

from __future__ import annotations

import contextlib
import io
import json

from server_pool.models import Instance


@contextlib.contextmanager
def captured():
    """Captura stdout+stderr — o watchdog escreve em tela o tempo todo."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        yield out


@contextlib.contextmanager
def quiet():
    with captured():
        yield


class FakeResponse:
    def __init__(self, status_code: int, body: str = "{}") -> None:
        self.status_code = status_code
        self.text = body

    def json(self):
        return json.loads(self.text)

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def default_body(status_code: int) -> str:
    if status_code == 200:
        # Completion real traz `usage` (stream_options.include_usage).
        return '{"choices": [], "usage": {"completion_tokens": 3}}'
    return json.dumps({"error": {"message": f"[{status_code}]: provider error"}})


class FakeSession:
    """Só aceita o que o probe e o combo usam — URL inesperada = AssertionError."""

    def __init__(self, client: FakeClient) -> None:
        self.client = client

    def get(self, url: str, timeout: int | None = None) -> FakeResponse:
        if url.endswith("/api/combos"):
            if not self.client.health_ok:
                raise RuntimeError("instância não saudável")
            return FakeResponse(200, json.dumps({"combos": self.client.server_combos}))
        raise AssertionError(f"GET inesperado: {url}")

    def post(
        self,
        url: str,
        json: dict | None = None,
        headers: dict | None = None,
        timeout: int | None = None,
    ) -> FakeResponse:
        if url.endswith("/v1/chat/completions"):
            payload = json or {}
            model = payload["model"]
            self.client.probes.append(model)
            self.client.probe_calls.append(
                {"url": url, "json": payload, "headers": headers or {}}
            )
            status = self.client.statuses[model]
            body = self.client.bodies.get(model, default_body(status))
            return FakeResponse(status, body)
        raise AssertionError(f"POST inesperado: {url}")


class FakeClient:
    """RouterClient fake: `statuses` decide o código de cada probe.

    `server_combos` espelha o estado real no servidor — get/create/delete
    operam sobre ele, então o diff do `apply_combo_plan` é observável.
    """

    def __init__(
        self,
        statuses: dict[str, int],
        bodies: dict[str, str] | None = None,
        server_combos: list[dict] | None = None,
        health_ok: bool = True,
        api_keys: list[dict] | None = None,
        login_ok: bool = True,
    ) -> None:
        self.instance = Instance(name="test", host="localhost:20129", password="123456")
        self.statuses = statuses
        self.bodies = bodies or {}
        self.timeout = 15
        self.health_ok = health_ok
        self.login_ok = login_ok
        self.session = FakeSession(self)
        self.probes: list[str] = []
        self.probe_calls: list[dict] = []
        self.server_combos: list[dict] = [dict(c) for c in (server_combos or [])]
        self.created_combos: list[dict] = []
        self.api_keys: list[dict] = [
            dict(k)
            for k in (
                api_keys
                if api_keys is not None
                else [{"id": "k1", "name": "pool-key", "key": "sk-pool"}]
            )
        ]
        self.login_calls = 0

    def login(self) -> bool:
        self.login_calls += 1
        return self.login_ok

    def get_api_keys(self) -> list[dict]:
        return [dict(k) for k in self.api_keys]

    def get_combos(self) -> list[dict]:
        return [dict(c) for c in self.server_combos]

    def create_combo(self, name: str, models: list[str]) -> dict:
        combo = {"id": name, "name": name, "models": list(models)}
        self.server_combos.append(combo)
        self.created_combos.append(combo)
        return combo

    def delete_combo(self, combo_id: str) -> bool:
        self.server_combos = [
            c for c in self.server_combos if c.get("id") != combo_id
        ]
        return True
