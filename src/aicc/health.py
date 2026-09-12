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
from .proof_transport import WorkerState
from .state import Capability, Health, RunState, SystemState


def _cap(
    key: str,
    label: str,
    health: Health,
    detail: str,
    blocking: str = "",
    last_success: str = "",
    state: str = "",
) -> Capability:
    return Capability(
        key=key,
        label=label,
        health=health.value,
        detail=detail,
        blocking_reason=blocking,
        last_success=last_success,
        state=state,
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
    """Has the AI worker actually executed, on the machine where client work runs?

    **The recorded proof is the authority here, not this process's environment.** That ordering
    is the correction of a real mistake: the earlier version asked `ClaudeWorker.available()`
    first, which inspects whichever machine happens to be rendering the dashboard. That is the
    wrong machine. Client jobs run on a GitHub Actions runner; the dashboard is built in a
    different job which - by design now - holds no Claude credential at all. Asking the local
    environment produced a confident answer to a question nobody asked.

    So the order is: what did the last validated proof from a runner establish? Only when no
    proof has ever arrived does the local environment get a word, and then only to say which
    piece is missing *here* - which is diagnostic, never a green light.

    Three things had to be fixed to get here, each because the previous version could show green
    over nothing:

    1. A credential is not a working worker. An earlier version reported HEALTHY on the token
       alone while ``ClaudeWorker.execute`` raised in every environment.
    2. A credential plus an executor is not a working worker either. A CI run with both present
       failed with `401 OAuth access token is invalid`.
    3. A proof from the wrong machine is not a working worker. A pass on a laptop attests to that
       laptop's credential, not to the repository secret Actions uses.

    ``state.py``'s own contract is that a capability is "derived from a live probe, never from a
    config flag, because a config flag records an intention and a probe records reality". A
    validated attestation of a real `claude -p` call is that probe.
    """
    from . import worker_proof
    from .fulfillment.worker import ClaudeWorker

    proof = worker_proof.last_result()
    state = proof.get("state", str(WorkerState.NOT_YET_VERIFIED))
    reason = proof.get("reason", "")
    run_url = proof.get("run_url", "")
    where = f" Evidence: {run_url}" if run_url else ""

    # The seven states, each mapped to the lamp it earns and the action it calls for.
    #
    # GREEN/RED cannot express what a person needs to do next. "Broken" and "never tried" call
    # for different actions, and so do "the credential was rejected" and "the window is spent" -
    # one needs a person at a browser, the other needs an hour of patience. Exactly one state may
    # be green, and `proof_transport.GREEN_STATES` asserts that at import.
    if state == WorkerState.HEALTHY:
        return _cap(
            "ai_worker",
            "AI Worker",
            Health.HEALTHY,
            f"Verified. {reason}",
            last_success=proof.get("generated_at", ""),
            state=str(WorkerState.HEALTHY),
        )

    if state == WorkerState.AUTH_FAILED:
        return _cap(
            "ai_worker",
            "AI Worker",
            Health.DOWN,
            f"AUTH FAILED. {reason}{where}",
            "Andres must run `claude setup-token` and update the CLAUDE_CODE_OAUTH_TOKEN "
            "repository secret. Nothing else unblocks this, and it does not recover on its own.",
            state=str(WorkerState.AUTH_FAILED),
        )

    if state == WorkerState.CAPACITY_LIMITED:
        return _cap(
            "ai_worker",
            "AI Worker",
            Health.DEGRADED,
            f"CAPACITY LIMITED. {reason}{where}",
            "Nothing to do. The window resets on its own and the consequence is waiting, never a "
            "bill. The rule-based worker carries the pipeline meanwhile.",
            state=str(WorkerState.CAPACITY_LIMITED),
        )

    if state == WorkerState.STALE_PROOF:
        return _cap(
            "ai_worker",
            "AI Worker",
            Health.DEGRADED,
            f"STALE PROOF. {reason}{where}",
            "The daily Claude worker run has not landed a fresh proof. Check whether the scheduled workflow is still running.",
            state=str(WorkerState.STALE_PROOF),
        )

    if state == WorkerState.DEGRADED:
        return _cap(
            "ai_worker",
            "AI Worker",
            Health.DEGRADED,
            f"DEGRADED. {reason}{where}",
            "The worker ran and failed for a reason that is neither auth nor capacity. Read the run linked above.",
            state=str(WorkerState.DEGRADED),
        )

    # No valid proof has ever arrived. Only now does the local environment get a word, and only
    # to say which piece is missing HERE - which is a different claim from "the worker is broken".
    available, why = ClaudeWorker.available()
    if not available and os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
        return _cap(
            "ai_worker",
            "AI Worker",
            Health.DEGRADED,
            f"CONFIGURED BUT NOT OPERATIONAL. {why}",
            "A credential is present here and nothing can run it. AI work executes on a GitHub "
            "Actions runner where the CLI is installed; this environment is not that runner.",
            state=str(WorkerState.CONFIGURED_NOT_OPERATIONAL),
        )
    if not available and os.environ.get("ANTHROPIC_API_KEY"):
        return _cap(
            "ai_worker",
            "AI Worker",
            Health.DEGRADED,
            "CONFIGURED BUT NOT OPERATIONAL. A paid API key is present. That bills per token and is outside the zero-cost rule.",
            "Paid API billing is not approved. Use CLAUDE_CODE_OAUTH_TOKEN instead.",
            state=str(WorkerState.CONFIGURED_NOT_OPERATIONAL),
        )

    rejections = proof.get("rejections") or []
    detail = f"NOT YET VERIFIED. {reason}"
    if rejections:
        detail += " Rejected because: " + "; ".join(str(r) for r in rejections[:3])
    return _cap(
        "ai_worker",
        "AI Worker",
        Health.NOT_CONFIGURED,
        detail + where,
        "A credential merely existing is not evidence. The Claude worker workflow must land a "
        "validated proof from a runner before this can read HEALTHY.",
        state=str(WorkerState.NOT_YET_VERIFIED),
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
