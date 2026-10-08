package gateway

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

type Upstream struct {
	Name       string   `yaml:"name"`
	BaseURL    string   `yaml:"base_url"`
	APIKey     string   `yaml:"api_key"`
	Models     []string `yaml:"models"`
	Default    bool     `yaml:"default"`
	Timeout    int      `yaml:"timeout"` // seconds
	readyUntil time.Time
}

const (
	defaultUpstreamsPath = "upstreams.yaml"
	defaultModelName     = "claude-sonnet-5"
)

type UpstreamsConfig struct {
	Upstreams    []Upstream `yaml:"upstreams"`
	DefaultModel string     `yaml:"default_model"`
	Gateway      GatewayCfg `yaml:"gateway"`
}

type GatewayCfg struct {
	ListenAddr       string `yaml:"listen_addr"`
	HealthPath       string `yaml:"health_path"`
	MaxRequestBodyMB int    `yaml:"max_request_body_mb"`
}

func LoadUpstreamsConfig(path string) (*UpstreamsConfig, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("read config: %w", err)
	}

	// Expand environment variables
	expanded := os.ExpandEnv(string(data))

	var cfg UpstreamsConfig
	if err := yaml.Unmarshal([]byte(expanded), &cfg); err != nil {
		return nil, fmt.Errorf("parse yaml: %w", err)
	}

	// Validate
	if len(cfg.Upstreams) == 0 {
		return nil, errors.New("at least one upstream required")
	}

	defaultCount := 0
	for i := range cfg.Upstreams {
		u := &cfg.Upstreams[i]
		if strings.TrimSpace(u.BaseURL) == "" {
			return nil, fmt.Errorf("upstream %q: base_url is required", u.Name)
		}
		if len(u.Models) == 0 {
			return nil, fmt.Errorf("upstream %q: at least one model required", u.Name)
		}
		if u.Default {
			defaultCount++
		}
		if u.Timeout <= 0 {
			u.Timeout = 120
		}
		// Normalize base_url
		u.BaseURL = strings.TrimRight(u.BaseURL, "/")
	}

	if defaultCount == 0 {
		cfg.Upstreams[0].Default = true
	} else if defaultCount > 1 {
		return nil, errors.New("only one upstream can be default")
	}

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

	if strings.TrimSpace(cfg.DefaultModel) == "" {
		cfg.DefaultModel = cfg.Upstreams[0].Models[0]
	}

	if strings.TrimSpace(cfg.Gateway.ListenAddr) == "" {
		cfg.Gateway.ListenAddr = "127.0.0.1:11435"
	}
	if strings.TrimSpace(cfg.Gateway.HealthPath) == "" {
		cfg.Gateway.HealthPath = "/_gateway/health"
	}
	if cfg.Gateway.MaxRequestBodyMB <= 0 {
		cfg.Gateway.MaxRequestBodyMB = 64
	}

	return &cfg, nil
}

// FindUpstreamForModel retorna o upstream que serve o modelo solicitado.
// Se não encontrar, retorna o upstream default.
func (c *UpstreamsConfig) FindUpstreamForModel(model string) *Upstream {
	for i := range c.Upstreams {
		u := &c.Upstreams[i]
		for _, m := range u.Models {
			if m == model {
				return u
			}
		}
	}
	// Fallback: default upstream
	for i := range c.Upstreams {
		if c.Upstreams[i].Default {
			return &c.Upstreams[i]
		}
	}
	return &c.Upstreams[0]
}

func (c *UpstreamsConfig) AllModels() []string {
	seen := make(map[string]bool)
	var result []string
	for _, u := range c.Upstreams {
		for _, m := range u.Models {
			if !seen[m] {
				seen[m] = true
				result = append(result, m)
			}
		}
	}
	return result
}

// DefaultUpstream returns the default upstream
func (c *UpstreamsConfig) DefaultUpstream() *Upstream {
	for i := range c.Upstreams {
		if c.Upstreams[i].Default {
			return &c.Upstreams[i]
		}
	}
	return &c.Upstreams[0]
}

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
