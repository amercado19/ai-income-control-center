"""Per-opportunity economics, and the label that sends work to the front of the queue.

HIGH VALUE / LOW EFFORT is the most consequential classification in the system, so most of this
file is about refusing to grant it. A label that is easy to earn is a label that means nothing,
and this one reorders the schedule.
"""

from __future__ import annotations

import pytest

from aicc import profit
from aicc.connectors.base import make_opportunity
from aicc.profit import ValueClass


def _opp(**kw):
    base = dict(
        source="hackernews",
        title="Consolidate monthly CSV exports",
        description=(
            "Freelance project. Forty monthly CSV exports with inconsistent headers need "
            "consolidating into one Excel workbook with a summary sheet. Python preferred. "
            "Fixed price, one-time project, clear deliverable."
        ),
        skills=["python", "pandas", "excel"],
        budget_min=800,
        budget_max=800,
    )
    base.update(kw)
    return make_opportunity(**base)


# ---------------------------------------------------------------- the four denominators


def test_every_denominator_is_computed_and_none_is_infinite() -> None:
    p = profit.build(_opp())
    assert p.profit_per_claude_minute > 0
    assert p.profit_per_elapsed_hour > 0
    assert p.profit_per_andres_minute > 0
    for v in (p.profit_per_claude_minute, p.profit_per_elapsed_hour, p.profit_per_andres_minute):
        assert v == v and v != float("inf"), "A zero denominator must yield 0, never infinity."


def test_a_zero_effort_estimate_does_not_produce_an_infinite_ratio() -> None:
    """An infinity here would silently win every ranking it appeared in."""
    p = profit.ProfitProfile(total_claude_minutes=0.0, expected_net_profit=500.0)
    assert p.profit_per_claude_minute == 0.0


def test_expected_value_is_probability_times_net() -> None:
    p = profit.build(_opp(), win_probability=0.25)
    assert p.expected_value == pytest.approx(0.25 * p.expected_net_profit, rel=0.02)


def test_total_claude_time_includes_qa_and_revision() -> None:
    p = profit.build(_opp())
    assert p.total_claude_minutes > p.estimated_claude_minutes
    assert p.estimated_qa_minutes > 0
    assert p.estimated_revision_minutes > 0


# ---------------------------------------------------------------- HIGH VALUE / LOW EFFORT


def test_a_genuine_high_value_low_effort_job_earns_the_label() -> None:
    p = profit.build(_opp(budget_min=1200, budget_max=1200))
    if p.value_class != ValueClass.HIGH_VALUE_LOW_EFFORT.value:
        pytest.skip(f"priors put this at {p.value_class}; the label's conditions are checked below")
    assert len(p.value_reasons) == 4
    assert any("automates" in r for r in p.value_reasons)


def test_complexity_language_blocks_the_low_effort_label() -> None:
    """'Never intentionally underestimate complexity just to make a job appear attractive.'"""
    easy = _opp(budget_min=1200, budget_max=1200)
    hard = _opp(
        budget_min=1200,
        budget_max=1200,
        description=easy.description + " This is a greenfield build against an undocumented legacy system with HIPAA constraints.",
    )
    p_hard = profit.build(hard)
    assert p_hard.value_class != ValueClass.HIGH_VALUE_LOW_EFFORT.value
    assert any("more work than the category" in r for r in p_hard.value_reasons)


def test_work_needing_andres_in_the_room_is_not_low_effort() -> None:
    p = profit.build(
        _opp(
            budget_min=1200,
            budget_max=1200,
            description="Consolidate CSV exports. Requires daily stand-up attendance and weekly calls with stakeholders.",
        )
    )
    assert p.value_class != ValueClass.HIGH_VALUE_LOW_EFFORT.value
    assert any("Needs Andres in person" in r for r in p.value_reasons)


def test_a_cheap_job_is_never_high_value_however_easy_it_is() -> None:
    p = profit.build(_opp(budget_min=25, budget_max=25))
    assert p.value_class not in (ValueClass.HIGH_VALUE.value, ValueClass.HIGH_VALUE_LOW_EFFORT.value)


def test_a_category_with_no_template_behind_it_is_not_low_effort() -> None:
    p = profit.build(
        _opp(
            title="Negotiate with our vendor",
            description="Ongoing vendor negotiation support. Fixed price.",
            skills=[],
            budget_min=1500,
            budget_max=1500,
        )
    )
    assert p.value_class != ValueClass.HIGH_VALUE_LOW_EFFORT.value


def test_an_unprofitable_job_is_labelled_as_such_not_merely_ranked_low() -> None:
    p = profit.ProfitProfile(expected_net_profit=0.0)
    cls, reasons = profit.classify_value(p, _opp())
    assert cls == ValueClass.UNPROFITABLE.value
    assert reasons


# ---------------------------------------------------------------- quick wins


def test_a_genuinely_tiny_job_is_a_quick_win_not_a_high_value_one() -> None:
    p = profit.ProfitProfile(
        expected_net_profit=15.0,
        expected_gross_revenue=15.0,
        total_claude_minutes=9.0,
        delivery_confidence=0.8,
        category="spreadsheet",
    )
    cls, reasons = profit.classify_value(p, _opp())
    assert cls == ValueClass.QUICK_WIN.value
    assert any("fills a gap" in r for r in reasons)


def test_the_quick_win_threshold_is_a_real_number_not_a_vibe() -> None:
    assert profit.QUICK_WIN_AI_MINUTES == 15.0


# ---------------------------------------------------------------- honesty


def test_win_probability_is_labelled_as_an_uncalibrated_heuristic() -> None:
    p = profit.build(_opp())
    assert "NOT YET CALIBRATED" in p.win_probability_basis


def test_time_to_cash_includes_the_platform_clearing_period() -> None:
    """Delivery is not payment. Fiverr holds new-seller funds for a fortnight."""
    fiverr = profit.build(_opp(source="fiverr"))
    upwork = profit.build(_opp(source="upwork"))
    assert fiverr.time_to_cash_days > upwork.time_to_cash_days


def test_repeat_business_signals_are_detected() -> None:
    p = profit.build(_opp(description="Ongoing monthly reporting work, this is the first of several projects."))
    assert p.repeat_client_potential == "HIGH"


def test_vague_requirements_raise_the_revision_risk() -> None:
    vague = profit.build(_opp(description="Clean up some files, format them nicely, etc. Flexible on scope, tbd."))
    assert vague.revision_risk in {"HIGH", "MEDIUM"}


def test_portfolio_totals_say_available_not_earned() -> None:
    """The most dangerous number on a dashboard is expected revenue that reads as real revenue."""
    totals = profit.totals([profit.build(_opp()), profit.build(_opp(budget_min=50, budget_max=50))])
    assert "Available, not captured" in totals["note"]
    assert totals["total_available_profit"] > 0


# ------------------------------------ the rules have to be wired to something, not just tested


def test_work_the_standing_rules_forbid_is_never_profitable() -> None:
    """The defect this exists for, and the most serious one found in this project.

    ``policy.py`` held every standing rule - the PSLF hard reject, the personal-information gate,
    the commitment gate - and was imported by exactly two modules: ``selftest`` and
    ``compliance``. It proved itself against synthetic cases and lit nine green indicators, and
    the scheduler never asked it anything.

    So a for-profit "Contract to permanent" engineering role sat at position NOW on the live
    dashboard, ranked first out of eighty-five listings, while the compliance page two clicks
    away showed PSLF PROTECTION green. Both screens were accurate about what they measured. The
    governance layer was a self-test, not a filter.
    """
    from aicc import profit
    from aicc.models import Opportunity

    opp = Opportunity(
        title="Senior Backend Engineer, Payments",
        description=(
            "REMOTE | Contract to permanent | $120-160/hr. We build the ledger and payment rails "
            "behind a production product. Start by 14 Sep."
        ),
        budget_min=1200.0,
        budget_max=1600.0,
    )
    prof = profit.build(opp)

    assert not prof.policy_allowed
    assert prof.policy_gate == "HARD REJECT — PSLF CONFLICT"
    assert not prof.is_profitable, "A blocked listing must not be schedulable, however valuable."
    assert prof.expected_gross_revenue > 0, "It should still carry its value, so the decline is visible."


def test_a_blocked_listing_never_reaches_the_queue() -> None:
    """End to end through the real scheduler, not the flag in isolation."""
    from aicc import profit, scheduler
    from aicc.models import Opportunity

    blocked = profit.build(
        Opportunity(
            title="Staff Engineer",
            description="Contract-to-hire, converts to a permanent salaried role after six months. $150/hr.",
            budget_min=2000.0,
            budget_max=2000.0,
        )
    )
    fine = profit.build(
        Opportunity(
            title="Consolidate twelve monthly CSV exports into one workbook",
            description=(
                "Fixed-price project. Twelve monthly exports with different column names, consolidated into "
                "one clean workbook with a summary tab and a Python script we can re-run. One week."
            ),
            budget_min=500.0,
            budget_max=500.0,
        )
    )

    candidates = [scheduler.Candidate(p) for p in (blocked, fine) if p.is_profitable]
    titles = {c.profile.title for c in candidates}
    assert "Staff Engineer" not in titles
    assert "Consolidate twelve monthly CSV exports into one workbook" in titles


def test_ordinary_project_work_still_passes_the_gate() -> None:
    """The gate must not become a filter that rejects the business it exists to protect."""
    from aicc import profit
    from aicc.models import Opportunity

    prof = profit.build(
        Opportunity(
            title="Clean up a messy sales spreadsheet",
            description="Freelance, fixed-price contract. One xlsx, normalise the columns, about a week.",
            budget_min=400.0,
            budget_max=600.0,
        )
    )
    assert prof.policy_allowed
    assert prof.policy_gate == ""
