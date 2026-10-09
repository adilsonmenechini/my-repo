"""9Router Pool Manager package."""

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
from .config import BASE_DIR, CONFIG_FILE, load_config, save_config, validate_config
from .console import C, error, info, success, title, warning
from .docker import (
    generate_docker_compose,
    generate_env,
    update_config_for_scale,
)
from .fetch import fetch_opencode_free_models, update_config_with_models
from .models import (
    Instance,
    RouterClient,
    master_instance,
    slave_instances,
    wait_for_instance,
)
from .sync import (
    hot_reload_master_models,
    quarantine_slave,
    replace_slave,
    unquarantine_slave,
)
from .watch import watch_pool

__all__ = [
    "backup_pool",
    "clean_pool",
    "create_pool",
    "list_pool",
    "scale_pool",
    "slave_add",
    "slave_delete",
    "sync_pool",
    "test_pool",
    "replace_slave",
    "quarantine_slave",
    "unquarantine_slave",
    "hot_reload_master_models",
    "watch_pool",
    "BASE_DIR",
    "CONFIG_FILE",
    "load_config",
    "save_config",
    "validate_config",
    "C",
    "error",
    "info",
    "success",
    "title",
    "warning",
    "generate_docker_compose",
    "generate_env",
    "update_config_for_scale",
    "fetch_opencode_free_models",
    "update_config_with_models",
    "Instance",
    "RouterClient",
    "master_instance",
    "slave_instances",
    "wait_for_instance",
]
