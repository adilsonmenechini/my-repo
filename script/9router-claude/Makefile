# Proxy 9Router-Claude — processos locais, sem Docker
GO    ?= $(shell command -v go 2>/dev/null || echo $(HOME)/.local/go/bin/go)
GOFMT ?= $(shell command -v gofmt 2>/dev/null || echo $(HOME)/.local/go/bin/gofmt)
VENV  := .venv
PY    := $(VENV)/bin/python
UVICORN := $(VENV)/bin/uvicorn
UV    ?= $(shell command -v uv 2>/dev/null || echo $(HOME)/.local/bin/uv)

.PHONY: verify fmt-check vet test test-py build setup up down logs status

## verify: lint + testes (equivalente local do CI)
verify: fmt-check vet test test-py

fmt-check:
	@out="$$($(GOFMT) -l cmd internal)" || { echo "gofmt falhou: $(GOFMT)"; exit 1; }; \
	if [ -n "$$out" ]; then echo "gofmt precisa rodar em:"; echo "$$out"; exit 1; fi

vet:
	$(GO) vet ./...

test:
	$(GO) test -race -count=1 ./...

test-py:
	@py="$(CURDIR)/$(PY)"; \
	if test -x "$$py"; then :; else \
		echo "aviso: venv ausente ($$py) — usando python3 (rode 'make setup')"; py=python3; fi; \
	cd converter && "$$py" test_converter.py

build:
	$(GO) build -o bin/gateway ./cmd/gateway

setup:
	test -d $(VENV) || $(UV) venv $(VENV)
	$(UV) pip install --quiet --python $(PY) -r converter/requirements.txt

# Sobe converter (8080) + gateway (11435) em background, lendo o .env
up: build setup
	@for f in .run/*.pid; do \
		test -f "$$f" || continue; \
		p=$$(cat "$$f" 2>/dev/null); test -n "$$p" || continue; \
		if kill -0 "$$p" 2>/dev/null; then \
			echo "proxy já rodando (pid $$p em $$f) — rode 'make down' antes"; exit 1; \
		fi; \
	done
	@test -f .env || { cp .env.example .env; echo "crie o .env e preencha GATEWAY_UPSTREAM_API_KEY"; exit 1; }
	@mkdir -p .run
	@set -a; . ./.env; set +a; \
	nohup $(UVICORN) app.main:app --app-dir converter --host 127.0.0.1 --port 8080 \
		> .run/converter.log 2>&1 & echo $$! > .run/converter.pid
	@set -a; . ./.env; set +a; \
	nohup ./bin/gateway > .run/gateway.log 2>&1 & echo $$! > .run/gateway.pid
	@for i in $$(seq 1 15); do \
		curl -sf http://127.0.0.1:8080/health >/dev/null 2>&1 && \
		curl -sf http://127.0.0.1:11435/_gateway/health >/dev/null 2>&1 && \
		echo "proxy pronto: http://127.0.0.1:11435" && exit 0; \
		sleep 1; done; \
	echo "FALHA ao subir — veja .run/*.log:"; tail -n 30 .run/*.log; \
	$(MAKE) --no-print-directory down; exit 1

down:
	@ours() { case "$$(ps -o args= -p "$$1" 2>/dev/null)" in \
		*bin/gateway*|*uvicorn*) return 0 ;; \
		*) return 1 ;; \
	esac; }; \
	for f in .run/*.pid; do \
		test -f "$$f" || continue; \
		p=$$(cat "$$f" 2>/dev/null); test -n "$$p" || continue; \
		c=$$(ps -o args= -p "$$p" 2>/dev/null); \
		if test -z "$$c"; then echo "pid $$p já não existe (stale)"; \
		elif ours "$$p"; then kill "$$p" 2>/dev/null || true; echo "parando pid $$p: $$c"; \
		else echo "ignorando pid $$p (não é nosso): $$c"; fi; \
	done; \
	for i in $$(seq 1 12); do \
		live=""; \
		for f in .run/*.pid; do \
			test -f "$$f" || continue; \
			p=$$(cat "$$f" 2>/dev/null); \
			if test -n "$$p" && ours "$$p" && kill -0 "$$p" 2>/dev/null; then live=1; fi; \
		done; \
		test -n "$$live" || break; \
		sleep 0.5; \
	done; \
	for f in .run/*.pid; do \
		test -f "$$f" || continue; \
		p=$$(cat "$$f" 2>/dev/null); \
		if test -n "$$p" && ours "$$p" && kill -0 "$$p" 2>/dev/null; then \
			echo "pid $$p não parou — SIGKILL"; kill -9 "$$p" 2>/dev/null || true; \
		fi; \
	done; \
	rm -rf .run; \
	echo "proxy parado"

logs:
	@tail -n 50 -f .run/*.log

status:
	@echo -n "gateway:   "; curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:11435/_gateway/health
	@echo -n "converter: "; curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8080/health
