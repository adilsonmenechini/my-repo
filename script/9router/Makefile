# 9Router — duas stacks no mesmo diretório, comandos separados.
#
#   federal: docker-compose.federal.yaml + .env.federal  (rede sbx-9router)
#   pool:    docker-compose.pool.yaml    + .env.pool     (rede sbx-9router)
#
# Cada stack tem seu próprio projeto Docker (`name:` dentro do compose), então
# subir uma não toca na outra. Os alvos são prefixados justamente para isso:
# `federal-*` só opera a stack federal, `pool-*` só a do pool.

FEDERAL := docker compose -f docker-compose.federal.yaml --env-file .env.federal
POOLCM  := docker compose -f docker-compose.pool.yaml --env-file .env.pool
POOL    := python3 pool.py
# Python da venv quando existe (pytest/requests); senão o do sistema.
PY      := $(if $(wildcard .venv/bin/python),.venv/bin/python,python3)

.DEFAULT_GOAL := help

.PHONY: help \
	federal-up federal-down federal-down-v federal-build federal-logs federal-ps federal-start \
	pool-up pool-down pool-down-v pool-build pool-logs pool-ps pool-start \
	pool-list pool-test pool-sync pool-watch pool-watch-fast pool-create pool-scale \
	pool-backup pool-clean pool-slave-add pool-slave-delete pool-unit

help:
	@echo "9Router — federal + pool (pasta única)"
	@echo ""
	@echo "Stack federal (docker-compose.federal.yaml):"
	@echo "  make federal-up       sobe (build + up -d)"
	@echo "  make federal-down     para, mantendo os volumes"
	@echo "  make federal-down-v   para e APAGA os volumes (dados em /app/data)"
	@echo "  make federal-build    build das imagens"
	@echo "  make federal-ps       status dos containers"
	@echo "  make federal-logs     logs em foreground"
	@echo "  make federal-start    down + build + up"
	@echo ""
	@echo "Stack pool (docker-compose.pool.yaml + pool.py):"
	@echo "  make pool-up          sobe (build + up -d)"
	@echo "  make pool-down        para, mantendo os volumes"
	@echo "  make pool-down-v      para e APAGA os volumes (dados em /app/data)"
	@echo "  make pool-build       build das imagens"
	@echo "  make pool-ps          status dos containers"
	@echo "  make pool-logs        logs em foreground"
	@echo "  make pool-start       down + build + up"
	@echo "  make pool-list        instancias do config.json"
	@echo "  make pool-test        conectividade com as instancias"
	@echo "  make pool-sync        sincroniza master/slaves"
	@echo "  make pool-watch       watchdog"
	@echo "  make pool-watch-fast  watchdog (saude 60s, auto-discovery 5min)"
	@echo "  make pool-create      pool base: 1 master + rs000 + 2 slaves"
	@echo "  make pool-scale N=6   escala os slaves regulares"
	@echo "  make pool-backup      backup do estado do master"
	@echo "  make pool-clean       derruba o pool e libera volumes do pool"
	@echo "  make pool-unit        testes unitarios (pytest)"
	@echo "  make pool-slave-add NAME=rs003 HOST=localhost:20132"
	@echo "  make pool-slave-delete NAME=rs003"

# ============================================================================
# Stack federal
# ============================================================================

federal-up:
	$(FEDERAL) up -d --build

federal-down:
	$(FEDERAL) down

federal-down-v:
	$(FEDERAL) down -v

federal-build:
	$(FEDERAL) build

federal-logs:
	$(FEDERAL) logs -f

federal-ps:
	$(FEDERAL) ps

federal-start: federal-down federal-build federal-up

# ============================================================================
# Stack pool — containers
# ============================================================================

pool-up:
	$(POOLCM) up -d --build

pool-down:
	$(POOLCM) down

pool-down-v:
	$(POOLCM) down -v

pool-build:
	$(POOLCM) build

pool-logs:
	$(POOLCM) logs -f

pool-ps:
	$(POOLCM) ps

pool-start: pool-down pool-build pool-up

# ============================================================================
# Stack pool — pool.py
# ============================================================================

pool-list:
	$(POOL) list

pool-test:
	$(POOL) test

pool-sync:
	$(POOL) sync

pool-watch:
	$(POOL) watch

# Saúde a cada 60s, auto-discovery a cada 5min.
pool-watch-fast:
	$(POOL) watch --interval 60 --fetch-interval 300

pool-create:
	$(POOL) create

pool-scale:
	$(POOL) scale $(N)

pool-backup:
	$(POOL) backup

pool-unit:
	$(PY) -m pytest -q

# O filtro é `9router-pool_` (e não `9router`): `name=9router` casa também com
# os volumes da stack federal e apagaria os dados dela.
pool-clean:
	$(POOL) clean
	@volumes=$$(docker volume ls --filter name=9router-pool_ -q 2>/dev/null); \
	if [ -n "$$volumes" ]; then \
		docker volume rm -f $$volumes; \
	fi

pool-slave-add:
	$(POOL) slave add $(NAME) $(HOST)

pool-slave-delete:
	$(POOL) slave delete $(NAME)
