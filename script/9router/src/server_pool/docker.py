"""Docker compose and env file generation."""

from __future__ import annotations

from typing import Any

from .config import BASE_DIR
from .console import info, success, warning

# Arquivos da stack do pool dentro da pasta única `9router/` (junto com a stack
# federal). Todo `docker compose` precisa dos dois flags: sem `-f` o compose
# cai no default do diretório e lê o arquivo da outra stack; sem `--env-file`
# a interpolação de `${JWT_SECRET}` perde o valor real e vira o default do
# template. Os nomes também são os que `create`/`scale` gravam em disco.
COMPOSE_FILE = "docker-compose.pool.yaml"
ENV_FILE = ".env.pool"
COMPOSE_ARGS = ["-f", COMPOSE_FILE, "--env-file", ENV_FILE]


def generate_docker_compose(config: dict[str, Any], num_slaves: int) -> str:
    """Generate docker-compose.yaml with N slaves using named volumes.
    
    rs000 is a permanent slave that is always created alongside the master.
    Regular slaves start from rs001.
    """
    master_port = 20228
    base_port = 20129

    slave_services = []
    volumes_def = []
    
    # rs000 - Permanent slave (always present, port 20129)
    slave_num = "000"
    port = base_port  # 20129
    vol_name = f"9router-slave-{slave_num}-data"
    slave_services.append(f"""
  9router-slave-{slave_num}:
    <<: *service-slave
    build:
      context: .
      dockerfile: Dockerfile
    image: decolua/9router-custom
    container_name: sbx-9router-slave-{slave_num}
    pull_policy: missing
    ports:
      - "${{ROUTER_PORT_SLAVE_{slave_num}:-{port}}}:{port}"
    volumes:
      - {vol_name}:/app/data
    healthcheck:
      test: ["CMD", "wget", "-q", "-O", "/dev/null", "--timeout=5", "http://127.0.0.1:{port}/"]
      interval: 10s
      timeout: 10s
      retries: 10
      start_period: 30s
    environment:
      DATA_DIR: /app/data
      PORT: "${{ROUTER_PORT_SLAVE_{slave_num}:-{port}}}"
      HOSTNAME: 0.0.0.0
      ROLE: slave
      JWT_SECRET: "${{JWT_SECRET:-P4s5w0rd}}"
      #MACHINE_ID_SALT: $(openssl rand -hex 32)
      INITIAL_PASSWORD: "${{INITIAL_PASSWORD:-123456}}"
    extra_hosts:
      - host.docker.internal:host-gateway
    networks:
      - sbx-9router""")
    volumes_def.append(f"  {vol_name}:")

    # Regular slaves start from rs001 (num_slaves = number of regular slaves)
    for i in range(1, num_slaves + 1):
        slave_num = f"{i:03d}"
        port = base_port + i  # 20130, 20131, ...
        vol_name = f"9router-slave-{slave_num}-data"
        slave_services.append(f"""
  9router-slave-{slave_num}:
    <<: *service-slave
    build:
      context: .
      dockerfile: Dockerfile
    image: decolua/9router-custom
    container_name: sbx-9router-slave-{slave_num}
    pull_policy: missing
    ports:
      - "${{ROUTER_PORT_SLAVE_{slave_num}:-{port}}}:{port}"
    volumes:
      - {vol_name}:/app/data
    healthcheck:
      test: ["CMD", "wget", "-q", "-O", "/dev/null", "--timeout=5", "http://127.0.0.1:{port}/"]
      interval: 10s
      timeout: 10s
      retries: 10
      start_period: 30s
    environment:
      DATA_DIR: /app/data
      PORT: "${{ROUTER_PORT_SLAVE_{slave_num}:-{port}}}"
      HOSTNAME: 0.0.0.0
      ROLE: slave
      JWT_SECRET: "${{JWT_SECRET:-P4s5w0rd}}"
      MACHINE_ID_SALT: $(openssl rand -hex 32)
      INITIAL_PASSWORD: "${{INITIAL_PASSWORD:-123456}}"
    extra_hosts:
      - host.docker.internal:host-gateway
    networks:
      - sbx-9router""")
        volumes_def.append(f"  {vol_name}:")

    compose = f"""# Gerado por `pool create`/`pool scale` — edite o template em docker.py.
# Projeto fixo: o nome não pode derivar do diretório, senão o compose criaria
# um conjunto novo de volumes e abandonaria os que têm dados em /app/data.
name: 9router-pool

x-common: &common
  restart: unless-stopped
  stop_grace_period: 10s
  env_file: {ENV_FILE}
  logging:
    driver: json-file
    options:
      max-size: "20m"
      max-file: "3"

x-service-master: &service-master
  <<: *common
  deploy:
    resources:
      limits:
        memory: 512M
        cpus: "1.0"
      reservations:
        memory: 256M
        cpus: "0.25"

x-service-slave: &service-slave
  <<: *common
  deploy:
    resources:
      limits:
        memory: 256M
        cpus: "0.5"
      reservations:
        memory: 128M
        cpus: "0.125"

services:
  9router-master:
    <<: *service-master
    build:
      context: .
      dockerfile: Dockerfile
    image: decolua/9router-custom
    container_name: sbx-9router-master
    pull_policy: missing
    ports:
      - "${{ROUTER_PORT_MASTER:-{master_port}}}:{master_port}"
    volumes:
      - 9router-master-data:/app/data
    healthcheck:
      test: ["CMD", "wget", "-q", "-O", "/dev/null", "--timeout=5", "http://127.0.0.1:{master_port}/"]
      interval: 10s
      timeout: 10s
      retries: 10
      start_period: 30s
    environment:
      DATA_DIR: /app/data
      PORT: "${{ROUTER_PORT_MASTER:-{master_port}}}"
      MACHINE_ID_SALT: $(openssl rand -hex 32)
      HOSTNAME: 0.0.0.0
      ROLE: master
      JWT_SECRET: "${{JWT_SECRET:-P4s5w0rd}}"
      INITIAL_PASSWORD: "${{INITIAL_PASSWORD:-123456}}"
    networks:
      - sbx-9router
{"".join(slave_services)}

volumes:
  9router-master-data:
{chr(10).join(volumes_def)}

networks:
  sbx-9router:
    name: sbx-9router
    driver: bridge
"""
    return compose


def generate_env(num_slaves: int) -> str:
    """Generate .env file with port assignments.
    
    rs000 is always on port 20129.
    Regular slaves start from rs001 on port 20130.
    """
    base_port = 20129
    lines = [
        "# sbx — Providers Environment",
        "TZ=America/Sao_Paulo",
        "ROUTER_PORT_MASTER=20228",
    ]
    # rs000 - Permanent slave
    lines.append(f"ROUTER_PORT_SLAVE_000={base_port}")
    # Regular slaves start from rs001
    for i in range(1, num_slaves + 1):
        slave_num = f"{i:03d}"
        port = base_port + i  # 20130, 20131, ...
        lines.append(f"ROUTER_PORT_SLAVE_{slave_num}={port}")
    lines.extend([
        "ROUTER_DEBUG=false",
        "",
        "JWT_SECRET=N7ZndMLMYqJu8QLRtY+dE1VynAdESAJfc7maXp+lHqY=",
        "INITIAL_PASSWORD=123456",
    ])
    return "\n".join(lines) + "\n"


def update_config_for_scale(config: dict[str, Any], num_slaves: int) -> dict[str, Any]:
    """Update config.json with N slaves.
    
    rs000 is always present (permanent slave).
    Regular slaves start from rs001.
    num_slaves = number of regular slaves (rs001, rs002, ...)
    """
    base_port = 20129
    password = config["defaults"].get("password", "123456")
    slaves = []
    
    # rs000 - Permanent slave (always first, port 20129)
    slaves.append({
        "name": "rs000",
        "host": f"localhost:{base_port}",
        "docker_host": f"9router-slave-000:{base_port}",
        "password": password,
    })
    
    # Regular slaves start from rs001 (port 20130, 20131, ...)
    for i in range(1, num_slaves + 1):
        slave_num = f"{i:03d}"
        port = base_port + i  # 20130, 20131, ...
        slaves.append({
            "name": f"rs{slave_num}",
            "host": f"localhost:{port}",
            "docker_host": f"9router-slave-{slave_num}:{port}",
            "password": password,
        })
    config["slaves"] = slaves
    return config


def create_data_directories(num_slaves: int) -> None:
    """Create data directories for each slave."""
    base_dir = BASE_DIR / "data" / "9router" / "slave"

    for i in range(1, num_slaves + 1):
        slave_num = f"{i:03d}"
        slave_dir = base_dir / slave_num

        try:
            slave_dir.mkdir(parents=True, exist_ok=True)
            info(f"Diretório: {slave_dir}")
        except PermissionError:
            warning(f"Sem permissão para criar: {slave_dir}")
            info(f"Execute: sudo chown -R access:access {base_dir}")
        except Exception as exc:
            warning(f"Erro ao criar {slave_dir}: {exc}")


def remove_data_directories(from_slave: int, to_slave: int) -> None:
    """Remove data directories for slaves from_slave..to_slave."""
    import shutil
    base_dir = BASE_DIR / "data" / "9router" / "slave"

    for i in range(from_slave, to_slave + 1):
        slave_num = f"{i:03d}"
        slave_dir = base_dir / slave_num

        if not slave_dir.exists():
            continue

        try:
            shutil.rmtree(slave_dir)
            info(f"Diretório removido: {slave_dir}")
        except PermissionError:
            warning(f"Sem permissão para remover: {slave_dir}")
            info(f"Execute: sudo rm -rf {slave_dir}")
        except Exception as exc:
            warning(f"Erro ao remover {slave_dir}: {exc}")
