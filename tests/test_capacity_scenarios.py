"""The five capacity scenarios, written as the situations they describe rather than as units.

`test_capacity.py` tests the capacity module's parts. This file tests the *behaviours* the
business depends on, each named after the situation a person would recognise. The difference
matters when something regresses: a failure here says "the system would now reject a profitable
job it should have queued", which is a sentence anyone can act on, rather than "pre_job_check
returned the wrong enum".

The last scenario is the one that decides whether this system is safe to leave running. The
others are about earning more; that one is about never being surprised by a bill.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from aicc import capacity as cap
from aicc import scheduler
from aicc.capacity import CapacityStatus
from aicc.profit import ProfitProfile, ValueClass
from aicc.scheduler import Candidate

NOW = datetime(2026, 9, 12, 12, 0, tzinfo=UTC)


def _profile(title: str, gross: float, claude_minutes: float, **kw) -> ProfitProfile:
    return ProfitProfile(
        opportunity_id=title.lower().replace(" ", "-"),
        title=title,
        expected_gross_revenue=gross,
        expected_net_revenue=gross,
        expected_net_profit=gross,
        win_probability=kw.get("win", 1.0),
        expected_value=round(gross * kw.get("win", 1.0), 2),
        estimated_claude_minutes=claude_minutes / 1.85,
        total_claude_minutes=claude_minutes,
        andres_active_minutes=kw.get("andres_minutes", 10.0),
        estimated_completion_hours=kw.get("completion_hours", 1.0),
        deadline_slack_hours=kw.get("deadline_slack"),
        delivery_confidence=kw.get("confidence", 0.85),
        value_class=kw.get("value_class", ValueClass.STANDARD.value),
    )


def _estimate(safe_minutes: float, *, reserved: float = 0.0) -> cap.Estimate:
    window = cap.NOMINAL_WINDOW_MINUTES
    emergency = window * cap.EMERGENCY_RESERVE_FRACTION
    return cap.Estimate(
        window_minutes=window,
        used_minutes=max(0.0, window - reserved - emergency - safe_minutes),
        reserved_minutes=reserved,
        confidence=cap.Confidence.ESTIMATED.value,
        basis="scenario fixture",
        window_started=(NOW - timedelta(hours=1)).isoformat(),
        next_reset=(NOW + timedelta(hours=4)).isoformat(),
    )


# =============================================================== A


def test_scenario_a_plenty_of_capacity_and_an_easy_500_dollar_job() -> None:
    """Expected: SAFE TO START, and near the front of the queue.

    Sixty minutes of AI work on a fresh window. There is no reason to hesitate, and a system
    that hedged here would be leaving money on the table for no gain.
    """
    verdict = cap.pre_job_check(worker_minutes=60.0, est=_estimate(300.0), now=NOW)
    assert verdict.status == CapacityStatus.SAFE_TO_START.value, verdict.reason
    assert not verdict.needs_human

    job = Candidate(_profile("Easy $500 job", 500.0, 111.0, completion_hours=3.0))
    plan = scheduler.plan([job], est=_estimate(300.0), now=NOW)
    assert job in plan.selected
    rows = scheduler.profit_queue(plan)
    assert rows[0]["title"] == "Easy $500 job"
    assert rows[0]["position"] == "NOW"


# =============================================================== B


def test_scenario_b_four_quick_jobs_and_one_large_one_are_all_captured() -> None:
    """Expected: pursue every profitable feasible job, not $60 OR $500.

    This is the amendment's worked example. A system that returns either one alone has chosen,
    and choosing is the failure - the right answer is $560.
    """
    big = Candidate(_profile("$500 project", 500.0, 111.0, completion_hours=3.0))
    smalls = [Candidate(_profile(f"$15 quick job {i}", 15.0, 9.25, completion_hours=0.3)) for i in range(4)]

    plan = scheduler.plan([big, *smalls], horizon_hours=0, est=_estimate(162.0), now=NOW)
    captured = sum(c.profile.expected_gross_revenue for c in plan.selected)

    assert captured == pytest.approx(560.0), [c.profile.title for c in plan.selected]
    assert plan.expected_missed_profit == 0.0
    assert len(plan.selected) == 5


def test_scenario_b_variant_the_large_job_survives_when_not_everything_fits() -> None:
    """The same shape with less capacity. The money is kept, not the count."""
    big = Candidate(_profile("$500 project", 500.0, 111.0, completion_hours=4.0))
    smalls = [Candidate(_profile(f"$15 job {i}", 15.0, 10.0, completion_hours=0.3)) for i in range(12)]

    plan = scheduler.plan([big, *smalls], horizon_hours=0, est=_estimate(120.0), now=NOW)
    assert "$500 project" in {c.profile.title for c in plan.selected}
    assert plan.expected_captured_profit >= 500.0


# =============================================================== C


def test_scenario_c_low_capacity_and_a_deadline_days_away_means_wait_not_reject() -> None:
    """Expected: WAIT FOR RESET.

    'Do NOT reject an excellent profitable job simply because current capacity is temporarily
    low.' Capacity refills; the deadline is what decides whether waiting is viable.
    """
    verdict = cap.pre_job_check(
        worker_minutes=200.0,
        deadline=NOW + timedelta(days=4),
        est=_estimate(20.0),
        now=NOW,
    )
    assert verdict.status == CapacityStatus.WAIT_FOR_RESET.value, verdict.reason
    assert verdict.wait_until
    assert not verdict.needs_human

    job = Candidate(_profile("$500, due Friday", 500.0, 370.0, deadline_slack=96.0, completion_hours=8.0))
    plan = scheduler.plan([job], horizon_hours=0, est=_estimate(20.0), now=NOW)
    assert job not in plan.blocked, "A profitable job with a workable deadline must not be rejected."
    assert job.capacity_status == CapacityStatus.WAIT_FOR_RESET.value


def test_scenario_c_boundary_a_deadline_that_cannot_be_met_is_said_so_early() -> None:
    """Deferring forever is not honesty either. Work that genuinely cannot ship on time should
    be flagged before it is accepted, not discovered halfway through."""
    verdict = cap.pre_job_check(worker_minutes=9_000.0, deadline=NOW + timedelta(days=2), est=_estimate(20.0), now=NOW)
    assert verdict.status == CapacityStatus.RISKY.value
    assert verdict.needs_human


# =============================================================== D


def test_scenario_d_a_paid_job_takes_capacity_from_prospecting_not_the_other_way_round() -> None:
    """Expected: accepted paid work keeps its QA and revision reserve; speculation yields.

    The reservation is made on acceptance and covers worker, reviewer, one revision and an
    emergency margin - so a flood of speculative opportunities cannot eat the capacity the
    delivery depends on.
    """
    res = cap.reserve("paid-job-1", worker_minutes=60.0)
    assert res.reviewer_minutes > 0 and res.revision_minutes > 0 and res.emergency_minutes > 0
    assert res.total_minutes == pytest.approx(120.0)

    est = cap.estimate(NOW)
    allowed = cap.permitted_now(est)
    for protected in cap.PROTECTED_WORK:
        assert allowed[protected], f"{protected} was shed to make room for speculation."

    committed = Candidate(
        _profile("Accepted client job", 500.0, 111.0, deadline_slack=30.0, completion_hours=4.0),
        committed=True,
    )
    flood = [Candidate(_profile(f"Speculative {i}", 20.0, 9.0, completion_hours=0.3)) for i in range(25)]
    plan = scheduler.plan([committed, *flood], horizon_hours=0, est=_estimate(30.0), now=NOW)

    assert committed in plan.selected and committed.decision == "COMMITTED"
    speculative_minutes = sum(c.minutes for c in plan.selected if not c.committed)
    assert speculative_minutes <= 30.0, "Speculation drew on capacity reserved for a paid delivery."


def test_scenario_d_research_is_the_first_thing_shed() -> None:
    """The order is the design: research, then low-value proposals, then semantic analysis.
    Paid work is last because it is what the capacity is being held for."""
    cap.reserve("hog", worker_minutes=cap.NOMINAL_WINDOW_MINUTES / 2.2)
    allowed = cap.permitted_now(cap.estimate(NOW))
    assert not allowed["speculative_research"]
    assert allowed["active_paid_worker"] and allowed["active_paid_revision"]


# =============================================================== E


def test_scenario_e_exhausted_capacity_pauses_and_queues_rather_than_billing() -> None:
    """Expected: AI work pauses and queues. The consequence is slower processing, never a bill.

    The single most important assertion in this file. Everything else here is about earning
    more; this is about never being surprised by a charge.
    """
    import os

    cap.mark_exhausted((NOW + timedelta(hours=3)).isoformat())

    verdict = cap.pre_job_check(worker_minutes=30.0, deadline=NOW + timedelta(days=3), est=cap.estimate(NOW), now=NOW)
    assert verdict.status == CapacityStatus.WAIT_FOR_RESET.value
    assert verdict.wait_until, "A paused job must say when it expects to resume."
    assert not verdict.needs_human

    snap = cap.snapshot()
    assert snap["status"] == CapacityStatus.WAIT_FOR_RESET.value
    assert snap["paid_api_fallback"] == "DISABLED"
    assert snap["exhausted_until"]

    assert not os.environ.get("ANTHROPIC_API_KEY"), "A paid key is present; exhaustion could become a bill."


def test_scenario_e_no_billable_path_exists_in_the_capacity_or_degradation_layer() -> None:
    """Read the code, not just the behaviour - but read it precisely.

    The first version of this check scanned raw source for "anthropic_api_key" and failed on
    degradation.py twice over: once on the docstring *explaining why the key must never be used*,
    and once on `never_falls_back_to_paid`, which mentions the key in order to refuse it. A safety
    check that fires on the safety property is a check nobody keeps.

    So the assertion is specific. `capacity.py` must not mention it at all. `degradation.py` may
    mention it only inside the guard named for refusing it - anywhere else would be a path that
    reaches for paid billing rather than one that rejects it.
    """
    import ast
    import inspect

    from aicc import capacity, degradation

    KEY = "anthropic_api_key"
    GUARD = "never_falls_back_to_paid"

    assert KEY not in inspect.getsource(capacity).lower(), "capacity.py should not know the key exists."

    tree = ast.parse(inspect.getsource(degradation))
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) or node.name == GUARD:
            continue
        body = list(node.body)
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
            body.pop(0)  # the function's own docstring may discuss the key freely
        if any(KEY in ast.unparse(stmt).lower() for stmt in body):
            offenders.append(node.name)
    assert not offenders, f"{KEY!r} is referenced outside the {GUARD} guard, in: {offenders}"

    decision = degradation.classify("429 rate_limit_error: usage limit reached", status_code=429)
    assert degradation.never_falls_back_to_paid(decision)


def test_scenario_e_an_exhausted_window_with_a_hard_deadline_asks_a_human_rather_than_paying() -> None:
    """The tempting shortcut here is to reach for the API to save the deadline. It asks instead."""
    cap.mark_exhausted((NOW + timedelta(hours=3)).isoformat())
    verdict = cap.pre_job_check(worker_minutes=30.0, deadline=NOW + timedelta(minutes=30), est=cap.estimate(NOW), now=NOW)
    assert verdict.needs_human
    assert verdict.status == CapacityStatus.RISKY.value
    assert "NEEDS" in verdict.reason.upper() or "Andres" in verdict.reason


# =============================================================== honesty about precision


def test_no_scenario_ever_reports_an_exact_percentage_it_cannot_know() -> None:
    """Anthropic exposes no exact remaining-capacity telemetry to a runner, so every figure is
    labelled. A confident percentage would be invented, and invented numbers get acted on."""
    snap = cap.snapshot()
    assert snap["confidence"] in {"ESTIMATED", "MEASURED"}
    if snap["confidence"] != "MEASURED":
        assert "ESTIMATED" in snap["remaining_display"]
        assert "ESTIMATED" in snap["safe_new_work_display"]
    assert "does not expose exact" in snap["telemetry_note"]
