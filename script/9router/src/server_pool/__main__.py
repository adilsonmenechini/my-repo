"""9Router Pool Manager - Main entry point."""

from __future__ import annotations

import argparse
import sys

from .commands import (
    backup_pool,
    clean_pool,
    create_pool,
    list_pool,
    scale_pool,
    slave_add,
    slave_delete,
    sync_pool,
    test_pool,
)
from .config import load_config
from .watch import DEFAULT_MODEL_INTERVAL, watch_pool


def main() -> None:

    parser = argparse.ArgumentParser(
        description="9Router Pool Manager"
    )

    subparsers = parser.add_subparsers(
        dest="command",
        help="Comandos disponíveis",
    )

    # list
    subparsers.add_parser(
        "list",
        help="Listar configuração do pool",
    )

    # create
    create_parser = subparsers.add_parser(
        "create",
        help="Criar pool básico: 1 master + 1 slave, sobe e sincroniza",
    )
    create_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simular sem alterar nada",
    )

    # test
    subparsers.add_parser(
        "test",
        help="Testar conectividade com todas as instâncias",
    )

    # sync
    sync_parser = subparsers.add_parser(
        "sync",
        help="Sincronizar configuração dos slaves ao master",
    )

    sync_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simular sem alterar nada",
    )

    sync_parser.add_argument(
        "--health-check",
        action="store_true",
        help="Executar health check dos providers após sync (desativado por padrão - 9Router test endpoint não usa API key armazenada)",
    )

    # slave add
    slave_add_parser = subparsers.add_parser(
        "slave add",
        help="Adicionar um novo slave",
    )

    slave_add_parser.add_argument(
        "name",
        help="Nome do slave (ex: rs004)",
    )

    slave_add_parser.add_argument(
        "host",
        help="Host do slave para pool.py (ex: localhost:20132)",
    )

    slave_add_parser.add_argument(
        "--docker-host",
        help="Host Docker para o master (ex: 9router-slave-004:20132)",
        default="",
    )

    # slave delete
    slave_delete_parser = subparsers.add_parser(
        "slave delete",
        help="Remover um slave",
    )

    slave_delete_parser.add_argument(
        "name",
        help="Nome do slave",
    )

    slave_delete_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simular sem alterar nada",
    )

    # backup
    backup_parser = subparsers.add_parser(
        "backup",
        help="Fazer backup da configuração do master",
    )

    backup_parser.add_argument(
        "--output",
        help="Arquivo de saída (padrão: backup-YYYYMMDD-HHMMSS.json)",
    )

    # scale
    scale_parser = subparsers.add_parser(
        "scale",
        help="Escalar pool para N slaves",
    )
    scale_parser.add_argument(
        "num_slaves",
        type=int,
        help="Número de slaves (ex: 6)",
    )
    scale_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simular sem alterar nada",
    )
    scale_parser.add_argument(
        "--no-sync",
        action="store_true",
        help="Não executar sync após criar slaves",
    )

    # clean
    clean_parser = subparsers.add_parser(
        "clean",
        help="Limpar tudo: containers, dados, configs",
    )
    clean_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simular sem alterar nada",
    )

    # watch
    watch_parser = subparsers.add_parser(
        "watch",
        help="Monitorar saúde dos slaves e substituir automaticamente os com falha",
    )
    watch_parser.add_argument(
        "--interval",
        type=int,
        default=180,
        help="Segundos entre verificações (padrão: 180)",
    )
    watch_parser.add_argument(
        "--failures",
        type=int,
        default=2,
        help="Falhas consecutivas antes de substituir o slave (padrão: 2)",
    )
    watch_parser.add_argument(
        "--fetch-interval",
        type=int,
        default=600,
        help="Segundos entre auto-discovery de modelos (padrão: 600 / 6h, 0 para desativar)",
    )
    watch_parser.add_argument(
        "--model-interval",
        type=int,
        default=DEFAULT_MODEL_INTERVAL,
        help=(
            "Segundos entre revalidação dos modelos em uso "
            f"(padrão: {DEFAULT_MODEL_INTERVAL} / "
            f"{DEFAULT_MODEL_INTERVAL // 60}min, 0 para desativar)"
        ),
    )

    # diagnose
    diagnose_parser = subparsers.add_parser(
        "diagnose",
        help="Diagnóstico em camadas: identifica se problema é no slave, provider, combo ou master",
    )
    diagnose_parser.add_argument(
        "--slave",
        help="Diagnosticar um slave específico (ex: rs000)",
        default=None,
    )

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    config = load_config()

    if args.command == "list":
        list_pool(config)

    elif args.command == "create":
        create_pool(config, dry_run=args.dry_run)

    elif args.command == "test":
        test_pool(config)

    elif args.command == "sync":
        sync_pool(
            config,
            dry_run=args.dry_run,
            skip_health_check=not args.health_check,
        )

    elif args.command == "slave add":
        slave_add(config, args.name, args.host, docker_host=args.docker_host)

    elif args.command == "slave delete":
        slave_delete(
            config,
            args.name,
            dry_run=args.dry_run,
        )

    elif args.command == "backup":
        backup_pool(config, output=args.output)

    elif args.command == "scale":
        scale_pool(
            config,
            args.num_slaves,
            dry_run=args.dry_run,
            no_sync=args.no_sync,
        )

    elif args.command == "clean":
        clean_pool(
            config,
            dry_run=args.dry_run,
        )

    elif args.command == "watch":
        watch_pool(
            config,
            interval=args.interval,
            max_failures=args.failures,
            fetch_interval=args.fetch_interval,
            model_interval=args.model_interval,
        )

    elif args.command == "diagnose":
        from .diagnose import diagnose_all, diagnose_slave, print_diagnosis
        from .models import master_instance, slave_instances, RouterClient

        master = master_instance(config)
        master_client = RouterClient(master)
        if master_client.login():
            if args.slave:
                slaves = slave_instances(config)
                slave = next((s for s in slaves if s.name == args.slave), None)
                if not slave:
                    error(f"Slave '{args.slave}' não encontrado")
                    sys.exit(1)
                results = diagnose_slave(slave, config["defaults"], master_client)
                print_diagnosis(slave.name, results)
            else:
                diagnose_all(config, master_client)
        else:
            error("Login no master falhou — diagnóstico limitado")
            if args.slave:
                from .diagnose import diagnose_slave, print_diagnosis
                from .models import slave_instances
                slaves = slave_instances(config)
                slave = next((s for s in slaves if s.name == args.slave), None)
                if slave:
                    results = diagnose_slave(slave, config["defaults"])
                    print_diagnosis(slave.name, results)
            else:
                from .diagnose import diagnose_all
                diagnose_all(config)


