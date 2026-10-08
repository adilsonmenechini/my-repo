package main

import (
	"context"
	"fmt"
	"log/slog"
	"os"
	"os/signal"
	"syscall"
	"time"

	"9router-claude/internal/gateway"
)

func main() {
	if err := run(); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}

func run() error {
	cfg := gateway.Config{
		ListenAddr:   envOr("GATEWAY_LISTEN_ADDR", "127.0.0.1:11435"),
		ConverterURL: os.Getenv("CONVERTER_URL"),
		APIKey:       os.Getenv("GATEWAY_UPSTREAM_API_KEY"),
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

func envOr(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}
