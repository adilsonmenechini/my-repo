package gateway

import (
	"bytes"
	"log/slog"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func writeConfig(t *testing.T, body string) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "upstreams.yaml")
	if err := os.WriteFile(path, []byte(body), 0o600); err != nil {
		t.Fatalf("write config: %v", err)
	}
	return path
}

func TestLoadUpstreamsExpandsEnv(t *testing.T) {
	t.Setenv("NINEROUTER_API_KEY", "sk-from-env")
	path := writeConfig(t, `
upstreams:
  - name: primary
    base_url: http://127.0.0.1:20338/v1
    api_key: ${NINEROUTER_API_KEY}
    models: [claude-sonnet-5]
    default: true
default_model: claude-sonnet-5
`)
	cfg, err := LoadUpstreamsConfig(path)
	if err != nil {
		t.Fatalf("LoadUpstreamsConfig: %v", err)
	}
	if got := cfg.Upstreams[0].APIKey; got != "sk-from-env" {
		t.Errorf("api_key not expanded: got %q", got)
	}
	if cfg.Upstreams[0].BaseURL != "http://127.0.0.1:20338/v1" {
		t.Errorf("base_url trailing slash not trimmed: %q", cfg.Upstreams[0].BaseURL)
	}
}

func TestFindUpstreamForModel(t *testing.T) {
	path := writeConfig(t, `
upstreams:
  - name: primary
    base_url: http://127.0.0.1:20338/v1
    api_key: k1
    models: [claude-sonnet-5, sec/gemini/gemini-3.8-flash]
    default: true
  - name: openai
    base_url: https://api.openai.com/v1
    api_key: k2
    models: [gpt-4o]
    default: false
default_model: claude-sonnet-5
`)
	cfg, err := LoadUpstreamsConfig(path)
	if err != nil {
		t.Fatalf("LoadUpstreamsConfig: %v", err)
	}

	cases := map[string]string{
		"claude-sonnet-5":             "primary",
		"sec/gemini/gemini-3.8-flash": "primary",
		"gpt-4o":                      "openai",
		"modelo-desconhecido":         "primary",
	}
	for model, want := range cases {
		if got := cfg.FindUpstreamForModel(model).Name; got != want {
			t.Errorf("FindUpstreamForModel(%q) = %q, want %q", model, got, want)
		}
	}
}

func TestConfigRequiresOneDefault(t *testing.T) {
	path := writeConfig(t, `
upstreams:
  - name: a
    base_url: http://a/v1
    api_key: k
    models: [m1]
  - name: b
    base_url: http://b/v1
    api_key: k
    models: [m2]
default_model: m1
`)
	cfg, err := LoadUpstreamsConfig(path)
	if err != nil {
		t.Fatalf("LoadUpstreamsConfig: %v", err)
	}
	if !cfg.Upstreams[0].Default {
		t.Error("first upstream should be promoted to default when none is marked")
	}
	if cfg.Upstreams[1].Default {
		t.Error("second upstream should not be default")
	}
}

func TestConfigRejectsMissingBaseURL(t *testing.T) {
	path := writeConfig(t, `
upstreams:
  - name: broken
    api_key: k
    models: [m1]
    default: true
default_model: m1
`)
	if _, err := LoadUpstreamsConfig(path); err == nil {
		t.Fatal("expected error for upstream without base_url")
	}
}

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
	t.Setenv("GATEWAY_LISTEN_ADDR", "127.0.0.1:19998")
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
	if cfg.Gateway.ListenAddr != "127.0.0.1:19998" {
		t.Errorf("ListenAddr: got %q, want env value 127.0.0.1:19998 in the synthetic path", cfg.Gateway.ListenAddr)
	}
}

func TestResolveUpstreamsSyntheticRequiresAPIKey(t *testing.T) {
	t.Setenv("UPSTREAMS_CONFIG", "")
	t.Setenv("UPSTREAM_BASE_URL", "http://127.0.0.1:20338/v1")
	t.Setenv("GATEWAY_UPSTREAM_API_KEY", "")
	t.Chdir(t.TempDir())

	if _, err := ResolveUpstreams(); err == nil {
		t.Fatal("expected error when the synthesized default upstream has no api_key")
	}
}

func TestResolveUpstreamsExplicitPathMissingIsAnError(t *testing.T) {
	t.Setenv("UPSTREAMS_CONFIG", filepath.Join(t.TempDir(), "nao-existe.yaml"))
	if _, err := ResolveUpstreams(); err == nil {
		t.Fatal("expected error for an explicit UPSTREAMS_CONFIG path that does not exist")
	}
}
