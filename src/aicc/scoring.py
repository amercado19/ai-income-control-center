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

from .classes import CLASS_LABEL, classify_class, estimate_win, weights_for
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


# Keywords are matched on WORD BOUNDARIES, not as substrings. Plain `in text` matching sent a
# GPU-container-orchestration role to the PDF-extraction template, because "ocr" appears inside
# "Sociocracy" and "cli" inside "client". The proposal it produced talked confidently about
# invoices. A confidently wrong proposal is worse than a generic one.
_CATEGORY_RE: dict[str, re.Pattern[str]] = {
    category: re.compile(
        r"(?<![a-z])(?:" + "|".join(re.escape(k.strip()) for k in keywords) + r")s?(?![a-z])", re.I
    )  # trailing s? so "charts" matches "chart"
    for category, keywords in CATEGORY_KEYWORDS.items()
}

CATEGORY_PRIORITY = [
    # Most specific and most reliably deliverable first. Used only to break ties.
    "spreadsheet",
    "pdf_extraction",
    "data_cleaning",
    "financial_model",
    "dashboard",
    "data_pipeline",
    "reporting",
    "api_integration",
    "automation",
    "web_research",
    "scripting",
    "documentation",
    "qa_review",
]

MIN_CATEGORY_HITS = 2
"""Below this, no category is confident enough to pick a specialised template.

One keyword is not a classification. Falling back to `generic` costs a slightly blander proposal;
guessing wrong costs credibility with the client, which is far more expensive.
"""


def _scoring_text(opp: Opportunity) -> str:
    """The text to reason over: the unredacted posting when still in memory, else the stored
    excerpt. Redaction protects the store, not the analysis."""
    body = getattr(opp, "full_description", None) or opp.description
    return f"{opp.title} {body} {' '.join(opp.skills)}"


def classify(opp: Opportunity) -> str:
    text = _scoring_text(opp)
    scores = {category: len(set(pattern.findall(text))) for category, pattern in _CATEGORY_RE.items()}
    top = max(scores.values())
    if top < MIN_CATEGORY_HITS:
        return "generic"
    # Ties are broken by CATEGORY_PRIORITY rather than by falling back to generic: an Excel
    # cleanup job scores equally on `spreadsheet` and `data_cleaning`, and either template serves
    # it well. Generic is for genuinely unclassifiable work, not for near-neighbours.
    tied = [c for c, n in scores.items() if n == top]
    return min(tied, key=lambda c: CATEGORY_PRIORITY.index(c) if c in CATEGORY_PRIORITY else 99)


# ---------------------------------------------------------------------------
# Risk detection
# ---------------------------------------------------------------------------

# Geography. Andres is US-based, so a listing restricted to residents of somewhere else is a
# hard filter, not a preference - found live on a Polish outsourcing firm's posting that otherwise
# scored well. US-inclusive restrictions ("US citizens only", "must have US work authorization")
# are deliberately NOT matched here: those include him.
_GEO_EXCLUDING = re.compile(
    r"\b("
    r"(?:poland|polish|romania|romanian|india|indian|germany|german|france|french|spain|spanish|"
    r"portugal|brazil|brazilian|ukraine|ukrainian|philippines|pakistan|nigeria|canada|canadian|"
    r"australia|australian|uk|united kingdom|eu|european union|emea|latam|apac)"
    r"\s+(?:residents?|citizens?|nationals?)\s+only"
    r"|residents?\s+of\s+(?:poland|romania|india|germany|france|spain|brazil|ukraine|the\s+eu|europe)\s+only"
    r"|must\s+(?:be\s+)?(?:based|located|resident)\s+in\s+(?:poland|romania|india|germany|france|spain|"
    r"brazil|ukraine|the\s+eu|europe|the\s+uk|australia|canada)"
    r"|(?:eu|uk|emea|apac|latam)[- ]only"
    r"|only\s+(?:accepting|hiring)\s+(?:candidates\s+)?(?:from|in)\s+(?:the\s+)?(?:eu|uk|europe|india|poland)"
    r")\b",
    re.I,
)

# A client asking not to receive AI-written APPLICATIONS is not the same as a client prohibiting
# AI in the WORK. Found live: a defense contractor who reads every application personally and asks
# for no LLM-generated text, but has no stated objection to AI in the engineering. Rejecting that
# job outright threw away a legitimate opportunity; the right response is to write the proposal by
# hand.
_AI_PROPOSAL_ONLY = re.compile(
    r"\b("
    r"(?:don'?t|do not|please don'?t)\s+send\s+(?:over\s+)?(?:walls?\s+of\s+)?(?:llm|ai)[- ]generated"
    r"|no\s+(?:llm|ai)[- ]generated\s+(?:applications?|cover\s+letters?|emails?|text)"
    r"|i'?ll?\s+junk\s+anything\s+that\s+smells\s+of\s+ai"
    r"|(?:rather|prefer)\s+(?:to\s+)?(?:be\s+)?communicat\w*\s+with\s+humans"
    r"|written\s+by\s+(?:you|a\s+human),?\s+not\s+(?:an?\s+)?(?:ai|llm|chatgpt)"
    r")\b",
    re.I,
)

# The prohibition has to be aimed at the freelancer or the deliverable. A client describing
# their own process ("I do not use AI to screen applications") is not prohibiting anything.
_AI_PROHIBITED = re.compile(
    r"("
    r"\bno\s+ai\b(?!\s*(?:detection|screen))"
    r"|\bno\s+(?:chatgpt|llm|gpt)\b"
    r"|\bno\s+(?:ai|llm|chatgpt)[- ]generated\b"
    r"|\bmust\s+be\s+(?:100%\s+)?human[- ]written\b"
    r"|\bhuman[- ]written\s+only\b"
    r"|\b(?:human|hand)[- ]?(?:written|crafted)\s+content\s+only\b"
    r"|\b(?:ai|ai[- ]generated)\s+(?:content|work|submissions?|writing)\s+will\s+be\s+rejected\b"
    r"|\bwe\s+run\s+(?:every\s+\w+\s+through\s+)?ai\s+detection\b"
    r"|\bai\s+detection\s+(?:is\s+)?(?:used|run|applied)\b"
    r"|\byou\s+(?:must|may|should)\s+not\s+use\s+(?:ai|llms?|chatgpt)\b"
    r"|\b(?:do\s+not|don'?t)\s+use\s+(?:ai|llms?|chatgpt)\s+(?:for|on|in|to\s+(?:write|produce|complete))\b"
    r"|\bwithout\s+(?:the\s+use\s+of\s+)?(?:ai|llms?)\b"
    r"|\bstrictly\s+no\s+ai\b"
    r")",
    re.I,
)

RISK_PATTERNS: list[tuple[RiskFlag, list[str]]] = [
    # AI_PROHIBITED is handled by a dedicated regex below, not by substring matching. Two live
    # failures forced that: the bare substring "no ai" matches "no aircraft", and "do not use ai"
    # matched a client writing "I do not use AI to screen your applications" - the client
    # describing their OWN process, which is the opposite of a prohibition on us.
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
    RiskFlag.GEO_EXCLUDED: "Restricted to residents of a country you are not in.",
    RiskFlag.PROMPT_INJECTION_ATTEMPT: (
        "The listing contains text attempting to issue instructions to this system. That is "
        "an attack, not a client. Rejected and surfaced for review."
    ),
}

# Applied as point deductions rather than rejection.
PENALTY_POINTS = {
    RiskFlag.PROPRIETARY_SOFTWARE_REQUIRED: 20.0,
    RiskFlag.UNCLEAR_DELIVERABLES: 10.0,
    RiskFlag.UNREALISTIC_DEADLINE: 12.0,
    RiskFlag.POOR_CLIENT_HISTORY: 15.0,
    RiskFlag.SECURITY_SENSITIVE: 12.0,
    # Not a reject: the job is fine, the AI-written proposal is not. Write this one by hand.
    RiskFlag.AI_PROPOSAL_DISCOURAGED: 6.0,
    # Deliberately the largest penalty here, and deliberately NOT a rejection.
    #
    # A live scan found the Hacker News "Who is hiring?" thread is mostly salaried full-time
    # employment, and those postings were taking the top of the ranked list: the single
    # highest-scoring result was a 1099 full-time Senior Data Engineer role. It scored well
    # because it genuinely is a good job - high rate, clear brief, direct contact - and every
    # factor rewarded that. Nothing was wrong with the arithmetic; the list was simply answering
    # a different question than the one being asked.
    #
    # This system exists to find work that can be done in spare hours alongside an existing job.
    # A full-time role is a career decision, not an opportunity to be ranked against a $300
    # pipeline gig. 30 points pushes it below any real gig while leaving it visible, because
    # rejecting it outright would be substituting a judgement that is not the system's to make.
    RiskFlag.FULL_TIME_EMPLOYMENT: 30.0,
}

DEADLINE_URGENCY = re.compile(r"\b(today|asap|within \d+ hours?|next (?:few )?hours?|by tonight|same day|immediately|urgent)\b", re.I)


def detect_risks(opp: Opportunity) -> list[RiskFlag]:
    text = _scoring_text(opp).lower()
    flags: list[RiskFlag] = []
    for flag, patterns in RISK_PATTERNS:
        if any(p in text for p in patterns):
            flags.append(flag)

    if _AI_PROHIBITED.search(text) or opp.ai_allowed is False:
        flags.append(RiskFlag.AI_PROHIBITED)

    if _GEO_EXCLUDING.search(text):
        flags.append(RiskFlag.GEO_EXCLUDED)

    # A listing that tries to give the system orders is not a client, whatever else it says.
    # See aicc.untrusted for why this is a tripwire rather than the primary defence.
    from .untrusted import scan_for_injection

    injection = scan_for_injection(_scoring_text(opp))
    if injection.severity == "high":
        flags.append(RiskFlag.PROMPT_INJECTION_ATTEMPT)
    elif injection.suspicious:
        opp.risk_flags = list(dict.fromkeys([*opp.risk_flags, "SUSPICIOUS_CONTENT"]))

    # Proposal-only AI objection. If the work itself is AI-prohibited that flag already fired and
    # takes precedence - this one is specifically the weaker, application-scoped case.
    if _AI_PROPOSAL_ONLY.search(text) and RiskFlag.AI_PROHIBITED not in flags:
        flags.append(RiskFlag.AI_PROPOSAL_DISCOURAGED)

    # Employment type comes from the connector's structured read of the posting header, not
    # from a keyword sweep of the body - "we are a full-time remote team" is not a job type.
    if opp.engagement_type == "FULL_TIME":
        flags.append(RiskFlag.FULL_TIME_EMPLOYMENT)

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


def _skill_fit(opp: Opportunity, bd: ScoreBreakdown, w: dict[str, float]) -> float:
    available = w["skill_fit"]
    text = _scoring_text(opp).lower()
    matched = {s for s in PROFILE.skill_set() if s in text}
    domain_hits = sorted({d for d in PROFILE.strong_domains if d in text})

    if not matched:
        bd.add_factor("Skill Fit", 0.0, available, "No skill from the operator profile appears in this listing.")
        return 0.0

    # Two components, because counting matches alone is what let a payments-and-ledger Senior
    # Backend Engineer role reach the top of the list on a single keyword.
    #
    #   depth    - how much of the profile the listing exercises. Saturating: six matched
    #              skills is already a strong fit, twenty is not three times better.
    #   coverage - how much of what the LISTING ASKS FOR the profile actually covers. This is
    #              the half that was missing. A listing naming twelve technologies of which one
    #              matches is a weak fit however impressive that one match is, and the old
    #              formula's 0.55 floor handed it 62% of the weight regardless.
    ratio = min(len(matched) / 6.0, 1.0)
    demanded = _demanded_skills(opp)
    if len(demanded) >= 3:
        coverage = len(matched & demanded) / len(demanded)
        strength = 0.45 * ratio + 0.55 * min(coverage * 1.4, 1.0)
    else:
        # Too few stated skills to measure coverage against, so fall back to depth alone rather
        # than inventing a denominator. The floor here is deliberately higher than the coverage
        # branch's: a listing that names two skills is not thereby a worse fit than one that
        # names ten, and an earlier version of this change penalised exactly that - it pushed a
        # genuinely well-matched spreadsheet-consolidation job below the STRONG threshold
        # because the client had described the work in prose instead of listing technologies.
        coverage = None
        strength = 0.45 + 0.55 * ratio

    awarded = available * (0.20 + 0.80 * strength)
    if domain_hits:
        awarded = min(available, awarded + 2.0)

    # The signal a human uses reading a job ad: "they want five specific things and I can
    # evidence one of them." Coverage cannot see this, because a listing that states its
    # requirements in prose ("must have shipped a double-entry ledger in production") names no
    # tags to count. That is exactly how a payments-and-ledger role kept reaching rank 2 on a
    # single keyword match.
    # Tidied here as well as in the proposal: the same phrase is read by a human on the
    # approval card, and "you asked for shipped: a double-entry ledger" reads as sloppiness
    # wherever it appears.
    from .proposals import _tidy_requirement

    unmet = [t for t in (_tidy_requirement(u) for u in _unmet_hard_requirements(opp)) if t]
    if len(unmet) >= 2:
        # Scaled, not fixed: four unmet requirements is a worse fit than two. Capped at half
        # the factor, because a requirements list is a wish list and some of it is negotiable.
        penalty = min(available * 0.5, available * 0.14 * len(unmet))
        awarded = max(0.0, awarded - penalty)
        bd.add_penalty(
            "Unmet stated requirements",
            round(penalty, 1),
            f"The listing names {len(unmet)} hard requirement(s) with nothing in the operator profile behind them: "
            + "; ".join(f'"{u[:70]}"' for u in unmet[:3])
            + ".",
        )

    evidence = f"Matched {len(matched)} profile skills: {', '.join(sorted(matched)[:8])}"
    if len(matched) > 8:
        evidence += f" (+{len(matched) - 8} more)"
    if coverage is not None:
        evidence += f". Covers {coverage * 100:.0f}% of the {len(demanded)} skills this listing names"
        missing = sorted(demanded - matched)[:4]
        if missing:
            evidence += f" (missing: {', '.join(missing)})"
    if domain_hits:
        evidence += f". Domain overlap: {', '.join(domain_hits)}"
    bd.add_factor("Skill Fit", awarded, available, evidence + ".")
    return awarded


def _demanded_skills(opp: Opportunity) -> set[str]:
    """The technical skills a listing actually asks for, with tag noise removed.

    Two kinds of noise make a raw skill list a bad denominator:

    * **SEO tag variants.** A Himalayas listing for "Financial Systems Expert" carries thirteen
      tags - financial-systems-consultant, financial-systems-advisor, finance-systems-specialist
      and ten more - which are one concept repeated for search, not thirteen requirements.
      Counting them would make every tagged listing look impossible to satisfy.
    * **Non-skills.** "remote" is a working arrangement, not a competency.

    So only terms in the shared vocabulary count, and near-duplicates collapse to one.
    """
    from .connectors.base import SKILL_VOCAB

    vocab = {v.lower() for v in SKILL_VOCAB}
    out: set[str] = set()
    seen_stems: set[str] = set()
    for raw in opp.skills:
        skill = raw.strip().lower()
        if skill in _NOT_A_SKILL or skill not in vocab:
            continue
        stem = re.sub(r"[^a-z]", "", skill)[:10]
        if stem in seen_stems:
            continue
        seen_stems.add(stem)
        out.add(skill)
    return out


# Explicit, hard requirements on the candidate - the things a listing says you must ALREADY
# have. Distinct from a skills list, and far more binding: "nice to have: Rust" is an
# invitation, "must have shipped a double-entry ledger in production" is a gate.
_HARD_REQUIREMENT = re.compile(
    r"(?:must have|have shipped|you have built|you['’]ve built|required experience|"
    r"we require|you must be|\d+\+?\s*years?(?:\s+of)?)\s*[:\-]?\s*([^.;\n]{12,140})",
    re.I,
)


def _unmet_hard_requirements(opp: Opportunity) -> list[str]:
    """Stated requirements with nothing in the operator profile behind them.

    Deliberately conservative in both directions. A requirement counts as MET if any profile
    skill, strong domain, or demonstrated-capability word appears in it - a generous test,
    because over-rejecting costs real opportunities. And "nice to have" phrasing is excluded
    entirely, since an optional extra is not a gate.
    """
    body = (getattr(opp, "full_description", None) or opp.description or "").lower()
    vocabulary = PROFILE.skill_set() | {d.lower() for d in PROFILE.strong_domains}
    for claim in PROFILE.demonstrated:
        vocabulary |= {w for w in claim.lower().split() if len(w) > 3}

    unmet: list[str] = []
    seen: set[str] = set()
    for m in _HARD_REQUIREMENT.finditer(body):
        chunk = m.group(1).strip(" ,:-")
        # A requirements sentence is nearly always a list: "must have shipped X, Y, and Z" is
        # three gates, not one. Counting the whole sentence as a single requirement was enough
        # on its own to keep a four-requirement listing below the penalty threshold.
        for phrase in re.split(r",\s*(?:and\s+)?|\s+and\s+(?=[a-z])", chunk):
            phrase = phrase.strip(" ,:-")
            if len(phrase) < 10 or "nice to have" in phrase or "bonus" in phrase:
                continue
            key = phrase[:40]
            if key in seen:
                continue
            seen.add(key)
            if not any(term in phrase for term in vocabulary):
                unmet.append(phrase)
    return unmet[:6]


# Working arrangements and locations, not competencies. Counting them as demands both inflates
# the denominator and rewards matching them, neither of which says anything about fit.
_NOT_A_SKILL = {"remote", "hybrid", "onsite", "on-site", "contract", "full-time", "part-time", "freelance"}


def _automation_potential(econ: Economics, bd: ScoreBreakdown, w: dict[str, float]) -> float:
    available = w["automation_potential"]
    pct = automation_pct(econ)
    awarded = available * (pct / 100.0)
    bd.add_factor(
        "Automation Potential",
        awarded,
        available,
        f"{pct}% of estimated effort is AI-carryable ({econ.estimated_ai_hours:.1f}h AI / {econ.estimated_human_hours:.1f}h human).",
    )
    return awarded


def _profitability(econ: Economics, bd: ScoreBreakdown, w: dict[str, float]) -> float:
    available = w["expected_profitability"]
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
    "python_jobs": (
        0.70,
        "Small, on-stack board; applying is a direct link with far fewer applicants than an aggregator.",
    ),
    "freelancer_com": (
        0.20,
        "Bidding marketplace with heavy competition - 200+ bids observed on popular projects and an "
        "hourly long tail at $2-8. Listings are pre-filtered to under 25 existing bids, but a new "
        "account with no platform history still starts behind.",
    ),
    "contra": (0.60, "Commission-free and lower volume than Upwork, but still a marketplace."),
    "himalayas": (0.45, "Aggregated listing; applicant volume unknown and often high."),
    "remoteok": (0.45, "Aggregated listing; applicant volume unknown and often high."),
    "weworkremotely": (0.45, "Aggregated listing; applicant volume unknown and often high."),
    "upwork": (
        0.25,
        "New account with no Job Success Score competes against established freelancers; clients filter on JSS before reading proposals.",
    ),
    "fiverr": (0.30, "Inbound only; ranking depends on gig history this account does not yet have."),
    "demo": (
        0.75,
        "Synthetic. The demo listings are written as direct-client scenarios (a named company "
        "briefing a specific piece of work), not marketplace bids, so they carry a direct-client "
        "prior rather than a marketplace one.",
    ),
}


def _likelihood_of_winning(opp: Opportunity, econ: Economics, bd: ScoreBreakdown, w: dict[str, float]) -> float:
    available = w["likelihood_of_winning"]
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


def _clarity(opp: Opportunity, bd: ScoreBreakdown, w: dict[str, float]) -> float:
    available = w["clarity_of_requirements"]
    desc = opp.description or ""
    notes: list[str] = []

    # Components are expressed as FRACTIONS of the available points, not fixed numbers. The
    # per-class weighting changes `available`, and a hardcoded 4 + 2 + 2 + 2 quietly exceeded it
    # once clarity was up-weighted for small jobs.
    earned = 0.0
    if len(desc) >= 800:
        earned += 0.40
        notes.append("detailed description (>=800 chars)")
    elif len(desc) >= 300:
        earned += 0.25
        notes.append("moderate description (>=300 chars)")
    else:
        notes.append("thin description")

    if opp.skills:
        earned += 0.20
        notes.append(f"{len(opp.skills)} skills specified")
    if opp.budget_min is not None or opp.budget_max is not None:
        earned += 0.20
        notes.append("budget stated")
    if re.search(r"\b(deliverable|acceptance|requirement|scope|milestone)s?\b", desc, re.I):
        earned += 0.20
        notes.append("deliverables or scope named")

    awarded = min(available, available * earned)
    bd.add_factor("Clarity of Requirements", awarded, available, "; ".join(notes).capitalize() + ".")
    return awarded


def _risk_factor(flags: list[RiskFlag], bd: ScoreBreakdown, w: dict[str, float]) -> float:
    available = w["risk"]
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
    # Per-class weights: a $60 spreadsheet job and a $160/hour senior contract are not the same
    # business and must not be scored on the same assumptions. See aicc.classes.
    cls_ = classify_class(opp)
    opp.opportunity_class = cls_.value
    w = weights_for(cls_, WEIGHTS)
    bd.weights_used = dict(w)
    bd.opportunity_class = CLASS_LABEL[cls_]

    total = 0.0
    total += _skill_fit(opp, bd, w)
    total += _automation_potential(econ, bd, w)
    total += _profitability(econ, bd, w)
    total += _likelihood_of_winning(opp, econ, bd, w)
    total += _clarity(opp, bd, w)
    total += _risk_factor(flags, bd, w)
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
    opp.match_score = round(bd.factors.get("Skill Fit", {}).get("awarded", 0.0) / max(w["skill_fit"], 0.01) * 100, 1)
    opp.profit_score = round(
        bd.factors.get("Expected Profitability", {}).get("awarded", 0.0) / max(w["expected_profitability"], 0.01) * 100, 1
    )
    opp.competition_score = round(
        bd.factors.get("Likelihood of Winning", {}).get("awarded", 0.0) / max(w["likelihood_of_winning"], 0.01) * 100, 1
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

    prior, _reason = SOURCE_WIN_PRIOR.get(opp.source, (0.40, "Unknown source."))
    opp.win_estimate = estimate_win(opp, source_prior=prior, class_=cls_).to_dict()

    opp.score_breakdown = bd.to_dict()
    return bd


def _penalty_evidence(flag: RiskFlag) -> str:
    return {
        RiskFlag.PROPRIETARY_SOFTWARE_REQUIRED: "Listing requires licensed software that is not owned.",
        RiskFlag.UNCLEAR_DELIVERABLES: "Description is too thin to know what 'done' means.",
        RiskFlag.UNREALISTIC_DEADLINE: "Urgency language paired with an estimate over four hours.",
        RiskFlag.POOR_CLIENT_HISTORY: "Client history on the source platform is poor.",
        RiskFlag.SECURITY_SENSITIVE: "Touches production systems or regulated data.",
        RiskFlag.AI_PROPOSAL_DISCOURAGED: (
            "Client asked for applications written by a human. The job is fine - write this "
            "proposal yourself rather than sending a generated one."
        ),
        RiskFlag.FULL_TIME_EMPLOYMENT: (
            "This is a salaried full-time position, not freelance work. Heavily downranked "
            "rather than rejected: it may be a good job, but it is a career decision, and it "
            "cannot be done in spare hours alongside one."
        ),
    }.get(flag, flag.value)


def score_all(opps: list[Opportunity]) -> list[Opportunity]:
    for opp in opps:
        score_opportunity(opp)
    return sorted(opps, key=lambda o: o.score, reverse=True)


__all__ = ["score_opportunity", "score_all", "classify", "detect_risks", "band", "WEIGHTS", "BudgetType"]
