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


# --------------------------------------------- regressions found in the live market test
# Every case below is drawn from a real Hacker News listing in the September 2026 thread.


@pytest.mark.parametrize(
    "text,expected",
    [
        # Reef Technologies quoted two currencies. The USD figure is the one that counts.
        ("45-70 USD or 180-280 PLN per hour", (45.0, 70.0, "HOURLY")),
        # Vistulo quoted Polish zloty. Read as dollars this was a ~4x overvaluation.
        ("Senior Java Trading Systems Engineer - 270-300 zl/hr", (None, None, "UNKNOWN")),
        ("220-250 zl/hr net + VAT in PLN", (None, None, "UNKNOWN")),
        ("€60-80 per hour", (None, None, "UNKNOWN")),
        ("£75 per hour", (None, None, "UNKNOWN")),
        # ODK stated hours/week beside the rate. The duration must not be read as the price.
        ("Long-term contract, 30-40 hours/week | $90-110/hour USD", (90.0, 110.0, "HOURLY")),
        ("10-40 hrs/week, no rate given", (None, None, "UNKNOWN")),
        ("~20-40 hrs/wk 1099 contract", (None, None, "UNKNOWN")),
        ("$45/hour, 20-30 hrs per week", (45.0, 45.0, "HOURLY")),
        # Still works for the straightforward cases.
        ("$120-160/hr contract to perm", (120.0, 160.0, "HOURLY")),
        ("80-90 USD per hour", (80.0, 90.0, "HOURLY")),
        ("$23-$34 USD/hour", (23.0, 34.0, "HOURLY")),
    ],
)
def test_rate_extraction_against_real_listings(text, expected):
    from aicc.connectors.base import extract_rate

    assert extract_rate(text) == expected


@pytest.mark.parametrize(
    "text,should_flag",
    [
        ("All roles for Poland or Romanian residents only. B2B contract.", True),
        ("Must be based in the EU, remote", True),
        ("U.S. Citizens only, DoD prime contractor", False),
        ("You need to live in the USA and be a US citizen or Green Card holder", False),
        ("Remote worldwide", False),
    ],
)
def test_geographic_exclusion(text, should_flag):
    """A listing restricted to residents of another country is a hard filter, not a preference."""
    o = opp(text + " " * 260)
    assert (RiskFlag.GEO_EXCLUDED in scoring.detect_risks(o)) is should_flag


def test_geographic_exclusion_is_a_hard_reject():
    o = opp(
        "Fully remote, Poland or Romanian residents only, B2B contract. " * 8,
        budget_min=80.0,
        budget_max=90.0,
        budget_type=BudgetType.HOURLY.value,
    )
    assert scoring.score_opportunity(o).rejected is True


def test_ai_written_proposal_objection_is_not_a_prohibition_on_the_work():
    """A client who reads applications personally and asks for no LLM-generated text has not
    prohibited AI in the work. Rejecting the job outright discards a legitimate opportunity;
    the right response is to write that one proposal by hand."""
    text = (
        "Contract software engineers for autonomy and perception roles, remote US. "
        "I read every application myself. I do not use AI to screen your applications and will "
        "reply to every one; but please don't send over walls of LLM generated text, I'd much "
        "rather be communicating with humans. " * 3
    )
    o = opp(text, budget_min=120.0, budget_max=150.0, budget_type=BudgetType.HOURLY.value)
    bd = scoring.score_opportunity(o)
    assert RiskFlag.AI_PROPOSAL_DISCOURAGED.value in o.risk_flags
    assert RiskFlag.AI_PROHIBITED.value not in o.risk_flags
    assert bd.rejected is False, "the job is legitimate; only the generated proposal is unwelcome"
    assert any(p["name"] == RiskFlag.AI_PROPOSAL_DISCOURAGED.value for p in bd.penalties)


def test_work_level_ai_prohibition_still_rejects():
    o = opp(
        "Must be 100% human written, no AI. We run AI detection on every submission. " * 6,
        budget_min=400.0,
        budget_max=400.0,
        budget_type=BudgetType.FIXED.value,
    )
    assert scoring.score_opportunity(o).rejected is True


@pytest.mark.parametrize(
    "text,prohibited",
    [
        # The client describing their OWN process is not a prohibition on us.
        ("I do not use AI to screen your applications and will reply to every one", False),
        # Substring matching used to fire on this. It is an aircraft, not an AI policy.
        ("We operate a counter-drone aircraft, no aircraft experience needed", False),
        ("We are an AI company building AI products with AI tooling", False),
        ("Must be 100% human written, no AI, we run every submission through AI detection", True),
        ("Looking for human-written only content, no ChatGPT", True),
        ("You must not use AI for this work", True),
        ("AI generated content will be rejected", True),
        ("Strictly no AI", True),
    ],
)
def test_ai_prohibition_detection_is_precise(text, prohibited):
    o = opp(text + " " * 260)
    assert (RiskFlag.AI_PROHIBITED in scoring.detect_risks(o)) is prohibited


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Clean up 14 excel spreadsheets, normalize the columns, pivot tables", "spreadsheet"),
        ("Extract line items from 300 scanned PDF invoices using OCR", "pdf_extraction"),
        ("Build a scheduled ETL pipeline with airflow and dbt into snowflake", "data_pipeline"),
        ("Weekly competitor pricing research across six websites, compile a tracker", "web_research"),
        ("Build an internal dashboard showing burn rate per award with charts", "dashboard"),
        ("Write API integration with webhooks and REST endpoints", "api_integration"),
        # Substring matching sent this to pdf_extraction, because "ocr" hides inside "Sociocracy"
        # and "cli" inside "client". The proposal it produced discussed invoices at a company
        # building GPU container runners.
        ("We use Sociocracy 3.0 and need a client-facing engineer", "generic"),
        ("Senior Python Backend Engineer, GPU container runners, decentralized", "generic"),
    ],
)
def test_category_classification(text, expected):
    assert scoring.classify(Opportunity(description=text)) == expected


def test_one_keyword_is_not_a_classification():
    """A single weak hit must fall back to generic. A confidently wrong proposal template is
    worse than a blander correct one."""
    assert scoring.classify(Opportunity(description="We have an api.")) == "generic"


def test_hourly_roles_get_availability_not_a_delivery_date():
    """Quoting '4-5 business days' to a client hiring 20-40 hrs/week signals you misread the post."""
    from aicc import proposals

    hourly = opp("Ongoing contract work. " * 30, budget_min=90.0, budget_max=110.0, budget_type=BudgetType.HOURLY.value)
    fixed = opp("One-off project. " * 30, budget_min=500.0, budget_max=500.0, budget_type=BudgetType.FIXED.value)
    assert "start within" in proposals.build_turnaround(hourly)
    assert "business days" in proposals.build_turnaround(fixed)
    assert "Availability:" in proposals.generate(hourly).body
    assert "Timeline:" in proposals.generate(fixed).body


# --------------------------------------------------- opportunity classes + win probability


@pytest.mark.parametrize(
    "kw,expected",
    [
        (dict(budget_min=60.0, budget_max=60.0, budget_type=BudgetType.FIXED.value), "SMALL"),
        (dict(budget_min=250.0, budget_max=400.0, budget_type=BudgetType.FIXED.value), "MID"),
        (dict(budget_min=2000.0, budget_max=3000.0, budget_type=BudgetType.FIXED.value), "HIGH_VALUE"),
        (dict(budget_min=90.0, budget_max=110.0, budget_type=BudgetType.HOURLY.value), "ONGOING"),
        (dict(), "UNKNOWN"),
    ],
)
def test_opportunity_classification(kw, expected):
    from aicc.classes import classify_class

    assert classify_class(opp("Some work. " * 30, **kw)).value == expected


def test_hourly_is_ongoing_not_a_small_job():
    """A $90/hour engagement is not a '$90 job'. Treating it as one is how the effort model ends
    up quoting a four-day turnaround on a twelve-month contract."""
    from aicc.classes import OpportunityClass, classify_class

    o = opp("Ongoing data engineering. " * 20, budget_min=90.0, budget_max=90.0, budget_type=BudgetType.HOURLY.value)
    assert classify_class(o) is OpportunityClass.ONGOING


def test_ongoing_detected_from_text_even_with_a_fixed_budget():
    from aicc.classes import OpportunityClass, classify_class

    o = opp("Long-term contract, 30-40 hours per week. " * 12, budget_min=200.0, budget_max=200.0, budget_type=BudgetType.FIXED.value)
    assert classify_class(o) is OpportunityClass.ONGOING


def test_class_weights_renormalise_to_the_same_total():
    """Scores stay comparable across classes: what changes is what the score is MADE of."""
    from aicc.classes import CLASS_WEIGHT_PROFILE, weights_for

    for cls_ in CLASS_WEIGHT_PROFILE:
        w = weights_for(cls_, scoring.WEIGHTS)
        assert sum(w.values()) == pytest.approx(100.0, abs=0.2), cls_


def test_small_jobs_weight_automation_above_profit():
    """A $60 job that costs an hour of human attention is a loss, whatever the margin says."""
    from aicc.classes import OpportunityClass, weights_for

    w = weights_for(OpportunityClass.SMALL, scoring.WEIGHTS)
    assert w["automation_potential"] > w["expected_profitability"]


def test_ongoing_work_weights_skill_fit_above_automation():
    """Long engagements are judged on judgement, not throughput."""
    from aicc.classes import OpportunityClass, weights_for

    w = weights_for(OpportunityClass.ONGOING, scoring.WEIGHTS)
    assert w["skill_fit"] > w["automation_potential"] * 2


def test_no_factor_can_exceed_its_available_points_under_any_class():
    """Regression: clarity awarded fixed point values that quietly exceeded the available
    points once clarity was up-weighted for small jobs."""
    for budget, btype in [
        (60.0, BudgetType.FIXED.value),
        (300.0, BudgetType.FIXED.value),
        (5000.0, BudgetType.FIXED.value),
        (95.0, BudgetType.HOURLY.value),
    ]:
        o = opp(
            "Detailed brief with deliverables and acceptance criteria. " * 30,
            budget_min=budget,
            budget_max=budget,
            budget_type=btype,
            skills=["python", "excel", "sql"],
        )
        bd = scoring.score_opportunity(o)
        for name, f in bd.factors.items():
            assert f["awarded"] <= f["available"] + 1e-9, f"{name} exceeded its weight at {budget}"


def test_win_estimate_is_labelled_uncalibrated():
    """A number presented as a probability invites you to act on it as one. This model has never
    seen a won or lost job and says so."""
    from aicc.classes import CALIBRATION_STATUS

    o = opp("Python data pipeline work. " * 30, budget_min=1000.0, budget_max=1000.0, budget_type=BudgetType.FIXED.value)
    scoring.score_opportunity(o)
    assert o.win_estimate["calibration"] == CALIBRATION_STATUS
    assert "HEURISTIC" in o.win_estimate["calibration"]


def test_win_estimate_records_every_adjustment():
    o = opp(
        "Senior engineer, 8+ years required, portfolio of similar work essential. " * 8,
        budget_min=5000.0,
        budget_max=5000.0,
        budget_type=BudgetType.FIXED.value,
    )
    scoring.score_opportunity(o)
    factors = o.win_estimate["factors"]
    assert factors[0]["factor"] == "Source prior"
    for f in factors:
        assert f["why"].strip(), "every adjustment must carry its reason"
    assert any("Senior" in f["factor"] for f in factors)


def test_reputation_demand_lowers_win_probability():
    """This account has no reviews and no Job Success Score. The model should say so."""
    plain = opp("Build a python script to merge CSV files. " * 20, budget_min=400.0, budget_max=400.0, budget_type=BudgetType.FIXED.value)
    gated = opp(
        "Build a python script to merge CSV files. Must be Top Rated with 100+ completed jobs and a proven track record. " * 10,
        budget_min=400.0,
        budget_max=400.0,
        budget_type=BudgetType.FIXED.value,
    )
    scoring.score_opportunity(plain)
    scoring.score_opportunity(gated)
    assert gated.win_estimate["probability_high"] < plain.win_estimate["probability_high"]


def test_win_probability_is_a_range_never_a_point():
    o = opp("Work. " * 40, budget_min=500.0, budget_max=500.0, budget_type=BudgetType.FIXED.value)
    scoring.score_opportunity(o)
    we = o.win_estimate
    assert we["probability_high"] > we["probability_low"], "a point estimate implies precision we lack"
    assert we["band"] in {"High", "Medium", "Low"}
