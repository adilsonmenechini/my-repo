"""Configuration management."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from .console import error

BASE_DIR = Path(__file__).resolve().parent.parent.parent
CONFIG_FILE = BASE_DIR / "config.json"


def load_config() -> dict[str, Any]:
    if not CONFIG_FILE.exists():
        error(f"Arquivo não encontrado: {CONFIG_FILE}")
        sys.exit(1)

    try:
        with CONFIG_FILE.open("r", encoding="utf-8") as f:
            config = json.load(f)
    except json.JSONDecodeError as exc:
        error(f"config.json inválido: {exc}")
        sys.exit(1)

    validate_config(config)
    return config


def save_config(config: dict[str, Any]) -> None:
    temp_file = CONFIG_FILE.with_suffix(".tmp")

    with temp_file.open("w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)
        f.write("\n")

    temp_file.replace(CONFIG_FILE)


def validate_config(config: dict[str, Any]) -> None:
    if "master" not in config:
        raise RuntimeError("config.json precisa possuir 'master'")

    if "defaults" not in config:
        raise RuntimeError("config.json precisa possuir 'defaults'")

    if "slaves" not in config:
        raise RuntimeError("config.json precisa possuir 'slaves'")

    defaults = config["defaults"]

    if "models" not in defaults:
        defaults["models"] = []

    if "combos" not in defaults:
        defaults["combos"] = []

    if "publish" not in defaults:
        defaults["publish"] = []

    if not isinstance(config["slaves"], list):
        raise RuntimeError("'slaves' precisa ser uma lista")
