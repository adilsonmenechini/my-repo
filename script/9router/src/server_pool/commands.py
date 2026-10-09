"""Command functions for 9Router pool management."""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import Any

from .config import BASE_DIR, save_config
from .console import C, error, info, success, title, warning
from .docker import (
    COMPOSE_ARGS,
    COMPOSE_FILE,
    ENV_FILE,
    create_data_directories,
    generate_docker_compose,
    generate_env,
    remove_data_directories,
    update_config_for_scale,
)
from .fetch import fetch_opencode_free_models, update_config_with_models
from .models import (
    RouterClient,
    master_instance,
    slave_instances,
    wait_for_instance,
)
from .sync import (
    sync_pool,
    configure_slave,
    _add_slave_to_master,
)


def list_pool(config: dict[str, Any]) -> None:

    defaults = config["defaults"]
    slaves = slave_instances(config)
    master = master_instance(config)

    title("9ROUTER POOL")

    print(
        f"Master: {C.BOLD}{master.host}{C.END}"
    )

    print(
        f"Slaves: {len(slaves)}"
    )

    for slave in slaves:
        print(
            f"  - {slave.name} → {slave.host}"
        )

    print()

    models = defaults.get("models", [])
    combos = defaults.get("combos", [])
    publish = defaults.get("publish", [])

    print(
        f"Models: {len(models)}"
    )

    for model in models:
        print(f"  - {model}")

    print()

    print(
        f"Combos: {len(combos)}"
    )

    for combo in combos:
        print(
            f"  - {combo['name']} "
            f"({len(combo.get('models', []))} models)"
        )

        for model in combo.get("models", []):
            print(f"      {model}")

    print()

    print(
        f"Publish: {publish}"
    )


def test_pool(config: dict[str, Any]) -> None:

    slaves = slave_instances(config)
    master = master_instance(config)

    title("9ROUTER POOL TEST")

    all_instances = [master] + slaves

    ok_count = 0

    for instance in all_instances:

        print(
            f"\n{C.BOLD}{instance.name}{C.END} → {instance.host}"
        )

        client = RouterClient(instance)

        if client.login():
            success("login OK")

            try:
                nodes = client.get_provider_nodes()
                info(f"provider nodes: {len(nodes)}")
            except Exception as exc:
                warning(f"provider nodes: {exc}")

            try:
                providers = client.get_providers()
                info(f"provider connections: {len(providers)}")

                # Health check for master providers
                if instance.name == master.name and providers:
                    print()
                    info("Aguardando providers inicializarem...")
                    time.sleep(10)
                    for provider in providers:
                        pid = provider.get("id")
                        pname = provider.get("name")
                        if pid:
                            try:
                                # Usar timeout maior (90s) para health check
                                response = client.session.post(
                                    f"{client.instance.base_url}/api/providers/{pid}/test",
                                    json={},
                                    timeout=90,
                                )
                                response.raise_for_status()
                                result = response.json()
                                valid = result.get("valid", False)
                                err = result.get("error")
                                if valid:
                                    success(f"  {pname} → SAUDÁVEL")
                                else:
                                    warning(f"  {pname} → FALHOU: {err}")
                            except Exception as exc:
                                error(f"  {pname} → ERRO: {exc}")

            except Exception as exc:
                warning(f"provider connections: {exc}")

            try:
                combos = client.get_combos()
                info(f"combos: {len(combos)}")
            except Exception as exc:
                warning(f"combos: {exc}")

            try:
                custom = client.get_custom_models()
                info(f"custom models: {len(custom)}")
            except Exception as exc:
                warning(f"custom models: {exc}")

            try:
                models = client.get_models()
                info(f"models: {len(models)}")
            except Exception as exc:
                warning(f"models: {exc}")

            ok_count += 1

        else:
            error("login falhou")

    print()

    if ok_count == len(all_instances):
        success(
            f"Todos os {ok_count} instâncias OK"
        )
    else:
        warning(
            f"{ok_count}/{len(all_instances)} instâncias OK"
        )


def scale_pool(
    config: dict[str, Any],
    num_slaves: int,
    dry_run: bool = False,
    no_sync: bool = False,
) -> None:
    """Scale pool to N regular slaves (rs001...rsN).
    
    rs000 is a permanent slave that is always present and never scaled.
    """
    title(f"SCALE → {num_slaves} regular slaves (+ rs000 permanent)")

    if num_slaves < 2:
        error("Número de slaves regulares deve ser >= 2 (mínimo recomendado para réplica/failover)")
        return
    if num_slaves > 50:
        error("Número máximo de slaves regulares: 50")
        return

    # Count regular slaves (excluding rs000)
    current_regular = len([s for s in config["slaves"] if s["name"] != "rs000"])

    # No-op
    if num_slaves == current_regular:
        info(f"Pool já tem {num_slaves} slaves regulares — nada a fazer")
        return

    is_scale_down = num_slaves < current_regular

    if is_scale_down:
        info(f"Removendo {current_regular - num_slaves} slaves regulares...")
    else:
        info(f"Adicionando {num_slaves - current_regular} slaves regulares...")

    # 1. Generate docker-compose.pool.yaml
    info(f"Gerando {COMPOSE_FILE}...")
    compose_content = generate_docker_compose(config, num_slaves)
    if dry_run:
        info(f"[dry-run] escreveria {COMPOSE_FILE}")
    else:
        compose_path = BASE_DIR / COMPOSE_FILE
        with compose_path.open("w", encoding="utf-8") as f:
            f.write(compose_content)
        success(f"{COMPOSE_FILE} atualizado ({num_slaves} slaves)")

    # 2. Generate .env
    info(f"Gerando {ENV_FILE}...")
    env_content = generate_env(num_slaves)
    if dry_run:
        info(f"[dry-run] escreveria {ENV_FILE}")
    else:
        env_path = BASE_DIR / ENV_FILE
        with env_path.open("w", encoding="utf-8") as f:
            f.write(env_content)
        success(f"{ENV_FILE} atualizado ({num_slaves} slaves)")

    # 3. Update config.json
    info("Atualizando config.json...")
    config = update_config_for_scale(config, num_slaves)
    if dry_run:
        info("[dry-run] escreveria config.json")
    else:
        save_config(config)
        success(f"config.json atualizado ({num_slaves} slaves)")

    # 5. Run docker compose (com --remove-orphans para limpar containers antigos)
    if not dry_run:
        info("Executando docker compose up -d --remove-orphans...")
        result = subprocess.run(
            ["docker", "compose", *COMPOSE_ARGS, "up", "-d", "--remove-orphans"],
            cwd=BASE_DIR,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            success("docker compose up -d --remove-orphans concluído")
        else:
            error(f"docker compose up -d --remove-orphans falhou:")
            if result.stdout:
                info(result.stdout.strip())
            if result.stderr:
                error(result.stderr.strip())
            return

    # 6. Remover volumes órfãos (escala para baixo - apenas slaves regulares, rs000 é permanente)
    if is_scale_down and not dry_run:
        info("Removendo volumes órfãos (slaves regulares apenas)...")
        # current_regular = old regular slaves count, num_slaves = new regular slaves count
        # Remove volumes for regular slaves being removed: rs{num_slaves+1} to rs{current_regular}
        for i in range(num_slaves + 1, current_regular + 1):
            slave_num = f"{i:03d}"
            vol_name = f"9router-pool_9router-slave-{slave_num}-data"
            result = subprocess.run(
                ["docker", "volume", "rm", vol_name],
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                info(f"volume '{vol_name}' removido")
            else:
                warning(f"volume '{vol_name}' não encontrado ou em uso")

    # 7. Sync to master (skip on scale-down — slaves removed, not added)
    if not no_sync and not dry_run and not is_scale_down:
        info("Aguardando containers ficarem healthy...")
        expected = num_slaves + 2  # master + rs000 + regular slaves
        for attempt in range(30):
            time.sleep(3)
            check = subprocess.run(
                ["docker", "compose", *COMPOSE_ARGS, "ps", "--format", "json"],
                cwd=BASE_DIR,
                capture_output=True,
                text=True,
            )
            if check.returncode == 0:
                healthy = 0
                for line in check.stdout.strip().split("\n"):
                    if not line:
                        continue
                    data = json.loads(line)
                    state = data.get("State", "")
                    health = data.get("Health", "")
                    if state == "running" and health == "healthy":
                        healthy += 1
                if healthy >= expected:
                    break
                info(f"Healthy: {healthy}/{expected}... ({attempt + 1}/30)")
        else:
            warning("Containers podem não estar totalmente prontos")
        info("Sincronizando com master...")
        sync_pool(config, dry_run=False)

    print()
    if is_scale_down:
        success(f"Pool reduzido para {num_slaves} slaves regulares (+ rs000 permanente)")
    else:
        success(f"Pool escalado para {num_slaves} slaves regulares (+ rs000 permanente)!")
    info(f"Portas: 20228 (master), 20129 (rs000), 20130-{20129 + num_slaves} (slaves regulares)")


def slave_add(
    config: dict[str, Any],
    name: str,
    host: str,
    docker_host: str = "",
    no_sync: bool = False,
) -> None:

    # rs000 is permanent and cannot be added manually
    if name == "rs000":
        error("Slave 'rs000' é permanente e não pode ser adicionado manualmente")
        return

    for slave in config["slaves"]:
        if slave["name"] == name:
            error(f"Slave '{name}' já existe")
            return

    config["slaves"].append({
        "name": name,
        "host": host,
        "docker_host": docker_host,
        "password": config["defaults"].get(
            "password", "123456"
        ),
    })

    save_config(config)

    success(f"Slave '{name}' adicionado → {host} (docker: {docker_host or host})")

    if no_sync:
        info("Pulando sincronização no master (--no-sync)")
        info("Execute 'pool.py sync' depois de criar o container do slave")
        return

    # ------------------------------------------------------------------------
    # Sync incremental: configura o slave + adiciona ao master
    # ------------------------------------------------------------------------
    title(f"SYNC INCREMENTAL — Adicionando {name} ao master")

    defaults = config["defaults"]
    master = master_instance(config)

    # 1. Configurar o slave (combos, API key)
    slave_instance = next((s for s in slave_instances(config) if s.name == name), None)
    if not slave_instance:
        error(f"Slave '{name}' não encontrado no config atualizado")
        return

    api_key = configure_slave(slave_instance, defaults)
    if not api_key:
        error(f"configure_slave falhou para {name}")
        return

    # 2. Login no master
    master_client = RouterClient(master)
    if not master_client.login():
        error("Login no master falhou")
        return

    success("Master: login OK")

    # 3. Adicionar ao master (node + connection + models + test)
    try:
        _add_slave_to_master(master_client, slave_instance, api_key, defaults)
        success(f"Slave '{name}' sincronizado com o master com sucesso!")
    except Exception as exc:
        error(f"Falha ao adicionar '{name}' ao master: {exc}")


def slave_delete(
    config: dict[str, Any],
    name: str,
    dry_run: bool = False,
) -> None:

    # rs000 is permanent and cannot be deleted
    if name == "rs000":
        error("Slave 'rs000' é permanente e não pode ser removido")
        return

    found = False

    for i, slave in enumerate(config["slaves"]):
        if slave["name"] == name:
            found = True

            if dry_run:
                info(
                    f"[dry-run] removeria slave '{name}'"
                )
            else:
                config["slaves"].pop(i)
                save_config(config)
                success(f"Slave '{name}' removido")

            break

    if not found:
        error(f"Slave '{name}' não encontrado")


def backup_pool(
    config: dict[str, Any],
    output: str | None = None,
) -> None:

    master = master_instance(config)

    title(f"BACKUP {master.host}")

    client = RouterClient(master)

    if not client.login():
        error("login falhou")
        return

    backup_data = client.get_backup()

    if output:
        output_path = Path(output)
    else:
        timestamp = __import__("datetime").datetime.now().strftime(
            "%Y%m%d-%H%M%S"
        )
        output_path = BASE_DIR / f"backup-{timestamp}.json"

    with output_path.open("w", encoding="utf-8") as f:
        json.dump(backup_data, f, indent=2, ensure_ascii=False)
        f.write("\n")

    success(f"Backup salvo em: {output_path}")
    info(f"Provider nodes: {len(backup_data.get('providerNodes', []))}")
    info(f"Provider connections: {len(backup_data.get('providerConnections', []))}")
    info(f"Combos: {len(backup_data.get('combos', []))}")
    info(f"Custom models: {len(backup_data.get('customModels', []))}")


def clean_pool(
    config: dict[str, Any],
    dry_run: bool = False,
) -> None:
    """Limpa tudo: containers, dados, configs.
    
    rs000 é permanente - mantém o container e volume do rs000.
    """
    title("CLEAN — Remover tudo (exceto rs000 permanente)")

    master = master_instance(config)
    slaves = slave_instances(config)
    # Separate rs000 from regular slaves
    rs000_slave = next((s for s in slaves if s.name == "rs000"), None)
    regular_slaves = [s for s in slaves if s.name != "rs000"]
    total = len(regular_slaves) + 1  # regular slaves + master (rs000 is kept)

    info(f"Containers para remover: {total}")
    info(f"  - sbx-9router-master")
    for slave in regular_slaves:
        info(f"  - sbx-{slave.name}")
    if rs000_slave:
        info(f"  - sbx-{rs000_slave.name} (MANTIDO - permanente)")

    if dry_run:
        info("[dry-run] nada será removido")
        return

    # 1. Docker compose down (com -v remove volumes) - mas mantém rs000
    info("Parando containers e removendo volumes (exceto rs000)...")
    # Stop and remove only master and regular slaves
    services_to_remove = ["9router-master"] + [f"9router-slave-{s.name.replace('rs', '').zfill(3)}" for s in regular_slaves]
    for service in services_to_remove:
        subprocess.run(
            ["docker", "compose", *COMPOSE_ARGS, "stop", service],
            cwd=BASE_DIR, capture_output=True,
        )
        subprocess.run(
            ["docker", "compose", *COMPOSE_ARGS, "rm", "-f", service],
            cwd=BASE_DIR, capture_output=True,
        )
    # Remove volumes for regular slaves only
    for slave in regular_slaves:
        slave_num = slave.name.replace("rs", "").zfill(3)
        vol_name = f"9router-pool_9router-slave-{slave_num}-data"
        subprocess.run(
            ["docker", "volume", "rm", vol_name],
            capture_output=True,
        )
    success("containers e volumes removidos (rs000 mantido)")

    # 2. Reset config.json para rs000 + 2 slaves regulares (mínimo)
    info("Resetando config.json para rs000 + 2 slaves regulares...")
    config["slaves"] = [
        {
            "name": "rs000",
            "host": "localhost:20129",
            "docker_host": "9router-slave-000:20129",
            "password": "123456",
        },
        {
            "name": "rs001",
            "host": "localhost:20130",
            "docker_host": "9router-slave-001:20130",
            "password": "123456",
        },
        {
            "name": "rs002",
            "host": "localhost:20131",
            "docker_host": "9router-slave-002:20131",
            "password": "123456",
        },
    ]
    save_config(config)
    success("config.json resetado (rs000 + 2 slaves regulares)")

    # 3. Gerar compose e env do pool para 2 slaves regulares (+ rs000)
    info(f"Gerando {COMPOSE_FILE} para rs000 + 2 slaves regulares...")
    compose_content = generate_docker_compose(config, 2)
    compose_path = BASE_DIR / COMPOSE_FILE
    with compose_path.open("w", encoding="utf-8") as f:
        f.write(compose_content)
    success(f"{COMPOSE_FILE} atualizado")

    info(f"Gerando {ENV_FILE} para rs000 + 2 slaves regulares...")
    env_content = generate_env(2)
    env_path = BASE_DIR / ENV_FILE
    with env_path.open("w", encoding="utf-8") as f:
        f.write(env_content)
    success(f"{ENV_FILE} atualizado")

    print()
    success("Pool limpo! rs000 permanente mantido, demais removidos.")


def create_pool(
    config: dict[str, Any],
    dry_run: bool = False,
) -> None:
    """Cria pool básico: 1 master + rs000 permanente + 2 slaves regulares."""

    title("CREATE — Criar pool básico (1 master + rs000 + 2 slaves regulares)")

    if dry_run:
        info("[dry-run] nada será criado")
        return

    master = master_instance(config)

    # 1. Reset config.json para rs000 + 2 slaves regulares (mínimo)
    info("Resetando config.json para rs000 + 2 slaves regulares...")
    config["slaves"] = [
        {
            "name": "rs000",
            "host": "localhost:20129",
            "docker_host": "9router-slave-000:20129",
            "password": "123456",
        },
        {
            "name": "rs001",
            "host": "localhost:20130",
            "docker_host": "9router-slave-001:20130",
            "password": "123456",
        },
        {
            "name": "rs002",
            "host": "localhost:20131",
            "docker_host": "9router-slave-002:20131",
            "password": "123456",
        },
    ]
    save_config(config)
    success("config.json resetado (rs000 + 2 slaves regulares)")

    # 2. Gerar compose e env do pool
    info(f"Gerando {COMPOSE_FILE}...")
    compose_content = generate_docker_compose(config, 2)
    compose_path = BASE_DIR / COMPOSE_FILE
    with compose_path.open("w", encoding="utf-8") as f:
        f.write(compose_content)
    success("docker-compose.yaml atualizado (rs000 + 2 slaves regulares)")

    info(f"Gerando {ENV_FILE}...")
    env_content = generate_env(2)
    env_path = BASE_DIR / ENV_FILE
    with env_path.open("w", encoding="utf-8") as f:
        f.write(env_content)
    success(f"{ENV_FILE} atualizado")

    # 3. Docker compose up
    info("Iniciando containers...")
    result = subprocess.run(
        ["docker", "compose", *COMPOSE_ARGS, "up", "-d", "--remove-orphans"],
        cwd=BASE_DIR,
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        success("containers iniciados")
    else:
        error(f"docker compose up falhou: {result.stderr}")
        return

    # 4. Aguardar containers ficarem healthy
    time.sleep(5)
    info("Aguardando containers ficarem healthy...")
    for attempt in range(30):
        time.sleep(3)
        check = subprocess.run(
            ["docker", "compose", *COMPOSE_ARGS, "ps", "--format", "json"],
            cwd=BASE_DIR,
            capture_output=True,
            text=True,
        )
        if check.returncode == 0:
            healthy = 0
            for line in check.stdout.strip().split("\n"):
                if not line:
                    continue
                data = json.loads(line)
                state = data.get("State", "")
                health = data.get("Health", "")
                if state == "running" and health == "healthy":
                    healthy += 1
            if healthy >= 4:  # master + rs000 + 2 slaves regulares
                break
            info(f"Healthy: {healthy}/4... ({attempt + 1}/30)")
    else:
        warning("Containers podem não estar totalmente prontos")

    # 5. Health check no master via API (double-check)
    wait_for_instance(master, max_attempts=10, delay=3)

    # 6. Buscar modelos opencode free e atualizar config
    models = fetch_opencode_free_models(
        master_host=master.host,
        master_password=master.password,
    )
    update_config_with_models(config, models)

    # 7. Sync
    info("Sincronizando com master...")
    sync_pool(config, dry_run=False)

    print()
    success("Pool criado! 1 master + rs000 permanente + 2 slaves regulares")
    info("Portas: 20228 (master), 20129 (rs000), 20130 (rs001), 20131 (rs002)")
