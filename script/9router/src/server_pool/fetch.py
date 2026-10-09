"""Fetch opencode free models and test thinking capability."""

from __future__ import annotations

import time
from typing import Any

import requests as req

from .config import save_config
from .console import error, info, success, title, warning
from .models import Instance
from .probe import PROBE_FAILURES_FILE, ProbeEngine


def fetch_opencode_free_models(
    master_host: str = "localhost:20228",
    master_password: str = "123456",
) -> dict[str, list[str]]:
    """Busca modelos free do opencode via suggested-models e testa thinking."""

    title("FETCH — Buscar modelos opencode free")

    base_url = f"http://{master_host}"

    # 1. Login no master e obter API key (com retry)
    info("Fazendo login no master...")
    s = req.Session()
    api_key = ""

    for attempt in range(5):
        try:
            r = s.post(
                f"{base_url}/api/auth/login",
                json={"password": master_password},
                timeout=10,
            )
            if r.status_code != 200:
                info(f"Login falhou (attempt {attempt + 1}/5), retrying...")
                time.sleep(5)
                continue

            # Obter API key via /api/keys
            r2 = s.get(f"{base_url}/api/keys", timeout=10)
            if r2.status_code == 200:
                keys = r2.json().get("keys", [])
                # Se não tem API key, criar uma
                if not keys:
                    info("Criando API key no master...")
                    r3 = s.post(
                        f"{base_url}/api/keys",
                        json={"name": "pool-key"},
                        timeout=10,
                    )
                    if r3.status_code == 200:
                        key_data = r3.json()
                        api_key = key_data.get("key", "")
                        success("API key criada no master")
                    else:
                        info(f"Criar API key falhou (attempt {attempt + 1}/5), retrying...")
                        time.sleep(5)
                        continue
                else:
                    api_key = keys[0].get("key", "")
                    break
            else:
                info(f"Obter API keys falhou (attempt {attempt + 1}/5), retrying...")
                time.sleep(5)
                continue

        except Exception as exc:
            info(f"Erro de conexão (attempt {attempt + 1}/5): {exc}")
            time.sleep(5)
            continue

    if not api_key:
        error("Não obteve API key após 5 tentativas")
        return {"thinking": [], "no_thinking": []}

    info(f"API key: {api_key[:20]}...")

    # 2. Buscar lista de modelos free
    info("Buscando modelos opencode free...")
    try:
        r = s.get(
            f"{base_url}/api/providers/suggested-models",
            params={
                "url": "https://opencode.ai/zen/v1/models",
                "type": "opencode-free",
            },
            timeout=15,
        )
        if r.status_code != 200:
            error(f"Erro ao buscar modelos: {r.status_code}")
            return {"thinking": [], "no_thinking": []}

        data = r.json()
        models = [m["id"] for m in data.get("data", [])]
        success(f"Encontrados {len(models)} modelos free")
    except Exception as exc:
        error(f"Erro ao conectar: {exc}")
        return {"thinking": [], "no_thinking": []}

    # 3. Testar thinking de cada modelo
    info("Testando thinking de cada modelo...")

    # Mesmo caminho que sync e watch. O /v1/responses devolve um `usage` sem
    # `reasoning_tokens`, então o regex nunca casava e todo modelo caía no
    # sonnet — o combo do opus não nascia nunca.
    full_models = [f"oc/{model_id}" for model_id in models]

    engine = ProbeEngine(
        instance=Instance(
            name="master",
            host=master_host,
            password=master_password,
        ),
        api_key=api_key,
        transport=req.post,
        # Caminho explícito: é o `fetch.PROBE_FAILURES_FILE` (patchável), não
        # o default do módulo `probe`.
        failures_path=PROBE_FAILURES_FILE,
    )
    results = engine.check(
        full_models,
        strict=True,
        force=True,
        memory=True,
    )

    thinking: list[str] = []
    no_thinking: list[str] = []

    for full_model in full_models:
        result = results.get(full_model)

        if result is None:
            continue

        if not result.alive:
            warning(
                f"  ✗ {full_model} → status {result.status} "
                f"(quarentena: {result.kind})"
            )
            continue

        if result.kind == "thinking":
            thinking.append(full_model)
            info(
                f"  ✓ {full_model} → THINKING ({result.reasoning_tokens} tokens)"
            )
        else:
            no_thinking.append(full_model)
            info(f"  ✓ {full_model} → NO THINKING")

    success(
        f"Result: {len(thinking)} thinking, {len(no_thinking)} no thinking"
    )
    return {"thinking": thinking, "no_thinking": no_thinking}


def update_config_with_models(
    config: dict[str, Any],
    models: dict[str, list[str]],
) -> None:
    """Atualiza config.json com modelos descobertos."""
    thinking = models.get("thinking", [])
    no_thinking = models.get("no_thinking", [])
    all_models = thinking + no_thinking

    if not all_models:
        warning("Nenhum modelo encontrado, mantendo config atual")
        return

    title("UPDATE — Atualizando config.json")

    # defaults.models = todos os modelos
    config["defaults"]["models"] = all_models
    info(f"defaults.models: {len(all_models)} modelos")

    # combos
    config["defaults"]["combos"] = []

    if thinking:
        config["defaults"]["combos"].append(
            {"name": "claude-opus-5", "models": thinking}
        )
        info(f"combo claude-opus-5: {len(thinking)} modelos (thinking)")

    if no_thinking:
        config["defaults"]["combos"].append(
            {"name": "claude-sonnet-5", "models": no_thinking}
        )
        info(f"combo claude-sonnet-5: {len(no_thinking)} modelos (no thinking)")

    # publish = nomes dos combos
    config["defaults"]["publish"] = [
        c["name"] for c in config["defaults"]["combos"]
    ]

    save_config(config)
    success("config.json atualizado")
