#!/usr/bin/env python3
"""
9Router Pool Manager (CRUD API version)

Estrutura:

    config.json
        ├── master
        ├── defaults
        │    ├── models
        │    ├── combos
        │    └── publish
        │
        └── slaves
             ├── rs001
             ├── rs002
             └── rs003

Operações:

    python pool.py list
    python pool.py test

    python pool.py sync
    python pool.py sync --dry-run

    python pool.py slave add rs004 localhost:20132
    python pool.py slave delete rs004

    python pool.py backup
    python pool.py backup --output master-backup.json

Arquitetura:

    Master
        ├── providerNodes (anthropic-compatible) ← apontam para slaves
        ├── providerConnections (usam node ID como provider type)
        ├── customModels (modelos customizados por node)
        ├── combos (modelos prefixados: rs001/modelo)
        └── settings (round-robin por provider)

    Slaves
        ├── providerConnections (upstream: openrouter, etc.)
        ├── proxyPools (Tor)
        ├── combos (modelos do defaults)
        └── customModels (modelos upstream)

Versão: 2.2 (Modular — src/server_pool/)
"""

import sys
from pathlib import Path

# Adicionar src/ ao path para imports
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from server_pool.__main__ import main

if __name__ == "__main__":
    main()
