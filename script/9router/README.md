# 9Router Pool Manager

Gerencia um pool de 9Router com 1 Master + N Slaves (1-15) usando Round Robin load balancing.

## Duas stacks nesta pasta

Esta pasta (`script/9router/`) concentra as **duas** stacks do 9Router, cada uma com seu
próprio compose, seu env e seus comandos no Makefile — elas sobem ao mesmo tempo na mesma
rede `sbx-9router` e não se misturam:

| Stack | Compose | Env | Projeto Docker |
|---|---|---|---|
| Federal | `docker-compose.federal.yaml` | `.env.federal` | `9router-federation` |
| Pool (este README) | `docker-compose.pool.yaml` (gerado) | `.env.pool` (gerado) | `9router-pool` |

O nome do projeto está fixado no topo de cada compose (`name:`), então **não** depende do
nome da pasta: os volumes com dados (`9router-federation_*`, `9router-pool_*`) são os mesmos
de antes da fusão. Os comandos do Makefile são prefixados justamente para não se cruzarem:

```bash
make help              # lista todos
make federal-up        # stack federal
make federal-ps
make pool-up           # stack pool
make pool-sync
make pool-unit         # pytest
```

Nomes de arquivo e de alvo citados abaixo usam o prefixo `pool-` quando se referem ao
Makefile.

## Arquitetura

```
┌─────────────────────────────────────────────────────────┐
│                      CLIENTS                            │
│                (OpenCode, Claude, etc.)                  │
└──────────────────────┬──────────────────────────────────┘
                       │ API Key do Master
                       ▼
┌─────────────────────────────────────────────────────────┐
│                  9ROUTER MASTER                         │
│               http://localhost:20228                    │
│                                                         │
│  ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌─────────┐   │
│  │  rs001  │  │  rs002  │  │  rs003  │  │  rs00N  │   │
│  └────┬────┘  └────┬────┘  └────┬────┘  └────┬────┘   │
└───────┼────────────┼────────────┼────────────┼──────────┘
        │            │            │            │
        ▼            ▼            ▼            ▼
┌─────────┐  ┌─────────┐  ┌─────────┐  ┌─────────┐
│ SLAVE-1 │  │ SLAVE-2 │  │ SLAVE-3 │  │ SLAVE-N │
│ :20129  │  │ :20130  │  │ :20131  │  │ :20129+N│
└─────────┘  └─────────┘  └─────────┘  └─────────┘
```

## Pré-requisitos

- Docker + Docker Compose
- Python 3.10+
- `requests` library (`pip install requests`)

## Início Rápido

```bash
# 1. Criar pool básico (1 master + 2 slaves)
python3 pool.py create

# 2. Usar a API key do master nos clientes
```

## Comandos

### pool.py create

Cria pool básico: 1 master + 2 slaves, sobe containers e sincroniza.

```bash
python3 pool.py create
```

**O que faz:**
1. Reseta config.json para 2 slaves
2. Gera `docker-compose.pool.yaml` e `.env.pool`
3. Executa `docker compose up -d --remove-orphans`
4. Aguarda containers ficarem prontos
5. Executa `sync` (health check + combos + providers + API key)

**Opções:**
```bash
python3 pool.py create --dry-run  # Simula sem alterar
```

### pool.py list

Lista a configuração atual do pool.

```bash
python3 pool.py list
```

**Saída:**
```
9ROUTER POOL
─────────────────────────────────────────────────────────────────────
Master: localhost:20228
Slaves: 1
  - rs001 → localhost:20129

Models: 2
  - claude-sonnet-5
  - claude-opus-5

Combos: 2
  - claude-opus-5 (2 models)
  - claude-sonnet-5 (2 models)
```

### pool.py sync

Sincroniza a configuração local com o master. Cria providers, connections, combos, custom models e API keys.

```bash
python3 pool.py sync
```

**O que faz:**
1. Verifica saúde do slave antes de configurar
2. Cria API keys nos slaves
3. Cria combos nos slaves (com verificação)
4. Remove combos existentes no master
5. Cria provider nodes no master
6. Cria provider connections no master
7. Cria custom models no master
8. Cria combos no master
9. Configura round-robin
10. Exporta a API key do master

**Saída importante:**
```
API Key do Master (use nos clientes):
  sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxx

Exemplo OpenCode:
  apiKey: "sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxx"
  baseUrl: "http://localhost:20228/v1"
```

**Opções:**
```bash
python3 pool.py sync --dry-run             # Simular sem alterar
python3 pool.py sync --skip-health-check   # Ignorar validação lenta dos providers
```

### pool.py watch

Inicia o Watchdog do Pool Vivo & Autônomo (Monitoramento continuo, Auto-Discovery de modelos, Quarentena preventiva e Replace de slaves).

```bash
python3 pool.py watch
```

**O que faz:**
1. **Health Check Contínuo**: Verifica a saúde de cada slave a cada `--interval` segundos (padrão: 180s).
2. **Quarentena Preventiva (Circuit Breaker)**: Na 1ª falha ou erro `429 Rate Limit`, remove o slave dos combos do Master sem derrubar o container.
3. **Auto-Healing (Delete + Create)**: Se o erro persistir na 2ª checagem consecutiva (`--failures`), deleta o container e volume do slave (`docker volume rm`) e recria um totalmente novo zerado.
4. **Auto-Discovery & Hot-Reload**: Busca novos modelos gratuitos do OpenCode periodicamente (`--fetch-interval`), atualiza os combos no Master ao vivo sem interromper requisições.
5. **Revalidação de Modelos**: Re-sonda os modelos já presentes nos combos de cada slave a cada `--model-interval` segundos (padrão: 900s / 15min) e sincroniza os combos — quem não responde sai do roteamento, quem voltou é reinserido. O Master não é tocado.

**Opções:**
```bash
python3 pool.py watch --interval 60 --failures 2 --fetch-interval 600 --model-interval 900
```
- `--interval`: Segundos entre checagens de saúde (padrão: 60).
- `--failures`: Falhas consecutivas para acionar o Replace completo (padrão: 2).
- `--fetch-interval`: Segundos entre buscas de modelos free (padrão: 600 / 6h, 0 para desativar).
- `--model-interval`: Segundos entre revalidações dos modelos em uso (padrão: 900 / 15min, 0 para desativar).

Atalho pelo Makefile (a stack federal tem os alvos equivalentes com prefixo `federal-`):

```bash
make pool-watch-fast   # interval=60s, fetch=5min
```

### pool.py test

Testa conectividade com todas as instâncias.

```bash
python3 pool.py test
```

**Saída:**
```
9ROUTER POOL TEST
─────────────────────────────────────────────────────────────────────
master → localhost:20228
  [+] login OK
  [+] rs001 → SAUDÁVEL
  [+] rs002 → SAUDÁVEL
  [+] rs003 → SAUDÁVEL

rs001 → localhost:20129
  [+] login OK

rs002 → localhost:20130
  [+] login OK

rs003 → localhost:20131
  [+] login OK

[+] Todos os 4 instâncias OK
```

### pool.py scale N

Escala o pool para N slaves (1-50).

```bash
python3 pool.py scale 1      # 1 slave (padrão)
python3 pool.py scale 3      # 3 slaves
python3 pool.py scale 6      # 6 slaves
python3 pool.py scale 10     # 10 slaves
python3 pool.py scale 50     # 50 slaves (máximo)
```

**O que faz:**
1. Gera `docker-compose.pool.yaml` com N slaves
2. Gera `.env.pool` com portas (20129-20129+N)
3. Atualiza `config.json`
4. Executa `docker compose up -d --remove-orphans`
5. Remove volumes órfãos ao reduzir
6. Executa `sync`

**Opções:**
```bash
python3 pool.py scale 6 --dry-run    # Simular sem alterar
python3 pool.py scale 6 --no-sync    # Não sincronizar após criar
```

### pool.py slave add

Adiciona um slave manualmente.

```bash
python3 pool.py slave add rs004 localhost:20132 --docker-host 9router-slave-004:20132
```

### pool.py slave delete

Remove um slave.

```bash
python3 pool.py slave delete rs004
```

### pool.py backup

Faz backup da configuração do master.

```bash
python3 pool.py backup
python3 pool.py backup --output backup.json
```

### pool.py clean

Remove tudo: containers, dados, configs. **Não recria nada.**

```bash
python3 pool.py clean           # Remove tudo
python3 pool.py clean --dry-run # Simula sem alterar
```

**O que faz:**
1. `docker compose down -v --remove-orphans` — para e remove todos os containers e volumes
2. Reseta `config.json` para 1 slave
3. Gera `docker-compose.pool.yaml` e `.env.pool` para 1 slave

**Depois rode:** `python3 pool.py create` para recriar ou `python3 pool.py sync` para configurar

## Portas

| Serviço | Porta | Uso |
|---------|-------|-----|
| master | 20228 | API principal + Dashboard |
| slave-001 | 20129 | Backend 1 |
| slave-002 | 20130 | Backend 2 |
| slave-003 | 20131 | Backend 3 |
| slave-NNN | 20129+N | Backend N |

## Endpoints

| Endpoint | URL | Uso |
|----------|-----|-----|
| Master API | `http://localhost:20228/v1` | Clienthttp://localhost:20228/dashboard` | Configuração web |
| Slave API | `http://localhost:20129/v1` | Acesso direto (debug) |

## Configuração

### config.json

```json
{
  "master": {
    "name": "master",
    "host": "localhost:20228",
    "password": "123456"
  },
  "defaults": {
    "models": [
      "claude-sonnet-5",
      "claude-opus-5",
      "kc/openrouter/free",
      "kc/kilo-auto/free"
    ],
    "combos": [
      {
        "name": "claude-opus-5",
        "models": [
          "oc/deepseek-v4-flash-free",
          "oc/mimo-v2.5-free",
          "oc/nemotron-3-ultra-free"
        ]
      }
    ],
    "publish": ["claude-opus-5", "claude-sonnet-5"]
  },
  "slaves": [
    {
      "name": "rs001",
      "host": "localhost:20129",
      "docker_host": "9router-slave-001:20129",
      "password": "123456"
    }
  ]
}
```

### Adicionar Models

Edite `config.json` → `defaults.models`:

```json
"models": [
  "claude-sonnet-5",
  "claude-opus-5",
  "kc/openrouter/free",
  "kc/kilo-auto/free",
  "novo-modelo"
]
```

### Adicionar Combos

Edite `config.json` → `defaults.combos`:

```json
"combos": [
  {
    "name": "novo-combo",
    "models": ["modelo-a", "modelo-b"]
  }
]
```

### Publicar Combos

Edite `config.json` → `defaults.publish`:

```json
"publish": ["claude-opus-5", "claude-sonnet-5", "novo-combo"]
```

## Uso com OpenCode

Após rodar `python3 pool.py create` ou `python3 pool.py sync`, use a API key exportada:

```yaml
# ~/.opencode/config.yaml
providers:
  9router:
    apiKey: "sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxx"
    baseUrl: "http://localhost:20228/v1"
```

## Arquivos

| Arquivo | Descrição |
|---------|-----------|
| `pool.py` | Script principal de gerenciamento (entrypoint CLI) |
| `config.json` | Configuração do pool — **local, gitignored** (`cp config.example.json config.json` num clone novo) |
| `config.example.json` | Mesma topologia, versionada |
| `docker-compose.pool.yaml` | Definição dos containers do pool (gerado) |
| `docker-compose.federal.yaml` | Definição dos containers da stack federal (à mão) |
| `.env.pool` | Variáveis de ambiente do pool (gerado) |
| `.env.federal` | Variáveis de ambiente da stack federal |
| `.env-example` | Modelo dos dois envs |
| `src/server_pool/watch.py` | Módulo Watchdog (monitoramento, quarentena, auto-discovery, auto-healing) |
| `src/server_pool/sync.py` | Lógica de sincronização, hot-reload e replace de slaves |
| `src/server_pool/fetch.py` | Busca e teste de novos modelos gratuitos do OpenCode |

## Troubleshooting

### Provider não conecta ao slave

```bash
python3 pool.py test
```

Verifique se todos os slaves estão SAUDÁVEL.

### Combo não aparece no master

```bash
python3 pool.py sync
```

Re-sincroniza a configuração.

### Containers não iniciam

```bash
docker compose logs 9router-master
docker compose logs 9router-slave-001
```

### Limpar tudo e recomeçar

```bash
python3 pool.py clean
python3 pool.py create
```
