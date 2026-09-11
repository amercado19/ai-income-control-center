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


def start(actor: Actor = Actor.ANDRES) -> tuple[bool, str]:
    st = SystemState.load()
    if st.run_state == RunState.EMERGENCY_STOP.value:
        msg = "Refused: emergency stop is engaged. Resume explicitly first."
        audit.record("start_refused", actor=actor, object_type="system", result="refused", error=msg)
        return False, msg
    if st.mode == "LIVE" and not st.live_mode_acknowledged:
        msg = "Refused: LIVE mode requires the safety checklist acknowledgement."
        audit.record("start_refused", actor=actor, object_type="system", result="refused", error=msg)
        return False, msg
    before = st.run_state
    st.run_state = RunState.ACTIVE.value
    st.started_at = utcnow()
    st.save()
    audit.record("system_started", actor=actor, object_type="system", before=before, after=st.run_state)
    return True, "SYSTEM ACTIVE"


def pause(actor: Actor = Actor.ANDRES) -> tuple[bool, str]:
    st = SystemState.load()
    if st.run_state == RunState.EMERGENCY_STOP.value:
        return False, "Emergency stop already engaged."
    before = st.run_state
    st.run_state = RunState.PAUSED.value
    st.save()
    audit.record("system_paused", actor=actor, object_type="system", before=before, after=st.run_state)
    return True, "SYSTEM PAUSED"


def resume(actor: Actor = Actor.ANDRES) -> tuple[bool, str]:
    st = SystemState.load()
    before = st.run_state
    if st.run_state == RunState.EMERGENCY_STOP.value:
        st.emergency_stop_reason = ""
    st.run_state = RunState.ACTIVE.value
    st.save()
    audit.record("system_resumed", actor=actor, object_type="system", before=before, after=st.run_state)
    return True, "SYSTEM ACTIVE"


def emergency_stop(reason: str = "", actor: Actor = Actor.ANDRES) -> tuple[bool, str]:
    """Disables every external action immediately. Always succeeds - a stop must never fail."""
    st = SystemState.load()
    before = st.run_state
    st.run_state = RunState.EMERGENCY_STOP.value
    st.emergency_stop_reason = reason or "Engaged manually."
    st.stopped_at = utcnow()
    for key in st.automations:
        st.automations[key]["enabled"] = False
    st.save()
    audit.record(
        "emergency_stop",
        actor=actor,
        object_type="system",
        before=before,
        after=st.run_state,
        error=reason,
    )
    return True, "EMERGENCY STOP ENGAGED - all external actions disabled"


def stop(actor: Actor = Actor.ANDRES) -> tuple[bool, str]:
    st = SystemState.load()
    before = st.run_state
    st.run_state = RunState.OFF.value
    st.stopped_at = utcnow()
    st.save()
    audit.record("system_stopped", actor=actor, object_type="system", before=before, after=st.run_state)
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
    """Spec section 43. Only a human may call this, and the checklist must already pass."""
    from .health import live_mode_checklist

    checklist = live_mode_checklist()
    failed = [c for c in checklist if not c["passing"]]
    if failed:
        names = ", ".join(c["name"] for c in failed)
        audit.record("live_mode_refused", actor=actor, object_type="system", result="refused", error=names)
        return False, f"Refused: checklist not passing - {names}"
    st = SystemState.load()
    st.live_mode_acknowledged = True
    st.mode = "LIVE"
    st.save()
    audit.record("live_mode_enabled", actor=actor, object_type="system", after="LIVE")
    return True, "LIVE MODE ENABLED"


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
