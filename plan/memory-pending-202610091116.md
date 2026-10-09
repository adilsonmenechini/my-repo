# MEMORY PENDING 202610091116

O serviço/ ferramenta de escrita do AI Memory (`memory_write_page`) **não estava
disponível** nesta sessão (nenhum servidor MCP de memória registrado). Conforme o
CLAUDE.md §8 ("Fallback if AI Memory is unavailable"), o que seria gravado lá fica aqui
para ser sincronizado quando o serviço voltar.

Conteúdo a sincronizar (cópia integral em `plan/tasks/lessons-202610091116.md`):

1. **Renomear o diretório de um projeto Docker orfa volumes em silêncio.** O nome do projeto
   do Compose é o nome do diretório quando não há `name:` no arquivo; trocar de diretório
   faz o compose criar um conjunto novo de volumes e abandonar os que têm dados. Fixar com
   `name:` (ou `-p`) antes de mover, e conferir com `docker compose config` que o nome
   resolvido continua o antigo. Atenção a prefixos de volume hardcoded no código.
2. **`.gitignore` com `.env` exato não cobre `.env.<sufixo>`.** Ao renomear arquivo de
   segredo para nome com sufixo, atualizar o `.gitignore` na mesma mudança e confirmar com
   `git check-ignore -v <arquivo>`.

Deletar este arquivo após sincronizar.
