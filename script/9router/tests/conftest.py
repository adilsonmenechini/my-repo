"""Ajusta o sys.path para que `server_pool` e `helpers` sejam importáveis.

O pytest carrega este arquivo antes de importar os módulos de teste, então
os `import` no topo de cada teste funcionam sem repetir o caminho.
"""

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent

sys.path.insert(0, str(_HERE))            # tests/  -> `import helpers`
sys.path.insert(0, str(_HERE.parent / "src"))  # src/   -> `from server_pool...`


# Mantidos para tests/test_proxy_tor.py (ainda versionado): registra a opção
# e a marcação para que a coleta não avise marcador desconhecido. Fora do
# padrão, os testes de integração seguem dispensados.
def pytest_addoption(parser):
    parser.addoption(
        "--run-integration",
        action="store_true",
        default=False,
        help="run integration tests (needs tor + 9router up)",
    )


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "integration: tests needing live docker infra"
    )
