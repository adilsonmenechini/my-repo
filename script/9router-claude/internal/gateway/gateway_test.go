package gateway

import (
	"bytes"
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

func newTestGateway(t *testing.T, converterURL, apiKey string) *Gateway {
	t.Helper()
	g, err := New(Config{
		ListenAddr:   "127.0.0.1:0",
		ConverterURL: converterURL,
		APIKey:       apiKey,
		Logger:       slog.New(slog.NewTextHandler(io.Discard, nil)),
		ReadyWait:    300 * time.Millisecond,
		ReadyPoll:    10 * time.Millisecond,
		ReadyTTL:     time.Second,
	})
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	if err := g.Start(); err != nil {
		t.Fatalf("Start: %v", err)
	}
	t.Cleanup(func() { _ = g.Close(t.Context()) })
	return g
}

func baseURL(t *testing.T, g *Gateway) string {
	t.Helper()
	addr := g.Addr()
	if addr == "" {
		t.Fatal("gateway has no address")
	}
	return "http://" + addr
}

func doJSON(t *testing.T, url, method, body string, mutate func(*http.Request)) *http.Response {
	t.Helper()
	var reader io.Reader
	if body != "" {
		reader = strings.NewReader(body)
	}
	req, err := http.NewRequest(method, url, reader)
	if err != nil {
		t.Fatalf("NewRequest: %v", err)
	}
	if body != "" {
		req.Header.Set("Content-Type", "application/json")
	}
	if mutate != nil {
		mutate(req)
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("Do: %v", err)
	}
	t.Cleanup(func() { _ = resp.Body.Close() })
	return resp
}

func TestRejectsForeignHost(t *testing.T) {
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))
	defer upstream.Close()

	g := newTestGateway(t, upstream.URL, "")
	addr := g.Addr()

	for _, host := range []string{"evil.com:" + portOf(addr), "192.168.1.10:9999", "localhost:1"} {
		resp := doJSON(t, baseURL(t, g)+"/health", http.MethodGet, "", func(r *http.Request) {
			r.Host = host
		})
		if resp.StatusCode != http.StatusForbidden {
			t.Errorf("Host %q: got %d, want 403", host, resp.StatusCode)
		}
	}

	resp := doJSON(t, baseURL(t, g)+"/health", http.MethodGet, "", nil)
	if resp.StatusCode != http.StatusOK {
		t.Errorf("loopback host: got %d, want 200", resp.StatusCode)
	}
}

func portOf(addr string) string {
	i := strings.LastIndex(addr, ":")
	return addr[i+1:]
}

func TestRejectsOriginHeader(t *testing.T) {
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))
	defer upstream.Close()

	g := newTestGateway(t, upstream.URL, "")
	resp := doJSON(t, baseURL(t, g)+"/health", http.MethodGet, "", func(r *http.Request) {
		r.Header.Set("Origin", "https://evil.example")
	})
	if resp.StatusCode != http.StatusForbidden {
		t.Errorf("with Origin: got %d, want 403", resp.StatusCode)
	}
}

func TestGatewayHealth(t *testing.T) {
	g := newTestGateway(t, "http://127.0.0.1:1", "")

	resp := doJSON(t, baseURL(t, g)+"/_gateway/health", http.MethodGet, "", nil)
	if resp.StatusCode != http.StatusNoContent {
		t.Errorf("health: got %d, want 204", resp.StatusCode)
	}
	if got := resp.Header.Get(HealthHeader); got != "1" {
		t.Errorf("health header: got %q, want 1", got)
	}

	resp = doJSON(t, baseURL(t, g)+"/_gateway/health", http.MethodPost, "", nil)
	if resp.StatusCode != http.StatusMethodNotAllowed {
		t.Errorf("health POST: got %d, want 405", resp.StatusCode)
	}
}

func TestProxyPassesHealth(t *testing.T) {
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/health" {
			w.WriteHeader(http.StatusNotFound)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"status":"ok"}`))
	}))
	defer upstream.Close()

	g := newTestGateway(t, upstream.URL, "")
	resp := doJSON(t, baseURL(t, g)+"/health", http.MethodGet, "", nil)
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("proxied health: got %d, want 200", resp.StatusCode)
	}
	body, _ := io.ReadAll(resp.Body)
	if !strings.Contains(string(body), `"ok"`) {
		t.Errorf("proxied body: %q", body)
	}
}

func TestCountTokens(t *testing.T) {
	g := newTestGateway(t, "http://127.0.0.1:1", "")

	body := `{"model":"claude-sonnet-5","messages":[{"role":"user","content":"ola mundo"}]}`
	resp := doJSON(t, baseURL(t, g)+"/v1/messages/count_tokens", http.MethodPost, body, nil)
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("count_tokens: got %d, want 200", resp.StatusCode)
	}
	var out struct {
		InputTokens int `json:"input_tokens"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&out); err != nil {
		t.Fatalf("decode: %v", err)
	}
	if out.InputTokens <= 0 {
		t.Errorf("input_tokens: got %d, want > 0", out.InputTokens)
	}

	// system also counts
	withSystem := `{"model":"claude-sonnet-5","system":"voce e um assistente util e detalhado, sempre responde por extenso","messages":[{"role":"user","content":"ola mundo"}]}`
	resp = doJSON(t, baseURL(t, g)+"/v1/messages/count_tokens", http.MethodPost, withSystem, nil)
	var out2 struct {
		InputTokens int `json:"input_tokens"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&out2); err != nil {
		t.Fatalf("decode system: %v", err)
	}
	if out2.InputTokens <= out.InputTokens {
		t.Errorf("system should increase count: with=%d without=%d", out2.InputTokens, out.InputTokens)
	}

	for name, payload := range map[string]string{
		"invalid json":  `not-json`,
		"missing model": `{"messages":[]}`,
	} {
		resp := doJSON(t, baseURL(t, g)+"/v1/messages/count_tokens", http.MethodPost, payload, nil)
		if resp.StatusCode != http.StatusBadRequest {
			t.Errorf("%s: got %d, want 400", name, resp.StatusCode)
		}
		var errResp struct {
			Type  string `json:"type"`
			Error struct {
				Type string `json:"type"`
			} `json:"error"`
		}
		if err := json.NewDecoder(resp.Body).Decode(&errResp); err != nil {
			t.Fatalf("%s decode: %v", name, err)
		}
		if errResp.Type != "error" || errResp.Error.Type != "invalid_request_error" {
			t.Errorf("%s: got %+v, want anthropic invalid_request_error", name, errResp)
		}
	}
}

func TestStripsClientAuth(t *testing.T) {
	var gotAuth, gotCookie, gotAPIKey atomic.Value
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotAuth.Store(r.Header.Get("Authorization"))
		gotCookie.Store(r.Header.Get("Cookie"))
		gotAPIKey.Store(r.Header.Get("X-Api-Key"))
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"id":"msg_1"}`))
	}))
	defer upstream.Close()

	g := newTestGateway(t, upstream.URL, "")
	resp := doJSON(t, baseURL(t, g)+"/v1/messages", http.MethodPost,
		`{"model":"claude-sonnet-5","messages":[]}`, func(r *http.Request) {
			r.Header.Set("Authorization", "Bearer client-secret")
			r.Header.Set("Cookie", "session=abc")
			r.Header.Set("Proxy-Authorization", "Basic zzz")
		})
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("proxied message: got %d", resp.StatusCode)
	}
	if v, _ := gotAuth.Load().(string); v != "" {
		t.Errorf("Authorization leaked upstream: %q", v)
	}
	if v, _ := gotCookie.Load().(string); v != "" {
		t.Errorf("Cookie leaked upstream: %q", v)
	}
	if v, _ := gotAPIKey.Load().(string); v != "" {
		t.Errorf("X-Api-Key leaked upstream: %q", v)
	}
}

func TestInjectsUpstreamKey(t *testing.T) {
	var gotAuth atomic.Value
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotAuth.Store(r.Header.Get("Authorization"))
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"id":"msg_1"}`))
	}))
	defer upstream.Close()

	g := newTestGateway(t, upstream.URL, "sk-upstream")
	resp := doJSON(t, baseURL(t, g)+"/v1/messages", http.MethodPost,
		`{"model":"claude-sonnet-5","messages":[]}`, func(r *http.Request) {
			r.Header.Set("Authorization", "Bearer client-placeholder")
		})
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("proxied message: got %d", resp.StatusCode)
	}
	if v, _ := gotAuth.Load().(string); v != "Bearer sk-upstream" {
		t.Errorf("Authorization: got %q, want %q", v, "Bearer sk-upstream")
	}
}

func TestUpstreamNotReady502(t *testing.T) {
	// ConverterURL com porta fechada: readiness deve desistir e devolver 502.
	g := newTestGateway(t, "http://127.0.0.1:1", "")
	resp := doJSON(t, baseURL(t, g)+"/v1/messages", http.MethodPost,
		`{"model":"claude-sonnet-5","messages":[]}`, nil)
	if resp.StatusCode != http.StatusBadGateway {
		t.Errorf("upstream down: got %d, want 502", resp.StatusCode)
	}
}

func TestUpstreamReady200(t *testing.T) {
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"id":"msg_1"}`))
	}))
	defer upstream.Close()

	g := newTestGateway(t, upstream.URL, "")
	resp := doJSON(t, baseURL(t, g)+"/v1/messages", http.MethodPost,
		`{"model":"claude-sonnet-5","messages":[]}`, nil)
	if resp.StatusCode != http.StatusOK {
		t.Errorf("upstream up: got %d, want 200", resp.StatusCode)
	}
}

func TestRetriesWithoutImages(t *testing.T) {
	var calls atomic.Int32
	var secondBody atomic.Value
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		n := calls.Add(1)
		body, _ := io.ReadAll(r.Body)
		if n == 1 {
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(http.StatusBadRequest)
			_, _ = w.Write([]byte(`{"type":"error","error":{"type":"invalid_request_error","message":"This model does not support image input"}}`))
			return
		}
		secondBody.Store(string(body))
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"ok":true}`))
	}))
	defer upstream.Close()

	g := newTestGateway(t, upstream.URL, "")
	payload := `{"model":"claude-sonnet-5","messages":[{"role":"user","content":[` +
		`{"type":"text","text":"olha"},{"type":"image","source":{"type":"base64","media_type":"image/png","data":"AAAA"}}]}]}`
	resp := doJSON(t, baseURL(t, g)+"/v1/messages", http.MethodPost, payload, nil)

	if resp.StatusCode != http.StatusOK {
		t.Fatalf("after retry: got %d, want 200", resp.StatusCode)
	}
	if n := calls.Load(); n != 2 {
		t.Errorf("upstream calls: got %d, want 2", n)
	}
	second, _ := secondBody.Load().(string)
	if strings.Contains(second, `"type":"image"`) {
		t.Errorf("second request still has image block: %s", second)
	}
	if !strings.Contains(second, unsupportedImageNotice) {
		t.Errorf("second request missing image notice: %s", second)
	}
}

func TestNoRetryWithoutImageError(t *testing.T) {
	var calls atomic.Int32
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		w.WriteHeader(http.StatusBadRequest)
		_, _ = w.Write([]byte(`{"type":"error","error":{"type":"invalid_request_error","message":"max_tokens too small"}}`))
	}))
	defer upstream.Close()

	g := newTestGateway(t, upstream.URL, "")
	payload := `{"model":"claude-sonnet-5","messages":[{"role":"user","content":[{"type":"text","text":"oi"}]}]}`
	resp := doJSON(t, baseURL(t, g)+"/v1/messages", http.MethodPost, payload, nil)
	if resp.StatusCode != http.StatusBadRequest {
		t.Errorf("got %d, want 400 passthrough", resp.StatusCode)
	}
	if n := calls.Load(); n != 1 {
		t.Errorf("calls: got %d, want 1 (no retry for non-image errors)", n)
	}
}

func TestStreamingFlushesEarly(t *testing.T) {
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/event-stream")
		_, _ = w.Write([]byte("event: start\ndata: {\"chunk\":1}\n\n"))
		w.(http.Flusher).Flush()
		time.Sleep(600 * time.Millisecond)
		_, _ = w.Write([]byte("event: end\ndata: {}\n\n"))
	}))
	defer upstream.Close()

	g := newTestGateway(t, upstream.URL, "")
	resp := doJSON(t, baseURL(t, g)+"/v1/messages", http.MethodPost,
		`{"model":"claude-sonnet-5","stream":true,"messages":[]}`, nil)
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("stream: got %d", resp.StatusCode)
	}

	type result struct {
		n   int
		err error
	}
	done := make(chan result, 1)
	go func() {
		buf := make([]byte, 256)
		n, err := resp.Body.Read(buf)
		done <- result{n, err}
	}()

	select {
	case r := <-done:
		if r.err != nil && r.n == 0 {
			t.Fatalf("read: %v", r.err)
		}
		if r.n == 0 {
			t.Fatal("first chunk not received")
		}
	case <-time.After(300 * time.Millisecond):
		t.Fatal("first SSE chunk was not flushed before upstream finished (FlushInterval missing?)")
	}
}

func TestRejectsOversizedBody(t *testing.T) {
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))
	defer upstream.Close()

	oldLimit := maxRequestBodyBytes
	maxRequestBodyBytes = 64
	t.Cleanup(func() { maxRequestBodyBytes = oldLimit })

	g := newTestGateway(t, upstream.URL, "")
	big := bytes.Repeat([]byte("a"), 65)
	resp := doJSON(t, baseURL(t, g)+"/v1/messages", http.MethodPost, string(big), nil)
	if resp.StatusCode != http.StatusBadRequest {
		t.Errorf("oversized body: got %d, want 400", resp.StatusCode)
	}
}

func TestUnknownPath404(t *testing.T) {
	g := newTestGateway(t, "http://127.0.0.1:1", "")
	resp := doJSON(t, baseURL(t, g)+"/v1/other", http.MethodGet, "", nil)
	if resp.StatusCode != http.StatusNotFound {
		t.Errorf("unknown path: got %d, want 404", resp.StatusCode)
	}
}

func TestMethodNotAllowed(t *testing.T) {
	g := newTestGateway(t, "http://127.0.0.1:1", "")
	resp := doJSON(t, baseURL(t, g)+"/v1/messages", http.MethodGet, "", nil)
	if resp.StatusCode != http.StatusMethodNotAllowed {
		t.Errorf("GET /v1/messages: got %d, want 405", resp.StatusCode)
	}
}
