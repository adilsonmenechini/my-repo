"""Arquivos da stack: um compose e um env por stack, com projeto fixo.

A pasta virou uma só (`9router/`) com as duas stacks dentro — federal e pool.
Dois pontos passam a depender de contrato em vez de acaso:

1. O nome do projeto Docker não pode mais derivar do nome do diretório, senão
   o compose criaria um conjunto novo de volumes e abandonaria os que têm
   dados (`/app/data` de cada container). Por isso cada compose declara
   `name:` no topo.
2. As chamadas de `docker compose` no código não podem adivinhar o arquivo:
   elas referenciam `COMPOSE_ARGS`, que aponta para os arquivos da stack do
   pool. Sem `-f`/`--env-file` o compose cai no default do diretório e passa
   a ler o compose e o env da outra stack.

Os testes observam o contrato, não a implementação: constantes apontam para
arquivos que existem, e cada template declara o projeto e o env da própria
stack.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from server_pool import docker as docker_mod
from server_pool.config import BASE_DIR

# `generate_docker_compose` não lê o config — só o número de slaves importa.
CONFIG: dict = {}

ARQUIVOS_DA_STACK = (
    "docker-compose.federal.yaml",
    "docker-compose.pool.yaml",
    ".env.federal",
    ".env.pool",
)


def primeira_declaracao(texto: str) -> str:
    """Primeira linha que não é comentário nem vazia.

    O `name:` precisa ser a primeira declaração do arquivo: é ele que fixa o
    projeto Docker e, com ele, os volumes com dados.
    """
    for linha in texto.splitlines():
        linha = linha.strip()
        if linha and not linha.startswith("#"):
            return linha
    return ""


class ComposeArgsTest(unittest.TestCase):
    def test_compose_args_aponta_para_os_arquivos_da_stack(self) -> None:
        """Todo `docker compose` do pool precisa dos dois flags explícitos."""
        self.assertEqual(
            docker_mod.COMPOSE_ARGS,
            ["-f", "docker-compose.pool.yaml", "--env-file", ".env.pool"],
        )

    def test_arquivos_de_stack_existem_na_raiz(self) -> None:
        """As constantes não podem apontar para arquivo que não existe."""
        for nome in ARQUIVOS_DA_STACK:
            caminho = BASE_DIR / nome
            self.assertTrue(caminho.exists(), f"esperado: {caminho}")


class ProjetoFixoTest(unittest.TestCase):
    def test_compose_do_pool_fixa_o_nome_do_projeto(self) -> None:
        """`name:` é o que mantém os volumes `9router-pool_*` vivos."""
        conteudo = docker_mod.generate_docker_compose(CONFIG, 2)
        self.assertEqual(
            primeira_declaracao(conteudo),
            "name: 9router-pool",
            "compose gerado sem o nome fixo do projeto",
        )

    def test_compose_do_pool_no_disco_bate_com_o_template(self) -> None:
        """O arquivo versionado é gerado: editar só ele seria sobrescrito.

        `create`/`scale` regravam o compose a partir do template. Se os dois
        divergirem, o que está no disco não é o que sobe na próxima execução.
        """
        esperado = docker_mod.generate_docker_compose(CONFIG, 2)
        atual = (BASE_DIR / "docker-compose.pool.yaml").read_text(encoding="utf-8")
        self.assertEqual(atual, esperado)

    def test_compose_do_pool_usa_env_proprio(self) -> None:
        """O pool não pode injetar o env da stack federal nos seus containers."""
        conteudo = docker_mod.generate_docker_compose(CONFIG, 2)
        self.assertIn("env_file: .env.pool", conteudo)
        self.assertNotIn("env_file: .env.federal", conteudo)

    def test_compose_federal_fixa_nome_e_env(self) -> None:
        """Mesmo contrato na stack federal, que é escrita à mão."""
        texto = (BASE_DIR / "docker-compose.federal.yaml").read_text(encoding="utf-8")
        self.assertEqual(
            primeira_declaracao(texto),
            "name: 9router-federation",
            "compose federal sem o nome fixo do projeto",
        )
        self.assertIn("env_file: .env.federal", texto)
        self.assertNotIn("env_file: .env.pool", texto)


if __name__ == "__main__":
    unittest.main()
