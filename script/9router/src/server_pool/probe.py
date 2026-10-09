"""Motor de probe de modelos: paralelo, com cache TTL e quarentena por instância.

Concentra o que vivia duplicado e divergente entre `sync.py` (validação de
combo) e `fetch.py` (descoberta de modelos free). `fetch` e `sync` importam
daqui — este módulo não importa de nenhum dos dois.

Duas memórias, papéis diferentes:

- ``model-state.json``   → cache TTL do último resultado por modelo. Evita
  refazer a mesma completion a cada ciclo do watchdog.
- ``model-failures.json`` → quarentena de falha. Evita re-sondar quem já
  falhou de forma determinística, com prazo por classe de erro.

Ambas são gravadas com o escopo ``instance.host`` por cima do id do modelo.
Não é formalidade: no pool cada slave tem seus próprios combos, API key e
cota — uma cota estourada no rs001 não pode esconder o modelo no rs002.
Arquivos no formato legado (id do modelo direto na raiz) continuam sendo
lidos e são regravados aninhados no próximo save.
"""

from __future__ import annotations

import json
import re
import secrets
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

from .config import BASE_DIR
from .console import warning
from .models import Instance

# Quantos probes rodam ao mesmo tempo. Cada um é uma completion real no tier
# free — paralelo o bastante para derrubar o pior caso de N×timeout, contido
# o bastante para não estourar a cota de uma vez.
PROBE_WORKERS = 6

# Validade do cache por modelo. O watchdog revalida a cada 15min, então metade
# dos ciclos nem vira request.
PROBE_TTL_SECONDS = 30 * 60

# Timeout de um probe individual.
PROBE_TIMEOUT = 60

PROBE_STATE_FILE = BASE_DIR / "model-state.json"
PROBE_FAILURES_FILE = BASE_DIR / "model-failures.json"

# Sufixo que marca o tier free... exceto estes, que são free pela tabela
# oficial de preços do Zen mas não têm o sufixo.
EXTRA_FREE_IDS = frozenset({"big-pickle"})

# O Zen gateia os modelos free: só responde 200 para quem parece o cliente
# OpenCode oficial (issue #4101). UA sem versão → 403, versão < 1.17 → 426;
# session fora do formato ses_+12hex+14base62 → 403. O formato do session é
# o que o cliente oficial envia em x-opencode-session.
PROBE_UA = "opencode/2.0.10/cli"
PROBE_CLIENT = "cli"
PROBE_SESSION_ALPHABET = (
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
)


# --------------------------------------------------------------------------
# IDENTIDADE DO MODELO
# --------------------------------------------------------------------------


def bare_model_id(model_id: str) -> str:
    """Remove o prefixo do provider: 'oc/mimo-v2.5-free' → 'mimo-v2.5-free'."""
    return str(model_id).rsplit("/", 1)[-1].strip()


def bare_ids(model_ids: Any) -> set[str]:
    """Normaliza uma lista de ids (com ou sem prefixo) para ids sem provider."""
    if not model_ids:
        return set()
    return {bare_model_id(m) for m in model_ids if str(m).strip()}


def is_free_model(model_id: str) -> bool:
    """Regra única de 'é free': sufixo -free ou allowlist oficial."""
    bare = bare_model_id(model_id).lower()
    if not bare:
        return False
    return bare.endswith("-free") or bare in EXTRA_FREE_IDS


# --------------------------------------------------------------------------
# SHAPE DE REQUEST ACEITO PELO GATE DO ZEN
# --------------------------------------------------------------------------


def probe_session_id() -> str:
    """Session id no formato do cliente oficial: ses_ + 12 hex + 14 base62."""
    return (
        "ses_"
        + secrets.token_hex(6)
        + "".join(
            secrets.choice(PROBE_SESSION_ALPHABET) for _ in range(14)
        )
    )


def _probe_tool(name: str) -> dict[str, Any]:
    """Tool mínima pro gate: o Zen só valida os nomes (shell + read)."""
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": f"Tool {name} do agente.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    }


def probe_headers(api_key: str) -> dict[str, str]:
    """Headers com os quais o Zen aceita nosso request (gate da #4101).

    Chamado a cada probe: reusar um único dict para todos os probes de um
    ciclo mandaria o mesmo ``x-opencode-session`` em paralelo.
    """
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
        "User-Agent": PROBE_UA,
        "x-opencode-client": PROBE_CLIENT,
        "x-opencode-session": probe_session_id(),
    }


def probe_payload(
    model: str,
    prompt: str,
    max_tokens: int,
) -> dict[str, Any]:
    """Corpo de turno de agente: stream + tools com shell E read.

    Sem esse shape o Zen devolve 403 FreeTierError. Chat completions em
    vez de /v1/responses porque é o único formato cujo SSE traz usage com
    reasoning_tokens — a classificação thinking depende disso.

    ``reasoning_effort: high`` é o que dá sentido ao limiar do opus: sem
    ele o mesmo modelo oscila de 17 a 263 reasoning tokens conforme o
    backend do round-robin e o corte vira um chute.
    """
    return {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "reasoning_effort": "high",
        "stream": True,
        "stream_options": {"include_usage": True},
        "tools": [_probe_tool("shell"), _probe_tool("read")],
    }


# --------------------------------------------------------------------------
# CLASSIFICAÇÃO
# --------------------------------------------------------------------------

# Quanto `reasoning_tokens` o probe exige pra separar opus de sonnet.
#
# Medido com `reasoning_effort: high` no prompt canônico, no slave: pensadores
# deram 59-509, não-pensadores 0 — o corte em 50 pega as amostras de
# big-pickle que vieram em 59 e 74. Com o prompt antigo ("What is 2+2?") os
# dois lados do corte davam 0-4 e a classificação era um lance de dados.
# Observação: quando o upstream omite o contador (no master, 0 em 18/18
# medições) tudo cai no sonnet — limite do upstream, não do número.
PROBE_THINKING_MIN_TOKENS = 50


def classify_model_probe(
    status_code: int,
    text: str,
) -> tuple[str, int] | None:
    """Classifica o resultado do probe de um modelo.

    Returns:
        ("thinking" | "no_thinking", reasoning_tokens) quando o modelo
        está ativo, ou None quando não está (HTTP diferente de 200).
        "thinking" só com ``PROBE_THINKING_MIN_TOKENS`` ou mais — pensar
        pouco é ``no_thinking``, não opus.
    """
    if status_code != 200:
        return None

    reasoning_match = re.search(
        r'"reasoning_tokens":\s*(\d+)', text
    )
    reasoning_tokens = (
        int(reasoning_match.group(1)) if reasoning_match else 0
    )

    kind = (
        "thinking"
        if reasoning_tokens >= PROBE_THINKING_MIN_TOKENS
        else "no_thinking"
    )
    return kind, reasoning_tokens


def classify_probe_error(status_code: int, text: str) -> str | None:
    """Diz se a falha de um probe merece memória.

    Returns:
        "stable"    → determinística (modelo retirado): quarentena longa.
        "gate"      → 403 FreeTierError: request rejeitado pelo gate de
                      formato do Zen; reversível, quarentena curta.
        "rate_limit"→ cota que reseta: quarentena curta.
        None        → transitória (5xx, request malformado): só conta a
                      sequência, sem quarentena, até
                      PROBE_MAX_TRANSIENT_FAILURES seguidas.
    """
    body = text or ""

    if status_code == 429 or "FreeUsageLimitError" in body:
        return "rate_limit"

    # FreeTierError = "não parecia o cliente OpenCode": falha do nosso
    # formato, não do modelo. Guardar como stable esconderia qualquer
    # regressão nossa por 6h (foi exatamente o bug da issue #4101).
    if status_code == 403 and "FreeTierError" in body:
        return "gate"

    if status_code in (403, 404):
        return "stable"

    # 400 só é determinístico quando o corpo diz que o modelo não existe;
    # um 400 genérico pode ser bug nosso e guardá-lo esconderia o problema.
    if status_code == 400 and (
        "Model is unavailable" in body or "not found" in body
    ):
        return "stable"

    return None


def is_error_body(text: str) -> bool:
    """200 com ``{"type": "error"}`` é recusa do upstream, não sucesso.

    Só JSON puro conta: corpo SSE (``data: {...}``) não parseia e cai fora.
    """
    body = text or ""
    if not body.lstrip().startswith("{"):
        return False
    try:
        payload = json.loads(body)
    except (TypeError, ValueError):
        return False
    return isinstance(payload, dict) and payload.get("type") == "error"


def has_usage(text: str) -> bool:
    """200 só é completion se a resposta trouxe ``usage`` como objeto.

    O probe manda ``stream_options.include_usage``, então o chunk final traz
    ``"usage": {...}``. Sem isso a resposta não prova que o modelo rodou:
    200 vazio, SSE cortado no meio e ``"usage": null`` em todos os chunks
    caem aqui — e um modelo desses não pode entrar no combo como "ativo".
    """
    return bool(re.search(r'"usage"\s*:\s*\{', text or ""))


# --------------------------------------------------------------------------
# QUARENTENA (model-failures.json)
# --------------------------------------------------------------------------

# Por quanto tempo um tipo de falha fica em quarentena.
#   stable    → modelo retirado/indisponível (404, 400 "Model is
#               unavailable") ou 403 genérico: repetir a cada 10min não
#               muda nada, mas não queremos dormir pra sempre caso o Zen
#               abra a porta.
#   gate      → 403 FreeTierError: nosso request não passou no gate de
#               formato do Zen. Reversível (depende do corpo/headers que
#               enviamos, não do modelo), então janela curta: se o Zen
#               mudar as regras, re-sonda em 30min e não some por 6h.
#   rate_limit → cota que reseta sozinha: janela curta.
# Transitórios (5xx, rede) NÃO entram aqui — sem memória, re-testados todo
# ciclo, que é exatamente o comportamento correto.
PROBE_RETRY_SECONDS: dict[str, int] = {
    "stable": 6 * 3600,
    "gate": 30 * 60,
    "rate_limit": 30 * 60,
}

# Quantas falhas transitórias seguidas (5xx, rede) antes de um modelo ser
# considerado queimado. Uma falha isolada é azar do upstream; 2 seguidas é
# padrão — e é só aí que o transitório ganha a mesma quarentena longa.
# Sem isso o watch re-sondaria o mesmo 500 para sempre.
PROBE_MAX_TRANSIENT_FAILURES = 2

# Payload canônico do probe. Um só, porque o bucket (thinking/no_thinking)
# depende dele — sync, watch e fetch usam estes valores.
#
# O prompt é propositalmente difícil. Medido com `reasoning_effort: high` no
# slave: este problema deu big-pickle 59-247 e nemotron-3.5 170-509 reasoning
# tokens, enquanto muse-spark (não-pensador) ficou em 0. Com o antigo
# "What is 2+2? Think step by step." pensador e não-pensador davam os
# mesmos 0-4 e a classificação não separava nada.
PROBE_PROMPT = (
    "A bakery sells croissants for $3 each. You buy 7, pay with a $50 note "
    "and get change. Then you return 2 croissants. How much money do you "
    "have spent in the end? Reason step by step before answering."
)
PROBE_MAX_TOKENS = 2000


def failure_is_fresh(
    failures: dict[str, Any],
    model_id: str,
    now: float,
) -> bool:
    """True se o modelo ainda está dentro da quarentena."""
    entry = failures.get(bare_model_id(model_id))
    if not isinstance(entry, dict):
        return False

    ttl = PROBE_RETRY_SECONDS.get(entry.get("kind"))  # type: ignore[arg-type]
    if ttl is None:
        return False

    at = entry.get("at")
    if not isinstance(at, (int, float)) or isinstance(at, bool):
        return False

    return (now - at) < ttl


def _consecutive_failures(entry: Any) -> int:
    """Lê o contador de falhas seguidas de um registro, sendo generoso."""
    if not isinstance(entry, dict):
        return 0
    count = entry.get("count", 0)
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        return 0
    return count


def record_probe_failure(
    failures: dict[str, Any],
    model_id: str,
    kind: str | None,
    status_code: int,
    now: float,
) -> str:
    """Registra uma falha de probe e devolve o tipo efetivamente guardado.

    `kind=None` é um erro transitório (5xx, rede): guarda só a sequência,
    sem quarentena, até bater PROBE_MAX_TRANSIENT_FAILURES seguidas — aí
    promove para "stable". Uma falha isolada é azar do upstream; 2 seguidas
    já é padrão. Um `kind` determinístico (403/404) quarentena já na primeira.
    """
    bare = bare_model_id(model_id)
    count = _consecutive_failures(failures.get(bare)) + 1

    if kind is None:
        kind = "stable" if count >= PROBE_MAX_TRANSIENT_FAILURES else "transient"

    failures[bare] = {
        "kind": kind,
        "status": status_code,
        "at": now,
        "count": count,
    }
    return kind


# --------------------------------------------------------------------------
# PERSISTÊNCIA POR ESCOPO
# --------------------------------------------------------------------------


def _load_raw(path: Path) -> dict[str, Any]:
    """Lê o arquivo inteiro. Ausente/corrompido = vazio, nunca derruba."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except OSError as exc:
        warning(f"PROBE — estado ilegível ({path.name}): {str(exc)[:80]}")
        return {}
    except ValueError as exc:
        warning(f"PROBE — estado corrompido ({path.name}): {str(exc)[:80]}")
        return {}
    return raw if isinstance(raw, dict) else {}


def _is_failure_entry(value: Any) -> bool:
    """Formato legado de quarentena: ``{"kind": ..., "at": ...}``."""
    return isinstance(value, dict) and ("kind" in value or "at" in value)


def _is_state_entry(value: Any) -> bool:
    """Formato legado de cache: ``{"alive": ..., "checked_at": ...}``."""
    return isinstance(value, dict) and "checked_at" in value


def _read_scoped(
    path: Path,
    scope: str,
    is_entry: Callable[[Any], bool],
) -> dict[str, Any]:
    """Recorta o estado de ``scope``, aceitando o formato legado plano.

    O standalone já gravou ``{"<modelo>": {...}}`` (instância única). Esse
    formato é reconhecido e devolvido como se fosse do escopo pedido — a
    gravação seguinte o reescreve aninhado.
    """
    raw = _load_raw(path)
    if not raw:
        return {}

    if not scope:
        return dict(raw)

    scoped = raw.get(scope)
    if isinstance(scoped, dict):
        return dict(scoped)

    if all(is_entry(value) for value in raw.values()):
        return dict(raw)

    return {}


def _save_scoped(
    path: Path,
    scope: str,
    data: dict[str, Any],
) -> None:
    """Grava atômica, preservando os escopos das outras instâncias.

    Sem escopo o arquivo é plano (formato legado do standalone) e é gravado
    como está, para o round-trip ler igual.
    """
    if not scope:
        payload: dict[str, Any] = dict(data)
    else:
        raw = _load_raw(path)
        legacy = bool(raw) and (
            all(_is_failure_entry(v) for v in raw.values())
            or all(_is_state_entry(v) for v in raw.values())
        )
        payload = (
            {}
            if legacy
            else {
                key: value
                for key, value in raw.items()
                if key != scope and isinstance(value, dict)
            }
        )
        payload[scope] = data

    try:
        temp_file = path.with_suffix(path.suffix + ".tmp")
        temp_file.write_text(
            json.dumps(payload, indent=2) + "\n", encoding="utf-8"
        )
        temp_file.replace(path)
    except OSError as exc:
        # Um arquivo de cache não pode derrubar uma descoberta bem-sucedida.
        warning(f"PROBE — não gravou {path.name}: {str(exc)[:80]}")


def _persist(path: Path, scope: str, data: dict[str, Any]) -> None:
    """Não cria arquivo só para gravar vazio; reaproveita se já existe."""
    if data or path.exists():
        _save_scoped(path, scope, data)


def load_probe_failures(
    failures_path: Path = PROBE_FAILURES_FILE,
    scope: str = "",
) -> dict[str, Any]:
    """Lê a quarentena de um escopo. Ausente/corrompido = vazio, nunca derruba."""
    return _read_scoped(failures_path, scope, _is_failure_entry)


def save_probe_failures(
    failures: dict[str, Any],
    keep_ids: set[str] | None,
    failures_path: Path = PROBE_FAILURES_FILE,
    scope: str = "",
) -> None:
    """Grava a quarentena, podando quem já não está no listing.

    ``keep_ids=None`` mantém tudo (quem não tem listing não quer perder a
    memória de ontem).
    """
    kept = (
        {k: v for k, v in failures.items() if k in keep_ids}
        if keep_ids is not None
        else dict(failures)
    )
    _persist(failures_path, scope, kept)


def load_probe_state(
    state_path: Path = PROBE_STATE_FILE,
    scope: str = "",
) -> dict[str, Any]:
    """Lê o cache TTL de um escopo."""
    return _read_scoped(state_path, scope, _is_state_entry)


def save_probe_state(
    state: dict[str, Any],
    state_path: Path = PROBE_STATE_FILE,
    scope: str = "",
    keep_ids: set[str] | None = None,
) -> None:
    """Grava o cache TTL, podando modelos que saíram do universo conhecido."""
    kept = (
        {k: v for k, v in state.items() if k in keep_ids}
        if keep_ids is not None
        else dict(state)
    )
    _persist(state_path, scope, kept)


# --------------------------------------------------------------------------
# ENGINE
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ProbeResult:
    """Resultado da checagem de um modelo."""

    model: str
    alive: bool
    kind: str
    status: int
    reasoning_tokens: int
    checked_at: float
    from_cache: bool = False


def _default_transport(url: str, **kwargs: Any) -> Any:
    """requests.post puro: sessão nova por chamada, seguro entre threads."""
    return requests.post(url, **kwargs)


def _entry_to_result(
    model: str,
    entry: Any,
    from_cache: bool,
) -> ProbeResult | None:
    """Monta um ProbeResult a partir de uma entrada do cache, sendo generoso."""
    if not isinstance(entry, dict):
        return None

    checked_at = entry.get("checked_at")
    if not isinstance(checked_at, (int, float)) or isinstance(checked_at, bool):
        return None

    return ProbeResult(
        model=model,
        alive=bool(entry.get("alive", False)),
        kind=str(entry.get("kind", "")),
        status=int(entry.get("status", 0) or 0),
        reasoning_tokens=int(entry.get("reasoning_tokens", 0) or 0),
        checked_at=checked_at,
        from_cache=from_cache,
    )


class ProbeEngine:
    """Sonda modelos em paralelo, com cache TTL e quarentena por instância.

    Três chaves mudam o comportamento:

    ``force``   → ignora o cache TTL (nem lê nem grava). Sync e fetch usam
                  ``True``: são explícitos e precisam de frescor, e isso
                  mantém os testes herméticos.
    ``memory``  → lê/grava a quarentena. Sync usa ``False`` (não toca em
                  arquivo), watch e fetch usam ``True``.
    ``strict``  → ``True``: qualquer não-200 derruba o modelo (sync, fetch).
                  ``False``: transitório sem quarentena segue vivo, para o
                  watchdog não ficar removendo e reinserindo a cada 500
                  esporádico.
    """

    def __init__(
        self,
        instance: Instance,
        api_key: str,
        transport: Callable[..., Any] | None = None,
        workers: int = PROBE_WORKERS,
        ttl: int = PROBE_TTL_SECONDS,
        timeout: int = PROBE_TIMEOUT,
        state_path: Path = PROBE_STATE_FILE,
        failures_path: Path = PROBE_FAILURES_FILE,
        clock: Callable[[], float] | None = None,
        prompt: str = PROBE_PROMPT,
        max_tokens: int = PROBE_MAX_TOKENS,
    ) -> None:
        self.instance = instance
        self.api_key = api_key
        self.transport = transport or _default_transport
        self.workers = max(1, int(workers))
        self.ttl = ttl
        self.timeout = timeout
        self.state_path = Path(state_path)
        self.failures_path = Path(failures_path)
        self.clock = clock or time.time
        self.prompt = prompt
        self.max_tokens = max_tokens
        # Escopo do estado: a instância, nunca só o modelo.
        self.scope = instance.host

    # ------------------------------------------------------------------ API

    def check(
        self,
        models: list[str],
        *,
        strict: bool = True,
        force: bool = False,
        memory: bool = True,
        prune_to: set[str] | None = None,
    ) -> dict[str, ProbeResult]:
        """Checa ``models`` e devolve ``{modelo: ProbeResult}``.

        ``prune_to`` poda a quarentena para quem ainda existe no listing.
        """
        now = self.clock()
        use_cache = not force and self.ttl > 0

        state = load_probe_state(self.state_path, self.scope) if use_cache else {}
        failures = (
            load_probe_failures(self.failures_path, self.scope)
            if memory
            else {}
        )

        results: dict[str, ProbeResult] = {}
        pending: list[str] = []

        for model in models:
            if use_cache:
                cached = _entry_to_result(
                    model,
                    state.get(bare_model_id(model)),
                    from_cache=True,
                )
                if cached is not None and (now - cached.checked_at) < self.ttl:
                    results[model] = cached
                    continue

            if memory and failure_is_fresh(failures, model, now):
                entry = failures.get(bare_model_id(model)) or {}
                results[model] = ProbeResult(
                    model=model,
                    alive=False,
                    kind=str(entry.get("kind", "stable")),
                    status=int(entry.get("status", 0) or 0),
                    reasoning_tokens=0,
                    checked_at=now,
                    from_cache=True,
                )
                continue

            pending.append(model)

        if pending:
            results.update(
                self._probe_all(
                    pending,
                    strict=strict,
                    memory=memory,
                    failures=failures,
                    now=now,
                )
            )

        if memory:
            save_probe_failures(
                failures,
                {bare_model_id(m) for m in prune_to} if prune_to else None,
                self.failures_path,
                self.scope,
            )
        if use_cache:
            save_probe_state(
                results_to_state(results),
                self.state_path,
                self.scope,
                {bare_model_id(m) for m in results} if results else None,
            )

        return results

    # ------------------------------------------------------------ interno

    def _probe_all(
        self,
        models: list[str],
        *,
        strict: bool,
        memory: bool,
        failures: dict[str, Any],
        now: float,
    ) -> dict[str, ProbeResult]:
        results: dict[str, ProbeResult] = {}
        lock = threading.Lock()
        workers = min(self.workers, len(models))

        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(self._probe_one, model): model
                for model in models
            }
            for future in as_completed(futures):
                model = futures[future]
                outcome = future.result()

                status, text, raised = outcome

                if raised:
                    # Sem resposta não há evidência: não grava memória e,
                    # fora de strict, o modelo continua roteável.
                    results[model] = ProbeResult(
                        model=model,
                        alive=not strict,
                        kind="error",
                        status=0,
                        reasoning_tokens=0,
                        checked_at=now,
                    )
                    continue

                with lock:
                    results[model] = self._decide(
                        model, status, text,
                        strict=strict, memory=memory,
                        failures=failures, now=now,
                    )

        return results

    def _probe_one(self, model: str) -> tuple[int, str, bool]:
        """Executa um probe. Devolve (status, texto, falhou_por_exceção)."""
        try:
            response = self.transport(
                f"{self.instance.base_url}/v1/chat/completions",
                json=probe_payload(model, self.prompt, self.max_tokens),
                headers=probe_headers(self.api_key),
                timeout=self.timeout,
            )
        except Exception:  # noqa: BLE001 - qualquer falha de transporte = sem evidência
            return 0, "", True
        return int(response.status_code), str(response.text), False

    def _decide(
        self,
        model: str,
        status: int,
        text: str,
        *,
        strict: bool,
        memory: bool,
        failures: dict[str, Any],
        now: float,
    ) -> ProbeResult:
        probe = classify_model_probe(status, text)
        completed = has_usage(text)

        if probe is not None and completed and not is_error_body(text):
            kind, reasoning_tokens = probe
            if memory:
                failures.pop(bare_model_id(model), None)
            return ProbeResult(
                model=model,
                alive=True,
                kind=kind,
                status=status,
                reasoning_tokens=reasoning_tokens,
                checked_at=now,
            )

        kind_err = classify_probe_error(status, text)
        if memory:
            kind = record_probe_failure(failures, model, kind_err, status, now)
            quarantined = failure_is_fresh(failures, model, now)
        else:
            kind = kind_err or "transient"
            quarantined = False

        # 200 sem completion (corpo de erro ou sem `usage`) é recusa explícita
        # do upstream: sempre morta, mesmo fora de strict.
        definitive = status == 200 and (is_error_body(text) or not completed)
        alive = not (definitive or strict or quarantined)

        return ProbeResult(
            model=model,
            alive=alive,
            kind=kind,
            status=status,
            reasoning_tokens=0,
            checked_at=now,
        )


def results_to_state(results: dict[str, ProbeResult]) -> dict[str, Any]:
    """Converte resultados do ciclo em entradas do cache TTL."""
    return {
        bare_model_id(model): {
            "alive": result.alive,
            "kind": result.kind,
            "status": result.status,
            "reasoning_tokens": result.reasoning_tokens,
            "checked_at": result.checked_at,
        }
        for model, result in results.items()
        if is_cacheable(result)
    }


def is_cacheable(result: ProbeResult) -> bool:
    """Só o que é **evidência** entra no cache TTL.

    Transitório (5xx) e exceção de rede não provam nada: guardar esses faria o
    próximo ciclo acreditar num ``alive`` sem re-sondar por 30min — um 500
    embrulhado viveria mais do que o próprio sintoma. Falha classificada
    (403/gate/429) é recusa definitiva e sim pode ser guardada.
    """
    if result.kind in ("transient", "error"):
        return False
    return bool(result.alive) or result.kind in PROBE_RETRY_SECONDS
