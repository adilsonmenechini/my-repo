# 9Router Claude Proxy

Shell Go loopback-only (igual ao `internal/proxy/claude_desktop.go` do Ollama) na frente de um
conversor Anthropic ⇄ OpenAI, para usar **Claude Code** e **Claude Desktop** com o 9Router.
Sem Docker: dois processos locais gerenciados pelo Makefile.

```
Claude Code / Claude Desktop
   │  POST /v1/messages  (Anthropic)
   ▼
gateway Go  127.0.0.1:11435      ← strip de credenciais, readiness, count_tokens, retry de imagens
   ▼
converter Python  127.0.0.1:8080 ← Anthropic ⇄ OpenAI (stream SSE inclusa)
   ▼
9Router  127.0.0.1:20338/v1/chat/completions
```

## Subir

```bash
cp .env.example .env       # preencha GATEWAY_UPSTREAM_API_KEY
make up                    # venv + build + sobe converter e gateway
make status                # deve mostrar 204 / 200
make logs                  # acompanhar
make down                  # parar
```

Health: `curl -i http://127.0.0.1:11435/_gateway/health` → `204` + header `X-9Router-Claude-Gateway: 1`.

Pré-requisitos: Go (`~/.local/go` já instalado), Python 3.11+, e o 9Router rodando em `:20338`.

## Usar no Claude Code

```bash
export ANTHROPIC_BASE_URL=http://127.0.0.1:11435
export ANTHROPIC_MODEL=claude-sonnet-5   # ou o DEFAULT_MODEL do .env
unset ANTHROPIC_API_KEY                  # o gateway injeta a chave do .env
claude
```

Para valer em todo repositório, coloque as duas variáveis em `~/.claude/settings.json`
(`env`). O Claude Desktop aponta o gateway de terceiros para a mesma URL.

## Variáveis (.env)

| Variável | Default | Papel |
|---|---|---|
| `UPSTREAM_BASE_URL` | `http://127.0.0.1:20338/v1` | conversor → 9Router |
| `GATEWAY_UPSTREAM_API_KEY` | — (obrigatória) | injetada pelo gateway no upstream |
| `DEFAULT_MODEL` | `claude-sonnet-5` | modelo quando o cliente não envia `model` |
| `GATEWAY_LISTEN_ADDR` | `127.0.0.1:11435` | endereço do gateway |
| `CONVERTER_URL` | `http://127.0.0.1:8080` | conversor usado pelo gateway |
| `UPSTREAMS_CONFIG` | `upstreams.yaml` | caminho do YAML de upstreams (opcional) |

**Multi-upstream:** o gateway lê `upstreams.yaml` (fonte única: `base_url`, `api_key`,
`models` por upstream). Sem o arquivo ele monta um upstream só a partir de
`UPSTREAM_BASE_URL` + `GATEWAY_UPSTREAM_API_KEY`. O upstream `default` é obrigatório ter
chave; os demais podem ficar sem (aviso no log).

**Modelo pass-through:** o `model` que o Claude/env manda chega ao 9Router **intocado** —
o gateway só injeta `default_model` quando o request não traz `model`, e escolhe o upstream
pelo `models` declarado no YAML (sem match → upstream default). `sec/…`, `master/…` etc.
funcionam mesmo fora do catálogo `/v1/models`.

## Verificação

```bash
make verify    # gofmt + go vet + go test -race + testes do conversor
```

## Segurança

- Gateway escuta **só** em loopback (`127.0.0.1:11435`).
- `Host` não-loopback e qualquer `Origin` → `403` (sem CORS).
- `Authorization`/`Cookie`/`X-Api-Key` do cliente são descartados; só a chave do `.env` segue.
- `.env` está no `.gitignore` — nunca versionar.
