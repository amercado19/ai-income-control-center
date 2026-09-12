"""System health probes and the LIVE mode checklist (spec sections 30, 43, 47).

Everything here answers one question: *is this actually working right now?* Not "was it
configured", not "did it work yesterday" - is it working now. A probe that cannot confirm
success returns DEGRADED or DOWN. The only path to GREEN is a successful live check.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable
from datetime import UTC
from typing import Any

from . import storage
from .config import AUDIT_LOG, DATA_DIR, MAX_NEW_MONTHLY_CASH_SPEND
from .state import Capability, Health, RunState, SystemState


def _cap(key: str, label: str, health: Health, detail: str, blocking: str = "", last_success: str = "") -> Capability:
    return Capability(
        key=key,
        label=label,
        health=health.value,
        detail=detail,
        blocking_reason=blocking,
        last_success=last_success,
    )


# ---------------------------------------------------------------------------
# Probes
# ---------------------------------------------------------------------------


def probe_storage() -> Capability:
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        probe_file = DATA_DIR / ".health_probe"
        probe_file.write_text("ok", encoding="utf-8")
        probe_file.unlink()
        usage = shutil.disk_usage(DATA_DIR)
        free_mb = usage.free / (1024 * 1024)
        if free_mb < 50:
            return _cap("storage", "Storage", Health.DEGRADED, f"Only {free_mb:.0f} MB free.", "Low disk space.")
        return _cap("storage", "Storage", Health.HEALTHY, f"Writable. {free_mb:,.0f} MB free.")
    except OSError as exc:
        return _cap("storage", "Storage", Health.DOWN, "Data directory is not writable.", str(exc))


def probe_audit_log() -> Capability:
    if not AUDIT_LOG.exists():
        return _cap("audit_log", "Audit Log", Health.DEGRADED, "No events recorded yet.", "Log file not created.")
    lines = sum(1 for _ in AUDIT_LOG.open(encoding="utf-8"))
    return _cap("audit_log", "Audit Log", Health.HEALTHY, f"{lines:,} events recorded.")


def probe_opportunity_sources() -> Capability:
    """Aggregate liveness of the sources that can actually be scanned unattended."""
    from .connectors import LIVE_DISCOVERY_ORDER, get

    ok: list[str] = []
    failed: list[str] = []
    for name in LIVE_DISCOVERY_ORDER:
        connector = get(name)
        if connector is None:
            failed.append(f"{name} (not registered)")
            continue
        success, detail = connector.probe()
        (ok if success else failed).append(name if success else f"{name} ({detail[:60]})")

    if not ok:
        return _cap(
            "opportunity_sources",
            "Opportunity Sources",
            Health.DOWN,
            "No discovery source is reachable.",
            "; ".join(failed),
        )
    if failed:
        return _cap(
            "opportunity_sources",
            "Opportunity Sources",
            Health.DEGRADED,
            f"{len(ok)} of {len(ok) + len(failed)} sources reachable: {', '.join(ok)}.",
            "; ".join(failed),
        )
    return _cap("opportunity_sources", "Opportunity Sources", Health.HEALTHY, f"All {len(ok)} sources reachable: {', '.join(ok)}.")


def probe_ai_worker() -> Capability:
    """Is an unattended AI worker actually available *here*, and has it actually worked?

    GREEN requires three independent things, and each was added because the previous version
    could show green over nothing:

    1. A subscription credential - never a paid API key, which is outside the zero-cost rule.
    2. An executor: the ``claude`` CLI on PATH. An earlier version reported HEALTHY on the
       credential alone, while ``ClaudeWorker.execute`` raised in every environment and the
       pipeline silently fell back to the rule-based worker.
    3. **A passing worker proof.** Credential plus executor still is not evidence: a CI run with
       both present failed with `401 OAuth access token is invalid`. Presence is a config flag.
       ``state.py``'s own contract is that a capability is "derived from a live probe, never from
       a config flag, because a config flag records an intention and a probe records reality" -
       so the light now depends on a recorded model call that returned an exact nonce.

    The states below say which of the three is missing, because "not configured", "cannot run
    here" and "the credential is rejected" need three different responses from a person.
    """
    from . import worker_proof
    from .fulfillment.worker import ClaudeWorker

    available, why = ClaudeWorker.available()
    proof = worker_proof.last_result()

    if not available:
        if os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
            return _cap(
                "ai_worker",
                "AI Worker",
                Health.DEGRADED,
                why,
                "Configured but not operational in this environment. The rule-based worker is "
                "carrying the pipeline here; AI work runs on a GitHub Actions runner where the "
                "CLI is installed.",
            )
        if os.environ.get("ANTHROPIC_API_KEY"):
            return _cap(
                "ai_worker",
                "AI Worker",
                Health.DEGRADED,
                "API key present. This bills per token and is outside the Phase 1 zero-cost rule.",
                "Paid API billing is not approved. Prefer CLAUDE_CODE_OAUTH_TOKEN.",
            )
        return _cap(
            "ai_worker",
            "AI Worker",
            Health.NOT_CONFIGURED,
            "No Claude credential. Rule-based worker and reviewer handle the pipeline; AI drafting and AI review are unavailable.",
            "Run `claude setup-token` locally and add CLAUDE_CODE_OAUTH_TOKEN as a repository secret.",
        )

    if proof["state"] == "PASSED":
        return _cap(
            "ai_worker",
            "AI Worker",
            Health.HEALTHY,
            f"{why} Verified: {proof['detail']}",
            last_success=proof.get("at", ""),
        )

    if proof["state"] == "FAILED":
        return _cap(
            "ai_worker",
            "AI Worker",
            Health.DOWN,
            f"The last worker proof FAILED: {proof['detail']}",
            "Credential and executor are both present, so this is not a configuration gap - "
            "something in the AI path is broken. Run `python -m aicc worker-proof`, or dispatch "
            "the Claude worker workflow, and read the underlying error.",
        )

    if proof["state"] == "STALE":
        return _cap(
            "ai_worker",
            "AI Worker",
            Health.DEGRADED,
            f"Credential and executor present, but the evidence is old. {proof['detail']}",
            "Re-run the Claude worker proof to confirm the credential still works.",
        )

    # A pass on the wrong machine. Real evidence about a real credential, but not about the
    # environment client jobs run in, so it is worth showing and not worth going green over.
    if proof["state"] == "PASSED_ELSEWHERE":
        return _cap(
            "ai_worker",
            "AI Worker",
            Health.DEGRADED,
            f"Proved off-runner. {proof['detail']}",
            "Dispatch the Claude worker workflow so the proof comes from where client work executes.",
        )

    return _cap(
        "ai_worker",
        "AI Worker",
        Health.NOT_CONFIGURED,
        f"{why} But no worker proof has ever been recorded, so nothing has demonstrated that a model call actually succeeds from here.",
        "Dispatch the Claude worker workflow. A token and a binary are not evidence.",
    )


def probe_scheduler() -> Capability:
    """Did the scheduled discovery workflow actually run recently?"""
    from datetime import datetime

    runs = sorted(storage.DATA_DIR.glob("runs/*.json"))
    if not runs:
        return _cap(
            "scheduler",
            "Scheduler",
            Health.NOT_CONFIGURED,
            "No scheduled run has been recorded yet.",
            "Workflow has not run, or has never been enabled.",
        )
    latest = runs[-1]
    age_h = (datetime.now(UTC).timestamp() - latest.stat().st_mtime) / 3600
    if age_h > 24:
        return _cap(
            "scheduler", "Scheduler", Health.DEGRADED, f"Last run was {age_h:.0f} hours ago.", "Scheduled discovery may be failing."
        )
    return _cap("scheduler", "Scheduler", Health.HEALTHY, f"Last run {age_h:.1f} hours ago.")


def probe_payments() -> Capability:
    """Always NOT_CONFIGURED in Phase 1, by design (spec section 19)."""
    return _cap(
        "payments",
        "Payments",
        Health.NOT_CONFIGURED,
        "No payment processing is configured. Direct-client checkout stays COMING SOON until a "
        "business payment setup is deliberately activated.",
        "Personal PayPal is not used as a commercial checkout.",
    )


def probe_submission() -> Capability:
    """Proposal submission is human-gated everywhere, on purpose."""
    return _cap(
        "submission",
        "Marketplace Submission",
        Health.APPROVAL_REQUIRED,
        "Every submission requires explicit approval. Upwork additionally costs Connects and is "
        "priced through the cost gate before anything is sent.",
    )


def probe_cost_gate() -> Capability:
    return _cap(
        "cost_gate",
        "Cost Gate",
        Health.HEALTHY,
        f"Ceiling is ${MAX_NEW_MONTHLY_CASH_SPEND:.2f}/month. All cash requests fail closed.",
    )


DEFAULT_PROBES: dict[str, Callable[[], Capability]] = {
    "storage": probe_storage,
    "audit_log": probe_audit_log,
    "opportunity_sources": probe_opportunity_sources,
    "ai_worker": probe_ai_worker,
    "scheduler": probe_scheduler,
    "submission": probe_submission,
    "payments": probe_payments,
    "cost_gate": probe_cost_gate,
}


# ---------------------------------------------------------------------------
# LIVE mode checklist (spec section 43)
# ---------------------------------------------------------------------------


def live_mode_checklist() -> list[dict[str, Any]]:
    """Every item must pass before LIVE mode can be acknowledged."""
    st = SystemState.load()
    storage_cap = probe_storage()
    sources_cap = probe_opportunity_sources()

    return [
        {
            "name": "No paid services activated",
            "passing": MAX_NEW_MONTHLY_CASH_SPEND == 0.0,
            "detail": f"MAX_NEW_MONTHLY_CASH_SPEND = ${MAX_NEW_MONTHLY_CASH_SPEND:.2f}",
        },
        {
            "name": "Storage writable",
            "passing": storage_cap.health == Health.HEALTHY.value,
            "detail": storage_cap.detail,
        },
        {
            "name": "At least one opportunity source reachable",
            "passing": sources_cap.health in (Health.HEALTHY.value, Health.DEGRADED.value),
            "detail": sources_cap.detail,
        },
        {
            # Cannot be probed from here - it depends on who last touched a cron line on
            # GitHub - so it is carried as a standing reminder rather than a check that could
            # report a false green. Anthropic's own docs: a scheduled run is attributed to
            # "the one who last changed the workflow's cron schedule", and the action rejects
            # a bot actor. If anyone but Andres last edited a cron, scheduled AI runs refuse
            # to start, and they refuse quietly.
            "name": "Andres is the last editor of every cron schedule",
            "passing": None,
            "detail": (
                "Not verifiable from here. A scheduled run is attributed to whoever last changed "
                "the cron line, and the Claude action rejects a non-human actor - so if anyone "
                "else last edited a schedule, unattended AI runs stop without saying why. "
                "See docs/DEPLOYMENT.md."
            ),
        },
        {
            "name": "Proposal approval enabled",
            "passing": True,
            "detail": "Every proposal requires explicit approval. This is not configurable.",
        },
        {
            "name": "Financial actions manual",
            "passing": True,
            "detail": "Payments, withdrawals and purchases are never automated.",
        },
        {
            "name": "Emergency stop tested",
            "passing": _emergency_stop_tested(),
            "detail": "An emergency_stop event must appear in the audit log.",
        },
        {
            "name": "Demo data cleared",
            "passing": not any(o.is_demo for o in storage.opportunities.all()),
            "detail": "Synthetic records must not coexist with live ones.",
        },
        {
            "name": "System not in emergency stop",
            "passing": st.run_state != RunState.EMERGENCY_STOP.value,
            "detail": st.why_blocked() or "OK",
        },
    ]


def _emergency_stop_tested() -> bool:
    from . import audit

    return any(e.action == "emergency_stop" for e in audit.read_all())


def overall_status() -> tuple[str, str]:
    """One line for the top status bar. Truthful about degradation."""
    st = SystemState.load()
    if st.run_state == RunState.EMERGENCY_STOP.value:
        return "STOPPED", "RED"
    if st.run_state in (RunState.OFF.value, RunState.PAUSED.value):
        return "STOPPED" if st.run_state == RunState.OFF.value else "LIMITED", ("RED" if st.run_state == RunState.OFF.value else "YELLOW")

    # ACTIVE with nothing switched on is not running, whatever the run_state says.
    #
    # Found on the live system: it reported ACTIVE - LIVE with all eight automations disabled,
    # because an earlier emergency stop had switched them off and `resume` never put them back.
    # Nothing was scheduled, nothing would ever run, and the status bar was green about it.
    #
    # `resume` now restores them, which fixes that path. This check is the structural guarantee
    # underneath: however the automations came to be off - a stop, a manual toggle, a future bug -
    # the status must not claim the system is running when nothing can.
    if st.automations and not any(a.get("enabled") for a in st.automations.values()):
        return "IDLE - NOTHING ENABLED", "YELLOW"

    caps = [Capability(**{k: v for k, v in c.items() if k != "light"}) for c in st.capabilities.values()]
    if any(c.health == Health.DOWN.value for c in caps):
        return "LIMITED", "YELLOW"
    if any(c.health == Health.DEGRADED.value for c in caps):
        return "LIMITED", "YELLOW"
    return "RUNNING", "GREEN"
