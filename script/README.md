Segue uma versão mais simples para salvar em `docs/ai-memory.md`, com opção de configuração para Claude Code ou OpenCode e servidor executado via Docker Compose.

ai-memory — Setup

# ai-memory — Setup

Configuração do ai-memory para Claude Code ou OpenCode, utilizando um servidor já executado via Docker Compose.

## 1. Configurar `.env`

```
AI_MEMORY_SERVER_URL=http://localhost:49374
AI_MEMORY_AUTH_TOKEN=seu-token
```

* `AI_MEMORY_SERVER_URL`: URL do servidor.

* `AI_MEMORY_AUTH_TOKEN`: token de autenticação configurado no servidor.

Não versionar o `.env`.

## 2. Instalar o cliente

Instale o binário nativo do ai-memory seguindo a documentação oficial:

[https://github.com/akitaonrails/ai-memory](https://github.com/akitaonrails/ai-memory)

Verifique a instalação:

```
ai-memory --help
```

## 3. Escolher o agente

Carregue as variáveis de ambiente:

```
set -a
source .env
set +a
```

### Opção A — Claude Code

```
ai-memory install-mcp \
  --client claude-code \
  --apply \
  --server-url "$AI_MEMORY_SERVER_URL" \
  --auth-token "$AI_MEMORY_AUTH_TOKEN"

ai-memory install-hooks \
  --agent claude-code \
  --apply \
  --server-url "$AI_MEMORY_SERVER_URL" \
  --auth-token "$AI_MEMORY_AUTH_TOKEN"
```

### Opção B — OpenCode

Consulte os clientes suportados pela versão instalada:

```
ai-memory install-mcp --help
ai-memory install-hooks --help
```

Utilize o identificador de cliente OpenCode aceito pela versão instalada para registrar o MCP e os hooks. Não presuma que `opencode` seja aceito sem confirmar a ajuda do comando.

## 4. Validar

Para Claude Code:

```
claude mcp list
```

Para OpenCode, abra o agente e confirme se o MCP está disponível.

## Referências

* Repositório: [https://github.com/akitaonrails/ai-memory](https://github.com/akitaonrails/ai-memory)

* Documentação: [https://github.com/akitaonrails/ai-memory/tree/main/docs](https://github.com/akitaonrails/ai-memory/tree/main/docs)

**Importante:** o servidor é gerenciado exclusivamente pelo Docker Compose. Este procedimento configura apenas o cliente, o MCP e os hooks.

