"""Scoring and profitability behaviour, including the regressions that actually bit."""

from __future__ import annotations

import pytest

from aicc import money, scoring
from aicc.models import BudgetType, Opportunity, OpportunityStatus, RiskFlag


def opp(desc: str, **kw) -> Opportunity:
    base = dict(source="hackernews", title=kw.pop("title", "Test job"), description=desc, skills=kw.pop("skills", ["python"]))
    base.update(kw)
    return Opportunity(**base)


# ----------------------------------------------------------------- hard rejects


@pytest.mark.parametrize(
    "text,flag",
    [
        ("I need someone to write my dissertation for my graduate module", RiskFlag.ACADEMIC_DISHONESTY),
        ("This role is on-site only, you must be local to Austin", RiskFlag.PHYSICAL_PRESENCE_REQUIRED),
        ("You will receive funds and forward them. Telegram only. Send a small deposit first.", RiskFlag.LIKELY_SCAM),
        ("Equity only, no pay initially, we will share revenue later", RiskFlag.EQUITY_ONLY),
        ("Please share your login so we can access your account directly", RiskFlag.CREDENTIAL_SHARING_REQUESTED),
        ("Let us take this off upwork and pay you directly via zelle to avoid platform fees", RiskFlag.PAYMENT_OFF_PLATFORM),
    ],
)
def test_non_negotiable_rejections(text, flag):
    o = opp(text + " " * 200, budget_min=500.0, budget_max=500.0, budget_type=BudgetType.FIXED.value)
    bd = scoring.score_opportunity(o)
    assert flag.value in o.risk_flags
    assert bd.rejected is True
    assert o.score == 0.0
    assert o.status == OpportunityStatus.SKIP.value
    assert bd.rejection_reason


def test_ai_prohibited_is_rejected_by_default():
    o = opp(
        "Must be 100% human written, no AI, we run AI detection on every submission." + " " * 200,
        budget_min=400.0,
        budget_max=400.0,
        budget_type=BudgetType.FIXED.value,
    )
    bd = scoring.score_opportunity(o)
    assert bd.rejected is True
    assert "prohibits AI" in bd.rejection_reason


def test_ai_prohibited_can_be_accepted_as_explicitly_manual_work():
    """Doing an AI-prohibited job by hand is legitimate. Doing it secretly with AI is not."""
    o = opp("Must be 100% human written, no AI." + " " * 400, budget_min=2000.0, budget_max=2000.0, budget_type=BudgetType.FIXED.value)
    bd = scoring.score_opportunity(o, allow_manual_ai_prohibited=True)
    assert bd.rejected is False
    assert any(p["name"] == "AI_PROHIBITED_MANUAL" for p in bd.penalties)
    assert o.estimated_ai_effort == 0.0, "manual work must book zero AI hours"


# ------------------------------------------------------- security false positives


@pytest.mark.parametrize(
    "text,should_flag",
    [
        ("keep credentials out of the codebase and use environment variables", False),
        ("store API credentials securely in a secret manager, never in git", False),
        ("we need credentials handled properly", False),
        ("you will need access to our production database", True),
        ("the work involves patient data under HIPAA", True),
        ("handling cardholder data, PCI-DSS applies", True),
    ],
)
def test_security_detector_does_not_punish_good_hygiene(text, should_flag):
    """Regression: an early version matched the bare word 'credentials' and penalised clients
    for asking for good security practice."""
    o = opp(text + " " * 250, skills=["python"])
    flags = scoring.detect_risks(o)
    assert (RiskFlag.SECURITY_SENSITIVE in flags) is should_flag


def test_security_sensitive_goes_to_manual_review():
    o = opp(
        "You will need access to our production database to debug the reporting job. Detailed brief follows. " + " " * 600,
        budget_min=3000.0,
        budget_max=3000.0,
        budget_type=BudgetType.FIXED.value,
        skills=["python", "sql", "postgres"],
    )
    scoring.score_opportunity(o)
    assert o.status == OpportunityStatus.REVIEW.value


# ------------------------------------------------------------------ profitability


def test_bigger_profit_scores_higher_all_else_equal():
    """Regression: scoring on rate alone ranked a $450 job above a $2,475 job."""
    text = (
        "Build a scheduled python data pipeline that consolidates monthly CSV exports and "
        "produces a dashboard. Deliverables include the script, tests and documentation. "
        "Requirements: preserve every row, alert on source failure. " * 4
    )
    small = opp(text, budget_min=500.0, budget_max=500.0, budget_type=BudgetType.FIXED.value)
    large = opp(text, budget_min=5000.0, budget_max=5000.0, budget_type=BudgetType.FIXED.value)
    scoring.score_opportunity(small)
    scoring.score_opportunity(large)
    assert large.score > small.score, "a materially larger net profit must score higher"


def test_profit_per_hour_is_not_absurd():
    """Regression: dividing by human hours alone produced '$833 per hour' on a $500 job,
    which made every automatable job look identically perfect."""
    o = opp(
        "Clean up an excel spreadsheet and normalize the columns. " * 20,
        budget_min=500.0,
        budget_max=500.0,
        budget_type=BudgetType.FIXED.value,
    )
    econ = money.compute(o)
    assert econ.effective_hours > econ.estimated_human_hours, "AI hours must carry weight"
    assert econ.expected_profit_per_hour < econ.expected_profit_per_human_hour


def test_below_floor_value_scores_zero_profitability():
    o = opp("Tiny script to rename files. " * 5, budget_min=15.0, budget_max=15.0, budget_type=BudgetType.FIXED.value)
    bd = scoring.score_opportunity(o)
    assert bd.factors["Expected Profitability"]["awarded"] == 0.0


def test_no_budget_means_no_profit_score_not_a_guess():
    o = opp("Interesting project, budget to be discussed. " * 20)
    bd = scoring.score_opportunity(o)
    assert bd.factors["Expected Profitability"]["awarded"] == 0.0
    assert "No budget stated" in bd.factors["Expected Profitability"]["evidence"]


def test_revenue_is_not_profit():
    o = opp("Excel cleanup task. " * 30, source="fiverr", budget_min=1000.0, budget_max=1000.0, budget_type=BudgetType.FIXED.value)
    econ = money.compute(o)
    assert econ.platform_fee == pytest.approx(200.0), "Fiverr takes 20%"
    assert econ.expected_net_profit < econ.client_price


def test_ai_cash_cost_is_zero_but_usage_is_not():
    o = opp("Excel cleanup task. " * 30, budget_min=500.0, budget_max=500.0, budget_type=BudgetType.FIXED.value)
    econ = money.compute(o)
    assert econ.ai_cash_cost == 0.0
    assert econ.ai_usage_units > 0, "usage draw must be tracked even though cash cost is zero"


def test_contra_is_commission_free():
    o = opp("Task. " * 40, source="contra", budget_min=1000.0, budget_max=1000.0, budget_type=BudgetType.FIXED.value)
    assert money.compute(o).platform_fee == 0.0


# ------------------------------------------------------------------ transparency


def test_every_factor_carries_evidence(sample_opportunity):
    bd = scoring.score_opportunity(sample_opportunity)
    assert set(bd.factors) == {
        "Skill Fit",
        "Automation Potential",
        "Expected Profitability",
        "Likelihood of Winning",
        "Clarity of Requirements",
        "Risk",
    }
    for name, f in bd.factors.items():
        assert f["evidence"].strip(), f"{name} has no evidence"
        assert 0 <= f["awarded"] <= f["available"]


def test_weights_sum_to_one_hundred():
    assert sum(scoring.WEIGHTS.values()) == 100.0


def test_score_is_bounded():
    for text in ["", "x" * 5000, "python excel sql api dashboard automation " * 100]:
        o = opp(text, budget_min=10_000.0, budget_max=10_000.0, budget_type=BudgetType.FIXED.value)
        scoring.score_opportunity(o)
        assert 0.0 <= o.score <= 100.0


@pytest.mark.parametrize("score,expected", [(95, "EXCELLENT"), (85, "STRONG"), (70, "REVIEW"), (30, "SKIP")])
def test_bands(score, expected):
    assert scoring.band(score)[0] == expected


def test_economics_are_labelled_as_estimates(sample_opportunity):
    econ = money.compute(sample_opportunity)
    assert "ESTIMATE" in econ.method
    assert "not an observation" in econ.method.lower()
    assert econ.inputs["price_basis"]
    assert econ.inputs["effort_basis"]


def test_upwork_win_prior_reflects_no_job_success_score():
    """Honesty check: a new Upwork account genuinely is at a disadvantage, and the score
    should say so rather than flattering us."""
    prior, reason = scoring.SOURCE_WIN_PRIOR["upwork"]
    hn_prior, _ = scoring.SOURCE_WIN_PRIOR["hackernews"]
    assert prior < hn_prior
    assert "job success score" in reason.lower()
