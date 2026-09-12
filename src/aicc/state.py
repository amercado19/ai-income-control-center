"""System state machine and honest capability reporting (spec sections 10, 47, 48).

Two rules govern this module.

1. EMERGENCY STOP disables all external actions immediately and cannot be cleared by anything
   except an explicit human resume. Nothing else in the codebase may set ``ACTIVE``.

2. A capability reports GREEN only when it is actually operational right now. A capability that
   is designed, coded and tested but has no working credential reports BLOCKED, not green.
   ``Capability.status`` is derived from a live probe, never from a config flag, because a
   config flag records an intention and a probe records reality.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any

from . import audit
from .config import STATE_FILE
from .models import Actor, utcnow


class RunState(StrEnum):
    OFF = "OFF"
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    EMERGENCY_STOP = "EMERGENCY_STOP"


class Health(StrEnum):
    HEALTHY = "HEALTHY"  # green
    DEGRADED = "DEGRADED"  # yellow
    DOWN = "DOWN"  # red
    NOT_CONFIGURED = "NOT_CONFIGURED"  # white - designed but never claimed to work
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"  # yellow - works, but gated on a human by design


LIGHT = {
    Health.HEALTHY: "GREEN",
    Health.DEGRADED: "YELLOW",
    Health.APPROVAL_REQUIRED: "YELLOW",
    Health.DOWN: "RED",
    Health.NOT_CONFIGURED: "WHITE",
}


@dataclass
class Capability:
    """One thing the system either can or cannot do, and the honest reason."""

    key: str
    label: str
    health: str = Health.NOT_CONFIGURED.value
    detail: str = ""
    last_success: str = ""
    blocking_reason: str = ""
    state: str = ""
    """A finer-grained state name, where the four lamps are not enough to say what to do.

    The AI Worker needs seven (`proof_transport.WorkerState`): a rejected credential needs a
    person at a browser, a spent usage window needs nobody at all, and "never verified" is not
    the same as "broken". All three would otherwise share one lamp, and a reader would have to
    parse prose to tell them apart. Empty for capabilities whose lamp already says everything.
    """

    @property
    def light(self) -> str:
        return LIGHT.get(Health(self.health), "WHITE")

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["light"] = self.light
        return d


@dataclass
class AutomationToggle:
    """One scheduled automation (spec section 31)."""

    key: str
    label: str
    enabled: bool = True
    last_run: str = ""
    next_run: str = ""
    last_status: str = ""
    last_error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


#: What EMERGENCY STOP halts. New work of every kind - not only outbound actions, because a
#: scheduled scan that keeps running is the system continuing to acquire obligations while its
#: owner believes it is stopped.
HALTED_BY_EMERGENCY_STOP = frozenset(
    {
        "ai_work",
        "claude_call",
        "marketplace_action",
        "proposal_drafting",
        "proposal_submission",
        "client_communication",
        "scheduled_acquisition",
        "opportunity_scan",
        "job_execution",
        "job_delivery",
        "fiverr_publish",
        "payment_action",
    }
)

#: What EMERGENCY STOP must never halt. These are the controls that make a stopped system
#: accountable: the record of what happened, the check on whether it is still safe, the redactor
#: that keeps private material out of a public repo, and the gate that refuses spending. Turning
#: any of them off with the stop button would remove the oversight exactly when it is needed.
NEVER_HALTED = frozenset(
    {
        "audit_log",
        "safety_selftest",
        "redaction",
        "cost_gate",
        "dashboard_build",
        "health_check",
        "injection_scan",
        "policy_evaluation",
    }
)

DEFAULT_AUTOMATIONS = [
    ("opportunity_scan", "Opportunity Scan"),
    ("job_scoring", "Job Scoring"),
    ("proposal_drafting", "Proposal Drafting"),
    ("job_worker", "Job Worker"),
    ("qa_worker", "QA Worker"),
    ("metrics", "Metrics"),
    ("backup", "Backup"),
    ("health_check", "Health Check"),
]


@dataclass
class SystemState:
    run_state: str = RunState.OFF.value
    mode: str = "DEMO"  # DEMO | LIVE
    live_mode_acknowledged: bool = False
    emergency_stop_reason: str = ""

    automations: dict[str, dict[str, Any]] = field(default_factory=dict)
    automations_disabled_by_stop: list[str] = field(default_factory=list)
    """Which automations the last emergency stop switched off, so `resume` can restore those and
    only those. Empty at rest; an emergency stop fills it and a resume clears it."""
    capabilities: dict[str, dict[str, Any]] = field(default_factory=dict)

    started_at: str = ""
    stopped_at: str = ""
    updated_at: str = field(default_factory=utcnow)

    # Set true only once a real (non-demo) revenue row exists. Gates the upgrade
    # recommendation system in spec section 50.
    has_real_revenue: bool = False

    def __post_init__(self) -> None:
        if not self.automations:
            self.automations = {k: AutomationToggle(k, label).to_dict() for k, label in DEFAULT_AUTOMATIONS}

    # -- external action permission -------------------------------------------------

    def external_actions_allowed(self) -> bool:
        """The single gate every outbound action must pass through."""
        return self.run_state == RunState.ACTIVE.value

    def activity_allowed(self, activity: str) -> tuple[bool, str]:
        """Whether one named activity may run in the current state.

        ``external_actions_allowed`` answers a coarser question - may anything leave the system -
        and that is not enough for a stop button. A stop that also silenced the audit log would
        destroy the record of why it was pressed, and a stop that deleted queued work would
        punish the person for using it. So the scope is explicit in both directions:

        * ``HALTED_BY_EMERGENCY_STOP`` - new work of every kind, including the scheduled scans
          that would otherwise quietly keep acquiring.
        * ``NEVER_HALTED`` - the audit log, the self-test, redaction, the cost gate and the
          dashboard. These are how the stopped system stays accountable and inspectable. A
          control that can switch off its own oversight is not a safety control.

        Nothing here deletes anything. Records, drafts and queued jobs survive a stop untouched;
        pressing it is meant to be cheap enough that he presses it when unsure.
        """
        key = (activity or "").strip().lower().replace(" ", "_").replace("-", "_")
        if key in NEVER_HALTED:
            return True, ""
        if self.run_state == RunState.EMERGENCY_STOP.value:
            return False, f"EMERGENCY STOP engaged. {self.emergency_stop_reason}".strip()
        if key in HALTED_BY_EMERGENCY_STOP:
            if self.run_state != RunState.ACTIVE.value:
                return False, self.why_blocked()
            return True, ""
        # Unknown activity: treated as halted work rather than as oversight. Fails safe.
        if self.run_state != RunState.ACTIVE.value:
            return False, self.why_blocked()
        return True, ""

    def emergency_stop_scope(self) -> dict[str, Any]:
        """What the stop button does and does not do, for the dashboard to render verbatim."""
        return {
            "engaged": self.run_state == RunState.EMERGENCY_STOP.value,
            "reason": self.emergency_stop_reason,
            "halts": sorted(HALTED_BY_EMERGENCY_STOP),
            "never_halts": sorted(NEVER_HALTED),
            "deletes_nothing": True,
            "note": (
                "Stops new work of every kind and preserves every existing record. The audit "
                "log, the safety self-test, redaction and the cost gate keep running - a stop "
                "that disabled its own oversight would be the least safe moment to have one."
            ),
        }

    def why_blocked(self) -> str:
        if self.run_state == RunState.EMERGENCY_STOP.value:
            return f"EMERGENCY STOP engaged. {self.emergency_stop_reason}".strip()
        if self.run_state == RunState.PAUSED.value:
            return "System is paused."
        if self.run_state == RunState.OFF.value:
            return "System is off. Press START BUSINESS."
        return ""

    # -- persistence ----------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> SystemState:
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})

    def save(self) -> None:
        self.updated_at = utcnow()
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = STATE_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
        tmp.replace(STATE_FILE)  # atomic on POSIX; a crash never leaves a half-written state

    @classmethod
    def load(cls) -> SystemState:
        if not STATE_FILE.exists():
            return cls()
        try:
            return cls.from_dict(json.loads(STATE_FILE.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            # A corrupt state file must not fail open into ACTIVE.
            audit.record("state_file_corrupt_recovered_to_off", actor=Actor.SYSTEM, result="error")
            return cls(run_state=RunState.OFF.value)


# ---------------------------------------------------------------------------
# Transitions - the only functions permitted to change run_state
# ---------------------------------------------------------------------------


def start(actor: Actor = Actor.ANDRES, source: str = "") -> tuple[bool, str]:
    st = SystemState.load()
    if st.run_state == RunState.EMERGENCY_STOP.value:
        msg = "Refused: emergency stop is engaged. Resume explicitly first."
        audit.record("start_refused", actor=actor, source=source, object_type="system", result="refused", error=msg)
        return False, msg
    if st.mode == "LIVE" and not st.live_mode_acknowledged:
        msg = "Refused: LIVE mode requires the safety checklist acknowledgement."
        audit.record("start_refused", actor=actor, source=source, object_type="system", result="refused", error=msg)
        return False, msg
    before = st.run_state
    st.run_state = RunState.ACTIVE.value
    st.started_at = utcnow()
    st.save()
    audit.record("system_started", actor=actor, source=source, object_type="system", before=before, after=st.run_state)
    return True, "SYSTEM ACTIVE"


def pause(actor: Actor = Actor.ANDRES, source: str = "") -> tuple[bool, str]:
    st = SystemState.load()
    if st.run_state == RunState.EMERGENCY_STOP.value:
        return False, "Emergency stop already engaged."
    before = st.run_state
    st.run_state = RunState.PAUSED.value
    st.save()
    audit.record("system_paused", actor=actor, source=source, object_type="system", before=before, after=st.run_state)
    return True, "SYSTEM PAUSED"


def resume(actor: Actor = Actor.ANDRES, source: str = "") -> tuple[bool, str]:
    """Undo a pause or an emergency stop - including what the stop switched off.

    The bug this exists for was found on the live system, by pressing the button and then reading
    the state file. It reported ACTIVE with all eight automations disabled, and had done for some
    time: an earlier emergency stop had switched them off, and `resume` set `run_state` back to
    ACTIVE without touching them. Nothing was scheduled to run. The dashboard said ACTIVE - LIVE.

    A resume that resumes nothing is worse than one that fails, because a failure is visible. So
    the stop now records which automations it disabled, and the resume restores exactly those -
    not a blanket "switch everything on", which would silently re-enable something Andres had
    turned off deliberately months earlier.
    """
    st = SystemState.load()
    before = st.run_state
    restored: list[str] = []
    if st.run_state == RunState.EMERGENCY_STOP.value:
        st.emergency_stop_reason = ""
        for key in st.automations_disabled_by_stop:
            if key in st.automations:
                st.automations[key]["enabled"] = True
                restored.append(key)
        st.automations_disabled_by_stop = []
    st.run_state = RunState.ACTIVE.value
    st.save()
    audit.record(
        "system_resumed",
        actor=actor,
        source=source,
        object_type="system",
        before=before,
        after={"run_state": st.run_state, "automations_restored": restored},
    )
    if restored:
        return True, f"SYSTEM ACTIVE - {len(restored)} automation(s) re-enabled: {', '.join(restored)}"
    return True, "SYSTEM ACTIVE"


def emergency_stop(reason: str = "", actor: Actor = Actor.ANDRES, source: str = "") -> tuple[bool, str]:
    """Disables every external action immediately. Always succeeds - a stop must never fail."""
    st = SystemState.load()
    before = st.run_state
    st.run_state = RunState.EMERGENCY_STOP.value
    st.emergency_stop_reason = reason or "Engaged manually."
    st.stopped_at = utcnow()
    # Remembered so `resume` can put back exactly what this switched off, and nothing else. An
    # automation Andres had already disabled must stay disabled through a stop and a resume.
    st.automations_disabled_by_stop = [k for k, a in st.automations.items() if a.get("enabled")]
    for key in st.automations:
        st.automations[key]["enabled"] = False
    st.save()
    audit.record(
        "emergency_stop",
        actor=actor,
        source=source,
        object_type="system",
        before=before,
        after=st.run_state,
        error=reason,
    )
    return True, "EMERGENCY STOP ENGAGED - all external actions disabled"


def stop(actor: Actor = Actor.ANDRES, source: str = "") -> tuple[bool, str]:
    st = SystemState.load()
    before = st.run_state
    st.run_state = RunState.OFF.value
    st.stopped_at = utcnow()
    st.save()
    audit.record("system_stopped", actor=actor, source=source, object_type="system", before=before, after=st.run_state)
    return True, "SYSTEM OFF"


def set_automation(key: str, enabled: bool, actor: Actor = Actor.ANDRES) -> bool:
    st = SystemState.load()
    if key not in st.automations:
        return False
    before = st.automations[key]["enabled"]
    st.automations[key]["enabled"] = enabled
    st.save()
    audit.record(
        "automation_toggled",
        actor=actor,
        object_type="automation",
        object_id=key,
        before=before,
        after=enabled,
    )
    return True


def acknowledge_live_mode(actor: Actor = Actor.ANDRES) -> tuple[bool, str]:
    """Spec section 43. The checklist must pass, and whoever flipped it is recorded truthfully.

    Two things worth stating plainly, because both were once wrong here.

    **An unverifiable item is not a failing item.** ``live_mode_checklist`` returns three states:
    True, False, and None for a condition that genuinely cannot be probed from inside the system
    (today: who last edited a cron schedule on GitHub). The original filter was
    ``if not c["passing"]``, which treats None as False - so live mode was permanently blocked on
    a question the system can never answer. Rendering an unverifiable condition as a failure is
    the same dishonesty as a green light with nothing behind it, only pointed the other way.
    Unverifiable items are surfaced in the return message instead, where a person can act on them.

    **The actor is recorded as whoever actually called it.** The default is ANDRES because this
    is normally his decision, but nothing here assumes it. When the system enables live mode on
    his instruction, it passes ``Actor.CLAUDE`` and the audit log says CLAUDE - because an AI
    action recorded under his name is a false statement about who did what, and the audit log is
    the one place in this system where that would be least recoverable.
    """
    from .health import live_mode_checklist

    checklist = live_mode_checklist()
    failed = [c for c in checklist if c["passing"] is False]
    unverifiable = [c for c in checklist if c["passing"] is None]
    if failed:
        names = ", ".join(c["name"] for c in failed)
        audit.record("live_mode_refused", actor=actor, object_type="system", result="refused", error=names)
        return False, f"Refused: checklist not passing - {names}"
    st = SystemState.load()
    st.live_mode_acknowledged = True
    st.mode = "LIVE"
    st.save()
    audit.record("live_mode_enabled", actor=actor, object_type="system", after="LIVE")
    msg = "LIVE MODE ENABLED"
    if unverifiable:
        msg += (
            " - with "
            + str(len(unverifiable))
            + " item(s) the system cannot verify from here: "
            + ", ".join(c["name"] for c in unverifiable)
            + ". Read them in docs/DEPLOYMENT.md; they are your call, not a blocker."
        )
    return True, msg


# ---------------------------------------------------------------------------
# Capability probing
# ---------------------------------------------------------------------------


def probe_capabilities(probes: dict[str, Callable[[], Capability]] | None = None) -> list[Capability]:
    """Run every capability probe and persist the results.

    Each probe returns a Capability reflecting what it actually observed. A probe that raises is
    recorded as DOWN with the exception text - never silently green.
    """
    from .health import DEFAULT_PROBES

    probes = probes if probes is not None else DEFAULT_PROBES
    results: list[Capability] = []
    for key, probe in probes.items():
        try:
            cap = probe()
        except Exception as exc:  # noqa: BLE001 - a failing probe is data, not a crash
            cap = Capability(
                key=key,
                label=key.replace("_", " ").title(),
                health=Health.DOWN.value,
                detail="Probe raised an exception.",
                blocking_reason=f"{type(exc).__name__}: {exc}",
            )
        results.append(cap)

    st = SystemState.load()
    st.capabilities = {c.key: c.to_dict() for c in results}
    st.save()
    return results
