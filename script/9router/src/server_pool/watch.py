"""Watchdog: monitora slaves, realiza auto-discovery de modelos e substituição com quarentena."""

from __future__ import annotations

from dataclasses import dataclass
import signal
import time
from typing import Any

from .config import load_config
from .console import error, info, success, title, warning
from .diagnose import diagnose_slave, print_diagnosis
from .fetch import fetch_opencode_free_models, update_config_with_models
from .models import Instance, RouterClient, master_instance, slave_instances
from .probe import PROBE_FAILURES_FILE, PROBE_STATE_FILE, ProbeEngine
from .sync import (
    apply_combo_plan,
    hot_reload_master_models,
    quarantine_slave,
    replace_slave,
    unquarantine_slave,
)

MIN_SLAVES = 2

# Defaults compartilhados com a CLI (`__main__`) — mudar aqui muda os dois.
DEFAULT_HEALTH_INTERVAL = 180
DEFAULT_FETCH_INTERVAL = 600
DEFAULT_MODEL_INTERVAL = 900


@dataclass
class WatchState:
    """Momento em que cada timer disparou pela última vez.

    Os horários começam em 0.0 para que o primeiro ciclo dispare todos —
    exceto discovery, que precisa de um `last_fetch` inicial para não repetir
    no boot o que a inicialização já fez.
    """

    last_fetch: float = 0.0
    last_model: float = 0.0


def _fresh_config() -> dict[str, Any] | None:
    """Relê o `config.json` — a intenção pode ter mudado desde o boot.

    `load_config` faz `sys.exit(1)` quando o arquivo está ausente ou com JSON
    inválido: é a política certa para a CLI, que encerra e devolve o shell.
    Num serviço de longa duração isso seria fatal — e `SystemExit` não é
    `Exception`, então escapa de um `try/except Exception` comum.

    `None` significa "não sei qual é a intenção". Quem recebe NÃO pode agir
    nem persistir: mandar o snapshot do boot de volta ao disco reverteria a
    edição que o operador acabou de fazer (rule: config é intenção).
    """
    try:
        return load_config()
    except (Exception, SystemExit) as exc:  # noqa: BLE001 - SystemExit não é Exception
        detail = "arquivo ausente ou JSON inválido" if isinstance(exc, SystemExit) else str(exc)
        warning(f"[watchdog] config.json ilegível ({detail}) — ciclo adiado")
        return None


def discover_models(config: dict[str, Any]) -> None:
    """Busca modelos free novos e, se houver, aplica no config + master."""
    info("\n[watchdog] Iniciando Auto-Discovery de modelos...")
    try:
        master = master_instance(config)
        discovered = fetch_opencode_free_models(
            master_host=master.host,
            master_password=master.password,
        )
        if discovered.get("thinking") or discovered.get("no_thinking"):
            update_config_with_models(config, discovered)
            hot_reload_master_models(config)
            success("[watchdog] Hot-Reload de novos modelos concluído!")
    except Exception as exc:  # noqa: BLE001 - ciclo falho não encerra o loop
        warning(f"[watchdog] Falha no Auto-Discovery de modelos: {exc}")


def run_tick(
    state: WatchState,
    *,
    fetch_interval: int,
    model_interval: int,
    now: float | None = None,
) -> None:
    """Um ciclo do watchdog: dispara o que venceu o próprio timer.

    Os dois timers são independentes de propósito — descoberta de modelos
    novos e saúde dos modelos que já usamos não têm nada a ver uma com a
    outra. O config é lido **uma vez** por ciclo e o mesmo snapshot alimenta
    os dois.
    """
    now = time.time() if now is None else now
    cfg = _fresh_config()

    if cfg is None:
        # Sem intenção não há o que descobrir nem revalidar, e aplicar um
        # snapshot velho reverteria a edição do operador. Os timers não
        # avançam de propósito: é leitura local barata, tentamos de novo no
        # próximo tick — e o health check do loop segue normalmente.
        return

    # Cada timer tem seu próprio try: uma falha num deles não pode nem
    # encerrar o watchdog nem adiar o outro. O relógio é adiantado mesmo em
    # caso de erro, senão um serviço fora do ar viraria retry-loop a cada
    # health cycle em vez de esperar o intervalo dele.
    if fetch_interval > 0 and (now - state.last_fetch) >= fetch_interval:
        try:
            discover_models(cfg)
        except Exception as exc:  # noqa: BLE001 - ciclo falho não encerra o loop
            warning(f"[watchdog] falha na Auto-Discovery: {exc}")
        state.last_fetch = now

    if model_interval > 0 and (now - state.last_model) >= model_interval:
        try:
            revalidate_models(cfg)
        except Exception as exc:  # noqa: BLE001 - ciclo falho não encerra o loop
            warning(f"[watchdog] falha na Revalidação: {exc}")
        state.last_model = now


def _is_permanent_slave(name: str) -> bool:
    """Check if slave is permanent (rs000)."""
    return name == "rs000"


def _is_rate_limited(err: str | None) -> bool:
    if not err:
        return False
    msg = str(err).lower()
    return "429" in msg or "rate limit" in msg or "too many" in msg


def _test_provider_remote(
    master_client: RouterClient,
    provider_id: str,
    timeout: int = 90,
) -> tuple[bool, str]:
    """Testa um provider via master usando RouterClient.test_provider().
    Retorna (valid, error_msg)."""
    try:
        data = master_client.test_provider(provider_id, timeout=timeout)
        err_msg = data.get("error") or ""
        return data.get("valid", False), str(err_msg)
    except Exception as exc:
        return False, str(exc)


def _target_models(defaults: dict[str, Any]) -> list[str]:
    """Modelos da intenção, na ordem do config, sem repetir."""
    combos = defaults.get("combos", [])
    return list(dict.fromkeys(m for combo in combos for m in combo.get("models", [])))


def _revalidate_slave(
    slave: Instance,
    combos: list[dict[str, Any]],
    targets: list[str],
) -> None:
    """Re-sonda os modelos já nos combos DESTE slave e sincroniza os combos dele.

    O master não entra aqui de propósito: os combos dele apontam para combos
    de slave (`rs001/claude-sonnet-5`), não para modelos — e mexer neles é
    trabalho do sync, não da revalidação.
    """
    client = RouterClient(slave)

    if not client.login():
        warning(f"[watchdog] login em {slave.name} falhou — revalidação adiada")
        return

    try:
        keys = client.get_api_keys()
    except Exception as exc:  # noqa: BLE001 - sem keys não há como sondar
        warning(f"[watchdog] erro ao listar API keys de {slave.name}: {exc}")
        return

    api_key = keys[0].get("key", "") if keys else ""

    if not api_key:
        warning(f"[watchdog] {slave.name} sem API key — revalidação adiada")
        return

    info(
        f"\n[watchdog] Revalidando {len(targets)} modelos em {slave.name} "
        f"({len(combos)} combos)..."
    )

    engine = ProbeEngine(
        instance=slave,
        api_key=api_key,
        transport=client.session.post,
        state_path=PROBE_STATE_FILE,
        failures_path=PROBE_FAILURES_FILE,
    )
    results = engine.check(targets, strict=False, force=False, memory=True)

    alive = {m for m, r in results.items() if r.alive}
    dead = [m for m in targets if m not in alive]

    for model_id in dead:
        result = results[model_id]
        origem = "cache/quarentena" if result.from_cache else "probe"
        warning(
            f"  ✗ {slave.name}/{model_id} → fora do roteamento "
            f"({result.kind}, {origem})"
        )

    info(f"  {slave.name}: {len(alive)}/{len(targets)} modelos vivos")

    # ------------------------------------------------------------------
    # intenção ∩ vivo → plano → diff (não recria combo inalterado)
    # ------------------------------------------------------------------
    existing = client.get_combos()
    previous = {
        combo.get("name"): list(combo.get("models", []))
        for combo in existing
        if combo.get("name")
    }

    plan: list[tuple[str, list[str] | None]] = []

    for combo in combos:
        combo_name = combo["name"]
        live = [m for m in combo.get("models", []) if m in alive]

        if live:
            plan.append((combo_name, live))
        elif previous.get(combo_name):
            warning(
                f"  combo '{combo_name}' sem modelos vivos — "
                "mantendo último conjunto válido"
            )
            plan.append((combo_name, None))
        else:
            # Sem vivos E sem histórico: NÃO criar. Um combo novo montado só
            # com modelos mortos ficaria servindo falha até a quarentena
            # expirar (até 6h) — é o mesmo "sem modelos válidos, pulando" do sync.
            plan.append((combo_name, None))

    apply_combo_plan(client, slave, plan, existing)


def revalidate_models(config: dict[str, Any]) -> None:
    """Re-sonda os modelos já nos combos e sincroniza cada slave.

    Regras herdadas do standalone:

    - ``config.json`` é **intenção**: nunca perde modelo por saúde. Quem
      filtra é o servidor — o combo vira intenção ∩ modelos que responderam.
    - ``strict=False``: um 500 isolado não sacode o roteamento; só quem
      entrou em quarentena sai.
    - Combo sem nenhum modelo vivo mantém o último conjunto válido (mesmo
      fallback do ``sync``), em vez de sumir do mapa.

    Cada slave é isolado: falha de login ou de probe em um não interrompe
    os demais.
    """
    defaults = config.get("defaults", {})
    combos = defaults.get("combos", [])
    targets = _target_models(defaults)

    if not targets:
        return

    for slave in slave_instances(config):
        try:
            _revalidate_slave(slave, combos, targets)
        except Exception as exc:  # noqa: BLE001 - um slave fora não cega os demais
            warning(f"[watchdog] falha na revalidação de {slave.name}: {exc}")


def watch_pool(
    config: dict[str, Any],
    interval: int = DEFAULT_HEALTH_INTERVAL,
    max_failures: int = 2,
    fetch_interval: int = DEFAULT_FETCH_INTERVAL,
    model_interval: int = DEFAULT_MODEL_INTERVAL,
) -> None:
    """
    Loop de monitoramento contínuo e autônomo (Pool Vivo).

    - Auto-Discovery de modelos a cada fetch_interval segundos (padrão 600 / 10min).
    - Revalidação dos modelos em uso a cada model_interval segundos (padrão 900 / 15min).
    - Quarentena preventiva na 1ª falha (remove do combo do master sem matar o container).
    - Replace completo (Delete+Create) na max_failures consecutiva.
    - Desfaz quarentena se o slave se recuperar.
    """
    master = master_instance(config)
    slaves = slave_instances(config)

    title("9ROUTER LIVE POOL WATCHDOG")
    info(f"Slaves: {len(slaves)} | Mínimo: {MIN_SLAVES}")
    info(f"Intervalo Health: {interval}s | Falhas para Replace: {max_failures}")
    if fetch_interval > 0:
        info(
            f"Auto-Discovery Modelos: a cada {fetch_interval}s "
            f"({fetch_interval // 60}min)"
        )
    else:
        info("Auto-Discovery Modelos: desativado")
    if model_interval > 0:
        info(
            f"Revalidação Modelos: a cada {model_interval}s "
            f"({model_interval // 60}min)"
        )
    else:
        info("Revalidação Modelos: desativado")
    info("Pressione Ctrl+C para parar\n")

    # Count regular slaves (excluding rs000) for minimum check
    regular_slaves = [s for s in slaves if not _is_permanent_slave(s.name)]
    if len(regular_slaves) < MIN_SLAVES:
        warning(f"Pool tem {len(regular_slaves)} slave(s) regular(es) — abaixo do mínimo de {MIN_SLAVES}!")

    failure_counts: dict[str, int] = {s.name: 0 for s in slaves}
    quarantined: set[str] = set()
    replacing: set[str] = set()
    # Discovery já rodou no boot: não repetir no primeiro tick.
    state = WatchState(last_fetch=time.time())

    def _stop(sig, frame):
        print()
        info("Watchdog encerrado.")
        raise SystemExit(0)

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    while True:
        # -------------------------------------------------------------------
        # 1. TIMERS INDEPENDENTES (descoberta + revalidação)
        # -------------------------------------------------------------------
        run_tick(
            state,
            fetch_interval=fetch_interval,
            model_interval=model_interval,
        )

        # -------------------------------------------------------------------
        # 2. CARREGAR CONFIG E VERIFICAR SAÚDE
        # -------------------------------------------------------------------
        # Health check é somente leitura: pode cair para o snapshot do boot
        # quando o arquivo está ilegível, ao contrário dos timers (que precisam
        # de intenção fresca para agir). O importante é não levantar SystemExit.
        fresh_config = _fresh_config() or config

        master_client = RouterClient(master)
        if not master_client.login():
            warning("[watchdog] login no master falhou — tentando no próximo ciclo")
            time.sleep(interval)
            continue

        try:
            providers = master_client.get_providers()
        except Exception as exc:
            warning(f"[watchdog] erro ao listar providers: {exc}")
            time.sleep(interval)
            continue

        provider_map = {p["name"]: p for p in providers if p.get("name")}

        ts = time.strftime("%H:%M:%S")
        active_slaves = slave_instances(fresh_config)
        print(f"\n[{ts}] Verificando {len(active_slaves)} slave(s)...")

        for slave in active_slaves:
            # rs000 é permanente - nunca entra em quarentena nem é substituído
            if _is_permanent_slave(slave.name):
                provider = provider_map.get(slave.name)
                if provider:
                    valid, err = _test_provider_remote(master_client, provider["id"], timeout=90)
                    if valid:
                        success(f"  {slave.name}: OK (permanente)")
                    else:
                        warning(f"  {slave.name}: FALHOU (permanente, não substituído) — {err[:80]}")
                else:
                    warning(f"  {slave.name}: ausente no master (permanente)")
                continue

            if slave.name in replacing:
                info(f"  {slave.name}: substituindo... aguardando")
                continue

            if slave.name not in failure_counts:
                failure_counts[slave.name] = 0

            provider = provider_map.get(slave.name)

            if not provider:
                warning(f"  {slave.name}: ausente no master → +1 falha")
                failure_counts[slave.name] += 1
            else:
                # Usar o método do RouterClient em vez de POST manual.
                # Isso garante require_login() e timeout adequado p/ Tor.
                valid, err = _test_provider_remote(master_client, provider["id"], timeout=90)
                kind = "429" if _is_rate_limited(err) else "ERRO"

                if valid:
                    # Se estava em quarentena e recuperou, tira da quarentena
                    if slave.name in quarantined:
                        unquarantine_slave(fresh_config, slave.name)
                        quarantined.remove(slave.name)
                    success(f"  {slave.name}: OK")
                    failure_counts[slave.name] = 0
                else:
                    cnt = failure_counts[slave.name] + 1
                    failure_counts[slave.name] = cnt
                    warning(f"  {slave.name}: {kind} ({cnt}/{max_failures}) — {err[:80]}")

                    # 1ª Falha -> Diagnóstico rápido + quarentena
                    if cnt == 1:
                        info(f"  {slave.name}: executando diagnóstico...")
                        results = diagnose_slave(slave, fresh_config.get("defaults", {}), master_client)
                        print_diagnosis(slave.name, results)

                        if slave.name not in quarantined:
                            if quarantine_slave(fresh_config, slave.name):
                                quarantined.add(slave.name)

            # Atingiu o limite -> Replace completo (Delete + Create)
            if failure_counts[slave.name] >= max_failures:
                current_slaves = slave_instances(fresh_config)
                # Only count regular slaves (not rs000, not being replaced)
                active_count = len([s for s in current_slaves 
                                   if not _is_permanent_slave(s.name) and s.name not in replacing])

                if active_count <= MIN_SLAVES:
                    warning(
                        f"  {slave.name}: precisa de replace mas pool tem apenas "
                        f"{active_count} slave(s) regular(es) ativo(s)."
                    )

                # Diagnóstico completo antes do replace
                info(f"  {slave.name}: diagnóstico pré-replace...")
                results = diagnose_slave(slave, fresh_config.get("defaults", {}), master_client)
                print_diagnosis(slave.name, results)

                warning(f"\n  ⚡ {slave.name}: iniciando DELETE + CREATE...")
                replacing.add(slave.name)
                failure_counts[slave.name] = 0

                ok = replace_slave(fresh_config, slave.name)

                if ok:
                    success(f"  ✓ {slave.name}: substituído com sucesso")
                    quarantined.discard(slave.name)
                else:
                    error(f"  ✗ {slave.name}: falha no replace — tentará no próximo ciclo")
                    failure_counts[slave.name] = max_failures - 1

                replacing.discard(slave.name)

        time.sleep(interval)
