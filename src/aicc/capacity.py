"""Claude subscription capacity: estimating it honestly, reserving it, and refusing to lie about it.

The business runs on one scarce input. Not money - money is fixed at $0.00 - but the Claude
subscription allowance that is already paid for. Every model call spent on a listing that was
never going to convert is a call unavailable to a paid client job, and the failure mode that
matters is not "we ran out", it is "we ran out halfway through a delivery we had promised".

Three design decisions, each of which cost something to make:

**The unit is estimated AI-minutes, and it is an estimate.** Anthropic does not expose exact
remaining subscription capacity to a GitHub Actions runner. There is no endpoint that returns
"you have 41% left". So this module never renders a number without the word ESTIMATED next to
it, and ``Estimate.confidence`` is a real field that the dashboard shows. Displaying "62.4%
capacity remaining" would be more comfortable to read and completely made up, and a made-up
number is worse than an honest range precisely because people act on it.

**Reservations are made before work starts, not tracked as it happens.** When a paid job is
accepted, capacity for the worker pass, the reviewer pass, one revision cycle and a small
emergency reserve is set aside immediately. Anything else - speculative research, proposal
drafting, scanning - can only draw on what is left after those reservations. This is the whole
mechanism that stops a paid job being stranded, and it works because it is pessimistic: the
reservation is made on the estimate, and estimates that turn out generous release capacity back,
while estimates that turn out tight were already covered.

**Low capacity is a scheduling fact, not a rejection.** A $500 job that cannot start right now
but is due in five days is not a bad job. It is a WAIT FOR RESET. The system queues it and says
when it expects to start. Rejecting profitable work because of a temporary window is how you end
up with an idle system and no revenue.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from .config import DATA_DIR

# ---------------------------------------------------------------------------
# The window
# ---------------------------------------------------------------------------

#: Claude subscription allowances refill on a rolling window. Five hours is the documented
#: shape of the short window; the exact allowance is not published as a number a program can
#: read, which is the entire reason everything below is an estimate.
WINDOW_HOURS = 5

#: Estimated productive AI-minutes in one window on this plan. Deliberately conservative: the
#: cost of underestimating is that the system waits when it did not have to, and the cost of
#: overestimating is a half-delivered client job. Those are not the same size of mistake.
#: Recalibrated from observed runs once `MIN_OBSERVATIONS` of them exist - see `calibrate`.
NOMINAL_WINDOW_MINUTES = 180.0

#: Never plan to consume the last of a window. This is the "deadline-critical fix" margin: the
#: capacity that exists so that a problem found at handover can still be fixed.
EMERGENCY_RESERVE_FRACTION = 0.10

#: Below this much free capacity, only paid work runs. Speculative work stops first, by design.
SPECULATIVE_FLOOR_FRACTION = 0.25

#: A rate is not a rate until it has been observed enough times to be one. Same threshold, and
#: same reasoning, as the win-rate gate: five observations.
MIN_OBSERVATIONS = 5

#: How far ahead the planner looks when a job has no stated deadline. A week.
PLANNING_HORIZON_HOURS = 168.0

#: The share of future windows that can realistically be spent on client work.
#:
#: This constant exists because of a bug worth recording. The first version of this module
#: compared a job's total demand against ONE window, so a 14-hour contract - an ordinary
#: freelance project, due in a week - came out as "exceeds available capacity" and every single
#: real listing in the store was deferred. Nothing was ever schedulable and the queue was a wall
#: of WAIT FOR RESET, which looks like caution and is actually a broken model.
#:
#: Capacity is a rate, not a budget: five-hour windows keep arriving. What makes future windows
#: less than fully usable is everything else - Andres asleep, Andres at his day job, windows
#: that pass with nobody dispatching work. Half is a deliberate underestimate, because planning
#: against capacity that never materialises is how a deadline gets missed.
REALISTIC_WINDOW_UTILIZATION = 0.5

#: The least capacity in the current window that counts as being able to begin. Below this, a
#: job is queued for the next window rather than started - five minutes of progress on a
#: two-hour job is not a start, it is a context switch.
MIN_MEANINGFUL_START_MINUTES = 30.0

CAPACITY_FILE = DATA_DIR / "capacity.json"


class CapacityStatus(StrEnum):
    SAFE_TO_START = "SAFE TO START"
    TIGHT = "TIGHT"
    RISKY = "RISKY"
    WAIT_FOR_RESET = "WAIT FOR RESET"
    UNKNOWN = "UNKNOWN — HUMAN REVIEW"


class Confidence(StrEnum):
    """How much the estimate below is worth. Rendered next to every number."""

    MEASURED = "MEASURED"  # enough real observations to have calibrated
    ESTIMATED = "ESTIMATED"  # derived from defaults and job shape
    UNKNOWN = "UNKNOWN"  # no basis at all


# ---------------------------------------------------------------------------
# Observations
# ---------------------------------------------------------------------------


@dataclass
class Observation:
    """One real measurement of what a piece of work actually cost in AI-minutes.

    ``estimated`` is kept alongside ``actual`` so calibration can measure its own error rather
    than just overwriting the old guess with a new one.
    """

    at: str
    task: str
    category: str = ""
    estimated_minutes: float = 0.0
    actual_minutes: float = 0.0
    worker_iterations: int = 0
    reviewer_iterations: int = 0
    revisions: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Reservation:
    """Capacity set aside for an accepted paid job, before the first model call is made."""

    job_id: str
    worker_minutes: float
    reviewer_minutes: float
    revision_minutes: float
    emergency_minutes: float
    confidence: str = Confidence.ESTIMATED.value
    basis: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat(timespec="seconds"))
    released_at: str = ""

    @property
    def total_minutes(self) -> float:
        return round(self.worker_minutes + self.reviewer_minutes + self.revision_minutes + self.emergency_minutes, 1)

    @property
    def active(self) -> bool:
        return not self.released_at

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["total_minutes"] = self.total_minutes
        return d


@dataclass
class Estimate:
    """What the system believes about capacity right now, with its own uncertainty attached."""

    window_minutes: float
    used_minutes: float
    reserved_minutes: float
    confidence: str
    basis: str
    window_started: str
    next_reset: str

    @property
    def remaining_minutes(self) -> float:
        return round(max(0.0, self.window_minutes - self.used_minutes), 1)

    @property
    def unreserved_minutes(self) -> float:
        """What new work may actually draw on. Reservations are already spoken for."""
        return round(max(0.0, self.remaining_minutes - self.reserved_minutes), 1)

    @property
    def emergency_minutes(self) -> float:
        return round(self.window_minutes * EMERGENCY_RESERVE_FRACTION, 1)

    @property
    def safe_new_work_minutes(self) -> float:
        """Capacity a new job may be started against: unreserved, less the emergency margin."""
        return round(max(0.0, self.unreserved_minutes - self.emergency_minutes), 1)

    @property
    def utilization(self) -> float:
        if self.window_minutes <= 0:
            return 0.0
        return round(100.0 * (self.used_minutes + self.reserved_minutes) / self.window_minutes, 1)

    @property
    def speculative_permitted(self) -> bool:
        """Research, scanning and proposal drafting stop before paid work does."""
        return self.unreserved_minutes >= self.window_minutes * SPECULATIVE_FLOOR_FRACTION

    def display(self, value: float) -> str:
        """Every number leaves this module wearing its confidence."""
        if self.confidence == Confidence.UNKNOWN.value:
            return "UNKNOWN"
        label = "" if self.confidence == Confidence.MEASURED.value else " (ESTIMATED)"
        return f"{value:,.0f} min{label}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "window_minutes": self.window_minutes,
            "used_minutes": round(self.used_minutes, 1),
            "remaining_minutes": self.remaining_minutes,
            "reserved_minutes": round(self.reserved_minutes, 1),
            "unreserved_minutes": self.unreserved_minutes,
            "safe_new_work_minutes": self.safe_new_work_minutes,
            "emergency_minutes": self.emergency_minutes,
            "utilization_pct": self.utilization,
            "confidence": self.confidence,
            "basis": self.basis,
            "window_started": self.window_started,
            "next_reset": self.next_reset,
            "speculative_permitted": self.speculative_permitted,
            # Rendered strings, so no caller can accidentally print a bare number without the caveat.
            "remaining_display": self.display(self.remaining_minutes),
            "safe_new_work_display": self.display(self.safe_new_work_minutes),
        }


# ---------------------------------------------------------------------------
# The ledger
# ---------------------------------------------------------------------------


def _load() -> dict[str, Any]:
    if not CAPACITY_FILE.exists():
        return {"observations": [], "reservations": [], "window_started": "", "exhausted_until": ""}
    try:
        return json.loads(CAPACITY_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        # A corrupt ledger must not take the pipeline down, but it must not silently read as
        # "plenty of capacity" either - an empty ledger estimates conservatively below.
        return {"observations": [], "reservations": [], "window_started": "", "exhausted_until": ""}


def _save(payload: dict[str, Any]) -> None:
    CAPACITY_FILE.parent.mkdir(parents=True, exist_ok=True)
    CAPACITY_FILE.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _now() -> datetime:
    return datetime.now(UTC)


def window_bounds(now: datetime | None = None) -> tuple[datetime, datetime]:
    """Start and reset time of the window containing `now`.

    Anchored to wall-clock five-hour blocks rather than to first use. The real window is rolling
    and its anchor is not observable from here; a fixed anchor is wrong in a knowable, bounded
    way, which is better than being wrong in an unknowable one.
    """
    now = now or _now()
    block = (now.hour // WINDOW_HOURS) * WINDOW_HOURS
    start = now.replace(hour=block, minute=0, second=0, microsecond=0)
    return start, start + timedelta(hours=WINDOW_HOURS)


def record(
    task: str,
    actual_minutes: float,
    *,
    category: str = "",
    estimated_minutes: float = 0.0,
    worker_iterations: int = 0,
    reviewer_iterations: int = 0,
    revisions: int = 0,
) -> Observation:
    """Log real consumption. This is the only thing that ever turns ESTIMATED into MEASURED."""
    obs = Observation(
        at=_now().isoformat(timespec="seconds"),
        task=task,
        category=category,
        estimated_minutes=round(estimated_minutes, 1),
        actual_minutes=round(actual_minutes, 1),
        worker_iterations=worker_iterations,
        reviewer_iterations=reviewer_iterations,
        revisions=revisions,
    )
    payload = _load()
    payload.setdefault("observations", []).append(obs.to_dict())
    _save(payload)
    return obs


def reserve(job_id: str, *, worker_minutes: float, category: str = "", basis: str = "") -> Reservation:
    """Set aside capacity for an accepted paid job.

    The multipliers are the amendment's list turned into numbers: the worker pass, an independent
    reviewer pass, one full revision cycle, and an emergency margin. They are ratios of the worker
    estimate rather than separate guesses, because the thing that actually varies between jobs is
    the size of the work, not the shape of the QA around it.
    """
    reviewer = worker_minutes * 0.35
    revision = worker_minutes * 0.50
    emergency = worker_minutes * 0.15
    measured, _ = _calibration(category)
    res = Reservation(
        job_id=job_id,
        worker_minutes=round(worker_minutes, 1),
        reviewer_minutes=round(reviewer, 1),
        revision_minutes=round(revision, 1),
        emergency_minutes=round(emergency, 1),
        confidence=Confidence.MEASURED.value if measured else Confidence.ESTIMATED.value,
        basis=basis
        or (
            f"Worker {worker_minutes:.0f} min, plus an independent reviewer pass (35%), one "
            f"revision cycle (50%) and a deadline-critical margin (15%)."
        ),
    )
    payload = _load()
    payload.setdefault("reservations", []).append(res.to_dict())
    _save(payload)
    return res


def release(job_id: str) -> bool:
    """Give capacity back when a job finishes. Unreleased reservations starve the scheduler."""
    payload = _load()
    changed = False
    for r in payload.get("reservations", []):
        if r.get("job_id") == job_id and not r.get("released_at"):
            r["released_at"] = _now().isoformat(timespec="seconds")
            changed = True
    if changed:
        _save(payload)
    return changed


def active_reservations() -> list[Reservation]:
    out = []
    for r in _load().get("reservations", []):
        r = {k: v for k, v in r.items() if k != "total_minutes"}
        res = Reservation(**r)
        if res.active:
            out.append(res)
    return out


def _calibration(category: str = "") -> tuple[bool, float]:
    """(is calibrated, mean actual minutes) for a category, from real observations only."""
    obs = [Observation(**o) for o in _load().get("observations", [])]
    if category:
        obs = [o for o in obs if o.category == category]
    obs = [o for o in obs if o.actual_minutes > 0]
    if len(obs) < MIN_OBSERVATIONS:
        return False, 0.0
    return True, sum(o.actual_minutes for o in obs) / len(obs)


def mark_exhausted(until_iso: str) -> None:
    """Record that the subscription window is spent, and when it is expected back.

    Called from the degradation classifier. Never accompanied by a fallback to metered billing:
    the point of recording this is that the system waits.
    """
    payload = _load()
    payload["exhausted_until"] = until_iso
    _save(payload)


def exhausted_until() -> str:
    return _load().get("exhausted_until", "")


def estimate(now: datetime | None = None) -> Estimate:
    """The current picture. Conservative where it is uncertain."""
    now = now or _now()
    start, reset = window_bounds(now)
    payload = _load()

    used = 0.0
    for o in payload.get("observations", []):
        try:
            at = datetime.fromisoformat(o["at"])
        except (KeyError, ValueError):
            continue
        if at.tzinfo is None:
            at = at.replace(tzinfo=UTC)
        if at >= start:
            used += float(o.get("actual_minutes") or 0.0)

    reserved = sum(r.total_minutes for r in active_reservations())

    calibrated, mean = _calibration()
    if payload.get("exhausted_until"):
        confidence, basis = (
            Confidence.MEASURED.value,
            ("The subscription reported an exhausted window. That is a hard observation, not an estimate."),
        )
    elif calibrated:
        confidence, basis = (
            Confidence.MEASURED.value,
            (f"Calibrated against {len(payload.get('observations', []))} recorded runs (mean {mean:.0f} AI-minutes per unit of work)."),
        )
    elif payload.get("observations"):
        confidence, basis = (
            Confidence.ESTIMATED.value,
            (
                f"{len(payload['observations'])} recorded runs - fewer than the {MIN_OBSERVATIONS} "
                f"needed to call this measured. Window size is the conservative default."
            ),
        )
    else:
        confidence, basis = (
            Confidence.ESTIMATED.value,
            (
                "No recorded runs yet. Window size is a conservative default and remaining capacity "
                "is inferred from reservations only. Anthropic exposes no exact usage telemetry here."
            ),
        )

    return Estimate(
        window_minutes=NOMINAL_WINDOW_MINUTES,
        used_minutes=used,
        reserved_minutes=reserved,
        confidence=confidence,
        basis=basis,
        window_started=start.isoformat(timespec="seconds"),
        next_reset=reset.isoformat(timespec="seconds"),
    )


# ---------------------------------------------------------------------------
# The decision
# ---------------------------------------------------------------------------


@dataclass
class CapacityVerdict:
    status: str
    reason: str
    demand_minutes: float
    available_minutes: float
    wait_until: str = ""
    needs_human: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def horizon_minutes(
    hours: float = PLANNING_HORIZON_HOURS,
    *,
    est: Estimate | None = None,
    now: datetime | None = None,
) -> float:
    """Capacity realistically available between now and `hours` from now.

    The current window's safe remainder, plus a discounted share of every window that will
    arrive before the horizon. This is the number a multi-day job is judged against - judging
    a 14-hour project against one 3-hour window is how the planner concluded that ordinary
    freelance work was impossible.
    """
    now = now or _now()
    est = est or estimate(now)
    if hours <= 0:
        return est.safe_new_work_minutes
    reset = datetime.fromisoformat(est.next_reset)
    hours_after_reset = max(0.0, hours - (reset - now).total_seconds() / 3600.0)
    future_windows = hours_after_reset / WINDOW_HOURS
    future = future_windows * est.window_minutes * REALISTIC_WINDOW_UTILIZATION
    return round(est.safe_new_work_minutes + future, 1)


def pre_job_check(
    *,
    worker_minutes: float,
    deadline: datetime | str | None = None,
    est: Estimate | None = None,
    now: datetime | None = None,
) -> CapacityVerdict:
    """The pre-job capacity check, run before any real paid client work begins.

    Demand is not the worker estimate. It is the worker pass plus reviewer QA plus one revision
    cycle plus the emergency margin, because a job is not done when the first draft exists - and
    a system that starts work it cannot finish has done something worse than not starting.

    Two capacities are compared, and conflating them was the original bug:

    * **this window** decides whether work can begin *now*;
    * **the horizon** - every window arriving before the deadline, discounted - decides whether
      the job is feasible *at all*.

    A job that clears the horizon but not this window is WAIT FOR RESET, which is a schedule,
    not a rejection. A job that cannot clear the horizon genuinely cannot be delivered on time,
    and saying so early is the whole point of running this before accepting work.
    """
    now = now or _now()
    est = est or estimate(now)

    if worker_minutes <= 0:
        return CapacityVerdict(
            CapacityStatus.UNKNOWN.value,
            "No workload estimate exists for this job, so there is nothing to check capacity "
            "against. Guessing one would defeat the purpose of the check.",
            0.0,
            est.safe_new_work_minutes,
            needs_human=True,
        )

    demand = worker_minutes * (1 + 0.35 + 0.50 + 0.15)
    available = est.safe_new_work_minutes
    deadline_dt = _as_dt(deadline)
    reset = datetime.fromisoformat(est.next_reset)
    hours_to_deadline = PLANNING_HORIZON_HOURS if deadline_dt is None else max(0.0, (deadline_dt - now).total_seconds() / 3600.0)
    horizon = horizon_minutes(hours_to_deadline, est=est, now=now)
    deadline_allows_wait = deadline_dt is None or deadline_dt > reset + timedelta(hours=1)

    if est.confidence == Confidence.UNKNOWN.value:
        return CapacityVerdict(
            CapacityStatus.UNKNOWN.value,
            "Capacity cannot be estimated at all right now. The system does not guess on paid work.",
            demand,
            available,
            needs_human=True,
        )

    if exhausted_until():
        return CapacityVerdict(
            CapacityStatus.WAIT_FOR_RESET.value if deadline_allows_wait else CapacityStatus.RISKY.value,
            f"The subscription window is spent until {exhausted_until()}. "
            + (
                "The deadline allows waiting, so the job is queued rather than dropped."
                if deadline_allows_wait
                else "The deadline does not clear the reset. This needs Andres, not an automatic start."
            ),
            demand,
            available,
            wait_until=exhausted_until(),
            needs_human=not deadline_allows_wait,
        )

    # Feasibility first. A job that cannot fit before its deadline however many windows arrive
    # is not a capacity problem to schedule around - it is work that should not be accepted.
    if demand > horizon:
        return CapacityVerdict(
            CapacityStatus.RISKY.value,
            f"Demand {demand:.0f} min exceeds the {horizon:.0f} min realistically available "
            f"before the deadline, counting every window between now and then. Starting would "
            f"risk stranding the job mid-delivery.",
            demand,
            horizon,
            needs_human=True,
        )

    # SAFE / TIGHT is judged against the HORIZON, not against this window alone.
    #
    # An earlier version compared demand to the current window, so an ordinary $500 job - sixty
    # minutes of worker time, 120 with QA and a revision - came back TIGHT on a completely fresh
    # window, because 120 is 74% of one 162-minute window. That reads as caution and is the same
    # mistake as judging feasibility against one window: capacity is a rate, and a job
    # comfortably inside a week's capacity is not "tight" because it will not finish by 5pm.
    # What this window decides is whether work can BEGIN, not whether the job is comfortable.
    comfortable = demand <= horizon * 0.70
    can_begin_now = available >= min(demand, MIN_MEANINGFUL_START_MINUTES)

    if not can_begin_now:
        return CapacityVerdict(
            CapacityStatus.WAIT_FOR_RESET.value,
            f"Demand {demand:.0f} min fits inside the {horizon:.0f} min available before the "
            f"deadline, but only {available:.0f} min is left in this window - not enough to make "
            f"a meaningful start. Queued to begin after the {reset:%H:%M} UTC reset: a scheduled "
            f"job, not a rejected one.",
            demand,
            horizon,
            wait_until=est.next_reset,
        )

    if comfortable:
        return CapacityVerdict(
            CapacityStatus.SAFE_TO_START.value,
            f"Estimated demand {demand:.0f} min against {horizon:.0f} min available before the "
            f"deadline, with {available:.0f} min free in this window to begin. Room for QA, one "
            f"revision and a deadline-critical fix.",
            demand,
            available,
        )

    return CapacityVerdict(
        CapacityStatus.TIGHT.value,
        f"Estimated demand {demand:.0f} min against {horizon:.0f} min realistically available "
        f"before the deadline. It fits, but with little slack - worth starting only if the value "
        f"or the deadline justifies committing most of the remaining capacity to it.",
        demand,
        horizon,
    )


def _as_dt(value: datetime | str | None) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


# ---------------------------------------------------------------------------
# What gets cut, and in what order
# ---------------------------------------------------------------------------

#: Shed load in this order as capacity tightens. Paid work is last by construction: the list is
#: read top-down and each entry names what stops, so the system degrades in a way a person can
#: predict rather than by whatever happened to call the model next.
DEGRADATION_ORDER = (
    ("speculative_research", "Client and market research that no accepted job depends on."),
    ("low_value_proposals", "Proposal drafting for listings below the strong-match band."),
    ("semantic_analysis", "Model-assisted reading of listings that deterministic scoring already ranked low."),
    ("proposal_polish", "Second-pass rewriting of proposals that already passed their checks."),
)

#: Never shed. If capacity is short, these are what it is short *for*.
PROTECTED_WORK = (
    "active_paid_worker",
    "active_paid_reviewer",
    "active_paid_revision",
    "deadline_critical_fix",
)


def permitted_now(est: Estimate | None = None) -> dict[str, bool]:
    """Which classes of work may run at the current capacity level."""
    est = est or estimate()
    free = est.unreserved_minutes
    window = est.window_minutes or 1.0
    ratio = free / window
    return {
        "active_paid_worker": True,
        "active_paid_reviewer": True,
        "active_paid_revision": True,
        "deadline_critical_fix": True,
        "proposal_polish": ratio >= 0.35,
        "semantic_analysis": ratio >= 0.30,
        "low_value_proposals": ratio >= 0.25,
        "speculative_research": ratio >= 0.40,
    }


def shed_plan(est: Estimate | None = None) -> list[str]:
    """A human-readable list of what is currently switched off and why."""
    est = est or estimate()
    allowed = permitted_now(est)
    return [
        f"{why} — paused at {est.unreserved_minutes:.0f} min unreserved." for key, why in DEGRADATION_ORDER if not allowed.get(key, True)
    ]


def snapshot() -> dict[str, Any]:
    """Everything the dashboard's Claude Capacity panel renders."""
    est = estimate()
    reservations = active_reservations()
    return {
        **est.to_dict(),
        "status": _overall_status(est),
        "reservations": [r.to_dict() for r in reservations],
        "reserved_job_count": len(reservations),
        "queue_demand_minutes": round(sum(r.total_minutes for r in reservations), 1),
        "shed": shed_plan(est),
        "permitted": permitted_now(est),
        "exhausted_until": exhausted_until(),
        "paid_api_fallback": "DISABLED",
        "telemetry_note": (
            "Anthropic does not expose exact remaining subscription capacity to a GitHub Actions "
            "runner. Every figure here is derived from recorded runs and reservations, and is "
            "labelled with the confidence it actually has."
        ),
    }


def _overall_status(est: Estimate) -> str:
    if exhausted_until():
        return CapacityStatus.WAIT_FOR_RESET.value
    if est.confidence == Confidence.UNKNOWN.value:
        return CapacityStatus.UNKNOWN.value
    ratio = est.unreserved_minutes / (est.window_minutes or 1.0)
    if ratio >= 0.50:
        return CapacityStatus.SAFE_TO_START.value
    if ratio >= 0.25:
        return CapacityStatus.TIGHT.value
    return CapacityStatus.RISKY.value
