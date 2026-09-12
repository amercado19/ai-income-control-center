"""Capacity estimation, reservation, and the refusal to spend money instead of waiting.

The single most important test in this file is the last one. Everything else here is scheduling
hygiene; that one is the difference between "the system got slower" and "the system sent Andres
a bill". The amendment's wording is the acceptance criterion: the consequence of reaching the
usage limit should be slower processing, not an unexpected charge.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from aicc import capacity as cap
from aicc.capacity import CapacityStatus, Confidence

NOW = datetime(2026, 9, 11, 12, 0, tzinfo=UTC)


# ---------------------------------------------------------------- honesty about precision


def test_an_estimate_never_renders_a_bare_number() -> None:
    """Anthropic exposes no exact remaining-capacity telemetry to a runner. A confident
    percentage would therefore be invented, and inventing it is worse than a wide range because
    people act on numbers that look precise."""
    est = cap.estimate(NOW)
    assert est.confidence in {Confidence.ESTIMATED.value, Confidence.MEASURED.value}
    assert "ESTIMATED" in est.display(100.0) or est.confidence == Confidence.MEASURED.value
    d = est.to_dict()
    assert "ESTIMATED" in d["remaining_display"] or d["confidence"] == Confidence.MEASURED.value


def test_a_fresh_system_says_it_is_estimating_and_says_why() -> None:
    est = cap.estimate(NOW)
    assert est.confidence == Confidence.ESTIMATED.value
    assert "no exact usage telemetry" in est.basis.lower() or "conservative default" in est.basis.lower()


def test_the_snapshot_carries_the_telemetry_caveat() -> None:
    snap = cap.snapshot()
    assert "does not expose exact" in snap["telemetry_note"]
    assert snap["paid_api_fallback"] == "DISABLED"


def test_measured_confidence_requires_five_real_observations() -> None:
    """Same threshold, and the same reasoning, as the win-rate gate: four numbers are not a rate."""
    for i in range(cap.MIN_OBSERVATIONS - 1):
        cap.record(f"job-{i}", 20.0, category="spreadsheet")
    assert cap.estimate(NOW).confidence == Confidence.ESTIMATED.value
    cap.record("job-final", 20.0, category="spreadsheet")
    assert cap.estimate(NOW).confidence == Confidence.MEASURED.value


# ---------------------------------------------------------------- reservations


def test_a_reservation_covers_qa_and_a_revision_not_just_the_first_draft() -> None:
    """A job is not done when the first draft exists. Reserving only the worker pass is how a
    delivery gets stranded at the revision nobody budgeted for."""
    res = cap.reserve("job-1", worker_minutes=60.0)
    assert res.worker_minutes == 60.0
    assert res.reviewer_minutes > 0
    assert res.revision_minutes > 0
    assert res.emergency_minutes > 0
    assert res.total_minutes == pytest.approx(120.0)


def test_reserved_capacity_is_not_available_to_new_work() -> None:
    before = cap.estimate(NOW).safe_new_work_minutes
    cap.reserve("job-2", worker_minutes=40.0)
    after = cap.estimate(NOW).safe_new_work_minutes
    assert after == pytest.approx(before - 80.0)


def test_releasing_a_finished_job_returns_its_capacity() -> None:
    before = cap.estimate(NOW).safe_new_work_minutes
    cap.reserve("job-3", worker_minutes=30.0)
    assert cap.estimate(NOW).safe_new_work_minutes < before
    assert cap.release("job-3")
    assert cap.estimate(NOW).safe_new_work_minutes == pytest.approx(before)


def test_an_emergency_margin_is_always_held_back() -> None:
    est = cap.estimate(NOW)
    assert est.emergency_minutes > 0
    assert est.safe_new_work_minutes == pytest.approx(est.unreserved_minutes - est.emergency_minutes)


# ---------------------------------------------------------------- the pre-job check


def test_a_small_job_against_a_fresh_window_is_safe_to_start() -> None:
    v = cap.pre_job_check(worker_minutes=20.0, now=NOW)
    assert v.status == CapacityStatus.SAFE_TO_START.value
    assert not v.needs_human


def test_a_job_that_nearly_fills_the_horizon_is_tight_not_safe() -> None:
    """TIGHT means the HORIZON is tight, not that the job outlasts today's window.

    An earlier version compared demand to the current window, so an ordinary $500 job came back
    TIGHT on a completely fresh window - 120 minutes is 74% of one 162-minute window. That is the
    same mistake as judging feasibility against one window, and it would have had the scheduler
    hedging on exactly the work it should take.
    """
    est = cap.estimate(NOW)
    horizon = cap.horizon_minutes(48.0, est=est, now=NOW)
    worker = horizon / 2.0 * 0.95  # demand is worker * 2.0, landing just inside the horizon
    v = cap.pre_job_check(worker_minutes=worker, deadline=NOW + timedelta(hours=48), est=est, now=NOW)
    assert v.status == CapacityStatus.TIGHT.value, v.reason
    assert "little slack" in v.reason


def test_a_multi_window_job_is_scheduled_across_windows_not_rejected() -> None:
    """The bug this assertion exists for.

    A 400-minute job is an ordinary freelance contract - roughly seven hours of AI work, due in
    five days. The first version of this module compared its whole demand against one five-hour
    window, called it over capacity, and deferred it. Every real listing in the store came out
    as WAIT FOR RESET and nothing was ever schedulable, which looks like caution and is a broken
    model: capacity is a rate, and more windows keep arriving before the deadline.
    """
    v = cap.pre_job_check(worker_minutes=400.0, deadline=NOW + timedelta(days=5), now=NOW)
    assert v.status == CapacityStatus.SAFE_TO_START.value, v.reason
    assert not v.needs_human
    assert "before the deadline" in v.reason


def test_a_job_is_queued_when_this_window_cannot_make_a_meaningful_start() -> None:
    """WAIT FOR RESET means "cannot begin yet", not "cannot be done".

    Five minutes of progress on a two-hour job is not a start, it is a context switch - so a job
    that fits the horizon but has almost nothing left in this window is queued for the reset.
    """
    est = cap.Estimate(
        window_minutes=cap.NOMINAL_WINDOW_MINUTES,
        used_minutes=cap.NOMINAL_WINDOW_MINUTES - 5.0,
        reserved_minutes=0.0,
        confidence=cap.Confidence.ESTIMATED.value,
        basis="nearly spent window",
        window_started=(NOW - timedelta(hours=4)).isoformat(),
        next_reset=(NOW + timedelta(hours=1)).isoformat(),
    )
    v = cap.pre_job_check(worker_minutes=120.0, deadline=NOW + timedelta(days=4), est=est, now=NOW)
    assert v.status == CapacityStatus.WAIT_FOR_RESET.value, v.reason
    assert v.wait_until
    assert not v.needs_human
    assert "meaningful start" in v.reason


def test_a_job_larger_than_every_remaining_window_is_genuinely_infeasible() -> None:
    """Deferring forever is not honesty. Work that cannot be delivered by its deadline should
    be said so before it is accepted, not discovered halfway through."""
    v = cap.pre_job_check(worker_minutes=5_000.0, deadline=NOW + timedelta(days=5), now=NOW)
    assert v.status == CapacityStatus.RISKY.value
    assert v.needs_human
    assert "before the deadline" in v.reason


def test_a_job_too_big_and_due_too_soon_is_risky_and_goes_to_a_human() -> None:
    v = cap.pre_job_check(worker_minutes=5_000.0, deadline=NOW + timedelta(hours=2), now=NOW)
    assert v.status == CapacityStatus.RISKY.value
    assert v.needs_human
    assert "stranding" in v.reason


def test_the_horizon_grows_with_the_time_available() -> None:
    day = cap.horizon_minutes(24.0, now=NOW)
    week = cap.horizon_minutes(168.0, now=NOW)
    window_only = cap.horizon_minutes(0.0, now=NOW)
    assert window_only < day < week
    assert window_only == cap.estimate(NOW).safe_new_work_minutes


def test_future_windows_are_discounted_rather_than_counted_in_full() -> None:
    """Andres sleeps and has a day job. Planning against capacity that never materialises is
    how a deadline gets missed."""
    est = cap.estimate(NOW)
    seven_days = cap.horizon_minutes(168.0, est=est, now=NOW)
    undiscounted = 168.0 / cap.WINDOW_HOURS * est.window_minutes
    assert seven_days < undiscounted * 0.75
    assert cap.REALISTIC_WINDOW_UTILIZATION <= 0.5


def test_a_job_with_no_workload_estimate_is_unknown_not_assumed_fine() -> None:
    v = cap.pre_job_check(worker_minutes=0.0, now=NOW)
    assert v.status == CapacityStatus.UNKNOWN.value
    assert v.needs_human
    assert "Guessing" in v.reason


def test_the_check_budgets_for_qa_and_revision_not_just_the_worker_pass() -> None:
    v = cap.pre_job_check(worker_minutes=30.0, now=NOW)
    assert v.demand_minutes == pytest.approx(60.0), "Demand must include QA, revision and margin."


# ---------------------------------------------------------------- degradation order


def test_speculative_work_stops_before_paid_work_does() -> None:
    """The order is the design: research first, then low-value proposals, then semantic analysis.
    Active paid work is last because it is what the capacity is being held for."""
    est = cap.estimate(NOW)
    cap.reserve("big-job", worker_minutes=est.window_minutes / 2.2)
    est = cap.estimate(NOW)
    allowed = cap.permitted_now(est)

    for protected in cap.PROTECTED_WORK:
        assert allowed[protected], f"{protected} was shed - that is exactly backwards."
    assert not allowed["speculative_research"], "Research should stop first."


def test_protected_work_never_appears_in_the_shed_list() -> None:
    shed_keys = {k for k, _ in cap.DEGRADATION_ORDER}
    assert not shed_keys & set(cap.PROTECTED_WORK)


def test_the_shed_plan_explains_itself() -> None:
    cap.reserve("hog", worker_minutes=cap.NOMINAL_WINDOW_MINUTES / 2.2)
    plan = cap.shed_plan()
    assert plan
    assert all("paused at" in line for line in plan)


# ---------------------------------------------------------------- exhaustion


def test_an_exhausted_window_defers_rather_than_failing_when_the_deadline_allows() -> None:
    cap.mark_exhausted((NOW + timedelta(hours=3)).isoformat())
    v = cap.pre_job_check(worker_minutes=10.0, deadline=NOW + timedelta(days=3), now=NOW)
    assert v.status == CapacityStatus.WAIT_FOR_RESET.value
    assert v.wait_until


def test_an_exhausted_window_with_a_hard_deadline_asks_andres() -> None:
    cap.mark_exhausted((NOW + timedelta(hours=3)).isoformat())
    v = cap.pre_job_check(worker_minutes=10.0, deadline=NOW + timedelta(hours=1), now=NOW)
    assert v.needs_human
    assert v.status == CapacityStatus.RISKY.value


def test_exhaustion_never_produces_a_paid_fallback_anywhere_in_the_module() -> None:
    """The load-bearing test of this file.

    Reaching the usage limit must slow the system down, not switch billing. Checked twice: the
    snapshot says the fallback is disabled, and the source itself is read for any mention of an
    API key, because the surest way for a paid path to appear is for someone to add one here
    while everything still looks green.
    """
    import inspect

    cap.mark_exhausted((NOW + timedelta(hours=2)).isoformat())
    snap = cap.snapshot()
    assert snap["paid_api_fallback"] == "DISABLED"
    assert snap["status"] == CapacityStatus.WAIT_FOR_RESET.value

    source = inspect.getsource(cap)
    lowered = source.lower()
    for forbidden in ("anthropic_api_key", "api_key=", "billing_mode", "fallback_to_api"):
        assert forbidden not in lowered, f"{forbidden!r} appears in capacity.py"


def test_window_bounds_are_stable_and_five_hours_wide() -> None:
    start, reset = cap.window_bounds(NOW)
    assert (reset - start) == timedelta(hours=cap.WINDOW_HOURS)
    assert start <= NOW < reset
