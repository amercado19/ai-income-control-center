"""Configuration, paths, and the cost gate.

The cost gate (spec section 49) is the single most important safety property in this codebase.
It fails CLOSED: any component that would cause a cash charge must call ``cost_gate.request()``
and must treat a refusal as final. There is no override flag, no environment variable, and no
"force" argument. Raising the ceiling requires editing ``MAX_NEW_MONTHLY_CASH_SPEND`` in this
file, which is a reviewable commit rather than a runtime accident.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.environ.get("AICC_DATA_DIR", REPO_ROOT / "data"))

OPPORTUNITIES_DIR = DATA_DIR / "opportunities"
PROPOSALS_DIR = DATA_DIR / "proposals"
JOBS_DIR = DATA_DIR / "jobs"
LEDGER_DIR = DATA_DIR / "ledger"
RUNS_DIR = DATA_DIR / "runs"

STATE_FILE = DATA_DIR / "system_state.json"
AUDIT_LOG = DATA_DIR / "audit_log.jsonl"
COST_REQUESTS = DATA_DIR / "cost_requests.jsonl"
LEARNING_FILE = DATA_DIR / "learning.json"

# Client work happens OUTSIDE the repository. Nothing under this path is ever committed;
# .gitignore also blocks it, so both the code and the VCS have to fail for a leak to happen.
WORKSPACE_ROOT = Path(os.environ.get("AICC_WORKSPACE_ROOT", REPO_ROOT / "workspaces"))


def ensure_dirs() -> None:
    for d in (OPPORTUNITIES_DIR, PROPOSALS_DIR, JOBS_DIR, LEDGER_DIR, RUNS_DIR, WORKSPACE_ROOT):
        d.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# The cost ceiling
# ---------------------------------------------------------------------------

MAX_NEW_MONTHLY_CASH_SPEND: float = 0.00
"""Hard ceiling on NEW recurring cash spend, in USD per month.

Phase 1 value is 0.00 and every cost request therefore fails closed. Changing this number is a
deliberate, reviewable act. Nothing in the codebase may change it at runtime.
"""


@dataclass
class CostRequest:
    """A component asking permission to spend money. Always denied while the ceiling is 0."""

    service: str
    reason: str
    monthly_estimate: float
    benefit: str
    can_continue_without: bool
    alternative: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "service": self.service,
            "reason": self.reason,
            "monthly_estimate": self.monthly_estimate,
            "benefit": self.benefit,
            "can_continue_without": self.can_continue_without,
            "alternative": self.alternative,
        }


@dataclass
class CostDecision:
    approved: bool
    reason: str
    request: CostRequest
    requires_human: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "approved": self.approved,
            "reason": self.reason,
            "requires_human": self.requires_human,
            "request": self.request.to_dict(),
        }


class CostGate:
    """Fails closed. Every refusal is logged so the dashboard can show what the $0 rule cost us."""

    def __init__(self, ceiling: float = MAX_NEW_MONTHLY_CASH_SPEND) -> None:
        self.ceiling = ceiling

    def request(self, req: CostRequest) -> CostDecision:
        if req.monthly_estimate <= 0:
            decision = CostDecision(True, "No cash cost.", req, requires_human=False)
        elif req.monthly_estimate > self.ceiling:
            decision = CostDecision(
                False,
                f"DECLINED. Requested ${req.monthly_estimate:.2f}/mo exceeds the "
                f"MAX_NEW_MONTHLY_CASH_SPEND ceiling of ${self.ceiling:.2f}/mo. "
                f"Andres must approve this explicitly before it can proceed.",
                req,
                requires_human=True,
            )
        else:
            decision = CostDecision(
                False,
                "DECLINED by default. Phase 1 policy declines all cash spend pending explicit approval.",
                req,
                requires_human=True,
            )
        self._log(decision)
        return decision

    @staticmethod
    def _log(decision: CostDecision) -> None:
        try:
            COST_REQUESTS.parent.mkdir(parents=True, exist_ok=True)
            with COST_REQUESTS.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(decision.to_dict()) + "\n")
        except OSError:
            # Never let bookkeeping failure mask the decision itself.
            pass


cost_gate = CostGate()


# ---------------------------------------------------------------------------
# Operator profile - drives scoring and proposal grounding
# ---------------------------------------------------------------------------


@dataclass
class OperatorProfile:
    """What Andres can truthfully claim. The proposal generator may only draw on this.

    Everything here is verifiable from the NFL/MLB repositories. Nothing aspirational goes in
    this file: if it is not in here, the proposal generator cannot say it (spec section 20).
    """

    name: str = "Andres Mercado"
    headline: str = "Python data pipelines, scheduled automation, and analytics dashboards"

    core_skills: list[str] = field(
        default_factory=lambda: [
            "python",
            "pandas",
            "sql",
            "sqlite",
            "excel",
            "vba",
            "csv",
            "etl",
            "data pipeline",
            "data cleaning",
            "data normalization",
            "api integration",
            "rest api",
            "web scraping",
            "github actions",
            "ci/cd",
            "automation",
            "scheduled jobs",
            "cron",
            "dashboard",
            "data visualization",
            "reporting",
            "financial modeling",
            "pytest",
            "mypy",
            "ruff",
            "statistics",
            "forecasting",
        ]
    )

    strong_domains: list[str] = field(
        default_factory=lambda: [
            "research finance",
            "grants",
            "sponsored programs",
            "budgeting",
            "sports analytics",
            "predictive modeling",
        ]
    )

    # Capabilities demonstrated by real, inspectable work. Each entry is a claim the
    # proposal generator is allowed to make, with the artifact that backs it.
    demonstrated: dict[str, str] = field(
        default_factory=lambda: {
            "automated python data pipelines": "Two production pipelines (NFL, MLB) ingesting multiple third-party data sources on a schedule.",
            "scheduled github actions": "A tiered cron scheduler with a phase gate that skips no-op slots to conserve Actions minutes.",
            "api integrations": "Odds, schedule, roster, injury, weather and news providers, each with credential-state degradation.",
            "generated dashboards": "Static dashboards built by CI and published to GitHub Pages, mobile-responsive with offline support.",
            "model pipelines": "Model training, calibration reporting and backtesting with committed metric summaries.",
            "automated data refresh": "Idempotent refresh steps with caching and rebuild-on-empty guards.",
            "testing and deployment systems": "ruff + mypy + pytest + secret scanning + dependency audit in CI, and verify-before-publish "
            "that refuses to deploy a broken dashboard build.",
        }
    )

    # Hourly rate floor used by the profitability engine. Below this, work is declined as
    # not worth the human minutes.
    target_hourly: float = 85.0
    minimum_hourly: float = 50.0
    minimum_job_value: float = 50.0

    # Public Service Loan Forgiveness. Andres must remain at a 501(c)(3) or government employer
    # for roughly seven more years, and has stated this is a non-negotiable filter on career
    # decisions rather than a preference to weigh.
    #
    # This is the single most consequential constraint in this file, and it is easy to miss
    # because it is not a skill or a rate. Taking a full-time role at a for-profit company does
    # not merely compete for his hours - it ends qualifying employment and forfeits seven years
    # of progress toward forgiveness. No hourly rate on a job board compensates for that, so
    # full-time for-profit employment is a REJECT rather than something to be scored.
    #
    # Contract, part-time and project work do NOT touch PSLF: the qualifying employer is where
    # you work full-time, and freelance work alongside it is exactly the arrangement this whole
    # system was built to support.
    pslf_qualifying_employment_required: bool = True
    pslf_years_remaining: int = 7

    # Hard constraints that reject an opportunity outright.
    will_not_do: list[str] = field(
        default_factory=lambda: [
            "academic assignment",
            "take my exam",
            "write my thesis",
            "write my dissertation",
            "on-site",
            "in person",
            "relocate",
            "unpaid",
            "equity only",
            "revenue share only",
        ]
    )

    def skill_set(self) -> set[str]:
        return {s.lower() for s in self.core_skills}


PROFILE = OperatorProfile()


# ---------------------------------------------------------------------------
# Branding (spec section 46) - rebrandable from configuration
# ---------------------------------------------------------------------------

BRAND_NAME = os.environ.get("AICC_BRAND_NAME", "AI Income Control Center")
BRAND_SHORT = os.environ.get("AICC_BRAND_SHORT", "AICC")
