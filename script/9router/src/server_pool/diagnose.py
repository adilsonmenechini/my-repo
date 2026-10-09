"""Diagnóstico em camadas do 9Router Pool.

Camadas testadas (de baixo para cima):

    1. SLAVE    — O container 9Router responde? Login OK?
    2. API_KEYS — O slave tem API keys configuradas?
    3. COMBO    — Os modelos do combo estão disponíveis no slave?
    4. MASTER   — O master enxerga o slave? Provider connection OK?

Uso:
    from .diagnose import diagnose_slave
    results = diagnose_slave(slave, defaults, master_client)
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import requests

from .console import C, error, info, success, title, warning
from .models import Instance, RouterClient


class Status(Enum):
    OK = "OK"
    FAIL = "FAIL"
    WARN = "WARN"
    SKIP = "SKIP"


@dataclass
class LayerResult:
    layer: str
    status: Status
    message: str
    details: dict[str, Any] = field(default_factory=dict)


def _check_slave_login(slave: Instance) -> LayerResult:
    """Camada 1: O slave responde? Login OK?"""
    client = RouterClient(slave)

    try:
        if client.login():
            return LayerResult(
                layer="SLAVE",
                status=Status.OK,
                message=f"Login OK em {slave.host}",
            )
        else:
            return LayerResult(
                layer="SLAVE",
                status=Status.FAIL,
                message=f"Login falhou em {slave.host}",
            )
    except Exception as exc:
        return LayerResult(
            layer="SLAVE",
            status=Status.FAIL,
            message=f"Slave inacessível: {exc}",
            details={"exception": str(exc)},
        )


def _check_api_keys(slave: Instance) -> LayerResult:
    """Camada 1b: O slave tem API keys configuradas?"""
    client = RouterClient(slave)

    if not client.login():
        return LayerResult(
            layer="API_KEYS",
            status=Status.FAIL,
            message="Login falhou",
        )

    try:
        keys = client.get_api_keys()
        pool_keys = [k for k in keys if k.get("name") == "pool-key"]

        if pool_keys:
            return LayerResult(
                layer="API_KEYS",
                status=Status.OK,
                message=f"{len(pool_keys)} pool-key(s) configurada(s)",
                details={"keys": [k.get("key", "")[:20] + "..." for k in pool_keys]},
            )
        elif keys:
            return LayerResult(
                layer="API_KEYS",
                status=Status.WARN,
                message=f"{len(keys)} key(s) existe(m), mas nenhuma 'pool-key'",
                details={"key_names": [k.get("name") for k in keys]},
            )
        else:
            return LayerResult(
                layer="API_KEYS",
                status=Status.FAIL,
                message="Nenhuma API key configurada no slave",
            )
    except Exception as exc:
        return LayerResult(
            layer="API_KEYS",
            status=Status.WARN,
            message=f"Não foi possível verificar API keys: {exc}",
        )


def _check_combos(slave: Instance, defaults: dict[str, Any]) -> LayerResult:
    """Camada 4: Os modelos do combo estão disponíveis no slave?"""
    client = RouterClient(slave)

    if not client.login():
        return LayerResult(
            layer="COMBO",
            status=Status.FAIL,
            message="Login falhou",
        )

    expected_combos = {
        c["name"]: c.get("models", [])
        for c in defaults.get("combos", [])
    }

    try:
        existing = client.get_combos()
        existing_map = {c["name"]: c for c in existing}

        missing = set(expected_combos.keys()) - set(existing_map.keys())
        issues = []

        for combo_name, expected_models in expected_combos.items():
            if combo_name in existing_map:
                combo = existing_map[combo_name]
                actual_models = set(combo.get("models", []))
                expected_set = set(expected_models)
                diff = expected_set - actual_models
                if diff:
                    issues.append(f"Combo '{combo_name}': faltando {len(diff)} modelos")

        if missing:
            return LayerResult(
                layer="COMBO",
                status=Status.FAIL,
                message=f"Combos faltando: {', '.join(missing)}",
                details={"missing": list(missing)},
            )
        elif issues:
            return LayerResult(
                layer="COMBO",
                status=Status.WARN,
                message="; ".join(issues),
            )
        else:
            total_models = sum(len(m) for m in expected_combos.values())
            return LayerResult(
                layer="COMBO",
                status=Status.OK,
                message=f"{len(expected_combos)} combo(s) OK ({total_models} modelos)",
            )
    except Exception as exc:
        return LayerResult(
            layer="COMBO",
            status=Status.WARN,
            message=f"Não foi possível verificar combos: {exc}",
        )


def _check_custom_models(slave: Instance) -> LayerResult:
    """Camada 4b: Os custom models estão configurados no slave?"""
    client = RouterClient(slave)

    if not client.login():
        return LayerResult(
            layer="CUSTOM_MODELS",
            status=Status.FAIL,
            message="Login falhou",
        )

    try:
        custom = client.get_custom_models()
        if custom:
            return LayerResult(
                layer="CUSTOM_MODELS",
                status=Status.OK,
                message=f"{len(custom)} custom model(s) configurado(s)",
            )
        else:
            return LayerResult(
                layer="CUSTOM_MODELS",
                status=Status.WARN,
                message="Nenhum custom model configurado",
            )
    except Exception as exc:
        return LayerResult(
            layer="CUSTOM_MODELS",
            status=Status.WARN,
            message=f"Não foi possível verificar custom models: {exc}",
        )


def _check_master_reachability(
    slave: Instance,
    master_client: RouterClient,
) -> LayerResult:
    """Camada 5: O master enxerga o slave? Provider connection OK?"""
    try:
        providers = master_client.get_providers()
        provider = next(
            (p for p in providers if p.get("name") == slave.name),
            None,
        )

        if not provider:
            return LayerResult(
                layer="MASTER",
                status=Status.FAIL,
                message=f"Slave '{slave.name}' não encontrado no master",
                details={
                    "known_providers": [p.get("name") for p in providers],
                },
            )

        test_status = provider.get("testStatus", "unknown")
        last_error = provider.get("lastError", "")
        is_active = provider.get("isActive", False)

        if test_status == "valid":
            return LayerResult(
                layer="MASTER",
                status=Status.OK,
                message=f"Provider '{slave.name}' saudável no master",
            )
        elif test_status == "error":
            return LayerResult(
                layer="MASTER",
                status=Status.WARN,
                message=f"Provider '{slave.name}' com erro no master: {last_error[:80]}",
                details={
                    "last_error": last_error,
                    "is_active": is_active,
                },
            )
        else:
            return LayerResult(
                layer="MASTER",
                status=Status.WARN,
                message=f"Provider '{slave.name}': status '{test_status}' no master",
                details={"is_active": is_active},
            )
    except Exception as exc:
        return LayerResult(
            layer="MASTER",
            status=Status.WARN,
            message=f"Não foi possível verificar master: {exc}",
        )


def _check_upstream_via_slave(slave: Instance) -> LayerResult:
    """Camada 3: O provider upstream responde via slave?
    
    Testa fazendo uma chamada real并通过 o slave.
    """
    client = RouterClient(slave)

    if not client.login():
        return LayerResult(
            layer="UPSTREAM",
            status=Status.FAIL,
            message="Login falhou",
        )

    try:
        keys = client.get_api_keys()
        if not keys:
            return LayerResult(
                layer="UPSTREAM",
                status=Status.FAIL,
                message="Sem API key para testar upstream",
            )

        api_key = keys[0].get("key", "")
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "anthropic-version": "2023-06-01",
        }

        payload = {
            "model": "claude-sonnet-5",
            "max_tokens": 5,
            "messages": [{"role": "user", "content": "ping"}],
        }

        r = requests.post(
            f"{slave.base_url}/v1/messages",
            json=payload,
            headers=headers,
            timeout=60,
        )

        if r.status_code == 200:
            return LayerResult(
                layer="UPSTREAM",
                status=Status.OK,
                message="Provider upstream responde OK",
            )
        elif r.status_code == 429:
            return LayerResult(
                layer="UPSTREAM",
                status=Status.WARN,
                message="Provider upstream: rate limited (429)",
            )
        elif r.status_code in (403, 503):
            try:
                err = r.json()
                err_msg = err.get("error", {}).get("message", r.text[:100])
            except Exception:
                err_msg = r.text[:100]
            return LayerResult(
                layer="UPSTREAM",
                status=Status.FAIL,
                message=f"Provider upstream falhou ({r.status_code}): {err_msg[:80]}",
                details={"status_code": r.status_code, "error": err_msg},
            )
        else:
            return LayerResult(
                layer="UPSTREAM",
                status=Status.FAIL,
                message=f"Provider upstream: HTTP {r.status_code}",
                details={"status_code": r.status_code},
            )
    except requests.Timeout:
        return LayerResult(
            layer="UPSTREAM",
            status=Status.WARN,
            message="Provider upstream: timeout (60s)",
        )
    except Exception as exc:
        return LayerResult(
            layer="UPSTREAM",
            status=Status.FAIL,
            message=f"Provider upstream: {exc}",
            details={"exception": str(exc)},
        )


def diagnose_slave(
    slave: Instance,
    defaults: dict[str, Any],
    master_client: RouterClient | None = None,
) -> list[LayerResult]:
    """Executa diagnóstico completo em camadas para um slave.
    
    Retorna lista de LayerResult ordenada por camada.
    """
    results: list[LayerResult] = []

    # Camada 1: SLAVE
    results.append(_check_slave_login(slave))
    if results[-1].status == Status.FAIL:
        # Se o slave não responde, pula o resto
        return results

    # Camada 1b: API KEYS
    results.append(_check_api_keys(slave))

    # Camada 4: COMBOS
    results.append(_check_combos(slave, defaults))

    # Camada 4b: CUSTOM MODELS
    results.append(_check_custom_models(slave))

    # Camada 5: MASTER
    if master_client:
        results.append(_check_master_reachability(slave, master_client))

    return results


def print_diagnosis(slave_name: str, results: list[LayerResult]) -> None:
    """Imprime diagnóstico formatado."""
    title(f"DIAGNÓSTICO — {slave_name}")

    icons = {
        Status.OK: f"{C.GREEN}✓{C.END}",
        Status.FAIL: f"{C.RED}✗{C.END}",
        Status.WARN: f"{C.YELLOW}!{C.END}",
        Status.SKIP: f"{C.CYAN}○{C.END}",
    }

    for r in results:
        icon = icons[r.status]
        print(f"  {icon} {r.layer:15s} {r.message}")

    # Resumo
    fails = [r for r in results if r.status == Status.FAIL]
    warns = [r for r in results if r.status == Status.WARN]

    print()
    if fails:
        error(
            f"{len(fails)} problema(s) encontrado(s): "
            + ", ".join(r.layer for r in fails)
        )
    elif warns:
        warning(
            f"{len(warns)} aviso(s): "
            + ", ".join(r.layer for r in warns)
        )
    else:
        success("Todas as camadas OK")


def diagnose_all(
    config: dict[str, Any],
    master_client: RouterClient | None = None,
) -> dict[str, list[LayerResult]]:
    """Diagnostica todos os slaves do pool."""
    from .models import slave_instances

    defaults = config["defaults"]
    slaves = slave_instances(config)
    all_results: dict[str, list[LayerResult]] = {}

    title("DIAGNÓSTICO DO POOL")
    print(f"Slaves: {len(slaves)}\n")

    for slave in slaves:
        results = diagnose_slave(slave, defaults, master_client)
        all_results[slave.name] = results
        print_diagnosis(slave.name, results)
        print()

    # Resumo geral
    title("RESUMO")
    total_ok = 0
    total_fail = 0
    total_warn = 0

    for name, results in all_results.items():
        fails = sum(1 for r in results if r.status == Status.FAIL)
        warns = sum(1 for r in results if r.status == Status.WARN)
        oks = sum(1 for r in results if r.status == Status.OK)

        if fails == 0 and warns == 0:
            total_ok += 1
            print(f"  {C.GREEN}✓{C.END} {name}: OK")
        elif fails == 0:
            total_warn += 1
            print(f"  {C.YELLOW}!{C.END} {name}: {warns} aviso(s)")
        else:
            total_fail += 1
            print(f"  {C.RED}✗{C.END} {name}: {fails} falha(s), {warns} aviso(s)")

    print()
    info(f"OK: {total_ok} | Avisos: {total_warn} | Falhas: {total_fail}")

    return all_results
