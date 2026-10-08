// Package gateway é o shell local (loopback-only) que expõe a API Anthropic
// para Claude Code / Claude Desktop e encaminha ao conversor Python.
// Adaptado de ollama/ollama internal/proxy/claude_desktop.go (MIT).
package gateway

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"net"
	"net/http"
	"net/http/httputil"
	"net/url"
	"strings"
	"sync"
	"sync/atomic"
	"time"
)

const (
	HealthPath   = "/_gateway/health"
	HealthHeader = "X-9Router-Claude-Gateway"

	defaultListenAddr = "127.0.0.1:11435"
	defaultConverter  = "http://127.0.0.1:8080"
	defaultReadyWait  = 10 * time.Second
	defaultReadyPoll  = 100 * time.Millisecond
	defaultReadyTTL   = 5 * time.Second

	unsupportedImageNotice = "[Image omitted by 9router gateway because the model does not support image recognition.]"
)

// maxRequestBodyBytes é variável (não const) para o teste poder reduzi-lo.
var maxRequestBodyBytes = 64 << 20

const maxErrorBodyBytes = 64 << 10

type Config struct {
	// ListenAddr é o endereço do gateway (GATEWAY_LISTEN_ADDR). Padrão 127.0.0.1:11435.
	ListenAddr string
	// ConverterURL é o conversor Python (CONVERTER_URL). Padrão http://127.0.0.1:8080.
	ConverterURL string
	// APIKey é a credencial do 9Router (GATEWAY_UPSTREAM_API_KEY), injetada no upstream.
	APIKey string
	// Upstreams é a fonte de upstreams (YAML/env). Nil ⇒ sintetizado de APIKey/DefaultModel.
	Upstreams *UpstreamsConfig
	// DefaultModel é injetado quando o request não traz "model".
	DefaultModel string
	Logger       *slog.Logger

	// Overrides de readiness (0 = padrão). Usados pelos testes.
	ReadyWait time.Duration
	ReadyPoll time.Duration
	ReadyTTL  time.Duration
}

type readinessCheck struct {
	done chan struct{}
	err  error
}

// Gateway é um reverse proxy loopback-only: Claude encanta o protocolo aqui,
// então não há TLS nem mudança de confiança do sistema.
type Gateway struct {
	listenAddr   string
	converterURL *url.URL
	proxy        *httputil.ReverseProxy
	transport    *http.Transport
	logger       *slog.Logger
	apiKey       string
	upstreams    *UpstreamsConfig
	readyWait    time.Duration
	readyPoll    time.Duration
	readyTTL     time.Duration
	routed       atomic.Uint64
	shutdown     chan struct{}
	shutdownOnce sync.Once

	readyMu    sync.Mutex
	readyUntil time.Time
	readyCheck *readinessCheck

	mu       sync.Mutex
	listener net.Listener
	server   *http.Server
}

func New(cfg Config) (*Gateway, error) {
	if strings.TrimSpace(cfg.ListenAddr) == "" {
		cfg.ListenAddr = defaultListenAddr
	}
	if strings.TrimSpace(cfg.ConverterURL) == "" {
		return nil, errors.New("converter URL is required (CONVERTER_URL)")
	}
	convURL, err := url.Parse(cfg.ConverterURL)
	if err != nil {
		return nil, fmt.Errorf("parse converter URL: %w", err)
	}
	if convURL.Scheme == "" || convURL.Host == "" {
		return nil, fmt.Errorf("invalid converter URL %q", cfg.ConverterURL)
	}

	if cfg.Upstreams == nil {
		cfg.Upstreams = syntheticUpstreams("", cfg.APIKey, cfg.DefaultModel)
	}

	logger := cfg.Logger
	if logger == nil {
		logger = slog.Default()
	}

	g := &Gateway{
		listenAddr:   cfg.ListenAddr,
		converterURL: convURL,
		logger:       logger,
		apiKey:       cfg.APIKey,
		upstreams:    cfg.Upstreams,
		shutdown:     make(chan struct{}),
		readyWait:    orDefault(cfg.ReadyWait, defaultReadyWait),
		readyPoll:    orDefault(cfg.ReadyPoll, defaultReadyPoll),
		readyTTL:     orDefault(cfg.ReadyTTL, defaultReadyTTL),
	}

	transport := http.DefaultTransport.(*http.Transport).Clone()
	transport.Proxy = nil // loopback direto: nunca proxy de sistema
	g.transport = transport

	rp := httputil.NewSingleHostReverseProxy(convURL)
	rp.Transport = transport
	// Flush negativo = flush imediato por chunk; SSE do Claude não pode ficar buffering.
	rp.FlushInterval = -1
	rp.ErrorHandler = func(w http.ResponseWriter, r *http.Request, err error) {
		g.markUpstreamNotReady()
		logger.Warn("upstream request failed", "path", r.URL.Path, "error", err)
		http.Error(w, "9router gateway unavailable", http.StatusBadGateway)
	}
	rp.ModifyResponse = func(resp *http.Response) error {
		if retried, err := g.retryWithoutUnsupportedImages(resp); err != nil {
			logger.Warn("image fallback failed", "path", resp.Request.URL.Path, "error", err)
		} else if retried {
			logger.Debug("retried request without unsupported images", "path", resp.Request.URL.Path)
		}
		return nil
	}
	g.proxy = rp
	return g, nil
}

func orDefault(v, d time.Duration) time.Duration {
	if v == 0 {
		return d
	}
	return v
}

func (g *Gateway) Start() error {
	g.mu.Lock()
	defer g.mu.Unlock()
	if g.listener != nil {
		return errors.New("gateway already started")
	}
	listener, err := net.Listen("tcp", g.listenAddr)
	if err != nil {
		return fmt.Errorf("listen for gateway: %w", err)
	}
	g.listener = listener
	g.server = &http.Server{Handler: g, ReadHeaderTimeout: 10 * time.Second}
	go func() {
		if err := g.server.Serve(listener); err != nil && !errors.Is(err, http.ErrServerClosed) {
			g.logger.Warn("gateway stopped", "error", err)
		}
	}()
	g.logger.Info("gateway started", "address", listener.Addr().String())
	return nil
}

func (g *Gateway) Close(ctx context.Context) error {
	g.shutdownOnce.Do(func() { close(g.shutdown) })
	g.mu.Lock()
	server := g.server
	g.mu.Unlock()
	var err error
	if server != nil {
		err = server.Shutdown(ctx)
	}
	g.transport.CloseIdleConnections()
	return err
}

func (g *Gateway) Addr() string {
	g.mu.Lock()
	defer g.mu.Unlock()
	if g.listener == nil {
		return ""
	}
	return g.listener.Addr().String()
}

func (g *Gateway) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	if !g.allowsHost(r.Host) {
		http.Error(w, "forbidden", http.StatusForbidden)
		return
	}
	// Cliente nativo (Claude Code/Desktop) não manda Origin; qualquer Origin é
	// tentativa de browser → não habilita CORS neste gateway loopback.
	if r.Header.Get("Origin") != "" {
		http.Error(w, "forbidden", http.StatusForbidden)
		return
	}

	if r.URL.Path == HealthPath {
		if r.Method != http.MethodGet {
			w.Header().Set("Allow", http.MethodGet)
			http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
			return
		}
		w.Header().Set(HealthHeader, "1")
		w.WriteHeader(http.StatusNoContent)
		return
	}

	switch r.URL.Path {
	case "/health": // repassado ao conversor (healthcheck do compose/test_proxy)
		g.forward(w, r, nil)
	case "/v1/models":
		if r.Method != http.MethodGet {
			w.Header().Set("Allow", http.MethodGet)
			http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
			return
		}
		g.forward(w, r, nil)
	case "/v1/messages/count_tokens":
		if r.Method != http.MethodPost {
			w.Header().Set("Allow", http.MethodPost)
			http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
			return
		}
		g.serveCountTokens(w, r)
	case "/v1/messages":
		if r.Method != http.MethodPost {
			w.Header().Set("Allow", http.MethodPost)
			http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
			return
		}
		g.serveMessage(w, r)
	default:
		http.NotFound(w, r)
	}
}

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

// serveCountTokens estima tokens localmente (≈4 bytes/token), como o
// EstimateCountTokens do Ollama — sem depender do conversor.
func (g *Gateway) serveCountTokens(w http.ResponseWriter, r *http.Request) {
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
}

// estimateCountTokens soma os bytes de todas as strings do payload e divide
// por 4 (heurística UTF-8 ≈ 4 bytes/token), com mínimo 1.
func estimateCountTokens(body []byte) int {
	var tree any
	if err := json.Unmarshal(body, &tree); err != nil {
		return 1
	}
	total := stringBytes(tree)
	tokens := (total + 3) / 4
	if tokens < 1 {
		return 1
	}
	return tokens
}

func stringBytes(v any) int {
	switch t := v.(type) {
	case string:
		return len(t)
	case float64, bool, nil:
		return 0
	case []any:
		n := 0
		for _, item := range t {
			n += stringBytes(item)
		}
		return n
	case map[string]any:
		n := 0
		for _, item := range t {
			n += stringBytes(item)
		}
		return n
	default:
		return 0
	}
}

// allowsHost aceita somente o próprio listener via localhost/loopback
// (espelha allowsHost do claude_desktop.go).
func (g *Gateway) allowsHost(host string) bool {
	_, listenerPort, err := net.SplitHostPort(g.Addr())
	if err != nil {
		return false
	}
	host, port, err := net.SplitHostPort(host)
	if err != nil || port != listenerPort {
		return false
	}
	if strings.EqualFold(host, "localhost") {
		return true
	}
	ip := net.ParseIP(host)
	return ip != nil && ip.IsLoopback()
}

// --- readiness -------------------------------------------------------------

func (g *Gateway) waitForUpstream(ctx context.Context) error {
	g.readyMu.Lock()
	if time.Now().Before(g.readyUntil) {
		g.readyMu.Unlock()
		return nil
	}
	check := g.readyCheck
	if check == nil {
		check = &readinessCheck{done: make(chan struct{})}
		g.readyCheck = check
		go g.runUpstreamReadinessCheck(check)
	}
	g.readyMu.Unlock()

	select {
	case <-ctx.Done():
		return ctx.Err()
	case <-check.done:
		return check.err
	}
}

func (g *Gateway) runUpstreamReadinessCheck(check *readinessCheck) {
	ctx, cancel := context.WithTimeout(context.Background(), g.readyWait)
	defer cancel()
	ticker := time.NewTicker(g.readyPoll)
	defer ticker.Stop()

	var err error
loop:
	for {
		select {
		case <-g.shutdown:
			err = errors.New("gateway stopped")
			break loop
		default:
		}

		dialer := &net.Dialer{Timeout: g.readyPoll}
		conn, dialErr := dialer.DialContext(ctx, "tcp", g.converterURL.Host)
		if dialErr == nil {
			_ = conn.Close()
			break
		}

		select {
		case <-g.shutdown:
			err = errors.New("gateway stopped")
			break loop
		case <-ctx.Done():
			err = fmt.Errorf("timed out waiting for %s", g.converterURL.Host)
			break loop
		case <-ticker.C:
		}
	}

	g.readyMu.Lock()
	if err == nil {
		g.readyUntil = time.Now().Add(g.readyTTL)
	}
	check.err = err
	if g.readyCheck == check {
		g.readyCheck = nil
	}
	close(check.done)
	g.readyMu.Unlock()
}

func (g *Gateway) markUpstreamNotReady() {
	g.readyMu.Lock()
	g.readyUntil = time.Time{}
	g.readyMu.Unlock()
}

// --- retry de imagens ------------------------------------------------------

// retryWithoutUnsupportedImages: se o upstream recusar imagens (400), troca os
// blocos image por texto e refaz a chamada uma vez (claude_desktop.go).
func (g *Gateway) retryWithoutUnsupportedImages(response *http.Response) (bool, error) {
	if response.StatusCode != http.StatusBadRequest ||
		response.Request.Method != http.MethodPost ||
		response.Request.URL.Path != "/v1/messages" ||
		response.Request.GetBody == nil {
		return false, nil
	}
	if !responseRejectsImages(response) {
		return false, nil
	}

	body, err := response.Request.GetBody()
	if err != nil {
		return false, fmt.Errorf("replay request: %w", err)
	}
	defer body.Close()
	rewritten, err := io.ReadAll(io.LimitReader(body, int64(maxRequestBodyBytes)+1))
	if err != nil {
		return false, fmt.Errorf("read replayable request: %w", err)
	}
	if len(rewritten) > maxRequestBodyBytes {
		return false, nil
	}

	var payload map[string]json.RawMessage
	if err := json.Unmarshal(rewritten, &payload); err != nil {
		return false, nil
	}
	changed, err := replaceUnsupportedImages(payload)
	if err != nil || !changed {
		return false, err
	}
	rewritten, err = json.Marshal(payload)
	if err != nil {
		return false, fmt.Errorf("encode sanitized request: %w", err)
	}

	retry := response.Request.Clone(response.Request.Context())
	setRequestBody(retry, rewritten)
	retryResponse, err := g.transport.RoundTrip(retry)
	if err != nil {
		if retryResponse != nil && retryResponse.Body != nil {
			_ = retryResponse.Body.Close()
		}
		return false, fmt.Errorf("retry request: %w", err)
	}
	_ = response.Body.Close()
	*response = *retryResponse
	return true, nil
}

func responseRejectsImages(response *http.Response) bool {
	if response.Body == nil {
		return false
	}
	body := response.Body
	prefix, err := io.ReadAll(io.LimitReader(body, maxErrorBodyBytes+1))
	response.Body = struct {
		io.Reader
		io.Closer
	}{Reader: io.MultiReader(bytes.NewReader(prefix), body), Closer: body}
	if err != nil || len(prefix) > maxErrorBodyBytes {
		return false
	}
	var errResp struct {
		Type  string `json:"type"`
		Error struct {
			Type    string `json:"type"`
			Message string `json:"message"`
		} `json:"error"`
	}
	if err := json.Unmarshal(prefix, &errResp); err != nil ||
		errResp.Type != "error" || errResp.Error.Type != "invalid_request_error" {
		return false
	}
	lower := strings.ToLower(errResp.Error.Message)
	mentionsImages := strings.Contains(lower, "image") || strings.Contains(lower, "vision")
	unsupported := strings.Contains(lower, "does not support") ||
		strings.Contains(lower, "not support") ||
		strings.Contains(lower, "unsupported")
	return mentionsImages && unsupported
}

func replaceUnsupportedImages(payload map[string]json.RawMessage) (bool, error) {
	rawMessages, ok := payload["messages"]
	if !ok {
		return false, nil
	}
	var messages []json.RawMessage
	if err := json.Unmarshal(rawMessages, &messages); err != nil {
		return false, fmt.Errorf("decode messages for image fallback: %w", err)
	}

	changed := false
	for i, rawMessage := range messages {
		var message map[string]json.RawMessage
		if err := json.Unmarshal(rawMessage, &message); err != nil {
			continue
		}
		content, ok := message["content"]
		if !ok {
			continue
		}
		rewritten, contentChanged, err := replaceImagesInContent(content)
		if err != nil {
			return false, err
		}
		if !contentChanged {
			continue
		}
		message["content"] = rewritten
		messages[i], err = json.Marshal(message)
		if err != nil {
			return false, fmt.Errorf("encode sanitized message: %w", err)
		}
		changed = true
	}
	if !changed {
		return false, nil
	}
	rewritten, err := json.Marshal(messages)
	if err != nil {
		return false, fmt.Errorf("encode sanitized messages: %w", err)
	}
	payload["messages"] = rewritten
	return true, nil
}

func replaceImagesInContent(content json.RawMessage) (json.RawMessage, bool, error) {
	var blocks []json.RawMessage
	if err := json.Unmarshal(content, &blocks); err != nil {
		var text string
		if json.Unmarshal(content, &text) == nil {
			return content, false, nil
		}
		return content, false, fmt.Errorf("decode content for image fallback: %w", err)
	}

	changed := false
	for i, raw := range blocks {
		var block struct {
			Type    string          `json:"type"`
			Content json.RawMessage `json:"content"`
		}
		if err := json.Unmarshal(raw, &block); err != nil {
			continue
		}
		switch block.Type {
		case "image":
			replacement, err := json.Marshal(struct {
				Type string `json:"type"`
				Text string `json:"text"`
			}{Type: "text", Text: unsupportedImageNotice})
			if err != nil {
				return nil, false, fmt.Errorf("encode image notice: %w", err)
			}
			blocks[i] = replacement
			changed = true
		case "tool_result":
			rewritten, contentChanged, err := replaceImagesInContent(block.Content)
			if err != nil {
				return nil, false, err
			}
			if !contentChanged {
				continue
			}
			var fields map[string]json.RawMessage
			if err := json.Unmarshal(raw, &fields); err != nil {
				continue
			}
			fields["content"] = rewritten
			blocks[i], err = json.Marshal(fields)
			if err != nil {
				return nil, false, fmt.Errorf("encode sanitized tool_result: %w", err)
			}
			changed = true
		}
	}
	if !changed {
		return content, false, nil
	}
	rewritten, err := json.Marshal(blocks)
	if err != nil {
		return nil, false, fmt.Errorf("encode sanitized content: %w", err)
	}
	return rewritten, true, nil
}

// --- helpers ---------------------------------------------------------------

func writeAnthropicError(w http.ResponseWriter, status int, errType, message string) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(map[string]any{
		"type":  "error",
		"error": map[string]string{"type": errType, "message": message},
	})
}

func readRequestBody(r *http.Request) ([]byte, error) {
	if r.Body == nil {
		return nil, errors.New("request body is required")
	}
	body, err := io.ReadAll(io.LimitReader(r.Body, int64(maxRequestBodyBytes)+1))
	if err != nil {
		return nil, fmt.Errorf("read request: %w", err)
	}
	if len(body) > maxRequestBodyBytes {
		return nil, fmt.Errorf("request exceeds %d bytes", maxRequestBodyBytes)
	}
	return body, nil
}

func setRequestBody(r *http.Request, body []byte) {
	_ = r.Body.Close()
	r.Body = io.NopCloser(bytes.NewReader(body))
	r.GetBody = func() (io.ReadCloser, error) {
		return io.NopCloser(bytes.NewReader(body)), nil
	}
	r.ContentLength = int64(len(body))
	r.Header.Set("Content-Length", fmt.Sprintf("%d", len(body)))
}
