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
