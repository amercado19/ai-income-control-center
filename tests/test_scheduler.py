"""The scheduler has one job: not to choose between big money and quick money.

The first test in this file is the amendment's own worked example, with its own numbers. If it
ever fails, the scheduler has regressed into a sorted list and the whole module is worth less
than nothing, because it would be making confident wrong calls about which work to skip.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from aicc import capacity as cap
from aicc import scheduler
from aicc.profit import ProfitProfile, ValueClass
from aicc.scheduler import Candidate, Lane

NOW = datetime(2026, 9, 11, 12, 0, tzinfo=UTC)


def _profile(
    title: str,
    gross: float,
    claude_minutes: float,
    *,
    win: float = 1.0,
    deadline_slack: float | None = None,
    value_class: str = ValueClass.STANDARD.value,
    andres_minutes: float = 10.0,
    completion_hours: float = 1.0,
) -> ProfitProfile:
    """A profile with the numbers stated directly, so a scheduler test is about scheduling."""
    return ProfitProfile(
        opportunity_id=title.lower().replace(" ", "-"),
        title=title,
        expected_gross_revenue=gross,
        expected_net_revenue=gross,
        expected_net_profit=gross,
        win_probability=win,
        expected_value=round(gross * win, 2),
        estimated_claude_minutes=claude_minutes / 1.85,
        total_claude_minutes=claude_minutes,
        andres_active_minutes=andres_minutes,
        estimated_completion_hours=completion_hours,
        deadline_slack_hours=deadline_slack,
        delivery_confidence=0.8,
        value_class=value_class,
        profit_per_claude_minute=round(gross / claude_minutes, 3),
    )


def _estimate(safe_minutes: float, *, reserved: float = 0.0) -> cap.Estimate:
    """A capacity estimate with a known amount of safe new-work capacity.

    `safe_new_work_minutes` is window - used - reserved - emergency, so `used` is set to make
    the arithmetic land on the number the test wants rather than the test asserting on a
    number it derived the same way the code does.
    """
    window = cap.NOMINAL_WINDOW_MINUTES
    emergency = window * cap.EMERGENCY_RESERVE_FRACTION
    used = window - reserved - emergency - safe_minutes
    return cap.Estimate(
        window_minutes=window,
        used_minutes=max(0.0, used),
        reserved_minutes=reserved,
        confidence=cap.Confidence.ESTIMATED.value,
        basis="fixture",
        window_started=(NOW - timedelta(hours=1)).isoformat(),
        next_reset=(NOW + timedelta(hours=4)).isoformat(),
    )


# ---------------------------------------------------------------------- the worked example


def test_big_money_and_quick_money_are_both_captured() -> None:
    """The amendment's example, with its numbers.

    One $500 job at roughly an hour of AI work, four $15 jobs at five minutes each. The right
    answer is $560, not "$500" and not "$60" - and a system that sorts by any single ratio gets
    one of the two wrong answers with complete confidence.
    """
    big = Candidate(_profile("Data pipeline rebuild", 500.0, 111.0, completion_hours=3.0))
    smalls = [Candidate(_profile(f"CSV cleanup {i}", 15.0, 9.25, completion_hours=0.3)) for i in range(4)]

    p = scheduler.plan([big, *smalls], horizon_hours=0, est=_estimate(162.0), now=NOW)

    titles = {c.profile.title for c in p.selected}
    assert "Data pipeline rebuild" in titles, "The large job must be scheduled."
    assert len([t for t in titles if t.startswith("CSV cleanup")]) == 4, "All four quick wins fit."
    assert sum(c.profile.expected_gross_revenue for c in p.selected) == pytest.approx(560.0)
    assert p.expected_missed_profit == 0.0


def test_a_large_job_is_never_crowded_out_by_small_ones() -> None:
    """When everything does not fit, the optimiser keeps the money, not the count.

    Twelve ten-minute $15 jobs total $180 and would consume the entire window. The $500 job
    wins because maximising total profit is the objective - no special rule needed.
    """
    big = Candidate(_profile("Reporting automation", 500.0, 111.0, completion_hours=4.0))
    smalls = [Candidate(_profile(f"Tiny {i}", 15.0, 10.0, completion_hours=0.3)) for i in range(12)]

    p = scheduler.plan([big, *smalls], horizon_hours=0, est=_estimate(120.0), now=NOW)

    assert "Reporting automation" in {c.profile.title for c in p.selected}
    assert p.expected_captured_profit >= 500.0
    assert p.expected_captured_profit > sum(c.profile.expected_net_profit for c in smalls)


def test_unused_capacity_is_filled_rather_than_left_idle() -> None:
    """With the big job scheduled and room to spare, quick wins take the gap."""
    big = Candidate(_profile("Pipeline", 500.0, 100.0, completion_hours=4.0))
    smalls = [Candidate(_profile(f"Quick {i}", 20.0, 10.0, completion_hours=0.3)) for i in range(5)]

    p = scheduler.plan([big, *smalls], horizon_hours=0, est=_estimate(160.0), now=NOW)
    used = sum(c.minutes for c in p.selected)
    assert used >= 150.0, f"Only {used:.0f} of 160 minutes scheduled - the gaps are money."


# ---------------------------------------------------------------------- commitments


def test_committed_work_keeps_its_capacity_whatever_else_appears() -> None:
    """'A $500 project due tomorrow should not be endangered because twenty $15 jobs appeared.'"""
    committed = Candidate(
        _profile("Accepted client job", 500.0, 111.0, deadline_slack=20.0, completion_hours=4.0),
        committed=True,
    )
    flood = [Candidate(_profile(f"Opportunist {i}", 15.0, 9.0, completion_hours=0.3)) for i in range(20)]

    p = scheduler.plan([committed, *flood], horizon_hours=0, est=_estimate(40.0), now=NOW)

    assert committed in p.selected
    assert committed.decision == "COMMITTED"
    assert p.committed_minutes == pytest.approx(111.0)
    # Only the leftover 40 minutes were ever available to the flood.
    scheduled_small = [c for c in p.selected if not c.committed]
    assert sum(c.minutes for c in scheduled_small) <= 40.0


def test_committed_work_is_valued_at_profit_not_expected_value() -> None:
    """A signed job is not a 20%-likely job. Valuing it as one lets the planner trade it away."""
    c = Candidate(_profile("Signed", 400.0, 60.0, win=0.2), committed=True)
    assert c.value == 400.0
    spec = Candidate(_profile("Speculative", 400.0, 60.0, win=0.2))
    assert spec.value == pytest.approx(80.0)


# ---------------------------------------------------------------------- deadlines


def test_work_that_cannot_meet_its_deadline_is_blocked_not_ranked() -> None:
    doomed = Candidate(_profile("Due in two hours", 900.0, 60.0, deadline_slack=2.0, completion_hours=9.0))
    p = scheduler.plan([doomed], horizon_hours=0, est=_estimate(160.0), now=NOW)
    assert doomed in p.blocked
    assert doomed not in p.selected
    assert "deadline" in doomed.reason.lower()


def test_an_urgent_job_leads_the_queue_even_when_something_pays_more() -> None:
    urgent = Candidate(_profile("Due tomorrow", 120.0, 30.0, deadline_slack=18.0, completion_hours=2.0))
    richer = Candidate(_profile("Pays more, no deadline", 600.0, 60.0, completion_hours=4.0))
    p = scheduler.plan([urgent, richer], horizon_hours=0, est=_estimate(160.0), now=NOW)
    queue = scheduler.profit_queue(p)
    assert queue[0]["title"] == "Due tomorrow", [r["title"] for r in queue]
    assert queue[0]["position"] == "NOW"


# ---------------------------------------------------------------------- capacity


def test_a_profitable_job_that_cannot_begin_yet_waits_for_reset_rather_than_being_rejected() -> None:
    """'Do NOT reject an excellent profitable job simply because current capacity is low.'

    The window here is nearly spent - five usable minutes - so the job genuinely cannot begin.
    It is still perfectly deliverable before a ten-day deadline, so the right answer is a
    queue position, not a rejection.
    """
    big = Candidate(_profile("Too big for right now", 800.0, 400.0, deadline_slack=240.0, completion_hours=20.0))
    p = scheduler.plan([big], horizon_hours=0, est=_estimate(5.0), now=NOW)
    assert big in p.deferred, big.decision
    assert big.decision == "WAIT FOR RESET"
    assert big not in p.blocked


def test_a_job_the_plan_cannot_fit_is_deferred_not_rejected() -> None:
    """Deferred is the weaker statement: the capacity went somewhere better this round. The job
    keeps its place and its reason, and nothing about it is marked bad."""
    big = Candidate(_profile("Outbid for capacity", 800.0, 400.0, deadline_slack=240.0, completion_hours=20.0))
    small = Candidate(_profile("Fits", 60.0, 25.0, completion_hours=1.0))
    p = scheduler.plan([big, small], horizon_hours=0, est=_estimate(30.0), now=NOW)
    assert big not in p.blocked
    assert big in p.deferred
    assert big.reason, "A deferred job must still say why."


def test_a_job_that_cannot_fit_and_cannot_wait_goes_to_andres() -> None:
    big = Candidate(_profile("Too big, due too soon", 800.0, 400.0, deadline_slack=3.0, completion_hours=2.0))
    p = scheduler.plan([big], horizon_hours=0, est=_estimate(30.0), now=NOW)
    assert big.decision in {"NEEDS ANDRES", "BLOCKED"}
    assert big not in p.selected


# ---------------------------------------------------------------------- opportunity cost


def test_opportunity_cost_is_computed_not_asserted() -> None:
    """Selecting a job that consumes the window should report what it displaced."""
    hog = Candidate(_profile("Consumes the window", 300.0, 150.0, completion_hours=6.0))
    others = [Candidate(_profile(f"Alternative {i}", 40.0, 30.0, completion_hours=1.0)) for i in range(5)]
    p = scheduler.plan([hog, *others], horizon_hours=0, est=_estimate(160.0), now=NOW)
    if hog in p.selected:
        assert hog.opportunity_cost > 0, "Taking the whole window displaced other work; say so."
        assert "displaces" in hog.reason


def test_a_job_filling_idle_capacity_displaces_nothing() -> None:
    only = Candidate(_profile("Alone", 50.0, 20.0, completion_hours=1.0))
    p = scheduler.plan([only], horizon_hours=0, est=_estimate(160.0), now=NOW)
    assert only in p.selected
    assert only.opportunity_cost == 0.0
    assert "displaces nothing" in only.reason


# ---------------------------------------------------------------------- lanes and the queue


def test_lanes_separate_the_kinds_of_work() -> None:
    hvle = _profile("Excel transform", 400.0, 30.0, value_class=ValueClass.HIGH_VALUE_LOW_EFFORT.value)
    quick = _profile("Tiny fix", 15.0, 9.0, value_class=ValueClass.QUICK_WIN.value)
    assert scheduler.assign_lane(hvle) == Lane.HIGH_VALUE_LOW_EFFORT.value
    assert scheduler.assign_lane(quick) == Lane.QUICK_WINS.value
    assert scheduler.assign_lane(quick, committed=True) == Lane.ACTIVE_PAID_WORK.value


def test_every_queue_row_explains_itself() -> None:
    """A queue that says what but not why is a queue nobody can correct."""
    cands = [
        Candidate(_profile("A", 500.0, 111.0, completion_hours=4.0)),
        Candidate(_profile("B", 15.0, 9.0, completion_hours=0.3)),
    ]
    p = scheduler.plan(cands, horizon_hours=0, est=_estimate(162.0), now=NOW)
    rows = scheduler.profit_queue(p)
    assert rows
    for row in rows:
        assert row["why"], row
        assert len(row["why"]) > 25, row["why"]


def test_the_knapsack_is_exact_where_greedy_would_fail() -> None:
    """The specific case a value-density sort gets wrong.

    Density: the small jobs score 2.0/min, the big one 1.9/min. Greedy takes all the small work
    for $120 and leaves 10 minutes idle. The exact answer is the big job plus two small, $230.
    """
    big = Candidate(_profile("Dense-looking loser", 190.0, 100.0, completion_hours=4.0))
    smalls = [Candidate(_profile(f"S{i}", 20.0, 10.0, completion_hours=0.3)) for i in range(6)]
    p = scheduler.plan([big, *smalls], horizon_hours=0, est=_estimate(120.0), now=NOW)
    assert p.expected_captured_profit == pytest.approx(230.0), [(c.profile.title, c.value) for c in p.selected]


def test_nothing_is_labelled_now_that_cannot_start_now() -> None:
    """A row badged NOW beside a WAIT FOR RESET capacity badge is a contradiction on the page,
    and the kind that teaches a reader to stop believing the labels."""
    startable = {cap.CapacityStatus.SAFE_TO_START.value, cap.CapacityStatus.TIGHT.value}
    cands = [
        Candidate(_profile("Huge", 2000.0, 900.0, completion_hours=30.0)),
        Candidate(_profile("Small", 60.0, 20.0, completion_hours=1.0)),
    ]
    p = scheduler.plan(cands, est=_estimate(60.0), now=NOW)
    for row in scheduler.profit_queue(p):
        if row["position"] == "NOW":
            assert row["capacity_status"] in startable | {"RESERVED"}, row


def test_at_most_one_row_is_now() -> None:
    cands = [Candidate(_profile(f"J{i}", 40.0, 10.0, completion_hours=0.5)) for i in range(5)]
    p = scheduler.plan(cands, horizon_hours=0, est=_estimate(162.0), now=NOW)
    rows = scheduler.profit_queue(p)
    assert sum(1 for r in rows if r["position"] == "NOW") <= 1
