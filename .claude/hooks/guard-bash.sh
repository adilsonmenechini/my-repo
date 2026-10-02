#!/usr/bin/env bash
# Hook PreToolUse (matcher: Bash). Lê o JSON do evento no stdin.
# Exit 2 bloqueia o comando e devolve o stderr ao agente.
input="$(cat)"

block() {
  echo "BLOQUEADO por guard-bash.sh: $1" >&2
  echo "Explique ao usuário o que precisava fazer e peça autorização explícita." >&2
  exit 2
}

# Force push
echo "$input" | grep -Eq 'git +push[^"]*( --force| -f( |"|$))' \
  && block "git push --force"

# Reescrita destrutiva de histórico/árvore
echo "$input" | grep -Eq 'git +reset +--hard' && block "git reset --hard"
echo "$input" | grep -Eq 'git +clean +-[a-z]*f' && block "git clean -f"

# Merge de PR (o merge é do usuário)
echo "$input" | grep -Eq 'gh +pr +merge' && block "gh pr merge (o merge é feito pelo usuário)"

# rm recursivo e forçado fora de /tmp
if echo "$input" | grep -Eq 'rm +-[a-zA-Z]*(rf|fr)'; then
  echo "$input" | grep -Eq 'rm +-[a-zA-Z]*(rf|fr) +/tmp/' \
    || block "rm -rf fora de /tmp"
fi

# Execução de script remoto
echo "$input" | grep -Eq '(curl|wget)[^|"]*\|[^"]*(sh|bash)' \
  && block "download executado direto no shell (curl | sh)"

# Leitura de arquivos de segredo via shell
echo "$input" | grep -Eq '(cat|less|more|head|tail|grep|source|cp|mv) +[^|;&"]*\.env([. ]|"|$)' \
  && ! echo "$input" | grep -Eq '\.env\.(example|sample|template)' \
  && block "acesso a arquivo .env"

exit 0
