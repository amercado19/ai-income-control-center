"""Opportunity classes and win probability (spec sections 12-13).

Two problems with a single scoring model, both of which showed up in the live market test.

**One.** A $50 spreadsheet cleanup and a $160/hour Senior Data Engineer contract were scored with
identical assumptions. They are not the same business. The small job is won on speed and price and
is decided in hours; the senior contract is won on demonstrated depth and is decided over weeks
against candidates with a track record. Scoring them the same way means the weights are wrong for
both.

**Two.** The system had no notion of how likely it was to actually *win* anything. "Likelihood of
Winning" was a per-source prior and nothing else - it could not tell that a listing demanding eight
years of production experience and a portfolio of similar work is a different proposition from a
one-off CSV merge, even on the same platform.

So: four classes, each with its own weight profile and effort assumptions, plus an explicit win
model whose output is labelled **INITIAL HEURISTIC** and stays labelled that way until there is
real outcome data to calibrate against. Spec section 13 asks for exactly that honesty, and
``analytics.scoring_calibration`` is where the calibration will land once outcomes exist.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import UTC
from enum import StrEnum
from typing import Any

from .config import PROFILE
from .models import BudgetType, Opportunity


class OpportunityClass(StrEnum):
    """Four genuinely different businesses wearing the same interface."""

    SMALL = "SMALL"  # A: $25-100 one-off, decided in hours
    MID = "MID"  # B: $100-500 scoped project
    HIGH_VALUE = "HIGH_VALUE"  # C: $500+ build
    ONGOING = "ONGOING"  # D: hourly / recurring engagement
    UNKNOWN = "UNKNOWN"  # no budget stated - cannot be classified


CLASS_LABEL = {
    OpportunityClass.SMALL: "A - Small automatable ($25-100)",
    OpportunityClass.MID: "B - Mid-size project ($100-500)",
    OpportunityClass.HIGH_VALUE: "C - High-value build ($500+)",
    OpportunityClass.ONGOING: "D - Ongoing / hourly contract",
    OpportunityClass.UNKNOWN: "Unclassified - no budget stated",
}


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

_ONGOING_HINT = re.compile(
    r"\b(ongoing|long[- ]term|retainer|recurring|per\s+week|hours?\s*/\s*week|hrs?\s*/\s*wk"
    r"|part[- ]time|full[- ]time|contract[- ]to[- ]hire|\d+\s*[-–]\s*\d+\s*(?:hours?|hrs?)\s*(?:a|per)\s*week)\b",
    re.I,
)


def classify_class(opp: Opportunity) -> OpportunityClass:
    """Which of the four businesses is this?

    Hourly and explicitly-ongoing work is class D regardless of the number attached to it: a
    $90/hour engagement is not a "$90 job", and treating it as one is how the effort model ends up
    quoting a four-day turnaround on a twelve-month contract.
    """
    if opp.budget_type == BudgetType.HOURLY.value:
        return OpportunityClass.ONGOING

    body = getattr(opp, "full_description", None) or opp.description or ""
    if _ONGOING_HINT.search(f"{opp.title} {body}"):
        return OpportunityClass.ONGOING

    budget = opp.budget_max or opp.budget_min
    if not budget:
        return OpportunityClass.UNKNOWN
    if budget < 100:
        return OpportunityClass.SMALL
    if budget < 500:
        return OpportunityClass.MID
    return OpportunityClass.HIGH_VALUE


# ---------------------------------------------------------------------------
# Per-class weight profiles
# ---------------------------------------------------------------------------

# Multipliers applied to the base weights in ``scoring.WEIGHTS``. They are renormalised back to
# 100 afterwards, so the score stays comparable across classes - what changes is what the score
# is *made of*, not its scale.
#
# The reasoning behind each profile:
#
# SMALL      Won on speed and throughput. Automation potential is everything, because a $60 job
#            that costs an hour of human attention is a loss. Absolute profit barely matters -
#            they are all small - so profitability is down-weighted and clarity is up-weighted,
#            since an ambiguous $60 job is pure downside.
# MID        The balanced case. Closest to the base weights.
# HIGH_VALUE Won on credibility. Skill fit and likelihood of winning matter most; a big budget you
#            cannot win is worth nothing. Risk matters more because the downside is larger.
# ONGOING    Won on demonstrated depth over weeks. Skill fit dominates; automation potential is
#            down-weighted because these engagements are judged on judgement, not throughput, and
#            over-claiming automation on a long contract is how you end up trapped in one.
CLASS_WEIGHT_PROFILE: dict[OpportunityClass, dict[str, float]] = {
    OpportunityClass.SMALL: {
        "skill_fit": 0.8,
        "automation_potential": 1.8,
        "expected_profitability": 0.5,
        "likelihood_of_winning": 1.0,
        "clarity_of_requirements": 1.6,
        "risk": 1.0,
    },
    OpportunityClass.MID: {
        "skill_fit": 1.0,
        "automation_potential": 1.1,
        "expected_profitability": 1.0,
        "likelihood_of_winning": 1.0,
        "clarity_of_requirements": 1.0,
        "risk": 1.0,
    },
    OpportunityClass.HIGH_VALUE: {
        "skill_fit": 1.4,
        "automation_potential": 0.8,
        "expected_profitability": 1.0,
        "likelihood_of_winning": 1.4,
        "clarity_of_requirements": 0.9,
        "risk": 1.2,
    },
    OpportunityClass.ONGOING: {
        "skill_fit": 1.6,
        "automation_potential": 0.5,
        "expected_profitability": 1.0,
        "likelihood_of_winning": 1.3,
        "clarity_of_requirements": 0.8,
        "risk": 1.1,
    },
    OpportunityClass.UNKNOWN: {
        "skill_fit": 1.0,
        "automation_potential": 1.0,
        "expected_profitability": 1.0,
        "likelihood_of_winning": 1.0,
        "clarity_of_requirements": 1.3,
        "risk": 1.0,
    },
}


def weights_for(cls_: OpportunityClass, base: dict[str, float]) -> dict[str, float]:
    """Apply the class profile and renormalise to the same total as the base weights."""
    profile = CLASS_WEIGHT_PROFILE.get(cls_, CLASS_WEIGHT_PROFILE[OpportunityClass.MID])
    scaled = {k: base[k] * profile.get(k, 1.0) for k in base}
    total = sum(scaled.values()) or 1.0
    target = sum(base.values())
    return {k: round(v * target / total, 2) for k, v in scaled.items()}


# ---------------------------------------------------------------------------
# Win probability - INITIAL HEURISTIC
# ---------------------------------------------------------------------------

CALIBRATION_STATUS = "INITIAL HEURISTIC - not calibrated against outcomes"
"""This label travels with every probability this module produces.

It is not modesty. A number presented as a probability invites you to act on it as one, and this
model has never seen a won or lost job. Once ``analytics.scoring_calibration`` has enough decided
outcomes, the weights below get fitted and this label changes. Until then it says what it is.
"""

# Signals that a listing demands a track record this account does not have.
_SENIORITY = re.compile(
    r"\b(?:senior|staff|principal|lead|architect|head\s+of|\d{1,2}\+?\s*(?:years?|yrs?)"
    r"|(?:minimum|at\s+least)\s+\d{1,2}\s*(?:years?|yrs?))\b",
    re.I,
)
_PORTFOLIO_DEMAND = re.compile(
    r"\b(?:portfolio|case\s+stud|previous\s+work|examples?\s+of\s+(?:your\s+)?work|references?"
    r"|must\s+have\s+(?:shipped|built|delivered)|show\s+us\s+(?:your|similar))\b",
    re.I,
)
_REPUTATION_DEMAND = re.compile(
    r"\b(?:top\s+rated|job\s+success|\d{2,3}%\s+(?:success|jss)|established\s+freelancer"
    r"|\d+\+?\s*(?:reviews?|completed\s+jobs?)|proven\s+track\s+record)\b",
    re.I,
)
_TIMEZONE_DEMAND = re.compile(r"\b(?:overlap|time\s*zone|timezone|EST|ET\b|PST|PT\b|CET|GMT|UTC[+-]?\d?)\b", re.I)
_US_FRIENDLY = re.compile(r"\b(?:US|USA|United States|EST|ET|PST|PT|Eastern|Pacific|Americas)\b", re.I)


@dataclass
class WinEstimate:
    """A deliberately humble estimate of whether this can be won."""

    band: str = "Low"  # High | Medium | Low
    probability_low: float = 0.0  # percentage points
    probability_high: float = 0.0
    factors: list[dict[str, Any]] = field(default_factory=list)
    calibration: str = CALIBRATION_STATUS

    @property
    def display(self) -> str:
        return f"{self.band} ({self.probability_low:.0f}-{self.probability_high:.0f}%)"

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["display"] = self.display
        return d


def estimate_win(opp: Opportunity, *, source_prior: float, class_: OpportunityClass) -> WinEstimate:
    """Rule-based win estimate. Every adjustment is recorded with its reason.

    Starts from the source prior (a structural fact about the channel) and adjusts on what the
    listing actually demands. The output is a band and a range, never a single decimal - a point
    estimate would imply a precision this model does not have.
    """
    est = WinEstimate()
    p = source_prior
    body = getattr(opp, "full_description", None) or opp.description or ""
    text = f"{opp.title} {body}"

    def adjust(delta: float, name: str, why: str) -> None:
        nonlocal p
        before = p
        p = max(0.02, min(0.95, p + delta))
        est.factors.append({"factor": name, "effect": f"{delta:+.0%}", "from": f"{before:.0%}", "to": f"{p:.0%}", "why": why})

    est.factors.append(
        {
            "factor": "Source prior",
            "effect": f"{source_prior:.0%}",
            "from": "-",
            "to": f"{p:.0%}",
            "why": "Structural competitiveness of this channel.",
        }
    )

    # -- what the listing demands that we cannot show -------------------------
    if _REPUTATION_DEMAND.search(text):
        adjust(-0.20, "Platform reputation required", "Asks for reviews, Job Success Score or completed-job counts. This account has none.")
    if _SENIORITY.search(text):
        adjust(-0.10, "Senior//long-experience framing", "Names a seniority level or a multi-year minimum, which filters on track record.")
    if _PORTFOLIO_DEMAND.search(text):
        # Not always bad: a portfolio demand is an opportunity when the portfolio is real.
        demonstrable = any(k in text.lower() for k in ("pipeline", "dashboard", "automation", "data", "python", "api"))
        if demonstrable:
            adjust(
                +0.05,
                "Portfolio requested, and ours is relevant",
                "Asks for prior work in an area the NFL/MLB pipelines genuinely demonstrate.",
            )
        else:
            adjust(-0.10, "Portfolio requested in an unrelated area", "Asks for prior work we cannot show for this kind of job.")

    # -- competition ----------------------------------------------------------
    if opp.source == "freelancer_com":
        bids = re.search(r"\((\d+)\s+bids?", opp.client or "")
        if bids:
            n = int(bids.group(1))
            if n >= 15:
                adjust(-0.08, f"{n} existing bids", "Late to a contested project.")
            elif n <= 3:
                adjust(+0.08, f"Only {n} existing bid(s)", "Early on a project that is still open.")

    # -- fit ------------------------------------------------------------------
    matched = [s for s in PROFILE.skill_set() if s in text.lower()]
    if len(matched) >= 5:
        adjust(+0.12, "Strong skill alignment", f"{len(matched)} profile skills named: {', '.join(sorted(matched)[:6])}.")
    elif len(matched) <= 1:
        adjust(-0.15, "Weak skill alignment", "Almost nothing in this listing matches the profile.")

    for domain in PROFILE.strong_domains:
        if domain in text.lower():
            adjust(+0.08, "Domain advantage", f"Mentions '{domain}', a background this operator actually has.")
            break

    # -- timezone -------------------------------------------------------------
    if _TIMEZONE_DEMAND.search(text):
        if _US_FRIENDLY.search(text):
            adjust(+0.05, "US timezone requested", "Being US-based is an advantage here, not a barrier.")
        else:
            adjust(-0.08, "Non-US timezone overlap required", "Overlap requirement works against a US-based operator.")

    # -- freshness ------------------------------------------------------------
    age_days = _age_days(opp.posted_time)
    if age_days is not None:
        if age_days <= 2:
            adjust(+0.06, "Posted recently", f"About {age_days:.0f} day(s) old; still being read.")
        elif age_days > 21:
            adjust(-0.15, "Stale listing", f"About {age_days:.0f} days old; likely filled or abandoned.")
        elif age_days > 10:
            adjust(-0.07, "Ageing listing", f"About {age_days:.0f} days old.")

    # -- cost to apply --------------------------------------------------------
    if opp.source in ("upwork", "freelancer_com"):
        est.factors.append(
            {
                "factor": "Applying costs credits",
                "effect": "0%",
                "from": f"{p:.0%}",
                "to": f"{p:.0%}",
                "why": "Does not change the odds, but it does change whether the odds are worth paying for.",
            }
        )

    # -- class effect ---------------------------------------------------------
    if class_ is OpportunityClass.SMALL:
        adjust(
            +0.10, "Small one-off job", "Decided quickly and largely on price and turnaround, where a new account is least disadvantaged."
        )
    elif class_ is OpportunityClass.ONGOING:
        adjust(-0.10, "Ongoing engagement", "Judged over weeks against candidates with a track record.")

    # Band and range. The range widens as the estimate moves away from the middle, because the
    # model is least trustworthy exactly where it is most confident.
    spread = 0.08 + 0.12 * abs(p - 0.5)
    est.probability_low = round(max(0.0, p - spread) * 100, 0)
    est.probability_high = round(min(1.0, p + spread) * 100, 0)
    est.band = "High" if p >= 0.55 else "Medium" if p >= 0.30 else "Low"
    return est


def _age_days(posted: object) -> float | None:
    """Age of a posting in days, or None when the date cannot be read.

    Deliberately typed ``object`` and coerced rather than trusting the declared ``str``.
    ``connectors.base.normalize_timestamp`` is the boundary that should guarantee a string,
    but this value originates with a third party, and a scoring run must degrade to "age
    unknown" rather than crash on a provider that starts returning something new. An unknown
    age costs a few points of confidence; a crash costs the whole scan.
    """
    from datetime import datetime

    if posted is None:
        return None
    if not isinstance(posted, str):
        from .connectors.base import normalize_timestamp

        posted = normalize_timestamp(posted)
    if not posted:
        return None
    for parse in (_parse_iso, _parse_rfc822):
        dt = parse(posted)
        if dt:
            return (datetime.now(UTC) - dt).total_seconds() / 86400
    return None


def _parse_iso(value: str):  # type: ignore[no-untyped-def]
    from datetime import datetime

    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    except ValueError:
        return None


def _parse_rfc822(value: str):  # type: ignore[no-untyped-def]
    from email.utils import parsedate_to_datetime

    try:
        dt = parsedate_to_datetime(value)
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    except (TypeError, ValueError):
        return None
