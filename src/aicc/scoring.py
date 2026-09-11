"""Transparent opportunity scoring (spec section 12).

A single opaque 0-100 number is not a score, it is a vibe. Every factor here records the points
awarded, the points available, and the evidence in the listing that drove it, so the dashboard
can answer "why did this get 84?" without anyone reading the source.

Weights (100 total):
    Skill Fit                25
    Automation Potential     20
    Expected Profitability   20
    Likelihood of Winning    15
    Clarity of Requirements  10
    Risk                     10

Hard rejects run before scoring and short-circuit it. Penalties are applied after.
"""

from __future__ import annotations

import re

from .config import PROFILE
from .models import BudgetType, Opportunity, OpportunityStatus, RiskFlag, ScoreBreakdown
from .money import Economics, automation_pct, compute

WEIGHTS = {
    "skill_fit": 25.0,
    "automation_potential": 20.0,
    "expected_profitability": 20.0,
    "likelihood_of_winning": 15.0,
    "clarity_of_requirements": 10.0,
    "risk": 10.0,
}

BANDS = [
    (90, "EXCELLENT", "FIRE"),
    (80, "STRONG", "GREEN"),
    (65, "REVIEW", "YELLOW"),
    (0, "SKIP", "WHITE"),
]

# Net profit at which the size component of the profitability score saturates. Above this,
# more money barely changes the decision; below it, the difference matters a lot.
PROFITABILITY_SATURATION = 2500.0


# ---------------------------------------------------------------------------
# Category classification
# ---------------------------------------------------------------------------

CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "spreadsheet": ["excel", "spreadsheet", "xlsx", "google sheet", "pivot", "vlookup", "formula", "vba", "macro"],
    "data_cleaning": ["clean", "cleanup", "deduplicate", "normalize", "standardize", "messy data", "data quality", "tidy"],
    "pdf_extraction": ["pdf", "scanned", "ocr", "invoice extraction", "parse document", "extract from pdf"],
    "web_research": ["research", "compile a list", "find contacts", "market research", "competitor", "lead list"],
    "data_pipeline": ["pipeline", "etl", "elt", "ingest", "data warehouse", "airflow", "dbt", "batch job"],
    "api_integration": ["api", "integration", "webhook", "rest", "graphql", "oauth", "third-party service"],
    "dashboard": ["dashboard", "visualization", "chart", "report view", "bi ", "looker", "tableau", "metabase", "grafana"],
    "scripting": ["script", "python script", "automate a task", "cli", "command line", "bot"],
    "automation": ["automate", "automation", "workflow", "zapier", "make.com", "scheduled", "cron", "github action"],
    "reporting": ["recurring report", "weekly report", "monthly report", "reporting", "generate report"],
    "financial_model": ["financial model", "forecast", "budget model", "valuation", "p&l", "cash flow", "projections"],
    "documentation": ["documentation", "write docs", "readme", "technical writing", "user guide"],
    "qa_review": ["qa", "quality check", "review output", "verify data", "audit the", "proofread data"],
}


def classify(opp: Opportunity) -> str:
    text = f"{opp.title} {opp.description} {' '.join(opp.skills)}".lower()
    best, best_hits = "generic", 0
    for category, keywords in CATEGORY_KEYWORDS.items():
        hits = sum(1 for kw in keywords if kw in text)
        if hits > best_hits:
            best, best_hits = category, hits
    return best


# ---------------------------------------------------------------------------
# Risk detection
# ---------------------------------------------------------------------------

RISK_PATTERNS: list[tuple[RiskFlag, list[str]]] = [
    (
        RiskFlag.AI_PROHIBITED,
        [
            "no ai",
            "no chatgpt",
            "without ai",
            "human written only",
            "human-written only",
            "ai generated content will be rejected",
            "no ai-generated",
            "must be 100% human",
            "do not use ai",
            "don't use ai",
            "ai detection",
        ],
    ),
    (
        RiskFlag.ACADEMIC_DISHONESTY,
        [
            "my exam",
            "take my test",
            "my assignment",
            "my homework",
            "write my essay",
            "my dissertation",
            "my thesis",
            "sit my",
            "complete my coursework",
            "online class for me",
            "attend my class",
            "proctored",
        ],
    ),
    (
        RiskFlag.PHYSICAL_PRESENCE_REQUIRED,
        [
            "on-site only",
            "onsite only",
            "must be local",
            "in person",
            "in-person",
            "must relocate",
            "hybrid in office",
            "come to our office",
        ],
    ),
    (
        RiskFlag.PROPRIETARY_SOFTWARE_REQUIRED,
        [
            "must own",
            "requires a license to",
            "sap license",
            "bloomberg terminal",
            "matlab license",
            "autocad license",
            "must have access to our",
        ],
    ),
    (
        RiskFlag.LIKELY_SCAM,
        [
            "send us a deposit",
            "pay a fee to start",
            "training fee",
            "buy your own equipment first",
            "telegram only",
            "whatsapp only for interview",
            "process payments for us",
            "receive and forward packages",
            "crypto wallet setup",
            "gift card",
        ],
    ),
    # Deliberately specific. An early version matched the bare word "credentials", which fired
    # on "keep credentials out of the codebase" - a client asking for GOOD hygiene - and cost a
    # perfectly good $1,440 job 15 points. A risk detector that punishes clients for having
    # security standards is worse than no detector. Each phrase below describes work that
    # actually touches sensitive systems or regulated data.
    (
        RiskFlag.SECURITY_SENSITIVE,
        [
            "access to our production",
            "access to production database",
            "production credentials",
            "patient data",
            "patient records",
            "phi data",
            "hipaa",
            "protected health information",
            "payment card data",
            "cardholder data",
            "pci-dss",
            "pci dss",
            "penetration test",
            "pentest",
            "vulnerability assessment",
            "aws root account",
            "ssh access to production",
            "root access to our",
            "social security numbers",
            "full ssn",
            "customer pii",
            "personally identifiable information",
        ],
    ),
    (
        RiskFlag.CREDENTIAL_SHARING_REQUESTED,
        [
            "share your login",
            "give us your password",
            "access to your account",
            "use your own account",
            "log in as us",
            "your api key",
        ],
    ),
    (
        RiskFlag.PAYMENT_OFF_PLATFORM,
        [
            "pay outside",
            "off platform payment",
            "avoid platform fees",
            "pay you directly via zelle",
            "pay via wire to avoid",
            "take this off upwork",
            "take this off fiverr",
        ],
    ),
    (
        RiskFlag.EQUITY_ONLY,
        [
            "equity only",
            "no pay initially",
            "unpaid trial",
            "profit share only",
            "revenue share only",
            "paid in exposure",
            "for free at first",
        ],
    ),
]

# Rejected outright. No score is computed; the opportunity is archived with a reason.
HARD_REJECT = {
    RiskFlag.ACADEMIC_DISHONESTY: "Academic dishonesty. Never accepted.",
    RiskFlag.PHYSICAL_PRESENCE_REQUIRED: "Requires physical presence. Not deliverable remotely.",
    RiskFlag.LIKELY_SCAM: "Matches known scam patterns.",
    RiskFlag.EQUITY_ONLY: "No cash compensation.",
    RiskFlag.CREDENTIAL_SHARING_REQUESTED: "Requests credential sharing.",
    RiskFlag.PAYMENT_OFF_PLATFORM: "Requests off-platform payment, which violates marketplace terms.",
}

# Applied as point deductions rather than rejection.
PENALTY_POINTS = {
    RiskFlag.PROPRIETARY_SOFTWARE_REQUIRED: 20.0,
    RiskFlag.UNCLEAR_DELIVERABLES: 10.0,
    RiskFlag.UNREALISTIC_DEADLINE: 12.0,
    RiskFlag.POOR_CLIENT_HISTORY: 15.0,
    RiskFlag.SECURITY_SENSITIVE: 12.0,
}

DEADLINE_URGENCY = re.compile(r"\b(today|asap|within \d+ hours?|next (?:few )?hours?|by tonight|same day|immediately|urgent)\b", re.I)


def detect_risks(opp: Opportunity) -> list[RiskFlag]:
    text = f"{opp.title} {opp.description}".lower()
    flags: list[RiskFlag] = []
    for flag, patterns in RISK_PATTERNS:
        if any(p in text for p in patterns):
            flags.append(flag)

    if opp.ai_allowed is False and RiskFlag.AI_PROHIBITED not in flags:
        flags.append(RiskFlag.AI_PROHIBITED)

    if len(opp.description or "") < 180 and not opp.skills:
        flags.append(RiskFlag.UNCLEAR_DELIVERABLES)

    if DEADLINE_URGENCY.search(text) and (opp.estimated_hours or 0) > 4:
        flags.append(RiskFlag.UNREALISTIC_DEADLINE)

    for word in PROFILE.will_not_do:
        if word in text and RiskFlag.ACADEMIC_DISHONESTY not in flags and word in ("academic assignment", "take my exam"):
            flags.append(RiskFlag.ACADEMIC_DISHONESTY)

    return list(dict.fromkeys(flags))


# ---------------------------------------------------------------------------
# Factor scoring
# ---------------------------------------------------------------------------


def _skill_fit(opp: Opportunity, bd: ScoreBreakdown) -> float:
    available = WEIGHTS["skill_fit"]
    text = f"{opp.title} {opp.description} {' '.join(opp.skills)}".lower()
    matched = sorted({s for s in PROFILE.skill_set() if s in text})
    domain_hits = sorted({d for d in PROFILE.strong_domains if d in text})

    if not matched:
        bd.add_factor("Skill Fit", 0.0, available, "No skill from the operator profile appears in this listing.")
        return 0.0

    # Saturating: 6 matched skills is already a strong fit; 20 is not three times better.
    ratio = min(len(matched) / 6.0, 1.0)
    awarded = available * (0.55 + 0.45 * ratio)
    if domain_hits:
        awarded = min(available, awarded + 2.0)

    evidence = f"Matched {len(matched)} profile skills: {', '.join(matched[:8])}"
    if len(matched) > 8:
        evidence += f" (+{len(matched) - 8} more)"
    if domain_hits:
        evidence += f". Domain overlap: {', '.join(domain_hits)}"
    bd.add_factor("Skill Fit", awarded, available, evidence + ".")
    return awarded


def _automation_potential(econ: Economics, bd: ScoreBreakdown) -> float:
    available = WEIGHTS["automation_potential"]
    pct = automation_pct(econ)
    awarded = available * (pct / 100.0)
    bd.add_factor(
        "Automation Potential",
        awarded,
        available,
        f"{pct}% of estimated effort is AI-carryable ({econ.estimated_ai_hours:.1f}h AI / {econ.estimated_human_hours:.1f}h human).",
    )
    return awarded


def _profitability(econ: Economics, bd: ScoreBreakdown) -> float:
    available = WEIGHTS["expected_profitability"]
    if econ.client_price <= 0:
        bd.add_factor("Expected Profitability", 0.0, available, "No budget stated, so profit cannot be estimated.")
        return 0.0
    if econ.client_price < PROFILE.minimum_job_value:
        bd.add_factor(
            "Expected Profitability",
            0.0,
            available,
            f"Estimated value ${econ.client_price:,.0f} is below the ${PROFILE.minimum_job_value:,.0f} floor.",
        )
        return 0.0

    # Two things matter and they are not the same thing: the RATE (is this a good use of an
    # hour?) and the ABSOLUTE PROFIT (is this worth the fixed overhead of winning and running a
    # job at all?). Scoring on rate alone ranks a $450 job above a $2,475 job, which is exactly
    # backwards for a business. Scoring on absolute profit alone rewards big, slow, low-margin
    # work. So the sub-score blends them 60/40.
    rate = econ.expected_profit_per_hour
    if rate >= PROFILE.target_hourly * 2:
        rate_component = 1.0
    elif rate >= PROFILE.target_hourly:
        rate_component = 0.85
    elif rate >= PROFILE.minimum_hourly:
        rate_component = 0.55
    else:
        rate_component = 0.15

    # Saturating on absolute net: the difference between $200 and $1,200 of profit is large;
    # between $4,000 and $5,000 it barely changes the decision.
    net = max(0.0, econ.expected_net_profit)
    size_component = min(1.0, (net / PROFITABILITY_SATURATION) ** 0.5)

    awarded = available * (0.60 * rate_component + 0.40 * size_component)

    bd.add_factor(
        "Expected Profitability",
        awarded,
        available,
        f"Estimated net ${econ.expected_net_profit:,.0f} on ${econ.client_price:,.0f} "
        f"({econ.margin_pct:.0f}% margin). ${rate:,.0f} per effective hour vs "
        f"${PROFILE.target_hourly:,.0f} target ({econ.effective_hours:.1f} effective h = "
        f"{econ.estimated_human_hours:.1f}h human + {econ.estimated_ai_hours:.1f}h AI discounted). "
        f"Leverage: ${econ.expected_profit_per_human_hour:,.0f} per human hour.",
    )
    return awarded


# Competition priors by source. These are structural facts about each channel, not guesses
# about a specific listing. See docs/MARKETPLACE_RULES.md for why Upwork scores lowest.
SOURCE_WIN_PRIOR: dict[str, tuple[float, str]] = {
    "hackernews": (0.85, "Direct email to the poster; no bidding war, no reputation gate."),
    "direct": (0.85, "Direct relationship."),
    "contra": (0.60, "Commission-free and lower volume than Upwork, but still a marketplace."),
    "himalayas": (0.45, "Aggregated listing; applicant volume unknown and often high."),
    "remoteok": (0.45, "Aggregated listing; applicant volume unknown and often high."),
    "weworkremotely": (0.45, "Aggregated listing; applicant volume unknown and often high."),
    "upwork": (
        0.25,
        "New account with no Job Success Score competes against established freelancers; clients filter on JSS before reading proposals.",
    ),
    "fiverr": (0.30, "Inbound only; ranking depends on gig history this account does not yet have."),
    "demo": (0.50, "Synthetic."),
}


def _likelihood_of_winning(opp: Opportunity, econ: Economics, bd: ScoreBreakdown) -> float:
    available = WEIGHTS["likelihood_of_winning"]
    prior, reason = SOURCE_WIN_PRIOR.get(opp.source, (0.40, "Unknown source; neutral prior."))
    factor = prior
    notes = [reason]

    # A specific, technical brief filters out most generic applicants, which helps a specialist.
    if len(opp.description or "") > 800 and len(opp.skills) >= 3:
        factor = min(1.0, factor + 0.10)
        notes.append("Detailed technical brief favours a specialist applicant (+10%).")

    # Very large budgets attract heavier competition and more scrutiny of track record.
    if econ.client_price >= 2000:
        factor = max(0.05, factor - 0.10)
        notes.append("Budget >= $2,000 attracts stronger competition (-10%).")

    awarded = available * factor
    bd.add_factor("Likelihood of Winning", awarded, available, " ".join(notes))
    return awarded


def _clarity(opp: Opportunity, bd: ScoreBreakdown) -> float:
    available = WEIGHTS["clarity_of_requirements"]
    score, notes = 0.0, []
    desc = opp.description or ""

    if len(desc) >= 800:
        score += 4
        notes.append("detailed description (>=800 chars)")
    elif len(desc) >= 300:
        score += 2.5
        notes.append("moderate description (>=300 chars)")
    else:
        notes.append("thin description")

    if opp.skills:
        score += 2
        notes.append(f"{len(opp.skills)} skills specified")
    if opp.budget_min is not None or opp.budget_max is not None:
        score += 2
        notes.append("budget stated")
    if re.search(r"\b(deliverable|acceptance|requirement|scope|milestone)s?\b", desc, re.I):
        score += 2
        notes.append("deliverables or scope named")

    bd.add_factor("Clarity of Requirements", min(score, available), available, "; ".join(notes).capitalize() + ".")
    return min(score, available)


def _risk_factor(flags: list[RiskFlag], bd: ScoreBreakdown) -> float:
    available = WEIGHTS["risk"]
    soft = [f for f in flags if f in PENALTY_POINTS or f == RiskFlag.AI_PROHIBITED]
    if not soft:
        bd.add_factor("Risk", available, available, "No risk flags detected.")
        return available
    awarded = max(0.0, available - 3.0 * len(soft))
    bd.add_factor("Risk", awarded, available, f"{len(soft)} risk flag(s): {', '.join(f.value for f in soft)}.")
    return awarded


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def band(score: float) -> tuple[str, str]:
    for threshold, name, light in BANDS:
        if score >= threshold:
            return name, light
    return "SKIP", "WHITE"


def score_opportunity(opp: Opportunity, *, allow_manual_ai_prohibited: bool = False) -> ScoreBreakdown:
    """Score one opportunity and write the result onto the record.

    ``allow_manual_ai_prohibited``: when Andres explicitly chooses to do an AI-prohibited job by
    hand, the flag stops being a rejection and becomes a large penalty instead - the job is
    legitimate, it just loses the automation advantage entirely (spec section 12).
    """
    bd = ScoreBreakdown()

    if not opp.category:
        opp.category = classify(opp)

    econ = compute(opp)
    opp.estimated_hours = econ.estimated_hours
    opp.estimated_ai_effort = econ.estimated_ai_hours
    opp.estimated_non_ai_effort = econ.estimated_human_hours
    opp.estimated_platform_fee = econ.platform_fee
    opp.estimated_net_revenue = econ.expected_net_profit
    opp.estimated_cost = round(econ.ai_cash_cost + econ.infrastructure_cost, 2)
    opp.confidence = econ.confidence

    flags = detect_risks(opp)
    opp.risk_flags = [f.value for f in flags]

    # --- hard rejects -------------------------------------------------------
    for flag in flags:
        if flag in HARD_REJECT:
            bd.rejected = True
            bd.rejection_reason = HARD_REJECT[flag]
            bd.final_score = 0.0
            opp.score = 0.0
            opp.score_band = "SKIP"
            opp.status = OpportunityStatus.SKIP.value
            opp.score_breakdown = bd.to_dict()
            return bd

    if RiskFlag.AI_PROHIBITED in flags and not allow_manual_ai_prohibited:
        bd.rejected = True
        bd.rejection_reason = (
            "Client prohibits AI. Automatic reject. Accepting this and using AI anyway would be "
            "dishonest; it can be re-opened as manual work if you choose to do it by hand."
        )
        bd.final_score = 0.0
        opp.score = 0.0
        opp.score_band = "SKIP"
        opp.status = OpportunityStatus.SKIP.value
        opp.score_breakdown = bd.to_dict()
        return bd

    # --- factors ------------------------------------------------------------
    total = 0.0
    total += _skill_fit(opp, bd)
    total += _automation_potential(econ, bd)
    total += _profitability(econ, bd)
    total += _likelihood_of_winning(opp, econ, bd)
    total += _clarity(opp, bd)
    total += _risk_factor(flags, bd)
    bd.raw_total = round(total, 1)

    # --- penalties ----------------------------------------------------------
    for flag in flags:
        if flag in PENALTY_POINTS:
            points = PENALTY_POINTS[flag]
            bd.add_penalty(flag.value, points, _penalty_evidence(flag))
            total -= points

    if RiskFlag.AI_PROHIBITED in flags and allow_manual_ai_prohibited:
        bd.add_penalty(
            "AI_PROHIBITED_MANUAL",
            25.0,
            "Client prohibits AI and you chose to do it manually. Every hour is a human hour.",
        )
        total -= 25.0
        opp.estimated_non_ai_effort = econ.estimated_hours
        opp.estimated_ai_effort = 0.0

    final = round(max(0.0, min(100.0, total)), 1)
    bd.final_score = final

    opp.score = final
    opp.match_score = round(bd.factors.get("Skill Fit", {}).get("awarded", 0.0) / WEIGHTS["skill_fit"] * 100, 1)
    opp.profit_score = round(bd.factors.get("Expected Profitability", {}).get("awarded", 0.0) / WEIGHTS["expected_profitability"] * 100, 1)
    opp.competition_score = round(
        bd.factors.get("Likelihood of Winning", {}).get("awarded", 0.0) / WEIGHTS["likelihood_of_winning"] * 100, 1
    )

    name, _light = band(final)
    opp.score_band = name
    opp.status = {
        "EXCELLENT": OpportunityStatus.STRONG_MATCH.value,
        "STRONG": OpportunityStatus.STRONG_MATCH.value,
        "REVIEW": OpportunityStatus.REVIEW.value,
        "SKIP": OpportunityStatus.SKIP.value,
    }[name]

    if RiskFlag.SECURITY_SENSITIVE in flags:
        opp.status = OpportunityStatus.REVIEW.value
        bd.add_penalty("MANUAL_REVIEW_REQUIRED", 0.0, "Security-sensitive work always goes to manual review.")

    opp.score_breakdown = bd.to_dict()
    return bd


def _penalty_evidence(flag: RiskFlag) -> str:
    return {
        RiskFlag.PROPRIETARY_SOFTWARE_REQUIRED: "Listing requires licensed software that is not owned.",
        RiskFlag.UNCLEAR_DELIVERABLES: "Description is too thin to know what 'done' means.",
        RiskFlag.UNREALISTIC_DEADLINE: "Urgency language paired with an estimate over four hours.",
        RiskFlag.POOR_CLIENT_HISTORY: "Client history on the source platform is poor.",
        RiskFlag.SECURITY_SENSITIVE: "Touches production systems or regulated data.",
    }.get(flag, flag.value)


def score_all(opps: list[Opportunity]) -> list[Opportunity]:
    for opp in opps:
        score_opportunity(opp)
    return sorted(opps, key=lambda o: o.score, reverse=True)


__all__ = ["score_opportunity", "score_all", "classify", "detect_risks", "band", "WEIGHTS", "BudgetType"]
