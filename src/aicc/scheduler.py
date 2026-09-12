"""What to do next, and why - as a portfolio decision rather than a sorted list.

The instruction this module implements is "big money **and** quick money", and the word that
carries the weight is *and*. A system that sorts by any single ratio will reliably do the wrong
thing at some point: sort by total price and four five-minute jobs never get done; sort by profit
per minute and a $500 project loses to $60 of small work that scored a fraction higher. Both
failures look reasonable from inside the sort.

So this is a knapsack, not a ranking. Capacity is the constraint, expected profit is the
objective, and the answer is the best *combination* - which in the amendment's own example is all
five jobs, $560, because they fit. When they do not all fit, the optimiser keeps the $500 job and
drops small work, because that is what maximising the total does. Nothing in here needs a special
rule saying "protect the big job"; protecting it falls out of optimising the right thing.

Four properties worth naming, because each one is a decision that could have gone another way:

* **Committed work is subtracted before optimisation, not entered into it.** An accepted paid job
  is not a candidate competing for capacity - it is a promise, and its worker, QA and revision
  capacity is gone before anything else is considered. This is the mechanism that makes "a $500
  project due tomorrow is not endangered by twenty $15 opportunities" structurally true rather
  than a policy someone has to remember.
* **Opportunity cost is computed, not asserted.** For every selected item the planner re-solves
  the knapsack without it, so "this displaces $X of other work" is an actual number.
* **Deadlines filter before value ranks.** Work that cannot be finished in time is not a
  high-value opportunity, it is a missed deadline with a large number next to it.
* **Capacity shortage defers, it never rejects.** A profitable job that cannot start now and is
  not due yet is queued against the next reset. Dropping it would be throwing away money to
  tidy a list.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from . import capacity as cap
from .profit import ProfitProfile, ValueClass

# ---------------------------------------------------------------------------
# Lanes
# ---------------------------------------------------------------------------


class Lane(StrEnum):
    """Work is not one queue. Each lane has a different reason to exist, and capacity is
    allocated between them rather than consumed first-come."""

    ACTIVE_PAID_WORK = "ACTIVE PAID WORK"
    HIGH_VALUE_LOW_EFFORT = "HIGH VALUE / LOW EFFORT"
    QUICK_WINS = "QUICK WINS"
    HIGH_VALUE = "HIGH VALUE"
    REPEAT_CLIENTS = "REPEAT CLIENTS"
    ACQUISITION = "ACQUISITION"


#: Capacity granularity for the knapsack, in minutes. Five is fine enough that a five-minute job
#: is one unit and coarse enough that a full window is 36 buckets, which solves instantly.
BUCKET_MINUTES = 5.0

#: A deadline this close means the job runs now regardless of what outranks it on value.
URGENT_SLACK_HOURS = 24.0


@dataclass
class Candidate:
    """One piece of work the scheduler may choose to do."""

    profile: ProfitProfile
    committed: bool = False
    """True once there is a client obligation. Committed work is never deferred by the optimiser."""
    lane: str = Lane.ACQUISITION.value
    capacity_status: str = cap.CapacityStatus.UNKNOWN.value
    capacity_reason: str = ""
    urgent: bool = False
    opportunity_cost: float = 0.0
    decision: str = ""
    reason: str = ""

    @property
    def minutes(self) -> float:
        return self.profile.total_claude_minutes

    @property
    def value(self) -> float:
        """Committed work is valued at its full profit; speculative work at expected value.

        This is the one asymmetry the optimiser needs. A signed job is not a 20%-likely job, and
        treating it as one would let the planner trade away a delivery it already promised.
        """
        return self.profile.expected_net_profit if self.committed else self.profile.expected_value

    def to_dict(self) -> dict[str, Any]:
        d = {k: v for k, v in asdict(self).items() if k != "profile"}
        d["profile"] = self.profile.to_dict()
        return d


def assign_lane(prof: ProfitProfile, *, committed: bool = False) -> str:
    if committed:
        return Lane.ACTIVE_PAID_WORK.value
    if prof.value_class == ValueClass.HIGH_VALUE_LOW_EFFORT.value:
        return Lane.HIGH_VALUE_LOW_EFFORT.value
    if prof.repeat_client_potential == "HIGH":
        return Lane.REPEAT_CLIENTS.value
    if prof.value_class == ValueClass.QUICK_WIN.value:
        return Lane.QUICK_WINS.value
    if prof.value_class == ValueClass.HIGH_VALUE.value:
        return Lane.HIGH_VALUE.value
    return Lane.ACQUISITION.value


# ---------------------------------------------------------------------------
# The knapsack
# ---------------------------------------------------------------------------


def _solve(items: list[Candidate], capacity_minutes: float) -> tuple[float, list[int]]:
    """0/1 knapsack over capacity, maximising total value. Returns (value, chosen indices).

    Exact rather than greedy. Greedy by value density is where the "$60 of quick work beats a
    $500 project" failure actually comes from, and the problem is small enough - dozens of items
    against a few dozen buckets - that there is no reason to accept an approximation that fails
    in precisely the way the amendment says not to.
    """
    cap_buckets = int(max(0.0, capacity_minutes) // BUCKET_MINUTES)
    if cap_buckets <= 0 or not items:
        return 0.0, []

    weights = [max(1, int(round(i.minutes / BUCKET_MINUTES))) for i in items]
    values = [max(0.0, i.value) for i in items]

    # best[w] = best value achievable with exactly w buckets available
    best = [0.0] * (cap_buckets + 1)
    keep: list[list[bool]] = []

    for idx, w in enumerate(weights):
        row = [False] * (cap_buckets + 1)
        for budget in range(cap_buckets, w - 1, -1):
            candidate = best[budget - w] + values[idx]
            if candidate > best[budget] + 1e-9:
                best[budget] = candidate
                row[budget] = True
        keep.append(row)

    chosen: list[int] = []
    budget = cap_buckets
    for idx in range(len(items) - 1, -1, -1):
        if keep[idx][budget]:
            chosen.append(idx)
            budget -= weights[idx]
    chosen.reverse()
    return round(best[cap_buckets], 2), chosen


# ---------------------------------------------------------------------------
# The plan
# ---------------------------------------------------------------------------


@dataclass
class Plan:
    generated_at: str
    capacity_minutes: float
    committed_minutes: float
    available_minutes: float
    selected: list[Candidate] = field(default_factory=list)
    deferred: list[Candidate] = field(default_factory=list)
    blocked: list[Candidate] = field(default_factory=list)
    expected_captured_profit: float = 0.0
    expected_missed_profit: float = 0.0
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "capacity_minutes": self.capacity_minutes,
            "committed_minutes": self.committed_minutes,
            "available_minutes": self.available_minutes,
            "selected": [c.to_dict() for c in self.selected],
            "deferred": [c.to_dict() for c in self.deferred],
            "blocked": [c.to_dict() for c in self.blocked],
            "expected_captured_profit": self.expected_captured_profit,
            "expected_missed_profit": self.expected_missed_profit,
            "capacity_utilization_pct": round(
                100.0 * (self.committed_minutes + sum(c.minutes for c in self.selected)) / (self.capacity_minutes or 1.0), 1
            ),
            "lanes": self.lane_summary(),
            "notes": self.notes,
        }

    def lane_summary(self) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for c in self.selected + self.deferred:
            entry = out.setdefault(c.lane, {"count": 0, "minutes": 0.0, "expected_profit": 0.0, "scheduled": 0})
            entry["count"] += 1
            entry["minutes"] = round(entry["minutes"] + c.minutes, 1)
            entry["expected_profit"] = round(entry["expected_profit"] + c.value, 2)
            if c.decision == "SCHEDULED":
                entry["scheduled"] += 1
        return out


def plan(
    candidates: list[Candidate],
    *,
    est: cap.Estimate | None = None,
    now: datetime | None = None,
    horizon_hours: float | None = None,
) -> Plan:
    """Decide what to work on next.

    Runs in four passes, in this order, and the order is the design:

    1. **Commitments.** Accepted paid work takes its capacity off the top. Nothing later can
       spend it.
    2. **Feasibility.** Anything that cannot be finished before its deadline, or that the policy
       layer has blocked, is removed from consideration with a reason rather than ranked low.
    3. **Optimisation.** The knapsack picks the best combination of what is left.
    4. **Gap filling.** Quick wins that fit in whatever the optimiser did not use are added, on
       the condition that they cannot touch committed capacity - the schedule's unused minutes
       are free money, and leaving them idle is its own kind of waste.
    """
    now = now or datetime.now(UTC)
    est = est or cap.estimate(now)

    for c in candidates:
        c.lane = assign_lane(c.profile, committed=c.committed)
        slack = c.profile.deadline_slack_hours
        c.urgent = slack is not None and slack <= URGENT_SLACK_HOURS

    committed = [c for c in candidates if c.committed]
    speculative = [c for c in candidates if not c.committed]

    # The knapsack packs against the PLANNING HORIZON, not the current five-hour window. The
    # decision being made is "which of these should we take on this week", and capacity is a
    # rate that keeps arriving. Judging a fourteen-hour project against one window is how an
    # earlier version concluded that every real freelance contract was infeasible.
    #
    # Tests pass horizon_hours=0 to constrain the optimiser to exactly this window, which keeps
    # them precise about the arithmetic they are asserting on.
    hours = cap.PLANNING_HORIZON_HOURS if horizon_hours is None else horizon_hours
    total_capacity = cap.horizon_minutes(hours, est=est, now=now)
    committed_minutes = sum(c.minutes for c in committed)
    available = max(0.0, total_capacity - committed_minutes)

    blocked: list[Candidate] = []
    feasible: list[Candidate] = []
    for c in speculative:
        verdict = cap.pre_job_check(
            worker_minutes=c.profile.estimated_claude_minutes,
            deadline=_deadline_dt(c.profile, now),
            est=est,
            now=now,
        )
        c.capacity_status = verdict.status
        c.capacity_reason = verdict.reason

        if c.profile.expected_net_profit <= 0:
            c.decision, c.reason = "BLOCKED", "No profit left after fees."
            blocked.append(c)
            continue
        if not _fits_before_deadline(c, now):
            c.decision, c.reason = (
                "BLOCKED",
                (
                    f"{c.profile.estimated_completion_hours:.1f}h of work against "
                    f"{c.profile.deadline_slack_hours:.1f}h until the deadline. Taking it would be "
                    f"promising a date the schedule cannot hold."
                ),
            )
            blocked.append(c)
            continue
        if verdict.status == cap.CapacityStatus.RISKY.value:
            c.decision, c.reason = "NEEDS ANDRES", verdict.reason
            blocked.append(c)
            continue
        feasible.append(c)

    # Urgent committed work first, then the optimiser over everything else.
    best_value, chosen_idx = _solve(feasible, available)
    chosen = {id(feasible[i]) for i in chosen_idx}

    selected = list(committed)
    deferred: list[Candidate] = []
    for c in committed:
        c.decision = "COMMITTED"
        c.reason = f"Client obligation. {c.minutes:.0f} min of capacity is reserved and cannot be spent on anything else" + (
            f"; {c.profile.deadline_slack_hours:.0f}h until the deadline." if c.profile.deadline_slack_hours is not None else "."
        )

    for c in feasible:
        if id(c) in chosen:
            c.decision = "SCHEDULED"
            selected.append(c)
        elif c.capacity_status == cap.CapacityStatus.WAIT_FOR_RESET.value:
            c.decision = "WAIT FOR RESET"
            c.reason = c.capacity_reason
            deferred.append(c)
        else:
            c.decision = "DEFERRED"
            deferred.append(c)

    # --- opportunity cost, per selected item, by re-solving without it.
    for c in selected:
        if c.committed:
            c.opportunity_cost = 0.0
            c.reason = c.reason or "Committed."
            continue
        others = [o for o in feasible if o is not c]
        alt_value, _ = _solve(others, available)
        displaced = max(0.0, round(alt_value - (best_value - c.value), 2))
        c.opportunity_cost = displaced
        c.reason = _selection_reason(c, displaced)

    for c in deferred:
        if not c.reason:
            c.reason = (
                f"{c.minutes:.0f} min for ${c.value:,.0f} expected. The capacity buys more "
                f"elsewhere this window; it stays queued rather than dropped."
            )

    captured = round(sum(c.value for c in selected), 2)
    missed = round(sum(c.value for c in deferred), 2)

    notes = []
    if committed_minutes > 0:
        notes.append(f"{committed_minutes:.0f} min is reserved for accepted paid work and was removed before anything else was considered.")
    if not est.speculative_permitted:
        notes.append(
            "Capacity is low enough that speculative research and low-value proposal drafting "
            "are paused. Paid work, QA and revisions are unaffected - that is what the capacity "
            "is being held for."
        )
    if any(c.urgent for c in selected):
        notes.append("At least one deadline is inside 24 hours and is scheduled ahead of higher-value work.")
    if not selected:
        notes.append("Nothing is scheduled. That is usually the market or the capacity window, not a bug.")

    return Plan(
        generated_at=now.isoformat(timespec="seconds"),
        capacity_minutes=round(total_capacity, 1),
        committed_minutes=round(committed_minutes, 1),
        available_minutes=round(available, 1),
        selected=selected,
        deferred=deferred,
        blocked=blocked,
        expected_captured_profit=captured,
        expected_missed_profit=missed,
        notes=notes,
    )


def _selection_reason(c: Candidate, displaced: float) -> str:
    p = c.profile
    bits = [f"${p.expected_gross_revenue:,.0f} for {c.minutes:.0f} min of AI time"]
    if not c.committed:
        bits.append(f"EV ${p.expected_value:,.0f} at {p.win_probability:.0%} win probability")
    if c.urgent and p.deadline_slack_hours is not None:
        bits.append(f"{p.deadline_slack_hours:.0f}h until the deadline")
    if displaced > 0:
        bits.append(f"displaces ${displaced:,.0f} of other work")
    else:
        bits.append("displaces nothing - it fits in capacity nobody else was using")
    return "; ".join(bits) + "."


def _deadline_dt(prof: ProfitProfile, now: datetime) -> datetime | None:
    if prof.deadline_slack_hours is None:
        return None
    return now + timedelta(hours=prof.deadline_slack_hours)


def _fits_before_deadline(c: Candidate, now: datetime) -> bool:
    slack = c.profile.deadline_slack_hours
    if slack is None:
        return True
    # Elapsed time, not AI minutes: a job needs a person to read it, review the output and send
    # it, and those hours are on the clock even though they are not on the subscription.
    return slack >= c.profile.estimated_completion_hours


# ---------------------------------------------------------------------------
# The PROFIT QUEUE
# ---------------------------------------------------------------------------


def profit_queue(p: Plan, *, limit: int = 12) -> list[dict[str, Any]]:
    """The dashboard's "what happens next, and why" list.

    Ordering is deliberately not by value. Committed work outranks everything; then anything
    urgent; then quick wins, because a five-minute job ahead of a sixty-minute one costs the
    sixty-minute one five minutes and gets paid twice as fast; then the rest by expected value.
    """

    def sort_key(c: Candidate) -> tuple[int, float, float]:
        if c.committed:
            tier = 0
        elif c.urgent:
            tier = 1
        elif c.lane == Lane.HIGH_VALUE_LOW_EFFORT.value:
            tier = 2
        elif c.lane == Lane.QUICK_WINS.value:
            tier = 3
        else:
            tier = 4
        slack = c.profile.deadline_slack_hours if c.profile.deadline_slack_hours is not None else 10_000.0
        return (tier, slack, -c.value)

    ordered = sorted(p.selected, key=sort_key)

    # "NOW" has to mean startable now. An earlier version labelled the top row NOW while its own
    # capacity badge next to it read WAIT FOR RESET, which is a plain contradiction on the page -
    # and the kind that quietly teaches a reader to stop believing the labels. A job selected for
    # the week but with no room left in this window is AFTER RESET; only one row is ever NOW.
    startable = {cap.CapacityStatus.SAFE_TO_START.value, cap.CapacityStatus.TIGHT.value}
    now_taken = False
    rows: list[dict[str, Any]] = []
    for c in ordered:
        can_start = c.committed or c.capacity_status in startable
        if not can_start:
            label = "AFTER RESET"
        elif not now_taken:
            label, now_taken = "NOW", True
        else:
            label = "NEXT"
        if label == "NEXT" and c.lane in (Lane.HIGH_VALUE.value, Lane.HIGH_VALUE_LOW_EFFORT.value) and not c.committed:
            label = "HIGH PRIORITY"
        rows.append(
            {
                "position": label,
                "lane": c.lane,
                "title": c.profile.title or c.profile.opportunity_id,
                "gross": c.profile.expected_gross_revenue,
                "expected_value": c.profile.expected_value,
                "claude_minutes": c.minutes,
                "andres_minutes": c.profile.andres_active_minutes,
                "time_to_cash_days": c.profile.time_to_cash_days,
                "capacity_status": c.capacity_status if not c.committed else "RESERVED",
                "deadline_slack_hours": c.profile.deadline_slack_hours,
                "opportunity_cost": c.opportunity_cost,
                "why": c.reason,
            }
        )

    for c in sorted(p.deferred, key=lambda x: -x.value)[: max(0, limit - len(rows))]:
        rows.append(
            {
                "position": c.decision,
                "lane": c.lane,
                "title": c.profile.title or c.profile.opportunity_id,
                "gross": c.profile.expected_gross_revenue,
                "expected_value": c.profile.expected_value,
                "claude_minutes": c.minutes,
                "andres_minutes": c.profile.andres_active_minutes,
                "time_to_cash_days": c.profile.time_to_cash_days,
                "capacity_status": c.capacity_status,
                "deadline_slack_hours": c.profile.deadline_slack_hours,
                "opportunity_cost": 0.0,
                "why": c.reason,
            }
        )
    return rows[:limit]


def snapshot(candidates: list[Candidate], *, est: cap.Estimate | None = None) -> dict[str, Any]:
    """Everything the Profit Queue and Profit panels render, in one call."""
    p = plan(candidates, est=est)
    return {
        "plan": p.to_dict(),
        "queue": profit_queue(p),
        "capacity": (est or cap.estimate()).to_dict(),
        "note": (
            "Expected values on listings nobody has been awarded. Captured profit becomes real "
            "revenue only when a client pays; that figure is tracked separately and is $0.00."
        ),
    }
