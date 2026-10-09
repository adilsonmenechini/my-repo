# Design: roteamento de upstream + catálogo no gateway (referência: ollama/internal/proxy)

Data: 2026-10-08 · Status: aprovado em revisão (seções 1–3) · Próximo passo: plano de implementação

## Contexto

O projeto é um proxy local: **Claude Code / Claude Desktop → gateway Go (`127.0.0.1:11435`) → conversor
Python (`127.0.0.1:8080`) → 9Router (`127.0.0.1:20338/v1`, fala API OpenAI)**. A conversão
Anthropic⇄OpenAI fica no Python; o gateway é o shell loopback-only (adaptado de
`ollama/ollama internal/proxy/claude_desktop.go`, MIT).

Evidências coletadas antes deste design:

- O 9Router expõe **75 modelos**; só 3 são raiz (`claude-sonnet-5`, `claude-opus-5`,
  `claude-haiku-5-5`), os demais são compostos (`master/…`, `sec/…`, `kc/…`, `gemini/…`).
- O conversor reescreve qualquer modelo por família (`resolve_upstream_model`):
  `claude-haiku-5-5` → `claude-haiku-4-5-20251001` → **404 `model_not_found` no 9Router**
  (sonda `max_tokens=1`); `gpt-4o` → `claude-sonnet-5` (silencioso). IDs Anthropic reais
  (`claude-sonnet-4-5-20250929`) também não existem no 9Router.
- `upstreams.yaml` + `LoadUpstreamsConfig` + `FindUpstreamForModel` existem mas **não estão
  ligados** ao `cmd/gateway/main.go` (código morto em runtime).
- O `.env` não tem `NINEROUTER_API_KEY` / `_2` / `OPENAI_API_KEY` — as `${...}` do YAML
  expandiriam para vazio. `TARGET_MODELS` no `.env` não é lido por ninguém.
- Só existe **um** upstream real (9Router em `:20338`).

## Objetivo

O gateway Go assume **roteamento de upstream e catálogo de modelos** (como o
`routeModel`/`serveModels` do Ollama), com o requisito central do usuário:

> **O `model` que o cliente/env manda chega ao upstream intocado.** Única reescrita
> permitida: injetar `default_model` quando o request não trouxer `model`.

## Não-objetivos

- Mover a conversão Anthropic⇄OpenAI para Go.
- Refresh dinâmico de catálogo a partir do 9Router (a "abordagem 2", deixada de fora).
- `timeout` por upstream (o YAML aceita, o código continua com os 120s fixos do conversor).
- Refactor do `gateway.go` em múltiplos arquivos (estilo Ollama).
- Tabela de `aliases` por família ou qualquer reescrita por adivinhação.

## Decisões aprovadas

1. **Header de roteamento** gateway → conversor (não uma instância de conversor por upstream,
   não o conversor resolvendo por nome). Fonte única da config: `upstreams.yaml` no Go.
2. **`upstreams.yaml` é opcional**: ausente ⇒ gateway sintetiza um upstream
   (`name: "env"`, `models: [default_model]`) a partir de `UPSTREAM_BASE_URL` +
   `GATEWAY_UPSTREAM_API_KEY` (roteamento equivalente ao atual).
3. **Precedência uniforme: env > YAML > hardcoded** para `listen_addr`, `default_model`
   e (sem YAML) `base_url`/`api_key`.
4. **O conversor vira tradutor puro de protocolo**: `model` do body vai intacto;
   `resolve_upstream_model`, `MODEL_ALIAS_MAP` e seus dois testes são **removidos**.
5. **Catálogo curado pelo YAML** (união dos `models`), no mesmo shape do conversor hoje;
   os 75 modelos do 9Router deixam de ser listados.
6. **Segurança do header novo**: conversor valida scheme (`http`/`https`), **rejeita `Origin` com 403**
   e perde o middleware CORS `allow_origins=["*"]` (o gateway já rejeita `Origin`).
7. **Falha cedo**: `LoadUpstreamsConfig` retorna erro se o **default** upstream ficar sem
   `api_key` após expansão; upstreams não-default sem chave geram warning no load.

## Arquitetura e fluxo

```
Claude Code / Claude Desktop
  POST /v1/messages {"model":"sec/gemini/gemini-3.8-flash",…}   Authorization: <placeholder>
    │
    ▼ gateway (127.0.0.1:11435)
 1. allowsHost / Origin→403 / limite de 64 MiB        (inalterado)
 2. model ausente ou vazio ⇒ injeta default_model      (única reescrita)
 3. upstream = FindUpstreamForModel(model)  (membership, senão default)
 4. Del Authorization, Cookie, X-Api-Key, Proxy-Authorization
    Set Authorization: Bearer <api_key do upstream escolhido>
    Set X-9Router-Upstream-Base: <base_url>
    Set X-9Router-Upstream: <nome>                     (log no conversor)
 5. reverse proxy → conversor                          (inalterado: FlushInterval=-1, retry de imagens)
    │
    ▼ conversor (127.0.0.1:8080)
 6. base = X-9Router-Upstream-Base || env UPSTREAM_BASE_URL (validado: http/https)
 7. model do body vai INTEIRO pro upstream
 8. POST {base}/chat/completions com Authorization herdada
 9. resposta/stream SSE → Anthropic
```

`GET /v1/models` passa a ser atendido **pelo gateway** (deixa de ser forward ao conversor);
`GET /health` continua em forward ao conversor. `/v1/messages/count_tokens` aplica a **mesma
regra de injeção** de `default_model` e continua estimando tokens localmente, sem validar o
modelo contra o catálogo. `/_gateway/health`, readiness/TTL, retry de imagens, `allowsHost` e o
strip de credenciais permanecem como estão.

## Contrato gateway → conversor

| Header | Sem o header (modo direto) |
|---|---|
| `X-9Router-Upstream-Base: <url>` | conversor usa `UPSTREAM_BASE_URL` do env |
| `Authorization: Bearer <api_key>` | conversor repassa o Authorization que vier (como hoje) |
| `X-9Router-Upstream: <nome>` | só usado em log |

Propriedade de segurança mantida: o conversor monta os headers do upstream do zero
(`Content-Type` + `Authorization`) — os `X-9Router-*` **nunca** seguem adiante.

## Config

### `upstreams.yaml`

```yaml
upstreams:
  - name: "9router-primary"
    base_url: "http://127.0.0.1:20338/v1"
    api_key: "${GATEWAY_UPSTREAM_API_KEY}"      # era ${NINEROUTER_API_KEY} (inexistente no .env)
    models: [claude-sonnet-5, claude-haiku-5-5, claude-opus-5]
    default: true
    timeout: 120                                # aceito, não implementado nesta rodada

  # - name: "9router-secondary"                 # exemplo documentado, comentado
  #   base_url: "http://127.0.0.1:20339/v1"
  #   api_key: "${NINEROUTER_API_KEY_2}"        # crie a env só se usar
  #   models: [gpt-4o, gpt-4o-mini]

default_model: "claude-sonnet-5"
gateway:
  listen_addr: "127.0.0.1:11435"   # GATEWAY_LISTEN_ADDR vence
  health_path: "/_gateway/health"
  max_request_body_mb: 64
```

- Caminho do arquivo: env `UPSTREAMS_CONFIG` (default `upstreams.yaml`).
  Caminho padrão ausente ⇒ modo sintetizado do env; caminho **explícito** ausente ⇒ erro.

### `.env` / `.env.example` / README

| Var | Decisão |
|---|---|
| `GATEWAY_UPSTREAM_API_KEY`, `UPSTREAM_BASE_URL`, `CONVERTER_URL`, `GATEWAY_LISTEN_ADDR`, `DEFAULT_MODEL` | mantidas |
| `TARGET_MODELS` | removida do `.env.example` (e do `.env` local, manualmente) — morta |
| `NINEROUTER_API_KEY*` | saem do YAML; vira comentário no `.env.example` |
| README | tabela de variáveis atualizada + nota de multi-upstream (5 linhas) |

## Mudanças no conversor (`converter/`)

1. `base_url = request.headers.get("X-9Router-Upstream-Base") or UPSTREAM_BASE_URL`,
   com validação de scheme e rejeição de `Origin` (403). Erros novos em formato Anthropic
   (`{"type":"error","error":{…}}`).
2. `requested_model = body.get("model") or DEFAULT_MODEL` — sem `resolve_upstream_model`.
3. Remover `resolve_upstream_model` (`converter.py:147` é o único call site),
   `MODEL_ALIAS_MAP` e os testes `test_identity_alias_map` / `test_resolve_upstream_model`.
4. `GET /v1/models` do conversor permanece (modo direto e testes próprios); o gateway para de repassar.

## Matriz de erro

| Situação | Comportamento |
|---|---|
| `model` ausente/vazio | injeta `default_model` e segue |
| YAML inválido ou default upstream sem chave | gateway não sobe; `make up` falha no health, **limpa os processos** e imprime os logs |
| conversor fora do ar | `502` + `markUpstreamNotReady` (já existe) |
| header `base` malformado | `400` em formato Anthropic |
| `Origin` no conversor | `403` |
| erro do 9Router (404/401/500) | repassado ao cliente como está |
| body > 64 MiB | `400` Anthropic-error (já existe) |

## Testes

**Go — config** (`config_test.go`, estende o existente): expansão de `api_key`; **erro** sem
chave no default; warning nos demais; precedência env > YAML (`t.Setenv`); modo sintetizado
sem YAML.

**Go — roteamento** (conversor espião via `httptest`):
- membership (`claude-haiku-5-5` → primary) e fallback (`sec/gemini/gemini-3.8-flash` → default);
- corpo encaminhado com `model` **intacto** para `gpt-4o`, `sec/…`, `claude-haiku-5-5`;
  apenas `""` vira `default_model`;
- `X-9Router-Upstream-Base` correto; `Authorization` = chave do upstream escolhido;
  credencial do cliente removida;
- `/v1/models`: shape atual, dedup, ordem do YAML, catálogo vazio ⇒ `[default_model]`;
- regressão: testes de host/Origin/health/imagem/count_tokens existentes continuam verdes.

**Python** (`test_converter.py`): base via header; header inválido → 400; `Origin` → 403;
`model` chega igual ao enviado; fallback para env quando não há header.

**E2E** (único upstream real): `make verify` verde; smoke manual:
`GET /v1/models` no gateway ⇒ 3 modelos · `claude-sonnet-5` ⇒ 200 ·
**`claude-haiku-5-5` ⇒ 200 (quebra hoje)** · `sec/gemini/gemini-3.8-flash` ⇒ chega intacto ·
`count_tokens` ⇒ 200.

## Limitações e riscos

- **ID inexistente vira 404 honesto.** Pass-through puro: se o Claude Desktop mandar
  `claude-sonnet-4-5-20250929`, o 9Router responde `model_not_found`. Hoje esse caso também
  falha (reescrito para um ID inexistente) — é troca de erro silencioso por erro honesto,
  não regressão. Escape, só se necessário: `aliases` exato-a-exato no YAML (nunca por família).
- **Catálista encolhe de ~78 para 3** — expondo modelos novos exige editar o YAML.
- `timeout` do YAML continua ignorado.
- `Origin` rejeitado no conversor quebra uso dele via browser (não é um caso de uso: é server-to-server).

## Ordem de implementação

1. `config.go`: validação de chave, precedência, modo sintetizado (+ testes).
2. Gateway: rota de upstream, headers, `serveModels` (+ testes com espião).
3. Conversor: header de base, rejeição de `Origin`, pass-through do model (+ testes).
4. `upstreams.yaml`, `.env.example`, README.
5. E2E + `make verify`.
