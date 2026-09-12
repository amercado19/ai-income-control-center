"""What one opportunity is actually worth, in the several different currencies that matter.

``money.py`` answers "how much cash does this job leave behind". This module answers the harder
question the scheduler needs: *given that we can only do some of the available work, what is this
one worth relative to the others* - which is not the same number, and is not even a single number.

The trap this module exists to avoid is ranking by one ratio. Profit per minute looks like the
obvious answer and is wrong in a specific, expensive way: four $15 jobs at five minutes each beat
a $500 job at sixty minutes on profit-per-minute ($3.00/min against $8.33/min - no wait, they
lose, and *that asymmetry is the point*). Flip the numbers slightly and the ratio flips, and a
system ranking on it would drop a $500 job to chase $60 of small work that happened to score a
fraction higher. Every single-metric ranking has a version of this failure. So every opportunity
carries four denominators - cash, Claude capacity, elapsed time, and Andres's own minutes - and
the scheduler in ``scheduler.py`` optimises the portfolio rather than sorting on any of them.

The second thing this module refuses to do is flatter a job. ``HIGH VALUE / LOW EFFORT`` is the
most consequential label here, because it sends work to the front of the queue. It is therefore
granted only on evidence present in the listing - a named category this stack genuinely automates,
plus a real price, plus no complexity signals - and the reasons are attached to the label so a
wrong one can be argued with. Quietly shading an effort estimate downward to make a job look
attractive would corrupt every downstream number at once, and would do it invisibly.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from . import money
from .models import BudgetType, Opportunity

# ---------------------------------------------------------------------------
# Labels
# ---------------------------------------------------------------------------


class ValueClass(StrEnum):
    HIGH_VALUE_LOW_EFFORT = "HIGH VALUE / LOW EFFORT"
    HIGH_VALUE = "HIGH VALUE"
    QUICK_WIN = "QUICK WIN"
    STANDARD = "STANDARD"
    MARGINAL = "MARGINAL"
    UNPROFITABLE = "UNPROFITABLE"


class RiskLevel(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    UNKNOWN = "UNKNOWN"


#: A job is a "quick win" when it is genuinely small. Fifteen minutes of AI work, not "smallish".
QUICK_WIN_AI_MINUTES = 15.0

#: Dollar floor for HIGH VALUE. Below this, even easy work is not worth reordering the queue for.
HIGH_VALUE_FLOOR = 200.0

#: The implied hourly a job must clear on AI time before it counts as low-effort-for-the-money.
#: Set against the Fiverr kit's own $76-88/h validated gigs, so the bar is the business's real
#: alternative use of the same hour rather than an aspiration.
HVLE_IMPLIED_HOURLY = 150.0

#: Categories this stack has real, reusable machinery for. Membership here is what makes an
#: effort estimate low *on evidence* rather than on optimism. Adding a category to this list is
#: a claim that the templates exist; `templates.py` reuse counters are what keep it honest.
AUTOMATABLE_CATEGORIES = frozenset(
    {
        "spreadsheet",
        "data_cleaning",
        "pdf_extraction",
        "scripting",
        "automation",
        "reporting",
        "dashboard",
        "api_integration",
        "documentation",
        "qa_review",
    }
)

#: Phrases that mean the work is bigger than its category suggests. Any of these blocks the
#: HIGH VALUE / LOW EFFORT label outright - the label's whole value is that it is hard to earn.
_COMPLEXITY = re.compile(
    r"\b(bespoke|from scratch|greenfield|architect|architecture review|migrate\s+\d+|legacy system|"
    r"undocumented|reverse[- ]engineer|proprietary format|on[- ]?prem|air[- ]?gapped|hipaa|soc\s?2|"
    r"pci|regulatory|compliance audit|multi[- ]tenant|real[- ]?time|low[- ]latency|distributed|"
    r"kubernetes|terraform|machine learning model|train a model|fine[- ]?tune)\b",
    re.I,
)

#: Phrases that mean the client will need more of Andres than of Claude.
_HUMAN_HEAVY = re.compile(
    r"\b(daily stand[- ]?up|weekly call|attend meetings|on[- ]?call|interview|workshop|training "
    r"session|present to|stakeholder management|client-facing)\b",
    re.I,
)

#: Signals of repeat business. Worth real weight: the second job from a client costs almost no
#: acquisition effort, which is the single largest hidden cost in this business.
_REPEAT = re.compile(
    r"\b(ongoing|recurring|monthly|weekly basis|long[- ]term|retainer|more work|additional "
    r"projects|first of several|pipeline of work|regular basis)\b",
    re.I,
)


# ---------------------------------------------------------------------------
# The record
# ---------------------------------------------------------------------------


@dataclass
class ProfitProfile:
    """Every economic number the scheduler and dashboard need for one opportunity.

    Deliberately verbose. A compact record would force the scheduler to recompute derived values
    and force the dashboard to trust them, and the two would drift.
    """

    opportunity_id: str = ""
    title: str = ""
    source: str = ""
    category: str = ""

    # revenue
    expected_gross_revenue: float = 0.0
    expected_fees: float = 0.0
    expected_net_revenue: float = 0.0

    # probability
    win_probability: float = 0.0
    win_probability_basis: str = ""
    expected_value: float = 0.0

    # effort
    estimated_claude_minutes: float = 0.0
    estimated_qa_minutes: float = 0.0
    estimated_revision_minutes: float = 0.0
    total_claude_minutes: float = 0.0
    andres_active_minutes: float = 0.0
    estimated_completion_hours: float = 0.0

    # timing
    deadline: str = ""
    deadline_slack_hours: float | None = None
    time_to_cash_days: float = 0.0

    # risk and upside
    delivery_confidence: float = 0.0
    revision_risk: str = RiskLevel.UNKNOWN.value
    repeat_client_potential: str = RiskLevel.UNKNOWN.value
    template_reuse_potential: str = RiskLevel.UNKNOWN.value

    # the four denominators
    expected_net_profit: float = 0.0
    profit_per_claude_minute: float = 0.0
    profit_per_elapsed_hour: float = 0.0
    profit_per_andres_minute: float = 0.0

    # labels
    value_class: str = ValueClass.STANDARD.value
    value_reasons: list[str] = field(default_factory=list)
    estimate_confidence: str = "ESTIMATED"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def is_profitable(self) -> bool:
        return self.expected_net_profit > 0 and self.value_class != ValueClass.UNPROFITABLE.value

    def headline(self) -> str:
        return (
            f"${self.expected_gross_revenue:,.0f} · {self.total_claude_minutes:.0f} min AI · "
            f"EV ${self.expected_value:,.0f} · {self.value_class}"
        )


# ---------------------------------------------------------------------------
# Building one
# ---------------------------------------------------------------------------

#: QA and revision are ratios of the worker estimate, matching `capacity.reserve`. Kept as named
#: constants in one place so the scheduler cannot plan against one set of assumptions while the
#: capacity ledger reserves against another - which would look correct in both files and be wrong.
QA_RATIO = 0.35
REVISION_RATIO = 0.50


def build(opp: Opportunity, *, win_probability: float | None = None) -> ProfitProfile:
    """Derive the full economic picture for one listing."""
    # `category` is written by `scoring.score_opportunity`, so an unscored listing arrives here
    # with an empty one and every category-dependent judgement below silently degrades to
    # "generic". That made HIGH VALUE / LOW EFFORT effectively unreachable - the label existed,
    # the code was correct, and nothing could ever earn it, which is the worst kind of dead
    # branch because it looks like a working feature. Resolve it here rather than depending on
    # the caller having scored first.
    if not opp.category:
        from . import scoring

        opp.category = scoring.classify(opp)

    econ = money.compute(opp)

    gross = econ.client_price
    fees = econ.platform_fee + econ.payment_fee
    net = max(0.0, gross - fees)

    worker_minutes = max(0.0, econ.estimated_ai_hours * 60.0)
    qa_minutes = worker_minutes * QA_RATIO
    revision_minutes = worker_minutes * REVISION_RATIO * max(1.0, econ.expected_revisions or 1.0)
    total_minutes = worker_minutes + qa_minutes + revision_minutes

    andres_minutes = max(money.MIN_HUMAN_MINUTES, econ.estimated_human_hours * 60.0)

    p = _win_probability(opp) if win_probability is None else max(0.0, min(1.0, win_probability))
    basis = _win_basis(opp) if win_probability is None else "Supplied by the caller."

    profit = max(0.0, net - econ.ai_cash_cost - econ.infrastructure_cost - econ.other_cost)
    ev = round(p * profit, 2)

    slack = _deadline_slack_hours(opp)
    text = f"{opp.title}\n{opp.description}"

    prof = ProfitProfile(
        opportunity_id=opp.id,
        title=opp.title,
        source=opp.source,
        category=opp.category or "generic",
        expected_gross_revenue=round(gross, 2),
        expected_fees=round(fees, 2),
        expected_net_revenue=round(net, 2),
        win_probability=round(p, 3),
        win_probability_basis=basis,
        expected_value=ev,
        estimated_claude_minutes=round(worker_minutes, 1),
        estimated_qa_minutes=round(qa_minutes, 1),
        estimated_revision_minutes=round(revision_minutes, 1),
        total_claude_minutes=round(total_minutes, 1),
        andres_active_minutes=round(andres_minutes, 1),
        estimated_completion_hours=round(econ.estimated_hours, 2),
        deadline=opp.deadline,
        deadline_slack_hours=slack,
        time_to_cash_days=_time_to_cash_days(opp, econ),
        # `money.Economics.confidence` is a 0-100 score. Everything here and on the dashboard
        # treats confidence as a fraction and formats it with `:.0%`, so converting at the
        # boundary is the difference between "65%" and "6500% delivery confidence" - which is
        # what this rendered before, and which no reader would have believed for a second.
        delivery_confidence=round(econ.confidence / 100.0, 3),
        revision_risk=_revision_risk(opp, econ),
        repeat_client_potential=_repeat_potential(text),
        template_reuse_potential=_template_potential(opp),
        expected_net_profit=round(profit, 2),
        estimate_confidence="ESTIMATED" if "prior" in (econ.method or "").lower() or not econ.method else "MEASURED",
    )

    # The four denominators. Guarded: a zero denominator means "not applicable", never infinity,
    # and an infinity here would silently win every ranking it appeared in.
    prof.profit_per_claude_minute = round(profit / total_minutes, 3) if total_minutes > 0 else 0.0
    prof.profit_per_elapsed_hour = round(profit / econ.estimated_hours, 2) if econ.estimated_hours > 0 else 0.0
    prof.profit_per_andres_minute = round(profit / andres_minutes, 3) if andres_minutes > 0 else 0.0

    prof.value_class, prof.value_reasons = classify_value(prof, opp)
    return prof


def classify_value(prof: ProfitProfile, opp: Opportunity) -> tuple[str, list[str]]:
    """Assign the value class, and say why.

    Order matters. HIGH VALUE / LOW EFFORT is checked first and is the hardest to earn; QUICK WIN
    is checked before HIGH VALUE so that a small job is never mislabelled as a large one on price
    alone. Every branch returns reasons, because a label that sends work to the front of the queue
    should be arguable.
    """
    text = f"{opp.title}\n{opp.description}"
    reasons: list[str] = []

    if prof.expected_net_profit <= 0:
        return ValueClass.UNPROFITABLE.value, ["Expected net profit is zero or negative after fees."]

    hours = prof.total_claude_minutes / 60.0
    implied_hourly = prof.expected_net_profit / hours if hours > 0 else 0.0

    complexity = sorted({m.group(0).lower() for m in _COMPLEXITY.finditer(text)})
    human_heavy = sorted({m.group(0).lower() for m in _HUMAN_HEAVY.finditer(text)})

    # ---- HIGH VALUE / LOW EFFORT: four independent conditions, all required.
    hvle: list[str] = []
    if prof.category in AUTOMATABLE_CATEGORIES:
        hvle.append(f"'{prof.category}' is a category this stack already automates end to end.")
    if prof.expected_gross_revenue >= HIGH_VALUE_FLOOR:
        hvle.append(f"${prof.expected_gross_revenue:,.0f} is a real price, not a token one.")
    if implied_hourly >= HVLE_IMPLIED_HOURLY:
        hvle.append(f"${implied_hourly:,.0f}/h implied against AI time, well above the ${HVLE_IMPLIED_HOURLY:,.0f}/h bar.")
    if prof.delivery_confidence >= 0.6:
        hvle.append(f"Delivery confidence {prof.delivery_confidence:.0%} - the requirements are legible.")

    if len(hvle) == 4 and not complexity and not human_heavy:
        return ValueClass.HIGH_VALUE_LOW_EFFORT.value, hvle

    if complexity and prof.category in AUTOMATABLE_CATEGORIES and prof.expected_gross_revenue >= HIGH_VALUE_FLOOR:
        reasons.append(
            f"Not labelled low-effort: the posting names {', '.join(complexity[:3])}, which is more work than the category alone implies."
        )
    if human_heavy:
        reasons.append(f"Needs Andres in person: {', '.join(human_heavy[:2])}.")

    # ---- QUICK WIN before HIGH VALUE, so a small job is never dressed up as a large one.
    if prof.total_claude_minutes <= QUICK_WIN_AI_MINUTES and prof.expected_net_profit > 0:
        reasons.insert(0, f"{prof.total_claude_minutes:.0f} minutes of AI work including QA - fills a gap in the schedule.")
        return ValueClass.QUICK_WIN.value, reasons

    if prof.expected_gross_revenue >= HIGH_VALUE_FLOOR and prof.delivery_confidence >= 0.5:
        reasons.insert(0, f"${prof.expected_gross_revenue:,.0f} at {prof.delivery_confidence:.0%} delivery confidence.")
        return ValueClass.HIGH_VALUE.value, reasons

    if implied_hourly < 25.0:
        reasons.insert(0, f"${implied_hourly:,.0f}/h implied - below anything worth the capacity.")
        return ValueClass.MARGINAL.value, reasons

    reasons.insert(0, f"${prof.expected_net_profit:,.0f} expected profit at ${implied_hourly:,.0f}/h implied.")
    return ValueClass.STANDARD.value, reasons


# ---------------------------------------------------------------------------
# The individual estimates
# ---------------------------------------------------------------------------


def _win_probability(opp: Opportunity) -> float:
    """An initial heuristic. Labelled as one everywhere it is shown.

    Reuses the score the system already computed rather than inventing a second opinion, then
    discounts for the two things that actually decide freelance bids and are not in the score:
    how many other people are bidding, and whether the client has said anything at all about
    budget. Calibration against real outcomes replaces this once five decided outcomes exist.
    """
    if opp.win_estimate and isinstance(opp.win_estimate, dict):
        p = opp.win_estimate.get("probability")
        if isinstance(p, int | float) and 0 <= p <= 1:
            return float(p)
    base = max(0.02, min(0.45, (opp.score or 0.0) / 250.0))
    if opp.budget_type == BudgetType.UNKNOWN.value:
        base *= 0.7
    if opp.competition_score and opp.competition_score < 40:
        base *= 0.6
    return round(base, 3)


def _win_basis(opp: Opportunity) -> str:
    if opp.win_estimate and isinstance(opp.win_estimate, dict) and opp.win_estimate.get("probability") is not None:
        return str(opp.win_estimate.get("basis") or "INITIAL HEURISTIC — NOT YET CALIBRATED.")
    return (
        "INITIAL HEURISTIC — NOT YET CALIBRATED. Derived from the match score, discounted for "
        "unstated budget and for competition. No real outcomes have been recorded yet, so this "
        "is a prior, not a measurement."
    )


def _deadline_slack_hours(opp: Opportunity, now: datetime | None = None) -> float | None:
    """Hours between now and the stated deadline. None when the client stated none."""
    if not opp.deadline:
        return None
    try:
        dt = datetime.fromisoformat(str(opp.deadline).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return round((dt - (now or datetime.now(UTC))).total_seconds() / 3600.0, 1)


def _time_to_cash_days(opp: Opportunity, econ: money.Economics) -> float:
    """Delivery time plus the platform's clearing period.

    Not a tiebreaker dressed up as a metric: a pre-revenue business genuinely prefers money that
    arrives. But it is one factor among several, because preferring fast money unconditionally is
    how you take four $15 jobs over a $500 one.
    """
    clearing = {
        "fiverr": 14.0,  # 7-day clearance for new sellers, plus order completion
        "upwork": 5.0,
        "freelancer": 5.0,
        "contra": 3.0,
    }.get((opp.source or "").lower(), 7.0)
    delivery_days = max(0.5, econ.estimated_hours / 6.0)
    return round(delivery_days + clearing, 1)


def _revision_risk(opp: Opportunity, econ: money.Economics) -> str:
    text = f"{opp.title} {opp.description}".lower()
    vague = sum(1 for w in ("etc", "and more", "similar", "as needed", "flexible", "tbd", "we'll see") if w in text)
    if econ.expected_revisions >= 1.2 or vague >= 2:
        return RiskLevel.HIGH.value
    if econ.expected_revisions >= 0.7 or vague == 1:
        return RiskLevel.MEDIUM.value
    if len(opp.description or "") < 120:
        return RiskLevel.UNKNOWN.value
    return RiskLevel.LOW.value


def _repeat_potential(text: str) -> str:
    hits = sorted({m.group(0).lower() for m in _REPEAT.finditer(text)})
    if len(hits) >= 2:
        return RiskLevel.HIGH.value
    if hits:
        return RiskLevel.MEDIUM.value
    return RiskLevel.LOW.value


def _template_potential(opp: Opportunity) -> str:
    if opp.category in AUTOMATABLE_CATEGORIES:
        return RiskLevel.HIGH.value
    if opp.category:
        return RiskLevel.MEDIUM.value
    return RiskLevel.UNKNOWN.value


# ---------------------------------------------------------------------------
# Aggregates the dashboard shows
# ---------------------------------------------------------------------------


def totals(profiles: list[ProfitProfile]) -> dict[str, Any]:
    """Portfolio-level money, split the way the Profit panel renders it."""
    profitable = [p for p in profiles if p.is_profitable]
    quick = [p for p in profitable if p.value_class == ValueClass.QUICK_WIN.value]
    high = [p for p in profitable if p.value_class in (ValueClass.HIGH_VALUE.value, ValueClass.HIGH_VALUE_LOW_EFFORT.value)]
    return {
        "opportunity_count": len(profiles),
        "profitable_count": len(profitable),
        "total_available_gross": round(sum(p.expected_gross_revenue for p in profitable), 2),
        "total_available_profit": round(sum(p.expected_net_profit for p in profitable), 2),
        "total_expected_value": round(sum(p.expected_value for p in profitable), 2),
        "quick_win_revenue": round(sum(p.expected_gross_revenue for p in quick), 2),
        "high_value_revenue": round(sum(p.expected_gross_revenue for p in high), 2),
        "total_claude_minutes": round(sum(p.total_claude_minutes for p in profitable), 1),
        "total_andres_minutes": round(sum(p.andres_active_minutes for p in profitable), 1),
        "median_time_to_cash_days": _median([p.time_to_cash_days for p in profitable]),
        "note": (
            "Available, not captured. These are expected values on listings nobody has been "
            "awarded. Real revenue is a separate figure and is currently $0.00."
        ),
    }


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    mid = len(s) // 2
    return round(s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) / 2, 1)
