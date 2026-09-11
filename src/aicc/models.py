"""Core domain schema.

One normalized shape for every opportunity regardless of source (spec section 11), plus the
proposal / job / QA / audit records the rest of the system reads and writes.

Design notes
------------
* Dataclasses + explicit ``to_dict`` / ``from_dict`` rather than pydantic: keeps the runtime
  dependency count at zero, which keeps the supply-chain attack surface at zero.
* Every monetary field is a ``float`` of US dollars. There is exactly one place money is
  computed (``aicc.money``) and it never mutates these records in place.
* ``ai_cash_cost`` and ``ai_usage_units`` are deliberately separate. Running the worker on a
  Claude subscription OAuth token costs $0.00 cash but does draw against the subscription's
  usage allowance. Collapsing those two into one number is how you end up believing a
  business is more profitable than it is.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


def utcnow() -> str:
    """ISO-8601 UTC timestamp. Every record in this system is stamped with this and nothing else."""
    return datetime.now(UTC).isoformat(timespec="seconds")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class OpportunityStatus(StrEnum):
    """Lifecycle of an opportunity (spec section 11)."""

    NEW = "NEW"
    SCORING = "SCORING"
    STRONG_MATCH = "STRONG_MATCH"
    REVIEW = "REVIEW"
    SKIP = "SKIP"
    PROPOSAL_DRAFTED = "PROPOSAL_DRAFTED"
    APPROVED = "APPROVED"
    SUBMITTED = "SUBMITTED"
    INTERVIEW = "INTERVIEW"
    WON = "WON"
    LOST = "LOST"
    EXPIRED = "EXPIRED"


class JobStatus(StrEnum):
    """Fulfillment pipeline states (spec section 22)."""

    RECEIVED = "RECEIVED"
    VALIDATE = "VALIDATE"
    PLAN = "PLAN"
    WORK = "WORK"
    VERIFY = "VERIFY"
    QA = "QA"
    FIX = "FIX"
    FINAL_QA = "FINAL_QA"
    READY_TO_DELIVER = "READY_TO_DELIVER"
    DELIVERED = "DELIVERED"
    PROBLEM = "PROBLEM"


class BudgetType(StrEnum):
    FIXED = "FIXED"
    HOURLY = "HOURLY"
    UNKNOWN = "UNKNOWN"


class AutomationPolicy(StrEnum):
    """What this system is permitted to do on a given source, verified against that source's
    current published rules. See docs/MARKETPLACE_RULES.md for the evidence behind each value.
    """

    FULL_AUTO = "FULL_AUTO"  # public API / documented feed, fetch + parse permitted
    ASSISTED = "ASSISTED"  # vendor-sanctioned agent surface (e.g. official MCP), human confirms writes
    MANUAL_IMPORT = "MANUAL_IMPORT"  # no permitted programmatic read; human pastes or forwards
    INBOUND_ONLY = "INBOUND_ONLY"  # no discovery surface exists at all; work arrives unsolicited
    DEMO = "DEMO"  # synthetic data, never real


class RiskFlag(StrEnum):
    AI_PROHIBITED = "AI_PROHIBITED"
    ACADEMIC_DISHONESTY = "ACADEMIC_DISHONESTY"
    PHYSICAL_PRESENCE_REQUIRED = "PHYSICAL_PRESENCE_REQUIRED"
    PROPRIETARY_SOFTWARE_REQUIRED = "PROPRIETARY_SOFTWARE_REQUIRED"
    UNCLEAR_DELIVERABLES = "UNCLEAR_DELIVERABLES"
    UNREALISTIC_DEADLINE = "UNREALISTIC_DEADLINE"
    LIKELY_SCAM = "LIKELY_SCAM"
    SECURITY_SENSITIVE = "SECURITY_SENSITIVE"
    POOR_CLIENT_HISTORY = "POOR_CLIENT_HISTORY"
    CREDENTIAL_SHARING_REQUESTED = "CREDENTIAL_SHARING_REQUESTED"
    PAYMENT_OFF_PLATFORM = "PAYMENT_OFF_PLATFORM"
    EQUITY_ONLY = "EQUITY_ONLY"
    GEO_EXCLUDED = "GEO_EXCLUDED"
    AI_PROPOSAL_DISCOURAGED = "AI_PROPOSAL_DISCOURAGED"
    PROMPT_INJECTION_ATTEMPT = "PROMPT_INJECTION_ATTEMPT"


class Actor(StrEnum):
    SYSTEM = "SYSTEM"
    CLAUDE = "CLAUDE"
    ANDRES = "ANDRES"
    GITHUB_ACTIONS = "GITHUB_ACTIONS"


# ---------------------------------------------------------------------------
# Opportunity
# ---------------------------------------------------------------------------


@dataclass
class ScoreBreakdown:
    """Why a score is what it is. The dashboard renders this verbatim.

    A single opaque number is not a score, it is a vibe. Every factor carries the points
    awarded, the points available, and a sentence of evidence naming what in the listing
    drove it.
    """

    factors: dict[str, dict[str, Any]] = field(default_factory=dict)
    penalties: list[dict[str, Any]] = field(default_factory=list)
    raw_total: float = 0.0
    final_score: float = 0.0
    rejected: bool = False
    rejection_reason: str = ""

    def add_factor(self, name: str, awarded: float, available: float, evidence: str) -> None:
        self.factors[name] = {
            "awarded": round(awarded, 1),
            "available": available,
            "evidence": evidence,
        }

    def add_penalty(self, name: str, points: float, evidence: str) -> None:
        self.penalties.append({"name": name, "points": round(points, 1), "evidence": evidence})

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ScoreBreakdown:
        return cls(
            factors=d.get("factors", {}),
            penalties=d.get("penalties", []),
            raw_total=d.get("raw_total", 0.0),
            final_score=d.get("final_score", 0.0),
            rejected=d.get("rejected", False),
            rejection_reason=d.get("rejection_reason", ""),
        )


@dataclass
class Opportunity:
    """One normalized opportunity. Spec section 11 field list, plus provenance."""

    # identity
    id: str = field(default_factory=lambda: new_id("opp"))
    source: str = ""
    external_id: str = ""

    # content
    title: str = ""
    description: str = ""
    client: str = ""
    url: str = ""

    # money
    budget_min: float | None = None
    budget_max: float | None = None
    budget_type: str = BudgetType.UNKNOWN.value
    currency: str = "USD"

    # timing
    posted_time: str = ""
    deadline: str = ""

    # classification
    skills: list[str] = field(default_factory=list)
    category: str = ""
    ai_allowed: bool | None = None  # None = not stated by the client
    automation_policy: str = AutomationPolicy.MANUAL_IMPORT.value
    risk_flags: list[str] = field(default_factory=list)

    # effort + economics (populated by aicc.money)
    estimated_hours: float = 0.0
    estimated_ai_effort: float = 0.0  # hours of work the AI worker can carry
    estimated_non_ai_effort: float = 0.0  # hours that must be Andres
    estimated_cost: float = 0.0
    estimated_platform_fee: float = 0.0
    estimated_net_revenue: float = 0.0

    # scores
    match_score: float = 0.0
    profit_score: float = 0.0
    competition_score: float = 0.0
    confidence: float = 0.0
    score: float = 0.0
    score_band: str = ""
    score_breakdown: dict[str, Any] = field(default_factory=dict)

    # lifecycle
    status: str = OpportunityStatus.NEW.value
    created_at: str = field(default_factory=utcnow)
    updated_at: str = field(default_factory=utcnow)

    # provenance - which run produced this record
    discovered_by_run: str = ""
    is_demo: bool = False

    def dedupe_key(self) -> str:
        """Stable identity across re-scans.

        Prefers ``source:external_id``. Falls back to a hash of source + normalized title +
        client so that a source which does not expose stable IDs still deduplicates.
        """
        if self.external_id:
            return f"{self.source}:{self.external_id}"
        basis = f"{self.source}|{self.title.strip().lower()}|{self.client.strip().lower()}"
        return f"{self.source}:h{hashlib.sha256(basis.encode()).hexdigest()[:16]}"

    def budget_display(self) -> str:
        if self.budget_min is None and self.budget_max is None:
            return "Not stated"
        unit = "/hr" if self.budget_type == BudgetType.HOURLY.value else ""
        if self.budget_min is not None and self.budget_max is not None:
            if self.budget_min == self.budget_max:
                return f"${self.budget_min:,.0f}{unit}"
            return f"${self.budget_min:,.0f}-${self.budget_max:,.0f}{unit}"
        single = self.budget_max if self.budget_min is None else self.budget_min
        return f"${single:,.0f}{unit}"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Opportunity:
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})


# ---------------------------------------------------------------------------
# Proposal
# ---------------------------------------------------------------------------


@dataclass
class Proposal:
    """A drafted proposal. Never leaves the system without an explicit human approval
    (spec section 5, Level 2)."""

    id: str = field(default_factory=lambda: new_id("prop"))
    opportunity_id: str = ""
    source: str = ""

    # the four things every proposal must contain to not be generic garbage (spec section 20)
    problem_statement: str = ""
    relevant_experience: str = ""
    proposed_solution: str = ""
    deliverables: list[str] = field(default_factory=list)
    turnaround: str = ""
    clarifying_question: str = ""

    body: str = ""
    quoted_price: float | None = None

    # truthfulness controls
    claims_made: list[str] = field(default_factory=list)
    claims_verified: bool = False
    ai_disclosure_included: bool = False

    # approval
    status: str = "DRAFT"  # DRAFT | AWAITING_APPROVAL | APPROVED | REJECTED | SUBMITTED
    approved_by: str = ""
    approved_at: str = ""
    submitted_at: str = ""

    # cost to submit, if the platform charges (e.g. Upwork Connects)
    submit_cost_units: int = 0
    submit_cost_currency: str = ""
    submit_cost_cash: float = 0.0

    created_at: str = field(default_factory=utcnow)
    updated_at: str = field(default_factory=utcnow)
    is_demo: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Proposal:
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})


# ---------------------------------------------------------------------------
# Job + QA
# ---------------------------------------------------------------------------


@dataclass
class QAReport:
    """Independent reviewer verdict (spec section 23).

    The reviewer is given the client requirements, the worker output, and the acceptance
    criteria. It is NOT given the worker's own assessment - that is the entire point of the
    separation, and ``worker_notes`` must never be passed into the reviewer prompt.
    """

    id: str = field(default_factory=lambda: new_id("qa"))
    job_id: str = ""
    round: int = 1

    # category scores, 0-100 each
    requirements_satisfied: float = 0.0
    accuracy: float = 0.0
    completeness: float = 0.0
    formatting: float = 0.0
    professional_quality: float = 0.0
    source_verification: float = 0.0
    security: float = 0.0
    file_integrity: float = 0.0
    client_instructions: float = 0.0

    overall_score: float = 0.0
    verdict: str = ""  # READY | AUTO_REVISE | HUMAN_REVIEW
    findings: list[dict[str, Any]] = field(default_factory=list)
    reviewer: str = "rule_based"  # rule_based | claude | human
    created_at: str = field(default_factory=utcnow)

    CATEGORIES = (
        "requirements_satisfied",
        "accuracy",
        "completeness",
        "formatting",
        "professional_quality",
        "source_verification",
        "security",
        "file_integrity",
        "client_instructions",
    )

    def compute_overall(self) -> float:
        scores = [getattr(self, c) for c in self.CATEGORIES]
        self.overall_score = round(sum(scores) / len(scores), 1)
        return self.overall_score

    def decide(self) -> str:
        """Spec section 23 thresholds. A hard failure in security or file integrity escalates
        to a human regardless of the average - you do not average your way past a security
        finding."""
        if self.security < 75 or self.file_integrity < 75:
            self.verdict = "HUMAN_REVIEW"
        elif self.overall_score >= 90:
            self.verdict = "READY"
        elif self.overall_score >= 75:
            self.verdict = "AUTO_REVISE"
        else:
            self.verdict = "HUMAN_REVIEW"
        return self.verdict

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("CATEGORIES", None)
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> QAReport:
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})


@dataclass
class Job:
    """A won piece of work moving through the fulfillment pipeline."""

    id: str = field(default_factory=lambda: new_id("job"))
    opportunity_id: str = ""
    proposal_id: str = ""
    source: str = ""
    client: str = ""
    title: str = ""
    job_type: str = "generic"  # code | spreadsheet | research | generic

    agreed_price: float = 0.0
    currency: str = "USD"
    deadline: str = ""

    requirements: list[str] = field(default_factory=list)
    acceptance_criteria: list[str] = field(default_factory=list)

    status: str = JobStatus.RECEIVED.value
    workspace: str = ""  # path OUTSIDE the repo; never committed
    deliverables: list[str] = field(default_factory=list)

    qa_rounds: list[dict[str, Any]] = field(default_factory=list)
    revision_count: int = 0
    max_auto_revisions: int = 2

    human_action_required: str = ""
    worker_notes: str = ""  # never shown to the reviewer

    human_minutes_spent: float = 0.0
    ai_usage_units: float = 0.0

    created_at: str = field(default_factory=utcnow)
    updated_at: str = field(default_factory=utcnow)
    delivered_at: str = ""
    is_demo: bool = False

    def latest_qa_score(self) -> float | None:
        if not self.qa_rounds:
            return None
        return float(self.qa_rounds[-1].get("overall_score", 0.0))

    def progress_pct(self) -> int:
        order = [
            JobStatus.RECEIVED,
            JobStatus.VALIDATE,
            JobStatus.PLAN,
            JobStatus.WORK,
            JobStatus.VERIFY,
            JobStatus.QA,
            JobStatus.FIX,
            JobStatus.FINAL_QA,
            JobStatus.READY_TO_DELIVER,
            JobStatus.DELIVERED,
        ]
        try:
            idx = [s.value for s in order].index(self.status)
        except ValueError:
            return 0
        return int(round(100 * idx / (len(order) - 1)))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Job:
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})


# ---------------------------------------------------------------------------
# Revenue + audit
# ---------------------------------------------------------------------------


@dataclass
class RevenueEntry:
    """A real, banked dollar. Nothing writes here except a confirmed payment.

    Demo entries carry ``is_demo=True`` and are excluded from every REAL REVENUE figure the
    dashboard shows. The upgrade-recommendation system (spec section 50) reads only real rows.
    """

    id: str = field(default_factory=lambda: new_id("rev"))
    job_id: str = ""
    source: str = ""
    client: str = ""
    description: str = ""

    gross: float = 0.0
    platform_fee: float = 0.0
    payment_fee: float = 0.0
    other_cost: float = 0.0
    ai_cash_cost: float = 0.0  # $0.00 while running on the subscription OAuth token
    net: float = 0.0

    ai_usage_units: float = 0.0  # tracked separately - not cash
    human_minutes: float = 0.0

    received_at: str = field(default_factory=utcnow)
    confirmed_by: str = ""  # who confirmed the money actually landed
    is_demo: bool = False

    def compute_net(self) -> float:
        self.net = round(self.gross - self.platform_fee - self.payment_fee - self.other_cost - self.ai_cash_cost, 2)
        return self.net

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> RevenueEntry:
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})


@dataclass
class AuditEvent:
    """Append-only record of every consequential action (spec section 32)."""

    id: str = field(default_factory=lambda: new_id("aud"))
    timestamp: str = field(default_factory=utcnow)
    actor: str = Actor.SYSTEM.value
    action: str = ""
    object_type: str = ""
    object_id: str = ""
    before: Any = None
    after: Any = None
    source: str = ""
    result: str = "ok"  # ok | refused | error
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> AuditEvent:
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})
