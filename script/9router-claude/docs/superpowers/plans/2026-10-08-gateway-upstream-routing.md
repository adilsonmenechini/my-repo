# Gateway Upstream Routing + Catálogo — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** O gateway Go passa a escolher o upstream por modelo e a servir o catálogo `/v1/models`, com o `model` do cliente chegando **intocado** ao 9Router (única reescrita: injetar `default_model` quando ausente), e o conversor Python vira tradutor puro de protocolo.

**Architecture:** Fonte única da config = `upstreams.yaml` (opcional; sem ele o gateway sintetiza um upstream do `.env`). O gateway injeta `X-9Router-Upstream-Base` / `X-9Router-Upstream` / `Authorization` (chave do upstream escolhido) e encaminha ao conversor por reverse proxy; o conversor lê a base do header (fallback env), rejeita `Origin` e não toca no `model`. Cadeia inalterada: Claude → gateway `127.0.0.1:11435` → conversor `127.0.0.1:8080` → 9Router `127.0.0.1:20338/v1`.

**Tech Stack:** Go 1.27 (stdlib + `gopkg.in/yaml.v3` já presente), Python 3.11+ (FastAPI/uvicorn/httpx na venv; testes puros stdlib), Makefile (`make verify` = gofmt + vet + `test -race` + test-py).

**Spec:** `docs/superpowers/specs/2026-10-08-gateway-upstream-routing-design.md` (o plano argumenta a partir dele; executores leem os dois)

## Global Constraints

- **Pass-through do modelo:** a única reescrita do `model` é injetar `default_model` quando o campo está ausente/vazio. Nunca reescrever por família (`haiku`/`opus`/`sonnet`), nunca fallback silencioso para outro modelo.
- **Precedência:** env > YAML > hardcoded para `GATEWAY_LISTEN_ADDR` e `DEFAULT_MODEL`. Com YAML presente, `base_url`/`api_key` vêm do YAML (env entra via expansão `${...}`).
- **Header gateway → conversor:** `X-9Router-Upstream-Base` (somente `http://` ou `https://`), `X-9Router-Upstream` (nome, log), `Authorization: Bearer <api_key do upstream>`. Sem header, o conversor usa `UPSTREAM_BASE_URL` do env.
- **Loopback-only:** gateway `127.0.0.1:11435`, conversor `127.0.0.1:8080`, 9Router `127.0.0.1:20338/v1`. Request com `Origin` → 403 nos dois processos. CORS `allow_origins=["*"]` removido do conversor.
- **Erros novos** do conversor em formato Anthropic: `{"type":"error","error":{"type":"invalid_request_error","message":"…"}}`.
- **Catálogo** vem do YAML: `claude-sonnet-5`, `claude-haiku-5-5`, `claude-opus-5`; shape idêntico ao do conversor hoje (`object` + `data` + `models`).
- **Sem dependências novas.** `test_converter.py` roda em stdlib (testes que precisam de `fastapi`/`httpx` fazem skip automático). `make verify` verde ao fim de cada task.
- IDs reais do 9Router (sonda de 2026-10-08): `claude-haiku-5-5` existe; `claude-haiku-4-5-20251001` **não** (404) — por isso o alias antigo sai.

## Review Focus

Estes são os inputs/falhas que o spec implica mas que nenhum teste do projeto cobre hoje; cada linha aponta o task que a prende com um teste:

1. **`count_tokens` sem `model`** — deixa de ser 400 e passa a 200 com o default injetado (cliente que dependia do 400 quebra): Task 2, `TestInjectsDefaultModelWhenMissing` + edição do caso `"missing model"` em `gateway_test.go`.
2. **`model` não-string** (`{"model":42}`) — deve ser 400 com shape Anthropic, nunca 500 nem roteamento errado: Task 2, `TestRejectsNonStringModel`.
3. **`X-9Router-Upstream-Base` malicioso** (`file://`, `ftp://`, sem host) — conversor responde 400 e nunca tenta abrir: Task 5, `test_resolve_upstream_base_rejects_bad_scheme` + `test_http_rejects_origin_and_validates_base`.
4. **Request com `Origin` no conversor** — browser ganharia SSRF com destino controlável agora que existe o header de base: Task 5, `test_http_rejects_origin_and_validates_base` (e o middleware remove o CORS `*`).
5. **YAML sem chave no upstream default** — o gateway deve **não subir** com erro claro (hoje ele subiria e falharia misteriosamente no 9Router): Task 1, `TestDefaultUpstreamRequiresAPIKey`; Task 4, fumaça de startup.
6. **Catálogo vazio** (YAML sem `models`) — cliente não pode receber lista vazia: Task 3, `TestServeModelsEmptyFallsBackToDefault`.
7. **Re-marshalling do body na injeção do `model`** — os campos `messages`/`stream` precisam sobreviver ao `json.Marshal` do mapa: Task 2, `TestInjectsDefaultModelWhenMissing` (decodifica o corpo capturado).

---

### Task 1: Config — validação de chave, precedência env e resolução

**Files:**
- Modify: `internal/gateway/config.go` (imports + novas funções no fim do arquivo)
- Test: `internal/gateway/config_test.go`

**Interfaces:**
- Consumes: `LoadUpstreamsConfig`, `UpstreamsConfig`, `Upstream`, `GatewayCfg` (já existentes no pacote).
- Produces (usados pelos Tasks 2 e 4):
  - `func ResolveUpstreams() (*UpstreamsConfig, error)`
  - `func (c *UpstreamsConfig) ApplyEnvOverrides()`
  - `func syntheticUpstreams(baseURL, apiKey, model string) *UpstreamsConfig`
  - `const defaultUpstreamsPath = "upstreams.yaml"` e `const defaultModelName = "claude-sonnet-5"`

- [ ] **Step 1: Escrever os testes falhando**

Adicione ao fim de `internal/gateway/config_test.go` e ajuste os imports para
`"bytes"`, `"log/slog"`, `"os"`, `"path/filepath"`, `"strings"`, `"testing"`:

```go
func TestDefaultUpstreamRequiresAPIKey(t *testing.T) {
	path := writeConfig(t, `
upstreams:
  - name: primary
    base_url: http://127.0.0.1:20338/v1
    api_key: ""
    models: [claude-sonnet-5]
    default: true
default_model: claude-sonnet-5
`)
	if _, err := LoadUpstreamsConfig(path); err == nil {
		t.Fatal("expected error when the default upstream has no api_key")
	}
}

func TestNonDefaultUpstreamWithoutAPIKeyWarns(t *testing.T) {
	var buf bytes.Buffer
	prev := slog.Default()
	slog.SetDefault(slog.New(slog.NewTextHandler(&buf, nil)))
	t.Cleanup(func() { slog.SetDefault(prev) })

	path := writeConfig(t, `
upstreams:
  - name: primary
    base_url: http://127.0.0.1:20338/v1
    api_key: k1
    models: [claude-sonnet-5]
    default: true
  - name: secondary
    base_url: http://127.0.0.1:20339/v1
    api_key: ${NAO_EXISTE_NENHUMA_VAR}
    models: [gpt-4o]
default_model: claude-sonnet-5
`)
	if _, err := LoadUpstreamsConfig(path); err != nil {
		t.Fatalf("LoadUpstreamsConfig: %v", err)
	}
	if !strings.Contains(buf.String(), "secondary") {
		t.Errorf("expected a warning naming the non-default upstream, got %q", buf.String())
	}
}

func TestEnvOverridesYAML(t *testing.T) {
	t.Setenv("DEFAULT_MODEL", "claude-opus-5")
	t.Setenv("GATEWAY_LISTEN_ADDR", "127.0.0.1:19999")
	path := writeConfig(t, `
upstreams:
  - name: primary
    base_url: http://127.0.0.1:20338/v1
    api_key: k
    models: [claude-sonnet-5, claude-opus-5]
    default: true
default_model: claude-sonnet-5
gateway:
  listen_addr: 127.0.0.1:11435
`)
	cfg, err := LoadUpstreamsConfig(path)
	if err != nil {
		t.Fatalf("LoadUpstreamsConfig: %v", err)
	}
	cfg.ApplyEnvOverrides()
	if cfg.DefaultModel != "claude-opus-5" {
		t.Errorf("DefaultModel: got %q, want env value claude-opus-5", cfg.DefaultModel)
	}
	if cfg.Gateway.ListenAddr != "127.0.0.1:19999" {
		t.Errorf("ListenAddr: got %q, want env value 127.0.0.1:19999", cfg.Gateway.ListenAddr)
	}
}

func TestEnvDoesNotOverrideYAMLWhenUnset(t *testing.T) {
	t.Setenv("DEFAULT_MODEL", "")
	t.Setenv("GATEWAY_LISTEN_ADDR", "")
	path := writeConfig(t, `
upstreams:
  - name: primary
    base_url: http://127.0.0.1:20338/v1
    api_key: k
    models: [claude-sonnet-5]
    default: true
default_model: claude-sonnet-5
gateway:
  listen_addr: 127.0.0.1:11435
`)
	cfg, err := LoadUpstreamsConfig(path)
	if err != nil {
		t.Fatalf("LoadUpstreamsConfig: %v", err)
	}
	cfg.ApplyEnvOverrides()
	if cfg.DefaultModel != "claude-sonnet-5" {
		t.Errorf("DefaultModel: got %q, want YAML value", cfg.DefaultModel)
	}
	if cfg.Gateway.ListenAddr != "127.0.0.1:11435" {
		t.Errorf("ListenAddr: got %q, want YAML value", cfg.Gateway.ListenAddr)
	}
}

func TestResolveUpstreamsSynthesizesWhenDefaultPathMissing(t *testing.T) {
	t.Setenv("UPSTREAMS_CONFIG", "")
	t.Setenv("UPSTREAM_BASE_URL", "http://127.0.0.1:20338/v1/")
	t.Setenv("GATEWAY_UPSTREAM_API_KEY", "sk-env")
	t.Setenv("DEFAULT_MODEL", "claude-opus-5")
	t.Chdir(t.TempDir())

	cfg, err := ResolveUpstreams()
	if err != nil {
		t.Fatalf("ResolveUpstreams: %v", err)
	}
	if len(cfg.Upstreams) != 1 || cfg.Upstreams[0].Name != "env" {
		t.Fatalf("upstreams: got %+v, want a single synthesized \"env\" upstream", cfg.Upstreams)
	}
	if cfg.Upstreams[0].BaseURL != "http://127.0.0.1:20338/v1" {
		t.Errorf("BaseURL: got %q, want env value with trailing slash trimmed", cfg.Upstreams[0].BaseURL)
	}
	if cfg.Upstreams[0].APIKey != "sk-env" {
		t.Errorf("APIKey: got %q, want sk-env", cfg.Upstreams[0].APIKey)
	}
	if cfg.DefaultModel != "claude-opus-5" {
		t.Errorf("DefaultModel: got %q, want claude-opus-5", cfg.DefaultModel)
	}
}

func TestResolveUpstreamsExplicitPathMissingIsAnError(t *testing.T) {
	t.Setenv("UPSTREAMS_CONFIG", filepath.Join(t.TempDir(), "nao-existe.yaml"))
	if _, err := ResolveUpstreams(); err == nil {
		t.Fatal("expected error for an explicit UPSTREAMS_CONFIG path that does not exist")
	}
}
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `cd /home/access/AI/my-repo/script/9router-claude && /home/access/.local/go/bin/go test ./internal/gateway/ -run 'APIKey|Env|ResolveUpstreams' -count=1`
Expected: FAIL com `undefined: ResolveUpstreams` / `undefined: ApplyEnvOverrides` (compilação).

- [ ] **Step 3: Implementar em `internal/gateway/config.go`**

Troque os imports por:

```go
import (
	"errors"
	"fmt"
	"io/fs"
	"log/slog"
	"os"
	"strings"
	"time"

	"gopkg.in/yaml.v3"
)
```

Adicione as constantes logo abaixo do `type Upstream struct` (antes de `UpstreamsConfig`),
ou no topo do arquivo após o `package`:

```go
const (
	defaultUpstreamsPath = "upstreams.yaml"
	defaultModelName     = "claude-sonnet-5"
)
```

Dentro de `LoadUpstreamsConfig`, **depois** do bloco de promoção de default
(`if defaultCount == 0 { … } else if defaultCount > 1 { … }`) e **antes** do bloco de
`DefaultModel`, insira a validação de chave:

```go
	// Chave obrigatória no upstream default; nos demais, só aviso.
	for i := range cfg.Upstreams {
		u := &cfg.Upstreams[i]
		if strings.TrimSpace(u.APIKey) != "" {
			continue
		}
		if u.Default {
			return nil, fmt.Errorf("upstream %q: api_key is required for the default upstream", u.Name)
		}
		slog.Warn("upstream sem api_key — requests pra ele serão rejeitados pelo provedor",
			"upstream", u.Name, "base_url", u.BaseURL)
	}
```

No fim do arquivo adicione:

```go
// ApplyEnvOverrides aplica a precedência env > YAML > hardcoded.
// Com YAML presente, base_url/api_key vêm do arquivo (a env entra via ${...}).
func (c *UpstreamsConfig) ApplyEnvOverrides() {
	if v := strings.TrimSpace(os.Getenv("DEFAULT_MODEL")); v != "" {
		c.DefaultModel = v
	}
	if v := strings.TrimSpace(os.Getenv("GATEWAY_LISTEN_ADDR")); v != "" {
		c.Gateway.ListenAddr = v
	}
}

// syntheticUpstreams monta um upstream único — usado quando não há YAML.
// baseURL vazio significa "sem X-9Router-Upstream-Base" (o conversor fica no env).
func syntheticUpstreams(baseURL, apiKey, model string) *UpstreamsConfig {
	model = strings.TrimSpace(model)
	if model == "" {
		model = defaultModelName
	}
	return &UpstreamsConfig{
		Upstreams: []Upstream{{
			Name:    "env",
			BaseURL: strings.TrimRight(strings.TrimSpace(baseURL), "/"),
			APIKey:  apiKey,
			Models:  []string{model},
			Default: true,
			Timeout: 120,
		}},
		DefaultModel: model,
		Gateway: GatewayCfg{
			ListenAddr:       "127.0.0.1:11435",
			HealthPath:       "/_gateway/health",
			MaxRequestBodyMB: 64,
		},
	}
}

// ResolveUpstreams carrega a config efetiva de upstreams.
//   - UPSTREAMS_CONFIG explícito e ausente => erro (o usuário pediu um arquivo que não existe)
//   - caminho padrão ausente                => upstream sintetizado do env, sem erro
func ResolveUpstreams() (*UpstreamsConfig, error) {
	explicit := os.Getenv("UPSTREAMS_CONFIG")
	path := explicit
	if path == "" {
		path = defaultUpstreamsPath
	}
	cfg, err := LoadUpstreamsConfig(path)
	if err != nil {
		if explicit == "" && errors.Is(err, fs.ErrNotExist) {
			return syntheticUpstreams(
				os.Getenv("UPSTREAM_BASE_URL"),
				os.Getenv("GATEWAY_UPSTREAM_API_KEY"),
				os.Getenv("DEFAULT_MODEL"),
			), nil
		}
		return nil, err
	}
	cfg.ApplyEnvOverrides()
	return cfg, nil
}
```

- [ ] **Step 4: Rodar e ver passar**

Run: `/home/access/.local/go/bin/go test ./internal/gateway/ -count=1 -v`
Expected: todos os PASS, incluindo os 6 novos; os 4 testes antigos seguem verdes.

- [ ] **Step 5: `make verify` e commit**

Run: `make verify` → saída verde (`gofmt` limpo, `vet` ok, `test -race` ok, 4 PASS do conversor).

```bash
cd /home/access/AI/my-repo
git add script/9router-claude/internal/gateway/config.go script/9router-claude/internal/gateway/config_test.go
git commit -m "feat(9router-claude): valida chave do upstream default e resolve config (env > YAML)"
```

---

### Task 2: Gateway — roteamento por modelo com pass-through do `model`

**Files:**
- Modify: `internal/gateway/gateway.go` (`Config`, `New`, `ServeHTTP`, `serveMessage`, `serveCountTokens`, `forward`, novas funções)
- Modify: `internal/gateway/gateway_test.go` (helper delega pro novo; caso `"missing model"` passa a esperar 200)
- Create: `internal/gateway/routing_test.go`

**Interfaces:**
- Consumes: `UpstreamsConfig`, `FindUpstreamForModel`, `syntheticUpstreams`, `defaultModelName` (Task 1).
- Produces (usados pelos Tasks 3, 4 e 7):
  - `Config.Upstreams *UpstreamsConfig` e `Config.DefaultModel string`
  - `func (g *Gateway) defaultModel() string`
  - `func ensureModelField(body *[]byte, defaultModel string) (string, error)`
  - `func (g *Gateway) forward(w http.ResponseWriter, r *http.Request, u *Upstream)`
  - headers emitidos: `X-9Router-Upstream-Base`, `X-9Router-Upstream`
  - helper de teste: `func newTestGatewayWith(t *testing.T, converterURL string, cfg Config) *Gateway`

- [ ] **Step 1: Escrever os testes falhando**

Crie `internal/gateway/routing_test.go`:

```go
package gateway

import (
	"encoding/json"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

func newTestGatewayWith(t *testing.T, converterURL string, cfg Config) *Gateway {
	t.Helper()
	cfg.ListenAddr = "127.0.0.1:0"
	cfg.ConverterURL = converterURL
	if cfg.Logger == nil {
		cfg.Logger = slog.New(slog.NewTextHandler(io.Discard, nil))
	}
	cfg.ReadyWait = 300 * time.Millisecond
	cfg.ReadyPoll = 10 * time.Millisecond
	cfg.ReadyTTL = time.Second
	g, err := New(cfg)
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	if err := g.Start(); err != nil {
		t.Fatalf("Start: %v", err)
	}
	t.Cleanup(func() { _ = g.Close(t.Context()) })
	return g
}

func testUpstreams() *UpstreamsConfig {
	return &UpstreamsConfig{
		Upstreams: []Upstream{
			{
				Name: "primary", BaseURL: "http://127.0.0.1:20338/v1", APIKey: "k-primary",
				Models: []string{"claude-sonnet-5", "claude-haiku-5-5"}, Default: true, Timeout: 120,
			},
			{
				Name: "openai", BaseURL: "https://api.openai.com/v1", APIKey: "k-openai",
				Models: []string{"gpt-4o"}, Timeout: 120,
			},
		},
		DefaultModel: "claude-sonnet-5",
	}
}

// spyConverter devolve um conversor falso que grava body e headers do último request.
func spyConverter(t *testing.T, body, base, name, auth *atomic.Value) *httptest.Server {
	t.Helper()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		b, _ := io.ReadAll(r.Body)
		body.Store(string(b))
		base.Store(r.Header.Get("X-9Router-Upstream-Base"))
		name.Store(r.Header.Get("X-9Router-Upstream"))
		auth.Store(r.Header.Get("Authorization"))
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"id":"msg_1"}`))
	}))
	t.Cleanup(srv.Close)
	return srv
}

func TestRoutesModelAndPreservesIt(t *testing.T) {
	var gotBody, gotBase, gotName, gotAuth atomic.Value
	upstream := spyConverter(t, &gotBody, &gotBase, &gotName, &gotAuth)
	g := newTestGatewayWith(t, upstream.URL, Config{Upstreams: testUpstreams()})

	cases := []struct{ model, wantBase, wantName, wantAuth string }{
		{"claude-haiku-5-5", "http://127.0.0.1:20338/v1", "primary", "Bearer k-primary"},
		{"gpt-4o", "https://api.openai.com/v1", "openai", "Bearer k-openai"},
		{"sec/gemini/gemini-3.8-flash", "http://127.0.0.1:20338/v1", "primary", "Bearer k-primary"},
		{"modelo-que-nao-existe", "http://127.0.0.1:20338/v1", "primary", "Bearer k-primary"},
	}
	for _, tc := range cases {
		resp := doJSON(t, baseURL(t, g)+"/v1/messages", http.MethodPost,
			`{"model":"`+tc.model+`","messages":[]}`, nil)
		if resp.StatusCode != http.StatusOK {
			t.Fatalf("%s: got %d, want 200", tc.model, resp.StatusCode)
		}
		if v, _ := gotBody.Load().(string); !strings.Contains(v, `"model":"`+tc.model+`"`) {
			t.Errorf("%s: model não chegou intacto: %s", tc.model, v)
		}
		if v, _ := gotBase.Load().(string); v != tc.wantBase {
			t.Errorf("%s: X-9Router-Upstream-Base = %q, want %q", tc.model, v, tc.wantBase)
		}
		if v, _ := gotName.Load().(string); v != tc.wantName {
			t.Errorf("%s: X-9Router-Upstream = %q, want %q", tc.model, v, tc.wantName)
		}
		if v, _ := gotAuth.Load().(string); v != tc.wantAuth {
			t.Errorf("%s: Authorization = %q, want %q", tc.model, v, tc.wantAuth)
		}
	}
}

func TestInjectsDefaultModelWhenMissing(t *testing.T) {
	var gotBody, gotBase, gotName, gotAuth atomic.Value
	upstream := spyConverter(t, &gotBody, &gotBase, &gotName, &gotAuth)
	g := newTestGatewayWith(t, upstream.URL, Config{Upstreams: testUpstreams()})

	resp := doJSON(t, baseURL(t, g)+"/v1/messages", http.MethodPost, `{"messages":[]}`, nil)
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("missing model: got %d, want 200", resp.StatusCode)
	}
	v, _ := gotBody.Load().(string)
	var seen map[string]any
	if err := json.Unmarshal([]byte(v), &seen); err != nil {
		t.Fatalf("corpo encaminhado não é JSON: %v (%s)", err, v)
	}
	if seen["model"] != "claude-sonnet-5" {
		t.Errorf("model injetado: got %v, want claude-sonnet-5", seen["model"])
	}
	if _, ok := seen["messages"]; !ok {
		t.Errorf("campo messages perdido no re-marshalling: %s", v)
	}

	resp = doJSON(t, baseURL(t, g)+"/v1/messages/count_tokens", http.MethodPost, `{"messages":[]}`, nil)
	if resp.StatusCode != http.StatusOK {
		t.Errorf("count_tokens sem model: got %d, want 200 (default injetado)", resp.StatusCode)
	}
}

func TestRejectsNonStringModel(t *testing.T) {
	g := newTestGatewayWith(t, "http://127.0.0.1:1", Config{Upstreams: testUpstreams()})
	resp := doJSON(t, baseURL(t, g)+"/v1/messages", http.MethodPost,
		`{"model":42,"messages":[]}`, nil)
	if resp.StatusCode != http.StatusBadRequest {
		t.Fatalf("model numérico: got %d, want 400", resp.StatusCode)
	}
	var errResp struct {
		Type  string `json:"type"`
		Error struct {
			Type string `json:"type"`
		} `json:"error"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&errResp); err != nil {
		t.Fatalf("decode: %v", err)
	}
	if errResp.Type != "error" || errResp.Error.Type != "invalid_request_error" {
		t.Errorf("shape: got %+v, want anthropic invalid_request_error", errResp)
	}
}

func TestSynthUpstreamSendsNoBaseHeader(t *testing.T) {
	var gotBody, gotBase, gotName, gotAuth atomic.Value
	upstream := spyConverter(t, &gotBody, &gotBase, &gotName, &gotAuth)

	// Sem Upstreams: New sintetiza um upstream "env" sem base_url.
	g := newTestGatewayWith(t, upstream.URL, Config{APIKey: "sk-synth", DefaultModel: "claude-sonnet-5"})
	resp := doJSON(t, baseURL(t, g)+"/v1/messages", http.MethodPost,
		`{"model":"claude-sonnet-5","messages":[]}`, nil)
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("synth: got %d, want 200", resp.StatusCode)
	}
	if v, _ := gotBase.Load().(string); v != "" {
		t.Errorf("X-9Router-Upstream-Base: got %q, want empty (conversor fica no env)", v)
	}
	if v, _ := gotAuth.Load().(string); v != "Bearer sk-synth" {
		t.Errorf("Authorization: got %q, want Bearer sk-synth", v)
	}
}
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `/home/access/.local/go/bin/go test ./internal/gateway/ -run 'Routes|Injects|NonString|Synth' -count=1`
Expected: FAIL na compilação (`unknown field 'Upstreams' in Config`, `newTestGatewayWith undefined`).

- [ ] **Step 3: Implementar em `internal/gateway/gateway.go`**

(a) Em `type Config struct`, adicione após `APIKey string`:

```go
	// Upstreams é a fonte de upstreams (YAML/env). Nil ⇒ sintetizado de APIKey/DefaultModel.
	Upstreams *UpstreamsConfig
	// DefaultModel é injetado quando o request não traz "model".
	DefaultModel string
```

(b) Em `type Gateway struct`, adicione após `apiKey string`:

```go
	upstreams *UpstreamsConfig
```

(c) Em `New`, logo após a validação de `convURL` e antes do bloco do `logger`:

```go
	if cfg.Upstreams == nil {
		cfg.Upstreams = syntheticUpstreams("", cfg.APIKey, cfg.DefaultModel)
	}
```

e na construção do struct `g` (`g := &Gateway{…}`) adicione `upstreams: cfg.Upstreams,`.

(d) Substitua `serveMessage` inteira por:

```go
// serveMessage lê o body (limite 64 MiB), garante "model" e escolhe o upstream.
// O model do cliente é sempre preservado; só um campo ausente recebe o default.
func (g *Gateway) serveMessage(w http.ResponseWriter, r *http.Request) {
	body, err := readRequestBody(r)
	if err != nil {
		writeAnthropicError(w, http.StatusBadRequest, "invalid_request_error", err.Error())
		return
	}
	model, err := ensureModelField(&body, g.defaultModel())
	if err != nil {
		writeAnthropicError(w, http.StatusBadRequest, "invalid_request_error", err.Error())
		return
	}
	setRequestBody(r, body)
	g.forward(w, r, g.upstreams.FindUpstreamForModel(model))
}
```

(e) Adicione logo após `serveMessage`:

```go
// ensureModelField garante "model" como string não-vazia no body.
// Única reescrita do gateway: injeta defaultModel quando ausente. Valor existente
// é sempre preservado (pass-through). Retorna o modelo usado pro roteamento.
func ensureModelField(body *[]byte, defaultModel string) (string, error) {
	var payload map[string]json.RawMessage
	if err := json.Unmarshal(*body, &payload); err != nil {
		return "", fmt.Errorf("decode request body: %w", err)
	}
	var model string
	if raw, ok := payload["model"]; ok {
		if err := json.Unmarshal(raw, &model); err != nil {
			return "", errors.New("model must be a string")
		}
	}
	model = strings.TrimSpace(model)
	if model == "" {
		model = defaultModel
		encoded, err := json.Marshal(model)
		if err != nil {
			return "", fmt.Errorf("encode default model: %w", err)
		}
		payload["model"] = encoded
		rewritten, err := json.Marshal(payload)
		if err != nil {
			return "", fmt.Errorf("encode request body: %w", err)
		}
		*body = rewritten
	}
	return model, nil
}

func (g *Gateway) defaultModel() string {
	if m := strings.TrimSpace(g.upstreams.DefaultModel); m != "" {
		return m
	}
	return defaultModelName
}
```

(f) Troque a assinatura de `forward` e o bloco de credenciais por:

```go
// forward remove credenciais do cliente, injeta a do upstream escolhido e encaminha.
// u == nil significa "sem roteamento" (ex.: /health): mantém só o fallback de Config.APIKey.
func (g *Gateway) forward(w http.ResponseWriter, r *http.Request, u *Upstream) {
	// Nunca repassa credencial/cookie do cliente (placeholder do Claude, sessão de browser).
	r.Header.Del("Authorization")
	r.Header.Del("Cookie")
	r.Header.Del("Proxy-Authorization")
	r.Header.Del("X-Api-Key")
	key := g.apiKey
	if u != nil && strings.TrimSpace(u.APIKey) != "" {
		key = u.APIKey
	}
	if key != "" {
		r.Header.Set("Authorization", "Bearer "+key)
	}
	if u != nil && u.BaseURL != "" {
		r.Header.Set("X-9Router-Upstream-Base", u.BaseURL)
		r.Header.Set("X-9Router-Upstream", u.Name)
	}
	r.Host = g.converterURL.Host

	if err := g.waitForUpstream(r.Context()); err != nil {
		g.logger.Warn("upstream not ready", "path", r.URL.Path, "error", err)
		http.Error(w, "9router gateway unavailable", http.StatusBadGateway)
		return
	}
	g.routed.Add(1)
	g.proxy.ServeHTTP(w, r)
}
```

(g) Nos callers: em `ServeHTTP`, o case `"/health"` vira `g.forward(w, r, nil)` e o case
`"/v1/models"` vira `g.forward(w, r, nil)` (o Task 3 substitui esse último).

(h) Substitua o corpo de `serveCountTokens` (mantendo a leitura do body) por:

```go
	body, err := readRequestBody(r)
	if err != nil {
		writeAnthropicError(w, http.StatusBadRequest, "invalid_request_error", err.Error())
		return
	}
	// Mesma regra do /v1/messages: model ausente recebe o default; nunca valida catálogo.
	if _, err := ensureModelField(&body, g.defaultModel()); err != nil {
		writeAnthropicError(w, http.StatusBadRequest, "invalid_request_error", err.Error())
		return
	}
	tokens := estimateCountTokens(body)
	w.Header().Set("Content-Type", "application/json")
	if err := json.NewEncoder(w).Encode(map[string]int{"input_tokens": tokens}); err != nil {
		g.logger.Debug("write token-count response", "error", err)
	}
```

(O `var probe struct {…}` e o `strings.TrimSpace(probe.Model)` saem junto; `encoding/json`
segue em uso no arquivo.)

(i) Em `internal/gateway/gateway_test.go`, troque `func newTestGateway(t *testing.T, converterURL, apiKey string) *Gateway` por uma delegação (apague o corpo antigo):

```go
func newTestGateway(t *testing.T, converterURL, apiKey string) *Gateway {
	t.Helper()
	return newTestGatewayWith(t, converterURL, Config{APIKey: apiKey})
}
```

(j) Em `TestCountTokens`, o case `"missing model"` **muda de expectativa** (400 → 200).
Remova-o do loop e acrescente, logo após o loop:

```go
	// model ausente agora injeta o default em vez de 400 (ver spec, Seção 3)
	resp = doJSON(t, baseURL(t, g)+"/v1/messages/count_tokens", http.MethodPost, `{"messages":[]}`, nil)
	if resp.StatusCode != http.StatusOK {
		t.Errorf("missing model: got %d, want 200 (default model injected)", resp.StatusCode)
	}
```

O loop fica só com `"invalid json": not-json`.

- [ ] **Step 4: Rodar e ver passar**

Run: `/home/access/.local/go/bin/go test ./internal/gateway/ -count=1 -race -v`
Expected: todos PASS (novos + os antigos, incluindo `TestInjectsUpstreamKey`,
`TestStripsClientAuth`, `TestRetriesWithoutImages`, `TestCountTokens` atualizado).

- [ ] **Step 5: `make verify` e commit**

Run: `make verify` → verde.

```bash
cd /home/access/AI/my-repo
git add script/9router-claude/internal/gateway/
git commit -m "feat(9router-claude): gateway roteia upstream por modelo com model pass-through"
```

---

### Task 3: Gateway — catálogo `/v1/models` servido pelo próprio gateway

**Files:**
- Modify: `internal/gateway/gateway.go` (case `/v1/models` do `ServeHTTP` + novas funções)
- Test: `internal/gateway/routing_test.go`

**Interfaces:**
- Consumes: `UpstreamsConfig.AllModels()` (já existe: união deduplicada, ordem do arquivo),
  `(*Gateway).defaultModel()` (Task 2).
- Produces: `func (g *Gateway) serveModels(w http.ResponseWriter)` e
  `func modelEntry(id string) map[string]any` — comportamento consumido pelos clientes
  Claude (Task 7 valida via E2E).

- [ ] **Step 1: Escrever os testes falhando**

Acrescente ao fim de `internal/gateway/routing_test.go`:

```go
func TestServeModelsFromCatalog(t *testing.T) {
	g := newTestGatewayWith(t, "http://127.0.0.1:1", Config{Upstreams: testUpstreams()})

	resp := doJSON(t, baseURL(t, g)+"/v1/models", http.MethodGet, "", nil)
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("GET /v1/models: got %d, want 200", resp.StatusCode)
	}
	var out struct {
		Object string `json:"object"`
		Data   []struct {
			ID string `json:"id"`
		} `json:"data"`
		Models []struct {
			ID string `json:"id"`
		} `json:"models"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&out); err != nil {
		t.Fatalf("decode: %v", err)
	}
	if out.Object != "list" {
		t.Errorf("object: got %q, want list", out.Object)
	}
	want := []string{"claude-sonnet-5", "claude-haiku-5-5", "gpt-4o"}
	if len(out.Data) != len(want) {
		t.Fatalf("data: got %d itens %v, want %v", len(out.Data), out.Data, want)
	}
	for i, id := range want {
		if out.Data[i].ID != id {
			t.Errorf("data[%d]: got %q, want %q", i, out.Data[i].ID, id)
		}
	}
	if len(out.Models) != len(out.Data) {
		t.Errorf("models: %d itens, data: %d itens (shapes precisam casar)", len(out.Models), len(out.Data))
	}

	// método errado continua 405
	resp = doJSON(t, baseURL(t, g)+"/v1/models", http.MethodPost, "", nil)
	if resp.StatusCode != http.StatusMethodNotAllowed {
		t.Errorf("POST /v1/models: got %d, want 405", resp.StatusCode)
	}
}

func TestServeModelsDedupsAcrossUpstreams(t *testing.T) {
	cfg := &UpstreamsConfig{
		Upstreams: []Upstream{
			{Name: "a", BaseURL: "http://127.0.0.1:1/v1", APIKey: "k",
				Models: []string{"m1", "m2"}, Default: true, Timeout: 120},
			{Name: "b", BaseURL: "http://127.0.0.1:2/v1", APIKey: "k",
				Models: []string{"m2", "m3"}, Timeout: 120},
		},
		DefaultModel: "m1",
	}
	g := newTestGatewayWith(t, "http://127.0.0.1:1", Config{Upstreams: cfg})

	resp := doJSON(t, baseURL(t, g)+"/v1/models", http.MethodGet, "", nil)
	var out struct {
		Data []struct {
			ID string `json:"id"`
		} `json:"data"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&out); err != nil {
		t.Fatalf("decode: %v", err)
	}
	want := []string{"m1", "m2", "m3"}
	if len(out.Data) != len(want) {
		t.Fatalf("got %v, want %v", out.Data, want)
	}
	for i, id := range want {
		if out.Data[i].ID != id {
			t.Errorf("data[%d]: got %q, want %q", i, out.Data[i].ID, id)
		}
	}
}

func TestServeModelsEmptyFallsBackToDefault(t *testing.T) {
	cfg := &UpstreamsConfig{
		Upstreams: []Upstream{
			{Name: "env", APIKey: "k", Models: nil, Default: true, Timeout: 120},
		},
		DefaultModel: "claude-sonnet-5",
	}
	g := newTestGatewayWith(t, "http://127.0.0.1:1", Config{Upstreams: cfg})

	resp := doJSON(t, baseURL(t, g)+"/v1/models", http.MethodGet, "", nil)
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("got %d, want 200", resp.StatusCode)
	}
	var out struct {
		Data []struct {
			ID string `json:"id"`
		} `json:"data"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&out); err != nil {
		t.Fatalf("decode: %v", err)
	}
	if len(out.Data) != 1 || out.Data[0].ID != "claude-sonnet-5" {
		t.Errorf("catálogo vazio: got %v, want [claude-sonnet-5]", out.Data)
	}
}
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `/home/access/.local/go/bin/go test ./internal/gateway/ -run 'ServeModels' -count=1`
Expected: FAIL — hoje `/v1/models` é forward pro conversor espião/`127.0.0.1:1` e não devolve
o catálogo (status 502/0 itens).

- [ ] **Step 3: Implementar**

Em `internal/gateway/gateway.go`, troque o case `"/v1/models"` do `ServeHTTP` por:

```go
	case "/v1/models":
		if r.Method != http.MethodGet {
			w.Header().Set("Allow", http.MethodGet)
			http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
			return
		}
		g.serveModels(w)
```

Adicione no fim do arquivo (seção `--- helpers ---` ou uma nova seção `--- catálogo ---`):

```go
// --- catálogo -------------------------------------------------------------

// serveModels atende /v1/models com o catálogo declarado no upstreams.yaml,
// no mesmo shape do conversor (object + data + models) para os clientes já
// validados continuarem aceitando a resposta.
func (g *Gateway) serveModels(w http.ResponseWriter) {
	models := g.upstreams.AllModels()
	if len(models) == 0 {
		models = []string{g.defaultModel()}
	}
	data := make([]map[string]any, 0, len(models))
	for _, id := range models {
		data = append(data, modelEntry(id))
	}
	w.Header().Set("Content-Type", "application/json")
	if err := json.NewEncoder(w).Encode(map[string]any{
		"object": "list",
		"data":   data,
		"models": data,
	}); err != nil {
		g.logger.Debug("write model catalog", "error", err)
	}
}

func modelEntry(id string) map[string]any {
	return map[string]any{
		"id":           id,
		"name":         id,
		"model":        id,
		"model_name":   id,
		"display_name": id,
		"object":       "model",
		"type":         "model",
		"owned_by":     "anthropic",
		"provider":     "anthropic",
		"created":      1700000000,
		"capabilities": map[string]bool{
			"completion": true, "chat": true, "stream": true,
			"vision": true, "tools": true,
		},
	}
}
```

- [ ] **Step 4: Rodar e ver passar**

Run: `/home/access/.local/go/bin/go test ./internal/gateway/ -count=1 -race`
Expected: todos PASS (inclusive os do Task 2 — `TestRoutesModelAndPreservesIt` não usa `/v1/models`).

- [ ] **Step 5: `make verify` e commit**

Run: `make verify` → verde.

```bash
cd /home/access/AI/my-repo
git add script/9router-claude/internal/gateway/
git commit -m "feat(9router-claude): gateway serve o catálogo /v1/models do upstreams.yaml"
```

---

### Task 4: Ligar o YAML no `cmd/gateway/main.go`

**Files:**
- Modify: `cmd/gateway/main.go`

**Interfaces:**
- Consumes: `gateway.ResolveUpstreams()`, `UpstreamsConfig.Gateway.ListenAddr`,
  `UpstreamsConfig.DefaultModel` (Task 1), `Config.Upstreams`/`Config.DefaultModel` (Task 2).
- Produces: startup real com YAML (usado pelo E2E do Task 7); `envOr` é removido.

- [ ] **Step 1: Implementar (sem unitário próprio — main é fio fino; a verificação é de fumaça)**

Troque o corpo de `run()` em `cmd/gateway/main.go` por:

```go
func run() error {
	upstreams, err := gateway.ResolveUpstreams()
	if err != nil {
		return fmt.Errorf("load upstreams config: %w", err)
	}
	cfg := gateway.Config{
		ListenAddr:   upstreams.Gateway.ListenAddr,
		ConverterURL: os.Getenv("CONVERTER_URL"),
		APIKey:       os.Getenv("GATEWAY_UPSTREAM_API_KEY"),
		DefaultModel: upstreams.DefaultModel,
		Upstreams:    upstreams,
	}
	if cfg.ConverterURL == "" {
		return fmt.Errorf("CONVERTER_URL is required (ex.: http://converter:8080)")
	}

	g, err := gateway.New(cfg)
	if err != nil {
		return err
	}
	if err := g.Start(); err != nil {
		return err
	}

	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()
	<-ctx.Done()

	slog.Info("shutting down")
	shutdownCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	return g.Close(shutdownCtx)
}
```

Delete a função `envOr` inteira (ficou sem uso) e o import `"9router-claude/internal/gateway"`
continua igual. Se `envOr` era o único uso de nada, o compiler reclama de import não usado —
rode o build pra conferir.

- [ ] **Step 2: Build + vet**

Run: `/home/access/.local/go/bin/go build ./... && /home/access/.local/go/bin/go vet ./...`
Expected: sem saída (sucesso).

- [ ] **Step 3: Fumaça — caminho explícito inexistente não sobe**

```bash
cd /home/access/AI/my-repo/script/9router-claude
UPSTREAMS_CONFIG=/tmp/opencode/upstreams-nao-existe.yaml CONVERTER_URL=http://127.0.0.1:8080 \
  ./bin/gateway; echo "exit=$?"
```
Expected: `load upstreams config: read config: … no such file or directory` e `exit=1`
(build o binário antes com `make build`).

- [ ] **Step 4: Fumaça — sem YAML, modo sintetizado sobe em porta alternativa**

```bash
mkdir -p /tmp/opencode/gw-synth && cd /tmp/opencode/gw-synth
( UPSTREAMS_CONFIG= GATEWAY_LISTEN_ADDR=127.0.0.1:11436 CONVERTER_URL=http://127.0.0.1:8080 \
  timeout 6 /home/access/AI/my-repo/script/9router-claude/bin/gateway >gw.log 2>&1 & ) ; sleep 1
curl -s -o /dev/null -w 'health=%{http_code}\n' http://127.0.0.1:11436/_gateway/health
cat gw.log; wait; rm -rf /tmp/opencode/gw-synth
```
Expected: `health=204`, log com `gateway started address=127.0.0.1:11436`, e o processo
morre sozinho no timeout (sem deixar órfão).

- [ ] **Step 5: `make verify` e commit**

Run: `make verify` → verde.

```bash
cd /home/access/AI/my-repo
git add script/9router-claude/cmd/gateway/main.go
git commit -m "feat(9router-claude): main carrega upstreams.yaml com precedência env > YAML"
```

---

### Task 5: Conversor — header de base, rejeição de `Origin` e `model` intocado

**Files:**
- Create: `converter/app/routing.py`
- Modify: `converter/app/main.py` (remover CORS, middleware de Origin, base do header, model sem resolve, `CLAUDE_ALIASES` com o ID real)
- Modify: `converter/app/converter.py` (remover `resolve_upstream_model`/`MODEL_ALIAS_MAP`, `TARGET_MODELS` com o ID real, pass-through)
- Test: `converter/test_converter.py`

**Interfaces:**
- Consumes: contrato de header do Task 2 (`X-9Router-Upstream-Base`, `X-9Router-Upstream`).
- Produces: `app.routing.resolve_upstream_base(header_value, env_default) -> str`,
  `app.routing.InvalidUpstreamBase`, `app.routing.browser_origin_rejected(origin) -> bool`,
  `app.routing.HEADER_BASE` / `HEADER_NAME`.

- [ ] **Step 1: Escrever os testes falhando**

Em `converter/test_converter.py`, troque o bloco de imports e os dois testes de resolve por:

```python
"""Testes unitários do conversor (rodam com stdlib: python3 test_converter.py)."""
import sys

from app.converter import (
    anthropic_to_openai_request,
    openai_to_anthropic_response,
)
from app.routing import (
    InvalidUpstreamBase,
    browser_origin_rejected,
    resolve_upstream_base,
)


def test_resolve_upstream_base_prefers_gateway_header():
    assert resolve_upstream_base("http://127.0.0.1:20338/v1", "http://env") == "http://127.0.0.1:20338/v1"
    assert resolve_upstream_base("http://127.0.0.1:20339/v1/", "http://env") == "http://127.0.0.1:20339/v1"


def test_resolve_upstream_base_falls_back_to_env():
    assert resolve_upstream_base(None, "http://127.0.0.1:20338/v1/") == "http://127.0.0.1:20338/v1"
    assert resolve_upstream_base("", "http://127.0.0.1:20338/v1") == "http://127.0.0.1:20338/v1"


def test_resolve_upstream_base_rejects_bad_scheme():
    for bad in ("ftp://x/v1", "file:///etc/passwd", "javascript:alert(1)", "sem-esquema"):
        try:
            resolve_upstream_base(bad, "http://env")
        except InvalidUpstreamBase:
            continue
        raise AssertionError(f"{bad!r} deveria ser rejeitado")


def test_origin_rejected_only_when_present():
    assert browser_origin_rejected("https://evil.example") is True
    assert browser_origin_rejected(None) is False
    assert browser_origin_rejected("") is False


def test_request_passes_model_through():
    # pass-through: o que o cliente manda chega igual no payload OpenAI
    for wanted in ("sec/gemini/gemini-3.8-flash", "gpt-4o", "claude-haiku-5-5", "qualquer-coisa"):
        req = anthropic_to_openai_request({
            "model": wanted,
            "max_tokens": 10,
            "messages": [{"role": "user", "content": "oi"}],
        })
        assert req["model"] == wanted, f"{wanted} -> {req['model']}"
    # sem model: fallback do próprio conversor (modo direto)
    req = anthropic_to_openai_request({"max_tokens": 10, "messages": [{"role": "user", "content": "oi"}]})
    assert req["model"] == "claude-sonnet-5"


def test_http_rejects_origin_and_validates_base():
    try:
        from fastapi.testclient import TestClient
        from app.main import app
    except Exception as exc:  # stdlib puro: pula o teste HTTP
        print(f"  (skip de teste HTTP: {exc})")
        return
    client = TestClient(app)
    # Origin sempre 403 (navegador não fala com este serviço)
    resp = client.get("/health", headers={"Origin": "https://evil.example"})
    assert resp.status_code == 403, resp.status_code
    assert resp.json()["error"]["type"] == "permission_error"
    # base do header respeitada e valida
    resp = client.get("/health")
    assert resp.status_code == 200, resp.status_code
    # header malformado => 400 em formato Anthropic, sem tocar na rede
    payload = {"model": "claude-sonnet-5", "max_tokens": 1, "messages": [{"role": "user", "content": "oi"}]}
    resp = client.post("/v1/messages", json=payload,
                       headers={"X-9Router-Upstream-Base": "file:///etc/passwd"})
    assert resp.status_code == 400, (resp.status_code, resp.text)
    body = resp.json()
    assert body["type"] == "error" and body["error"]["type"] == "invalid_request_error", body
```

Mantenha os testes `test_request_conversion_shape` e `test_response_conversion_is_anthropic`
intactos. **Apague** `test_identity_alias_map` e `test_resolve_upstream_model`.

- [ ] **Step 2: Rodar e ver falhar**

Run (tem que rodar de dentro de `converter/`, como o Makefile faz):
`cd /home/access/AI/my-repo/script/9router-claude/converter && python3 test_converter.py`
Expected: `ModuleNotFoundError: No module named 'app.routing'` (exit 1).

- [ ] **Step 3: Implementar**

Crie `converter/app/routing.py`:

```python
"""Roteamento de upstream no conversor: a base URL vem do gateway (header),
com fallback no env para o modo direto (sem gateway na frente)."""
from typing import Optional
from urllib.parse import urlparse

HEADER_BASE = "x-9router-upstream-base"
HEADER_NAME = "x-9router-upstream"


class InvalidUpstreamBase(ValueError):
    """X-9Router-Upstream-Base fora do contrato (scheme http/https)."""


def resolve_upstream_base(header_value: Optional[str], env_default: str) -> str:
    """Devolve a base do 9Router: header do gateway, senão o env (modo direto)."""
    if not header_value:
        return (env_default or "").rstrip("/")
    parsed = urlparse(header_value)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise InvalidUpstreamBase(f"invalid upstream base: {header_value!r}")
    return header_value.rstrip("/")


def browser_origin_rejected(origin: Optional[str]) -> bool:
    """Claude Code/Desktop é cliente nativo e não manda Origin: Origin = browser = recusa."""
    return bool(origin)
```

Em `converter/app/converter.py`:
1. Apague `TARGET_MODELS` com o haiku antigo e `MODEL_ALIAS_MAP` e a função
   `resolve_upstream_model` inteira (linhas 10–35). O `TARGET_MODELS` que sobra vira:

```python
TARGET_MODELS = [
    "claude-sonnet-5",
    "claude-haiku-5-5",
    "claude-opus-5"
]
```

2. Em `anthropic_to_openai_request`, troque as 3 primeiras linhas por:

```python
def anthropic_to_openai_request(anthropic_req: Dict[str, Any]) -> Dict[str, Any]:
    model = anthropic_req.get("model") or "claude-sonnet-5"

    system_prompt = format_system_prompt(anthropic_req.get("system"))
    raw_messages = anthropic_req.get("messages", [])

    logger.info(f"Converting Anthropic request (model pass-through): '{model}'")
```

(Nada mais no corpo usa `raw_model` — confirme procurando `raw_model` no arquivo antes de
commitar: só esse trecho e a função removida usavam.)

Em `converter/app/main.py`:
1. Remova o import `from fastapi.middleware.cors import CORSMiddleware` e o bloco
   `app.add_middleware(CORSMiddleware, allow_origins=["*"], …)`.
2. Após o `app = FastAPI(...)`, adicione os imports de routing e o middleware:

```python
from app.routing import HEADER_BASE, HEADER_NAME, InvalidUpstreamBase, browser_origin_rejected, resolve_upstream_base

app = FastAPI(title="Anthropic Compatible Proxy", version="1.0.0")


@app.middleware("http")
async def reject_browser_origin(request: Request, call_next):
    if browser_origin_rejected(request.headers.get("Origin")):
        return JSONResponse(
            status_code=403,
            content={"type": "error", "error": {"type": "permission_error",
                                                "message": "origin is not allowed"}},
        )
    return await call_next(request)
```

(O `from app.routing import …` pode ficar junto dos demais imports `from app.…` — mantenha
a ordem de importes do arquivo.)

3. Acrescente o helper de erro logo antes de `list_models`:

```python
def invalid_request(message: str) -> JSONResponse:
    return JSONResponse(
        status_code=400,
        content={"type": "error", "error": {"type": "invalid_request_error", "message": message}},
    )
```

4. `list_models` passa a aceitar `request: Request` e usar a base resolvida:

```python
@app.get("/v1/models")
@app.get("/models")
async def list_models(request: Request):
    """Proxy /v1/models returning Anthropic standard IDs first to satisfy client validation."""
    try:
        base = resolve_upstream_base(request.headers.get(HEADER_BASE), UPSTREAM_BASE_URL)
    except InvalidUpstreamBase as exc:
        return invalid_request(str(exc))
    logger.info(f"Fetching models from upstream {base}/models (via {request.headers.get(HEADER_NAME) or 'env'})")
    raw_models = []

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{base}/models")
```

(resto do corpo do `list_models` permanece; só as 3 linhas acima mudam —
`CLAUDE_ALIASES` abaixo também troca `claude-haiku-4-5-20251001` por `claude-haiku-5-5`.)

5. `CLAUDE_ALIASES` fica:

```python
CLAUDE_ALIASES = [
    "claude-sonnet-5",
    "claude-haiku-5-5",
    "claude-opus-5"
]
```

6. Em `create_message`, troque `requested_model` e a montagem da URL:

```python
    requested_model = body.get("model") or DEFAULT_MODEL
```

```python
    try:
        base = resolve_upstream_base(request.headers.get(HEADER_BASE), UPSTREAM_BASE_URL)
    except InvalidUpstreamBase as exc:
        logger.error(f"Rejected upstream base header: {exc}")
        return invalid_request(str(exc))
    upstream_url = f"{base}/chat/completions"
    logger.info(f"Forwarding request to upstream: POST {upstream_url} (via {request.headers.get(HEADER_NAME) or 'env'})")
```

(apague a linha antiga `upstream_url = f"{UPSTREAM_BASE_URL}/chat/completions"` e o
`logger.info(f"Forwarding request to upstream: POST {upstream_url}")` duplicado —
o `logger.info` do `/health` e o `logger.info(f"Fetching models from upstream {UPSTREAM_BASE_URL}/models")`
do `list_models` já foram cobertos nos passos acima.)

- [ ] **Step 4: Rodar e ver passar**

Run: `make test-py` → espera 7 PASS (os 4 que já existiam menos os 2 removidos, mais os 5 novos).
Depois rode também o caminho stdlib: `cd converter && python3 test_converter.py` → os testes
de routing passam; o de HTTP imprime `(skip …)` se não houver fastapi.
Confirme que nada referencia o código apagado:

```bash
grep -rn "resolve_upstream_model\|MODEL_ALIAS_MAP\|CORSMiddleware" converter/ --include='*.py'
```
Expected: sem ocorrências em `converter/app/*.py` (o `ref-9router/` é referência e não muda).

- [ ] **Step 5: `make verify` e commit**

Run: `make verify` → verde.

```bash
cd /home/access/AI/my-repo
git add script/9router-claude/converter/
git commit -m "feat(9router-claude): conversor honra header de upstream, rejeita Origin e não reescreve model"
```

---

### Task 6: Config de runtime — `upstreams.yaml`, `.env.example`, README

**Files:**
- Modify: `upstreams.yaml`
- Modify: `.env.example`
- Modify: `.env` (arquivo local, não versionado — remoção de var morta)
- Modify: `README.md`

**Interfaces:**
- Consumes: `ResolveUpstreams` (Task 1) e o contrato de header (Tasks 2/5).
- Produces: a config real que o E2E do Task 7 usa.

- [ ] **Step 1: Reescrever `upstreams.yaml`**

```yaml
# Configuração de upstreams do 9Router — fonte única lida pelo gateway (Go).
# Variáveis via ${NOME_DA_ENV}: a env influencia este arquivo por expansão.
upstreams:
  - name: "9router-primary"
    base_url: "http://127.0.0.1:20338/v1"
    api_key: "${GATEWAY_UPSTREAM_API_KEY}"
    models:
      - "claude-sonnet-5"
      - "claude-haiku-5-5"
      - "claude-opus-5"
    default: true
    timeout: 120        # declarado, ainda ignorado (conversor fixo em 120s)

  # Exemplos — descomente e crie as envs correspondentes se usar:
  # - name: "9router-secondary"
  #   base_url: "http://127.0.0.1:20339/v1"
  #   api_key: "${NINEROUTER_API_KEY_2}"
  #   models: ["gpt-4o", "gpt-4o-mini"]
  #   default: false
  #   timeout: 120
  #
  # - name: "openai-direct"
  #   base_url: "https://api.openai.com/v1"
  #   api_key: "${OPENAI_API_KEY}"
  #   models: ["gpt-4o", "gpt-4o-mini", "o1-preview"]
  #   default: false
  #   timeout: 180

# Modelo quando o cliente não envia "model" (env DEFAULT_MODEL vence este valor)
default_model: "claude-sonnet-5"

gateway:
  listen_addr: "127.0.0.1:11435"   # env GATEWAY_LISTEN_ADDR vence
  health_path: "/_gateway/health"
  max_request_body_mb: 64
```

- [ ] **Step 2: `.env.example` — remover var morta e documentar as novas**

Remova a linha `TARGET_MODELS=…` e acrescente, na seção de endereços:

```bash
# --- Multi-upstream (opcional) ---------------------------------------------
# UPSTREAMS_CONFIG=upstreams.yaml   # caminho do YAML; sem ele o gateway monta 1 upstream do env
# NINEROUTER_API_KEY_2=            # só se você descomentar o upstream secundário
# OPENAI_API_KEY=                  # só se você descomentar o openai-direct
```

- [ ] **Step 3: `.env` local — tirar a var morta (backup antes)**

```bash
cd /home/access/AI/my-repo/script/9router-claude
cp .env .env.bak-$(date +%Y%m%d)
grep -v '^TARGET_MODELS=' .env > .env.tmp && mv .env.tmp .env
grep -c 'TARGET_MODELS' .env || echo "TARGET_MODELS removida"
```

- [ ] **Step 4: README — tabela de variáveis e nota de multi-upstream**

Na tabela "Variáveis (.env)", remova a linha de `DEFAULT_MODEL` duplicada se existir e
acrescente a linha de `UPSTREAMS_CONFIG`; logo após a tabela, acrescente:

```markdown
**Multi-upstream:** o gateway lê `upstreams.yaml` (fonte única: `base_url`, `api_key`,
`models` por upstream). Sem o arquivo ele monta um upstream só a partir de
`UPSTREAM_BASE_URL` + `GATEWAY_UPSTREAM_API_KEY`. O upstream `default` é obrigatório ter
chave; os demais podem ficar sem (aviso no log).

**Modelo pass-through:** o `model` que o Claude/env manda chega ao 9Router **intocado** —
o gateway só injeta `default_model` quando o request não traz `model`, e escolhe o upstream
pelo `models` declarado no YAML (sem match → upstream default). `sec/…`, `master/…` etc.
funcionam mesmo fora do catálogo `/v1/models`.
```

- [ ] **Step 5: Validar a config e commitar**

Run: `make build && UPSTREAMS_CONFIG=upstreams.yaml ./bin/gateway` (o processo vai subir em
`11435` — **se o proxy real estiver no ar, a porta ocupada derruba ele**: rode só se
`make status` mostrar o proxy paro, ou valide com o Task 7). Em vez disso, valide a carga
sem subir:

```bash
cd /home/access/AI/my-repo/script/9router-claude
set -a; . ./.env; set +a
GATEWAY_LISTEN_ADDR=127.0.0.1:11499 UPSTREAMS_CONFIG=upstreams.yaml \
  timeout 3 ./bin/gateway > /tmp/opencode/gw-cfg.log 2>&1; grep -E "gateway started|api_key|error" /tmp/opencode/gw-cfg.log
```
Expected: `gateway started address=127.0.0.1:11499` e nenhum `api_key is required`.

```bash
cd /home/access/AI/my-repo
git add script/9router-claude/upstreams.yaml script/9router-claude/.env.example script/9router-claude/README.md
git commit -m "docs(9router-claude): upstreams.yaml como fonte única + .env/README atualizados"
```

---

### Task 7: E2E no proxy real + `make verify`

**Files:**
- Test: nenhum arquivo novo — validação de ponta a ponta contra o 9Router real.

**Interfaces:**
- Consumes: todos os tasks anteriores.
- Produces: evidência de que Claude Code/Desktop funcionam com o9Router.

- [ ] **Step 1: `make verify` completo**

Run: `make verify`
Expected: gofmt limpo · `vet` ok · `test -race` ok · 4+ testes Python PASS (sem SKIP se a
venv estiver criada).

- [ ] **Step 2: Subir de novo o proxy com o código novo**

```bash
cd /home/access/AI/my-repo/script/9router-claude
make down && make up
```
Expected: `proxy pronto: http://127.0.0.1:11435` (o `down` novo imprime `parando pid …: …`
e `proxy parado`; o `up` novo pode acusar `proxy já rodando` se o `down` falhar — aí
investigue antes de seguir).

- [ ] **Step 3: Saúde e catálogo**

```bash
curl -si http://127.0.0.1:11435/_gateway/health | head -3        # → HTTP/1.1 204 + X-9Router-Claude-Gateway: 1
curl -s http://127.0.0.1:11435/v1/models | python3 -m json.tool | head -20
```
Expected: `204`; e no catálogo exatamente 3 modelos em ordem:
`claude-sonnet-5`, `claude-haiku-5-5`, `claude-opus-5` (shape com `data` **e** `models`).

- [ ] **Step 4: Pass-through do modelo (o bug do haiku é a prova)**

```bash
for m in claude-sonnet-5 claude-haiku-5-5 sec/gemini/gemini-3.8-flash; do
  printf '%-32s' "$m:"
  curl -s -m 60 -o /tmp/opencode/e2e.json -w 'http=%{http_code}\n' \
    -H 'Content-Type: application/json' -H 'x-api-key: qualquer' \
    -d "{\"model\":\"$m\",\"max_tokens\":8,\"messages\":[{\"role\":\"user\",\"content\":\"diga ok\"}]}" \
    http://127.0.0.1:11435/v1/messages
  head -c 160 /tmp/opencode/e2e.json; echo
done
```
Expected: os 3 com `http=200` e corpo `{"type":"message",…}`. **`claude-haiku-5-5` era 404
antes deste trabalho** — é a prova do fix. Confirme no log que o modelo foi intocado:

```bash
grep -E 'Model: |via ' .run/converter.log | tail -6
```
Expected: `Model: sec/gemini/gemini-3.8-flash` (igual ao pedido) e `via 9router-primary`.

- [ ] **Step 5: Segurança e count_tokens**

```bash
# 403 com Origin no gateway
curl -s -o /dev/null -w 'gateway+Origin=%{http_code}\n' -H 'Origin: https://evil.example' \
  http://127.0.0.1:11435/_gateway/health
# 403 com Origin direto no conversor
curl -s -o /dev/null -w 'converter+Origin=%{http_code}\n' -H 'Origin: https://evil.example' \
  http://127.0.0.1:8080/health
# header de base inválido => 400 Anthropic no conversor
curl -s -w '\nconverter+base-ruim=%{http_code}\n' -H 'Content-Type: application/json' \
  -H 'X-9Router-Upstream-Base: file:///etc/passwd' \
  -d '{"model":"claude-sonnet-5","max_tokens":1,"messages":[]}' http://127.0.0.1:8080/v1/messages
# count_tokens sem model => 200
curl -s -o /dev/null -w 'count_tokens=%{http_code}\n' -H 'Content-Type: application/json' \
  -d '{"messages":[]}' http://127.0.0.1:11435/v1/messages/count_tokens
```
Expected: `403`, `403`, `400` com `{"type":"error",…}`, `200`.

- [ ] **Step 6: Estado final e commit**

```bash
cd /home/access/AI/my-repo/script/9router-claude
make status
git -C /home/access/AI/my-repo status --short -- script/9router-claude
git -C /home/access/AI/my-repo log --oneline -7
```
Expected: `gateway: 204` / `converter: 200`; working tree do projeto sem pendências
relacionadas ao plano (o `.env`, `.run/`, `bin/` são locais); a sequência de commits dos
Tasks 1–6 presente.

Se tudo verde: plano concluído — reporte ao humano que o proxy está no ar com o
roteamento novo.
