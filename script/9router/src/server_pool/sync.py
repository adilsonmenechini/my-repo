"""Synchronization logic for 9Router pool."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from .console import C, error, info, success, title, warning
from .docker import COMPOSE_ARGS
from .models import (
    Instance,
    RouterClient,
    master_instance,
    slave_instances,
    wait_for_instance,
)


def configure_slave(
    slave: Instance,
    defaults: dict[str, Any],
    dry_run: bool = False,
) -> str | None:
    """Configure slave and return API key for master to use."""

    title(
        f"SLAVE {slave.name} → {slave.host}"
    )

    models = defaults.get("models", [])
    combos = defaults.get("combos", [])

    info(
        f"Models configurados: {len(models)}"
    )

    info(
        f"Combos configurados: {len(combos)}"
    )

    if dry_run:

        print()

        for model in models:
            print(f"  model: {model}")

        for combo in combos:
            print(
                f"  combo: {combo['name']} "
                f"({len(combo.get('models', []))} models)"
            )

        return None

    client = RouterClient(slave)

    # Health check antes do login
    wait_for_instance(slave, max_attempts=15, delay=3)

    if not client.login():
        error(
            f"{slave.name}: login falhou"
        )
        return None

    success(
        f"{slave.name}: login OK"
    )

    # ------------------------------------------------------------------------
    # REMOVER API KEY EXISTENTE "pool-key"
    # ------------------------------------------------------------------------

    existing_keys = client.get_api_keys()

    for key in existing_keys:
        if key.get("name") == "pool-key":
            try:
                client.delete_api_key(key["id"])
                info(
                    f"{slave.name}: API key 'pool-key' removida"
                )
            except Exception as exc:
                warning(
                    f"{slave.name}: erro ao remover API key: {exc}"
                )

    # ------------------------------------------------------------------------
    # CRIAR NOVA API KEY "pool-key"
    # ------------------------------------------------------------------------

    api_key = ""

    try:
        key_data = client.create_api_key(name="pool-key")
        api_key = key_data.get("key", "")
        success(
            f"{slave.name}: API key criada"
        )
    except Exception as exc:
        error(
            f"{slave.name}: erro ao criar API key: {exc}"
        )

    # ------------------------------------------------------------------------
    # REMOVER COMBOS EXISTENTES
    # ------------------------------------------------------------------------

    existing_combos = client.get_combos()

    for combo in existing_combos:
        combo_id = combo.get("id")
        combo_name = combo.get("name")

        if combo_id:
            try:
                client.delete_combo(combo_id)
                info(
                    f"{slave.name}: combo '{combo_name}' removido"
                )
            except Exception as exc:
                warning(
                    f"{slave.name}: erro ao remover combo '{combo_name}': {exc}"
                )

    # ------------------------------------------------------------------------
    # HEALTH CHECK ANTES DE CRIAR COMBOS
    # ------------------------------------------------------------------------

    info(f"{slave.name}: verificando saúde...")

    try:
        r = client.session.get(
            f"{client.instance.base_url}/api/combos",
            timeout=client.timeout,
        )
        r.raise_for_status()
        success(f"{slave.name}: saudável")
    except Exception as exc:
        error(f"{slave.name}: não saudável: {exc}")
        return None

    # ------------------------------------------------------------------------
    # CRIAR COMBOS DO DEFAULTS
    # ------------------------------------------------------------------------

    for combo_config in combos:

        combo_name = combo_config["name"]
        combo_models = combo_config.get(
            "models",
            [],
        )

        try:
            client.create_combo(
                name=combo_name,
                models=combo_models,
            )

            success(
                f"{slave.name}: combo '{combo_name}' criado"
            )

        except Exception as exc:
            error(
                f"{slave.name}: erro ao criar combo '{combo_name}': {exc}"
            )

    # ------------------------------------------------------------------------
    # VERIFICAR COMBOS CRIADOS
    # ------------------------------------------------------------------------

    combos_criados = client.get_combos()
    nomes_esperados = {c["name"] for c in combos}
    nomes_criados = {c["name"] for c in combos_criados}

    if nomes_esperados == nomes_criados:
        success(
            f"{slave.name}: todos os {len(nomes_criados)} combos verificados"
        )
    else:
        faltando = nomes_esperados - nomes_criados
        if faltando:
            error(
                f"{slave.name}: combos faltando: {faltando}"
            )

    # ------------------------------------------------------------------------
    # REMOVER CUSTOM MODELS EXISTENTES (não criar novos no slave)
    # ------------------------------------------------------------------------

    existing_custom = client.get_custom_models()

    for cm in existing_custom:
        pa = cm.get("providerAlias", "")
        mid = cm.get("id", "")

        # Só remover modelos que NÃO estão em nenhum combo
        in_any_combo = any(
            mid in combo.get("models", [])
            for combo in combos
        )

        if not in_any_combo and pa == "oc":
            try:
                client.delete_custom_model(pa, mid)
                info(
                    f"{slave.name}: custom model '{pa}/{mid}' removido"
                )
            except Exception:
                pass

    success(
        f"{slave.name}: configuração aplicada"
    )

    return api_key


def sync_master(
    config: dict[str, Any],
    slave_api_keys: dict[str, str],
    dry_run: bool = False,
    skip_health_check: bool = False,
) -> None:

    master = master_instance(config)

    defaults = config["defaults"]
    slaves = slave_instances(config)

    title(f"MASTER {master.host}")

    # Health check antes de começar
    wait_for_instance(master, max_attempts=20, delay=3)

    master_client = RouterClient(master)

    if not master_client.login():
        error("Não foi possível fazer login no master")
        return

    success("Master: login OK")

    # ------------------------------------------------------------------------
    # REMOVER TODOS OS PROVIDER NODES EXISTENTES
    # ------------------------------------------------------------------------

    existing_nodes = master_client.get_provider_nodes()

    for node in existing_nodes:
        node_id = node.get("id")
        node_name = node.get("name")

        if dry_run:
            info(f"[dry-run] removeria provider node '{node_name}'")
        else:
            try:
                master_client.delete_provider_node(node_id)
                info(f"master: provider node '{node_name}' removido")
            except Exception as exc:
                warning(f"master: erro ao remover provider node '{node_name}': {exc}")

    # ------------------------------------------------------------------------
    # REMOVER TODAS AS PROVIDER CONNECTIONS EXISTENTES
    # ------------------------------------------------------------------------

    existing_providers = master_client.get_providers()

    for provider in existing_providers:
        provider_id = provider.get("id")
        provider_name = provider.get("name")

        if dry_run:
            info(f"[dry-run] removeria provider connection '{provider_name}'")
        else:
            try:
                master_client.delete_provider(provider_id)
                info(f"master: provider connection '{provider_name}' removido")
            except Exception as exc:
                warning(f"master: erro ao remover provider connection '{provider_name}': {exc}")

    # ------------------------------------------------------------------------
    # REMOVER TODOS OS CUSTOM MODELS
    # ------------------------------------------------------------------------

    existing_custom = master_client.get_custom_models()

    for cm in existing_custom:
        pa = cm.get("providerAlias", "")
        mid = cm.get("id", "")

        if dry_run:
            info(f"[dry-run] removeria custom model '{pa}/{mid}'")
        else:
            try:
                master_client.delete_custom_model(pa, mid)
                info(f"master: custom model '{pa}/{mid}' removido")
            except Exception as exc:
                warning(f"master: erro ao remover custom model '{pa}/{mid}': {exc}")

    # ------------------------------------------------------------------------
    # REMOVER COMBOS DOS SLAVES
    # ------------------------------------------------------------------------

    existing_combos = master_client.get_combos()

    for combo in existing_combos:
        combo_id = combo.get("id")
        combo_name = combo.get("name")

        if combo_name in defaults.get("publish", []):
            if dry_run:
                info(
                    f"[dry-run] removeria combo '{combo_name}'"
                )
            else:
                try:
                    master_client.delete_combo(combo_id)
                    info(
                        f"master: combo '{combo_name}' removido"
                    )
                except Exception as exc:
                    warning(
                        f"master: erro ao remover combo '{combo_name}': {exc}"
                    )

    if dry_run:
        return

    # ------------------------------------------------------------------------
    # 1. CRIAR PROVIDER NODES PARA CADA SLAVE
    # ------------------------------------------------------------------------

    node_ids: dict[str, str] = {}

    for slave in slaves:
        if slave.name not in slave_api_keys:
            warning(
                f"{slave.name}: não configurado, pulando publicação"
            )
            continue

        try:
            node = master_client.create_provider_node(
                name=slave.name,
                prefix=slave.name,
                base_url=f"{slave.docker_url}/v1",
                node_type="anthropic-compatible",
            )

            node_id = node.get("id", "")
            node_ids[slave.name] = node_id

            success(
                f"master: provider node '{slave.name}' criado "
                f"(→ {slave.docker_url}/v1, id={node_id})"
            )

        except Exception as exc:
            error(
                f"master: erro ao criar provider node '{slave.name}': {exc}"
            )

    # ------------------------------------------------------------------------
    # 2. CRIAR PROVIDER CONNECTIONS PARA CADA SLAVE
    # ------------------------------------------------------------------------

    for slave in slaves:
        if slave.name not in slave_api_keys:
            continue

        node_id = node_ids.get(slave.name, "")

        if not node_id:
            warning(
                f"{slave.name}: node ID não disponível"
            )
            continue

        try:
            api_key = slave_api_keys.get(slave.name, slave.password)

            master_client.create_provider(
                provider_type=node_id,
                name=slave.name,
                api_key=api_key,
                base_url=f"{slave.docker_url}/v1",
                priority=1,
                default_model="claude-sonnet-5",
                provider_specific_data={
                    "prefix": slave.name,
                    "baseUrl": f"{slave.docker_url}/v1",
                    "nodeName": slave.name,
                },
            )

            success(
                f"master: provider connection '{slave.name}' criado "
                f"(→ {slave.docker_url}/v1)"
            )

        except Exception as exc:
            error(
                f"master: erro ao criar provider connection '{slave.name}': {exc}"
            )

    # ------------------------------------------------------------------------
    # 3. CRIAR CUSTOM MODELS PARA CADA NODE
    # ------------------------------------------------------------------------

    for slave in slaves:
        if slave.name not in node_ids:
            continue

        node_id = node_ids[slave.name]

        # Modelos dos defaults (com retry)
        for model in defaults.get("models", []):
            parts = model.split("/", 1)
            if len(parts) == 2:
                alias, model_id = parts
            else:
                alias = "oc"
                model_id = model

            for attempt in range(3):
                try:
                    master_client.add_custom_model(
                        provider_alias=node_id,
                        model_id=model_id,
                        model_name=model_id,
                    )
                    info(
                        f"master: custom model '{node_id}/{model_id}' adicionado"
                    )
                    break
                except Exception as exc:
                    if attempt < 2:
                        warning(f"master: retry {attempt + 1}/3 para custom model '{node_id}/{model_id}'")
                        time.sleep(5)
                        # Re-login after connection error
                        try:
                            master_client.login()
                        except Exception:
                            pass
                    else:
                        warning(
                            f"master: erro ao adicionar custom model '{node_id}/{model_id}': {exc}"
                        )

        # Modelos dos combos (com retry)
        for combo_config in defaults.get("combos", []):
            for model in combo_config.get("models", []):
                parts = model.split("/", 1)
                if len(parts) == 2:
                    _alias, model_id = parts
                else:
                    _alias = "oc"
                    model_id = model

                for attempt in range(3):
                    try:
                        master_client.add_custom_model(
                            provider_alias=node_id,
                            model_id=model_id,
                            model_name=model_id,
                        )
                        break
                    except Exception:
                        if attempt < 2:
                            time.sleep(5)
                            try:
                                master_client.login()
                            except Exception:
                                pass

    # ------------------------------------------------------------------------
    # HEALTH CHECK (paralelo) — DEPOIS dos custom models, para que /test veja ≥1 model
    # ------------------------------------------------------------------------

    if skip_health_check:
        info("Health check ignorado (--skip-health-check)")
    else:
        info("Aguardando providers inicializarem (60s para Tor estabelecer circuitos)...")
        time.sleep(60)

        title("HEALTH CHECK (paralelo)")

        providers = master_client.get_providers()
        base_url = master_client.instance.base_url

        def _test_provider(provider: dict) -> tuple[str, bool, str]:
            """Testa um provider em thread separada. Retorna (name, valid, error)."""
            import requests as _req
            provider_id = provider.get("id", "")
            provider_name = provider.get("name", provider_id)
            session = _req.Session()
            # Re-login nesta thread com nova session
            try:
                session.post(
                    f"{base_url}/api/auth/login",
                    json={"password": master_client.instance.password},
                    timeout=10,
                )
            except Exception:
                pass
            try:
                response = session.post(
                    f"{base_url}/api/providers/{provider_id}/test",
                    json={},
                    timeout=120,
                )
                response.raise_for_status()
                data = response.json()
                valid = data.get("valid", False)
                err = data.get("error", "")
                return provider_name, valid, err
            except Exception as exc:
                return provider_name, False, str(exc)

        with ThreadPoolExecutor(max_workers=len(providers) or 1) as executor:
            future_to_provider = {
                executor.submit(_test_provider, p): p
                for p in providers
                if p.get("id")
            }
            for future in as_completed(future_to_provider):
                name, valid, err = future.result()
                if valid:
                    success(f"master: provider '{name}' → SAUDÁVEL")
                elif err:
                    warning(f"master: provider '{name}' → FALHOU: {err}")
                else:
                    warning(f"master: provider '{name}' → FALHOU (sem detalhes)")

    # ------------------------------------------------------------------------
    # 4. CRIAR COMBOS NO MASTER
    #
    # Cada combo contém:
    #   - Modelos originais do combo (sem prefixo)
    #   - defaults.models prefixados com cada slave (excluindo nomes de outros combos)
    # ------------------------------------------------------------------------

    publish = defaults.get("publish", [])
    all_models = defaults.get("models", [])

    # Nomes de todos os combos publicados (para excluir de prefixed models)
    other_combo_names = set()
    for c in defaults.get("combos", []):
        if c["name"] in publish:
            other_combo_names.add(c["name"])

    for combo_config in defaults.get("combos", []):
        combo_name = combo_config["name"]
        combo_models = combo_config.get("models", [])

        if combo_name not in publish:
            continue

        final_models = []

        # Apenas o nome do combo prefixado com cada slave
        for slave in slaves:
            if slave.name not in node_ids:
                continue
            prefixed = f"{slave.name}/{combo_name}"
            final_models.append(prefixed)

        if not final_models:
            warning(
                f"master: combo '{combo_name}' sem modelos para publicar"
            )
            continue

        for attempt in range(3):
            try:
                master_client.create_combo(
                    name=combo_name,
                    models=final_models,
                )

                success(
                    f"master: combo '{combo_name}' criado "
                    f"({len(final_models)} modelos)"
                )
                break

            except Exception as exc:
                if attempt < 2:
                    warning(f"master: retry {attempt + 1}/3 para combo '{combo_name}'")
                    time.sleep(5)
                    try:
                        master_client.login()
                    except Exception:
                        pass
                else:
                    error(
                        f"master: erro ao criar combo '{combo_name}': {exc}"
                    )

    # ------------------------------------------------------------------------
    # 5. CONFIGURAR ROUND-ROBIN NO MASTER
    # ------------------------------------------------------------------------

    settings = master_client.get_settings()

    provider_strategies = settings.get("providerStrategies", {})

    for slave in slaves:
        if slave.name in node_ids:
            node_id = node_ids[slave.name]
            provider_strategies[node_id] = {
                "fallbackStrategy": "round-robin",
                "stickyRoundRobinLimit": 1,
            }

    try:
        master_client.update_settings(
            providerStrategies=provider_strategies,
            stickyRoundRobinLimit=3,
            comboStrategy="round-robin",
            comboStickyRoundRobinLimit=3,
        )

        success(
            "master: round-robin configurado"
        )

    except Exception as exc:
        warning(
            f"master: erro ao configurar round-robin: {exc}"
        )


def sync_pool(
    config: dict[str, Any],
    dry_run: bool = False,
    skip_health_check: bool = True,
) -> None:
    """Aplica a topologia do config (slaves configurados + combos publicados).

    ``dry_run=True`` tem que significar **zero escrita** em qualquer host: as
    sub-rotinas imprimem o que fariam e o fluxo aborta antes de rotacionar
    key ou mexer em provider. Garantido por tests/test_sync_dry_run.py.
    """

    defaults = config["defaults"]
    slaves = slave_instances(config)
    master = master_instance(config)

    title("9ROUTER POOL SYNC")

    print(
        f"Slaves: {len(slaves)}"
    )

    print(
        f"Models: {len(defaults.get('models', []))}"
    )

    print(
        f"Combos: {len(defaults.get('combos', []))}"
    )

    print(
        f"Publish: {defaults.get('publish', [])}"
    )

    if dry_run:

        title("DRY RUN")

        for slave in slaves:

            print(
                f"\n{C.BOLD}{slave.name}{C.END} "
                f"→ {slave.host}"
            )

            print(
                "  Models:"
            )

            for model in defaults.get(
                "models",
                [],
            ):
                print(
                    f"    - {model}"
                )

            print(
                "  Combos:"
            )

            for combo in defaults.get(
                "combos",
                [],
            ):
                print(
                    f"    - {combo['name']}"
                )

                for model in combo.get(
                    "models",
                    [],
                ):
                    print(
                        f"        {model}"
                    )

            print(
                "  Publish:"
            )

            for combo_name in defaults.get(
                "publish",
                [],
            ):
                print(
                    f"    - {combo_name}"
                )

    # Preview do Master
    title("MASTER (preview)")

    print("  Combos a serem criados:")
    for combo_config in defaults.get("combos", []):
        combo_name = combo_config["name"]
        if combo_name not in defaults.get("publish", []):
            continue
        print(f"    - {combo_name}:")
        for slave in slaves:
            print(f"        - {slave.name}/{combo_name}")

    # ------------------------------------------------------------------------
    # 1. CONFIGURAR TODOS OS SLAVES
    # ------------------------------------------------------------------------

    slave_api_keys: dict[
        str,
        str
    ] = {}

    for slave in slaves:

        try:

            api_key = configure_slave(
                slave,
                defaults,
                dry_run=dry_run,
            )

            if api_key:
                slave_api_keys[
                    slave.name
                ] = api_key

        except Exception as exc:

            error(
                f"{slave.name}: {exc}"
            )

    # ------------------------------------------------------------------------
    # 2. PUBLICAR NO MASTER
    # ------------------------------------------------------------------------

    sync_master(
        config,
        slave_api_keys,
        dry_run=dry_run,
        skip_health_check=skip_health_check,
    )

    # Dry-run termina aqui de propósito: as rotinas acima já imprimiram o que
    # fariam, e o que vem abaixo é mutação de verdade (rotacionar a API key do
    # master e exportá-la). Antes o flag controlava só o preview e o sync
    # seguia com dry_run=False hardcoded — "simular" reconfigurava o pool.
    if dry_run:
        info(
            "[dry-run] simulação concluída — nada foi alterado"
        )
        return

    # ------------------------------------------------------------------------
    # EXPORTAR API KEY DO MASTER
    # ------------------------------------------------------------------------

    try:
        master_client = RouterClient(master)
        if master_client.login():
            keys = master_client.get_api_keys()
            
            # Se não tem API key, criar uma
            if not keys:
                info("Criando API key no master...")
                key_data = master_client.create_api_key(name="pool-key")
                if key_data:
                    keys = [key_data]
                    success("API key criada no master")
            
            if keys:
                default_key = keys[0].get("key", "")
                print()
                success("API Key do Master (use nos clientes):")
                info(f"  {default_key}")
                print()
                info("Exemplo OpenCode:")
                info(f'  apiKey: "{default_key}"')
                info(f'  baseUrl: "http://{master.host}/v1"')
            else:
                warning("Não foi possível criar API key no master")
        else:
            warning("Login no master falhou para exportar API key")
    except Exception as exc:
        warning(f"Erro ao exportar API key: {exc}")

    success(
        "\nSync concluído!"
    )


def _remove_slave_from_master(
    master_client: RouterClient,
    slave_name: str,
    defaults: dict[str, Any],
) -> None:
    """
    Remove provider connection, provider node e atualiza combos
    para excluir modelos do slave_name. Não toca em outros slaves.
    """
    try:
        providers = master_client.get_providers()
        for p in providers:
            if p.get("name") == slave_name:
                master_client.delete_provider(p["id"])
                info(f"master: provider connection '{slave_name}' removido")
    except Exception as exc:
        warning(f"master: erro ao remover provider connection '{slave_name}': {exc}")

    try:
        nodes = master_client.get_provider_nodes()
        for n in nodes:
            if n.get("name") == slave_name:
                master_client.delete_provider_node(n["id"])
                info(f"master: provider node '{slave_name}' removido")
    except Exception as exc:
        warning(f"master: erro ao remover provider node '{slave_name}': {exc}")

    try:
        publish = defaults.get("publish", [])
        existing_combos = master_client.get_combos()
        for combo in existing_combos:
            if combo.get("name") not in publish:
                continue
            old_models = combo.get("models", [])
            new_models = [m for m in old_models if not m.startswith(f"{slave_name}/")]
            if len(new_models) != len(old_models):
                master_client.delete_combo(combo["id"])
                master_client.create_combo(name=combo["name"], models=new_models)
                info(f"master: combo '{combo['name']}' atualizado (removido {slave_name})")
    except Exception as exc:
        warning(f"master: erro ao atualizar combos na remoção de '{slave_name}': {exc}")


def quarantine_slave(
    config: dict[str, Any],
    slave_name: str,
) -> bool:
    """
    Coloca o slave em quarentena: remove temporariamente os modelos do slave dos combos do master.
    O container permanece rodando, mas não recebe requisições dos clientes.
    """
    defaults = config["defaults"]
    master = master_instance(config)
    master_client = RouterClient(master)

    if not master_client.login():
        return False

    title(f"QUARENTENA {slave_name} — removendo dos combos do master")
    try:
        publish = defaults.get("publish", [])
        existing_combos = master_client.get_combos()
        for combo in existing_combos:
            if combo.get("name") not in publish:
                continue
            old_models = combo.get("models", [])
            new_models = [m for m in old_models if not m.startswith(f"{slave_name}/")]
            if len(new_models) != len(old_models):
                master_client.delete_combo(combo["id"])
                master_client.create_combo(name=combo["name"], models=new_models)
                warning(f"master: slave '{slave_name}' em quarentena (removido do combo '{combo['name']}')")
        return True
    except Exception as exc:
        warning(f"master: erro ao colocar '{slave_name}' em quarentena: {exc}")
        return False


def unquarantine_slave(
    config: dict[str, Any],
    slave_name: str,
) -> bool:
    """
    Remove o slave da quarentena: re-adiciona os modelos do slave aos combos do master.
    """
    defaults = config["defaults"]
    master = master_instance(config)
    master_client = RouterClient(master)

    if not master_client.login():
        return False

    title(f"DESFAZER QUARENTENA {slave_name} — devolvendo aos combos do master")
    try:
        all_models = defaults.get("models", [])
        publish = defaults.get("publish", [])
        other_combo_names = {c["name"] for c in defaults.get("combos", []) if c["name"] in publish}
        existing_combos = master_client.get_combos()

        for combo_config in defaults.get("combos", []):
            combo_name = combo_config["name"]
            if combo_name not in publish:
                continue
            existing = next((c for c in existing_combos if c.get("name") == combo_name), None)
            current_models = existing.get("models", []) if existing else []

            new_entries = []
            for model_str in all_models:
                if model_str in other_combo_names and model_str != combo_name:
                    continue
                entry = f"{slave_name}/{model_str}"
                if entry not in current_models:
                    new_entries.append(entry)

            if new_entries:
                if existing:
                    master_client.delete_combo(existing["id"])
                master_client.create_combo(
                    name=combo_name,
                    models=current_models + new_entries,
                )
                success(f"master: slave '{slave_name}' fora da quarentena (devolvido ao combo '{combo_name}')")
        return True
    except Exception as exc:
        warning(f"master: erro ao tirar '{slave_name}' da quarentena: {exc}")
        return False


def hot_reload_master_models(config: dict[str, Any]) -> None:
    """
    Atualiza Custom Models e Combos no Master com base no config.json sem reiniciar Slaves.
    """
    defaults = config["defaults"]
    slaves = slave_instances(config)
    master = master_instance(config)

    master_client = RouterClient(master)
    if not master_client.login():
        warning("hot_reload: login no master falhou")
        return

    title("HOT-RELOAD MODELOS NO MASTER")

    # 1. Mapear provider nodes existentes
    try:
        nodes = master_client.get_provider_nodes()
        node_ids = {n["name"]: n["id"] for n in nodes if n.get("name") and n.get("id")}
    except Exception as exc:
        warning(f"hot_reload: erro ao buscar provider nodes: {exc}")
        return

    # 2. Adicionar custom models para cada slave/node
    all_models = defaults.get("models", [])
    combo_models = [m for c in defaults.get("combos", []) for m in c.get("models", [])]
    all_target_models = set(all_models + combo_models)

    for slave in slaves:
        if slave.name not in node_ids:
            continue
        node_id = node_ids[slave.name]
        for model_str in all_target_models:
            parts = model_str.split("/", 1)
            model_id = parts[1] if len(parts) == 2 else model_str
            try:
                master_client.add_custom_model(
                    provider_alias=node_id,
                    model_id=model_id,
                    model_name=model_id,
                )
            except Exception:
                pass

    # 3. Recriar/atualizar combos no master
    publish = defaults.get("publish", [])
    other_combo_names = {c["name"] for c in defaults.get("combos", []) if c["name"] in publish}
    existing_combos = master_client.get_combos()

    for combo_config in defaults.get("combos", []):
        combo_name = combo_config["name"]
        if combo_name not in publish:
            continue

        final_models = []

        for slave in slaves:
            if slave.name not in node_ids:
                continue
            prefixed = f"{slave.name}/{combo_name}"
            final_models.append(prefixed)

        # Atualizar combo se existia ou criar novo
        existing = next((c for c in existing_combos if c.get("name") == combo_name), None)
        if existing:
            try:
                master_client.delete_combo(existing["id"])
            except Exception:
                pass
        try:
            master_client.create_combo(name=combo_name, models=final_models)
            success(f"master: hot-reload combo '{combo_name}' ({len(final_models)} modelos)")
        except Exception as exc:
            warning(f"master: erro no hot-reload combo '{combo_name}': {exc}")


def apply_combo_plan(
    client: RouterClient,
    instance: Instance,
    plan: list[tuple[str, list[str] | None]],
    existing_combos: list[dict[str, Any]] | None = None,
) -> None:
    """Diferencia o servidor a partir do plano e cria só o que mudou.

    ``plan`` é uma lista de ``(nome, modelos)`` onde ``modelos=None`` significa
    "manter o combo atual" (fallback quando ele ficou sem nenhum modelo vivo).

    Antes todo reload apagava e recriava TODOS os combos — durante o intervalo
    o combo não existia e request de cliente caía. Com diff, combo cujo
    conjunto de modelos não mudou não é tocado. Também é o que o watchdog usa
    para revalidar sem derrubar o roteamento.

    Combos ausentes do plano nunca são apagados: o plano só descreve o que é
    nosso. Se o servidor tem qualquer combo fora do plano (outro operador
    administra aquele host), a criação de combos gerenciados é suspensa —
    nesse caso só atualizamos os que já existem lá.
    """
    if existing_combos is None:
        existing_combos = client.get_combos()

    kept_names = {name for name, models in plan if models is None}
    targets = {name: models for name, models in plan if models is not None}
    unchanged: set[str] = set()
    existing_names = {c.get("name") for c in existing_combos}
    has_foreign = any(
        c.get("name") not in targets and c.get("name") not in kept_names
        for c in existing_combos
    )

    for combo in existing_combos:
        combo_id = combo.get("id")
        combo_name = combo.get("name")

        if combo_name in kept_names:
            info(
                f"{instance.name}: combo '{combo_name}' mantido (último conjunto válido)"
            )
            continue

        target = targets.get(combo_name)

        if target is None:
            continue

        if list(combo.get("models", [])) == target:
            unchanged.add(combo_name)
            info(f"{instance.name}: combo '{combo_name}' inalterado")
            continue

        if combo_id:
            try:
                client.delete_combo(combo_id)
                info(f"{instance.name}: combo '{combo_name}' removido")
            except Exception as exc:  # noqa: BLE001 - falha de um combo não aborta o plano
                warning(
                    f"{instance.name}: erro ao remover combo '{combo_name}': {exc}"
                )

    for combo_name, combo_models in plan:
        if combo_models is None:
            continue

        if combo_name in unchanged:
            continue

        if combo_name not in existing_names and has_foreign:
            continue

        try:
            client.create_combo(
                name=combo_name,
                models=combo_models,
            )
            success(
                f"{instance.name}: combo '{combo_name}' criado "
                f"com {len(combo_models)} modelos"
            )
        except Exception as exc:  # noqa: BLE001 - falha de um combo não aborta o plano
            error(
                f"{instance.name}: erro ao criar combo '{combo_name}': {exc}"
            )


def _add_slave_to_master(
    master_client: RouterClient,
    slave: Instance,
    api_key: str,
    defaults: dict[str, Any],
) -> str:
    """
    Cria: provider node + connection + custom models.
    Atualiza combos existentes adicionando modelos do slave.
    Retorna node_id criado.
    """
    node = master_client.create_provider_node(
        name=slave.name,
        prefix=slave.name,
        base_url=f"{slave.docker_url}/v1",
        node_type="anthropic-compatible",
    )
    node_id = node.get("id", "")
    success(f"master: provider node '{slave.name}' criado (id={node_id})")

    master_client.create_provider(
        provider_type=node_id,
        name=slave.name,
        api_key=api_key,
        base_url=f"{slave.docker_url}/v1",
        priority=1,
        default_model="claude-sonnet-5",
        provider_specific_data={
            "prefix": slave.name,
            "baseUrl": f"{slave.docker_url}/v1",
            "nodeName": slave.name,
        },
    )
    success(f"master: provider connection '{slave.name}' criado")

    all_models = defaults.get("models", [])
    combo_models = [m for c in defaults.get("combos", []) for m in c.get("models", [])]
    for model_str in set(all_models + combo_models):
        parts = model_str.split("/", 1)
        model_id = parts[1] if len(parts) == 2 else model_str
        try:
            master_client.add_custom_model(
                provider_alias=node_id,
                model_id=model_id,
                model_name=model_id,
            )
        except Exception:
            pass

    publish = defaults.get("publish", [])
    other_combo_names = {c["name"] for c in defaults.get("combos", []) if c["name"] in publish}
    existing_combos = master_client.get_combos()

    for combo_config in defaults.get("combos", []):
        combo_name = combo_config["name"]
        if combo_name not in publish:
            continue
        existing = next((c for c in existing_combos if c.get("name") == combo_name), None)
        current_models = existing.get("models", []) if existing else []

        new_entries = []
        for model_str in all_models:
            if model_str in other_combo_names and model_str != combo_name:
                continue
            entry = f"{slave.name}/{model_str}"
            if entry not in current_models:
                new_entries.append(entry)

        if new_entries:
            if existing:
                master_client.delete_combo(existing["id"])
            master_client.create_combo(
                name=combo_name,
                models=current_models + new_entries,
            )
            info(f"master: combo '{combo_name}' atualizado (+{len(new_entries)} modelos de {slave.name})")

    try:
        settings = master_client.get_settings()
        strats = settings.get("providerStrategies", {})
        strats[node_id] = {"fallbackStrategy": "round-robin", "stickyRoundRobinLimit": 1}
        master_client.update_settings(providerStrategies=strats)
    except Exception as exc:
        warning(f"master: round-robin para '{slave.name}': {exc}")

    # ------------------------------------------------------------------------
    # TESTAR O PROVIDER RECÉM-CRIADO (após custom models + combos)
    # ------------------------------------------------------------------------
    try:
        # Buscar o provider recém-criado pelo nome
        providers = master_client.get_providers()
        new_provider = next((p for p in providers if p.get("name") == slave.name), None)
        if new_provider and new_provider.get("id"):
            data = master_client.test_provider(new_provider["id"], timeout=120)
            valid = data.get("valid", False)
            err = data.get("error", "")
            if valid:
                success(f"master: provider '{slave.name}' → SAUDÁVEL")
            else:
                warning(f"master: provider '{slave.name}' → FALHOU: {err}")
        else:
            warning(f"master: provider '{slave.name}' não encontrado para teste")
    except Exception as exc:
        warning(f"master: erro ao testar provider '{slave.name}': {exc}")

    return node_id


def replace_slave(
    config: dict[str, Any],
    slave_name: str,
) -> bool:
    """
    Delete completo + create fresco de um slave:
    1. Remove do master
    2. Para e deleta container + volume
    3. Recria container
    4. Configura slave
    5. Re-adiciona ao master
    Retorna True se bem-sucedido.
    
    rs000 é permanente e nunca será substituído.
    """
    # rs000 is permanent and cannot be replaced
    if slave_name == "rs000":
        error(f"replace_slave: '{slave_name}' é permanente e não pode ser substituído")
        return False

    import subprocess
    from .config import BASE_DIR
    from .sync import configure_slave

    slaves = slave_instances(config)
    slave = next((s for s in slaves if s.name == slave_name), None)
    if not slave:
        error(f"replace_slave: slave '{slave_name}' não encontrado no config")
        return False

    defaults = config["defaults"]
    master = master_instance(config)
    master_client = RouterClient(master)

    if not master_client.login():
        error("replace_slave: login no master falhou")
        return False

    title(f"REPLACE {slave_name} — removendo do master")
    _remove_slave_from_master(master_client, slave_name, defaults)

    # Identificar o índice do slave para o nome do serviço docker
    # Supondo padronização rs001 -> 9router-slave-001
    slave_num = slave_name.replace("rs", "")
    service_name = f"9router-slave-{slave_num}"
    container_name = f"sbx-{service_name}"
    # Nome do volume criado pelo docker compose
    vol_name = f"9router-pool_{service_name}-data"

    info(f"Parando container '{container_name}'...")
    subprocess.run(
        ["docker", "compose", *COMPOSE_ARGS, "stop", service_name],
        cwd=BASE_DIR, capture_output=True,
    )
    subprocess.run(
        ["docker", "compose", *COMPOSE_ARGS, "rm", "-f", service_name],
        cwd=BASE_DIR, capture_output=True,
    )

    info(f"Removendo volume '{vol_name}'...")
    subprocess.run(
        ["docker", "volume", "rm", vol_name],
        capture_output=True,
    )

    info(f"Criando novo container '{service_name}'...")
    result = subprocess.run(
        ["docker", "compose", *COMPOSE_ARGS, "up", "-d", "--no-deps", service_name],
        cwd=BASE_DIR, capture_output=True, text=True,
    )
    if result.returncode != 0:
        error(f"replace_slave: docker compose up falhou: {result.stderr}")
        return False

    if not wait_for_instance(slave, max_attempts=20, delay=3):
        error(f"replace_slave: {slave_name} não ficou pronto a tempo")
        return False

    api_key = configure_slave(slave, defaults)
    if not api_key:
        error(f"replace_slave: configure_slave falhou para {slave_name}")
        return False

    title(f"REPLACE {slave_name} — re-adicionando ao master")
    _add_slave_to_master(master_client, slave, api_key, defaults)

    success(f"replace_slave: {slave_name} substituído com sucesso!")
    return True

