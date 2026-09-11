"""Profitability engine (spec section 13).

Revenue is not profit. This module is the only place that converts a client price into an
expected net, and it exposes the arithmetic rather than hiding it.

Two honesty rules are enforced structurally:

1. **An estimate is labelled an estimate.** Every figure returned carries ``method`` and
   ``inputs`` so the dashboard can show how it was derived. Nothing here is presented as an
   observation.

2. **AI cash cost and AI usage are different numbers.** Running the worker on a Claude
   subscription OAuth token is $0.00 cash. It still consumes subscription allowance. Reporting
   only the $0.00 would make every job look infinitely profitable and would hide the real
   constraint, which is allowance, not dollars.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from .config import PROFILE
from .models import BudgetType, Opportunity

# ---------------------------------------------------------------------------
# Platform economics, verified September 2026. See docs/MARKETPLACE_RULES.md.
# ---------------------------------------------------------------------------

PLATFORM_FEES: dict[str, dict[str, Any]] = {
    "upwork": {
        "rate": 0.10,
        "note": "10% freelancer service fee",
        "source": "https://support.upwork.com/hc/en-us/articles/211062538",
    },
    "fiverr": {
        "rate": 0.20,
        "note": "Seller keeps 80% of the purchase amount",
        "source": "https://help.fiverr.com/hc/en-us/articles/9234443621137-Your-earnings-page",
    },
    "contra": {
        "rate": 0.00,
        "note": "Commission-free for independents",
        "source": "https://contra.com/pricing",
    },
    "hackernews": {"rate": 0.00, "note": "Direct client, no intermediary", "source": "n/a"},
    "himalayas": {"rate": 0.00, "note": "Direct client, no intermediary", "source": "n/a"},
    "remoteok": {"rate": 0.00, "note": "Direct client, no intermediary", "source": "n/a"},
    "weworkremotely": {"rate": 0.00, "note": "Direct client, no intermediary", "source": "n/a"},
    "direct": {"rate": 0.00, "note": "Direct client, no intermediary", "source": "n/a"},
    "demo": {"rate": 0.10, "note": "Synthetic", "source": "demo"},
}

# Payment processing. Phase 1 takes no payments through this system at all, so the modelled
# figure is what a future business account would cost, flagged as not-yet-active.
PAYMENT_FEE_RATE = 0.0299
PAYMENT_FEE_FIXED = 0.49
PAYMENT_PROCESSING_ACTIVE = False  # spec section 19: stays False until Andres activates it

# Infrastructure. Everything in Phase 1 runs on already-owned free resources.
MONTHLY_INFRA_CASH_COST = 0.00

# AI. $0.00 cash on a subscription OAuth token; usage tracked in separate units.
AI_CASH_COST_PER_JOB = 0.00
AI_USAGE_UNITS_PER_HOUR = 1.0  # 1 unit == roughly one hour of agent work

# How much one AI hour "costs" relative to one human hour when ranking opportunities.
# Not a cash figure - a scarcity weight. AI hours consume subscription allowance and wall-clock
# time and carry revision risk, so they are discounted, not free. 0.25 means four AI hours cost
# about as much as one human hour.
AI_HOUR_WEIGHT = 0.25


@dataclass
class Economics:
    """The full money picture for one opportunity or job."""

    client_price: float = 0.0
    platform_fee: float = 0.0
    payment_fee: float = 0.0
    ai_cash_cost: float = 0.0
    infrastructure_cost: float = 0.0
    other_cost: float = 0.0

    expected_revisions: float = 0.0
    estimated_hours: float = 0.0
    estimated_ai_hours: float = 0.0
    estimated_human_hours: float = 0.0

    expected_net_profit: float = 0.0
    expected_profit_per_hour: float = 0.0  # per EFFECTIVE hour - the number to decide on
    expected_profit_per_human_hour: float = 0.0  # per human hour - the leverage number
    effective_hours: float = 0.0
    margin_pct: float = 0.0

    ai_usage_units: float = 0.0

    method: str = ""
    inputs: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0
    confidence_basis: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Effort estimation
# ---------------------------------------------------------------------------

# Hours and automatable share by category. These are PRIORS, not measurements, and they are
# labelled as such everywhere they surface. Once the ledger holds real completed jobs,
# `calibrate_from_ledger` replaces each prior with the observed median.
CATEGORY_PRIORS: dict[str, dict[str, float]] = {
    "spreadsheet": {"hours": 2.5, "automatable": 0.90, "revisions": 0.5},
    "data_cleaning": {"hours": 3.0, "automatable": 0.90, "revisions": 0.5},
    "pdf_extraction": {"hours": 3.0, "automatable": 0.85, "revisions": 0.6},
    "web_research": {"hours": 4.0, "automatable": 0.75, "revisions": 0.8},
    "data_pipeline": {"hours": 8.0, "automatable": 0.75, "revisions": 1.0},
    "api_integration": {"hours": 8.0, "automatable": 0.70, "revisions": 1.0},
    "dashboard": {"hours": 10.0, "automatable": 0.70, "revisions": 1.2},
    "scripting": {"hours": 4.0, "automatable": 0.80, "revisions": 0.7},
    "automation": {"hours": 6.0, "automatable": 0.80, "revisions": 0.8},
    "reporting": {"hours": 4.0, "automatable": 0.80, "revisions": 0.7},
    "financial_model": {"hours": 6.0, "automatable": 0.65, "revisions": 1.0},
    "documentation": {"hours": 3.0, "automatable": 0.85, "revisions": 0.5},
    "qa_review": {"hours": 2.0, "automatable": 0.85, "revisions": 0.3},
    "generic": {"hours": 6.0, "automatable": 0.50, "revisions": 1.0},
}

# Human oversight never goes to zero. Even a fully automated job needs reading the brief,
# checking QA output and sending the delivery.
MIN_HUMAN_MINUTES = 10.0


def estimate_effort(opp: Opportunity) -> tuple[float, float, float, float, str]:
    """Return (total_hours, ai_hours, human_hours, expected_revisions, method)."""
    prior = CATEGORY_PRIORS.get(opp.category, CATEGORY_PRIORS["generic"])
    hours = prior["hours"]
    automatable = prior["automatable"]
    revisions = prior["revisions"]
    basis = [f"category prior '{opp.category or 'generic'}' = {hours}h, {automatable:.0%} automatable"]

    # A stated budget is evidence about scope. Scale the prior toward what the client thinks
    # they are buying, but never below a floor - a $50 job is not 6 minutes of work.
    budget = opp.budget_max or opp.budget_min
    if budget and opp.budget_type == BudgetType.FIXED.value and budget > 0:
        implied = budget / PROFILE.target_hourly
        hours = max(0.5, (hours + implied) / 2)
        basis.append(f"blended with budget-implied {implied:.1f}h (${budget:,.0f} / ${PROFILE.target_hourly:.0f}/h)")

    total = round(hours * (1 + revisions * 0.25), 2)
    ai_hours = round(total * automatable, 2)
    human_hours = round(max(total - ai_hours, MIN_HUMAN_MINUTES / 60.0), 2)
    basis.append(f"revision allowance x{1 + revisions * 0.25:.2f}")
    basis.append(f"human floor {MIN_HUMAN_MINUTES:.0f} min")
    return total, ai_hours, human_hours, revisions, "; ".join(basis)


# ---------------------------------------------------------------------------
# Full economics
# ---------------------------------------------------------------------------


def price_point(opp: Opportunity) -> tuple[float, str]:
    """Pick the number to model against, conservatively.

    Hourly listings are converted using the estimated hours. When only a range is given, the
    midpoint is used - quoting the top of a range you were not offered is how estimates turn
    into disappointments.
    """
    if opp.budget_type == BudgetType.HOURLY.value:
        rate = opp.budget_min or opp.budget_max
        if rate:
            total, _, _, _, _ = estimate_effort(opp)
            return round(rate * total, 2), f"hourly ${rate:,.0f}/h x {total:.1f}h estimated"
        return 0.0, "hourly rate not stated"

    if opp.budget_min is not None and opp.budget_max is not None:
        return round((opp.budget_min + opp.budget_max) / 2, 2), "midpoint of stated range"
    single = opp.budget_max if opp.budget_min is None else opp.budget_min
    if single is not None:
        return float(single), "single stated figure"
    return 0.0, "no budget stated"


def compute(opp: Opportunity) -> Economics:
    """Full profitability estimate for one opportunity."""
    total_h, ai_h, human_h, revisions, effort_method = estimate_effort(opp)
    price, price_method = price_point(opp)

    fee_cfg = PLATFORM_FEES.get(opp.source, {"rate": 0.0, "note": "Unknown source; assuming no fee", "source": "n/a"})
    platform_fee = round(price * float(fee_cfg["rate"]), 2)

    payment_fee = round(price * PAYMENT_FEE_RATE + PAYMENT_FEE_FIXED, 2) if (PAYMENT_PROCESSING_ACTIVE and price > 0) else 0.0

    ai_cash = AI_CASH_COST_PER_JOB
    ai_usage = round(ai_h * AI_USAGE_UNITS_PER_HOUR, 2)

    net = round(price - platform_fee - payment_fee - ai_cash - MONTHLY_INFRA_CASH_COST, 2)

    # Effective hours, not human hours, is the number to decide on.
    #
    # Dividing net profit by human hours alone produces absurdities: a 90%-automatable $500 job
    # with a 30-minute human floor reports "$833/hour", which makes every automatable job look
    # equally perfect and destroys the ranking. AI hours are not free - they consume subscription
    # allowance, they occupy wall-clock time during which nothing else ships, and they carry
    # revision risk. They are cheaper than human hours, not costless, so they are discounted
    # rather than ignored.
    effective_h = round(human_h + AI_HOUR_WEIGHT * ai_h, 2)
    per_effective_hour = round(net / effective_h, 2) if effective_h > 0 else 0.0
    per_human_hour = round(net / human_h, 2) if human_h > 0 else 0.0
    margin = round(100 * net / price, 1) if price > 0 else 0.0

    # Confidence is about how much the estimate rests on stated facts rather than priors.
    confidence, conf_basis = _confidence(opp, price)

    return Economics(
        client_price=price,
        platform_fee=platform_fee,
        payment_fee=payment_fee,
        ai_cash_cost=ai_cash,
        infrastructure_cost=MONTHLY_INFRA_CASH_COST,
        other_cost=0.0,
        expected_revisions=revisions,
        estimated_hours=total_h,
        estimated_ai_hours=ai_h,
        estimated_human_hours=human_h,
        expected_net_profit=net,
        expected_profit_per_hour=per_effective_hour,
        expected_profit_per_human_hour=per_human_hour,
        effective_hours=effective_h,
        margin_pct=margin,
        ai_usage_units=ai_usage,
        method="ESTIMATE (category prior + stated budget). Not an observation.",
        inputs={
            "price_basis": price_method,
            "effort_basis": effort_method,
            "platform_fee_rate": fee_cfg["rate"],
            "platform_fee_note": fee_cfg["note"],
            "platform_fee_source": fee_cfg["source"],
            "payment_processing_active": PAYMENT_PROCESSING_ACTIVE,
            "ai_cash_cost_note": "Claude subscription OAuth token: $0.00 cash. Usage tracked separately.",
            "infra_cost_note": "GitHub Actions + Pages on existing free allowances.",
        },
        confidence=confidence,
        confidence_basis=conf_basis,
    )


def _confidence(opp: Opportunity, price: float) -> tuple[float, str]:
    """How much of this estimate rests on stated facts rather than priors."""
    score = 0.0
    reasons: list[str] = []
    if price > 0:
        score += 40
        reasons.append("budget stated (+40)")
    else:
        reasons.append("no budget stated (+0)")
    if opp.category and opp.category != "generic":
        score += 25
        reasons.append(f"category identified as '{opp.category}' (+25)")
    else:
        reasons.append("category unresolved (+0)")
    desc_len = len(opp.description or "")
    if desc_len >= 600:
        score += 25
        reasons.append("detailed description >=600 chars (+25)")
    elif desc_len >= 200:
        score += 15
        reasons.append("moderate description >=200 chars (+15)")
    else:
        reasons.append("thin description (+0)")
    if opp.skills:
        score += 10
        reasons.append(f"{len(opp.skills)} skills listed (+10)")
    return round(min(score, 100.0), 1), "; ".join(reasons)


def apply_to(opp: Opportunity) -> Economics:
    """Compute and write the economics onto the opportunity record."""
    econ = compute(opp)
    opp.estimated_hours = econ.estimated_hours
    opp.estimated_ai_effort = econ.estimated_ai_hours
    opp.estimated_non_ai_effort = econ.estimated_human_hours
    opp.estimated_cost = round(econ.ai_cash_cost + econ.infrastructure_cost + econ.other_cost, 2)
    opp.estimated_platform_fee = econ.platform_fee
    opp.estimated_net_revenue = econ.expected_net_profit
    opp.confidence = econ.confidence
    return econ


def automation_pct(econ: Economics) -> int:
    """Share of total effort the AI worker can carry. Shown on the dashboard as 'Automation %'."""
    if econ.estimated_hours <= 0:
        return 0
    return int(round(100 * econ.estimated_ai_hours / econ.estimated_hours))
