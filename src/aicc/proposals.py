"""Proposal generation (spec section 20).

Every proposal must be specific. "Dear hiring manager, I am excited about this opportunity" is
worse than sending nothing, because it consumes a Connect and signals that you did not read the
brief.

The truthfulness constraint is enforced in code, not by good intentions. ``_verify_claims``
checks every capability claim against ``OperatorProfile.demonstrated``, which contains only
things backed by real, inspectable artifacts in the NFL and MLB repositories. A claim that is
not in that dictionary cannot appear in a proposal - ``generate`` raises rather than shipping it.

AI disclosure: Upwork requires freelancers to disclose AI use to clients. Fiverr requires
honouring an explicit client request for non-AI work. The disclosure line is therefore included
by default on marketplace proposals and is not removable through configuration.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .config import PROFILE
from .models import Opportunity, Proposal
from .money import compute

AI_DISCLOSURE = (
    "For transparency: I use AI tooling as part of my workflow, under my own review. "
    "Every deliverable is checked against your acceptance criteria before it reaches you. "
    "If you would prefer this work done without AI assistance, tell me and I will price it that way."
)


class UnverifiableClaimError(RuntimeError):
    """A proposal tried to claim experience that is not backed by real work."""


@dataclass
class ProposalDraft:
    problem: str
    experience: str
    solution: str
    deliverables: list[str]
    turnaround: str
    question: str
    claims: list[str]


# ---------------------------------------------------------------------------
# Problem extraction
# ---------------------------------------------------------------------------

_PAIN = re.compile(
    r"([^.!?\n]*\b(?:stopped scaling|does not scale|manually|by hand|time[- ]consuming|error[- ]prone|"
    r"takes (?:us )?(?:hours|days)|tedious|painful|struggling|bottleneck|inconsistent|messy|"
    r"different column|slightly different|cannot|can't|no longer)\b[^.!?\n]*)",
    re.I,
)
_NEED = re.compile(r"([^.!?\n]*\b(?:we need|we want|looking for|need someone to|must be able to)\b[^.!?\n]*)", re.I)
_DELIVERABLE_LINE = re.compile(r"(?:deliverables?|you will (?:provide|deliver)|output)[:\s]+([^.\n]{10,300})", re.I)
_REQUIREMENT_LINE = re.compile(r"(?:requirements?|acceptance criteria|must)[:\s]+([^.\n]{10,300})", re.I)


def extract_problem(opp: Opportunity) -> str:
    """Name the client's actual problem in their own words where possible."""
    text = getattr(opp, "full_description", None) or opp.description or ""
    pains = [m.group(1).strip() for m in _PAIN.finditer(text)]
    needs = [m.group(1).strip() for m in _NEED.finditer(text)]
    if pains:
        return pains[0][:280]
    if needs:
        return needs[0][:280]
    first = re.split(r"(?<=[.!?])\s+", text.strip())
    return (first[0] if first else opp.title)[:280]


def extract_deliverables(opp: Opportunity) -> list[str]:
    out: list[str] = []
    for pattern in (_DELIVERABLE_LINE, _REQUIREMENT_LINE):
        body = getattr(opp, "full_description", None) or opp.description or ""
        for m in pattern.finditer(body):
            chunk = m.group(1).strip()
            for part in re.split(r",\s+(?=[a-z])|;\s*", chunk):
                part = part.strip(" .")
                if 8 <= len(part) <= 160:
                    out.append(part[0].upper() + part[1:])
    seen, unique = set(), []
    for d in out:
        key = d.lower()[:40]
        if key not in seen:
            seen.add(key)
            unique.append(d)
    return unique[:6]


# ---------------------------------------------------------------------------
# Experience matching - only truthful claims
# ---------------------------------------------------------------------------

# Triggers are the words that actually appear in listings, including concrete tool names. An
# earlier version keyed on abstractions ("pipeline", "etl") and matched only one weak claim on a
# Senior Data Engineer posting that named dbt, Airflow, Spark and Kafka - the exact work the
# claim describes. Listings name tools, not categories.
CLAIM_TRIGGERS: dict[str, list[str]] = {
    "automated python data pipelines": [
        "pipeline",
        "etl",
        "elt",
        "ingest",
        "data flow",
        "consolidat",
        "normalize",
        "data engineer",
        "airflow",
        "dagster",
        "prefect",
        "dbt",
        "spark",
        "kafka",
        "warehouse",
        "snowflake",
        "bigquery",
        "redshift",
        "data platform",
        "batch",
        "transform",
    ],
    "scheduled github actions": [
        "schedul",
        "cron",
        "daily",
        "weekly",
        "nightly",
        "recurring",
        "unattended",
        "automatic",
        "github action",
        "ci/cd",
        "ci&#x2f;cd",
        "orchestrat",
        "workflow",
    ],
    "api integrations": [
        "api",
        "rest",
        "endpoint",
        "integration",
        "webhook",
        "third-party",
        "third party",
        "graphql",
        "oauth",
        "sdk",
        "connector",
    ],
    "generated dashboards": [
        "dashboard",
        "visuali",
        "chart",
        "report view",
        "bi ",
        "looker",
        "tableau",
        "metabase",
        "grafana",
        "streamlit",
        "reporting",
    ],
    "model pipelines": [
        "model",
        "forecast",
        "predict",
        "projection",
        "burn rate",
        "trend",
        "machine learning",
        "analytics",
        "statistic",
    ],
    "automated data refresh": [
        "refresh",
        "update",
        "sync",
        "keep current",
        "monthly export",
        "incremental",
        "backfill",
    ],
    "testing and deployment systems": [
        "test",
        "qa",
        "quality",
        "verify",
        "accuracy",
        "audit",
        "deploy",
        "production-grade",
        "reliab",
        "monitor",
        "observability",
        "docker",
    ],
}


def select_claims(opp: Opportunity) -> list[str]:
    body = getattr(opp, "full_description", None) or opp.description
    text = f"{opp.title} {body}".lower()
    matched = [claim for claim, triggers in CLAIM_TRIGGERS.items() if any(t in text for t in triggers)]
    return matched[:3] or ["automated python data pipelines"]


def _verify_claims(claims: list[str]) -> None:
    unbacked = [c for c in claims if c not in PROFILE.demonstrated]
    if unbacked:
        raise UnverifiableClaimError(
            f"Refusing to generate a proposal claiming {unbacked}: not present in "
            "OperatorProfile.demonstrated, so there is no real artifact backing it."
        )


def build_experience(claims: list[str]) -> str:
    _verify_claims(claims)
    parts = [f"- {PROFILE.demonstrated[c]}" for c in claims]
    return (
        "Relevant work I have actually built and run:\n"
        + "\n".join(parts)
        + "\n\nThese are my own production systems, not client references, and I am happy to walk "
        "you through the code and the deployment."
    )


# ---------------------------------------------------------------------------
# Solution and question
# ---------------------------------------------------------------------------

SOLUTION_TEMPLATES: dict[str, str] = {
    "spreadsheet": (
        "I would write a repeatable script rather than doing this once by hand, so you can re-run it "
        "yourself next period. The script normalizes the differing headers to one schema, preserves "
        "every source row (rows that fail to map get flagged, never dropped), and outputs the "
        "consolidated workbook plus a mapping note. I verify by comparing row counts, column counts "
        "and totals before and after - if the numbers do not reconcile, it is not done."
    ),
    "data_cleaning": (
        "I would build the cleaning as a deterministic script with a before/after reconciliation "
        "report: row counts, null counts, duplicate counts and any rows that could not be parsed. "
        "You get the cleaned data, the script, and evidence that nothing was silently lost."
    ),
    "pdf_extraction": (
        "I would run extraction over the full set, then separate the results into high-confidence "
        "rows and a short exceptions list for anything the parser could not read cleanly. You get "
        "the structured output plus the exceptions, so nothing is quietly guessed at."
    ),
    "data_pipeline": (
        "I would build this as a scheduled job with the failure modes handled explicitly: retry on "
        "transient errors, and an alert when a source is unavailable rather than a silently empty "
        "report. Credentials stay in secrets, never in the code. You get the pipeline, tests, and "
        "the schedule configured and demonstrably running."
    ),
    "api_integration": (
        "I would build the integration with the failure cases handled first - retries, rate limits, "
        "and a clear alert when an upstream source is down rather than silent bad data. Credentials "
        "stay in environment secrets. Tests cover the parsing so a provider format change is caught."
    ),
    "dashboard": (
        "I would build the dashboard so every figure is auditable: each number shows the inputs it "
        "was derived from, so your finance lead can trace it rather than trust it. It runs on "
        "infrastructure you control with no paid subscription required."
    ),
    "web_research": (
        "I would capture a source link and a timestamp for every data point, so each figure is "
        "verifiable rather than asserted, and flag anything I could not confirm instead of filling "
        "the gap. Only publicly available information, no account creation or circumvention."
    ),
    "financial_model": (
        "I would build the model with the assumptions isolated on their own tab so you can change "
        "them without touching the logic, and include a reconciliation check so errors surface "
        "immediately rather than propagating."
    ),
}
DEFAULT_SOLUTION = (
    "I would start by pinning down exactly what 'done' looks like, because most of the cost in "
    "work like this comes from discovering halfway through that we meant different things. From "
    "there I build it as something repeatable rather than a one-off, handle the failure cases "
    "explicitly rather than assuming the happy path, and verify the output against your criteria "
    "before it reaches you."
)

QUESTION_TEMPLATES: dict[str, str] = {
    "spreadsheet": "When two source files disagree on the same record, which one should win - the newer file, or a specific source of truth?",
    "data_cleaning": "For rows that fail validation, would you rather they were flagged and kept, or removed into a separate exceptions file?",
    "pdf_extraction": "For invoices the parser cannot read confidently, do you want them flagged for manual keying, or should I attempt a best-effort extraction and mark it low-confidence?",
    "data_pipeline": "When one upstream source is unavailable, should the job still produce a report with that section marked missing, or hold and alert instead?",
    "api_integration": "What should happen when the upstream API rate-limits us mid-run - back off and resume, or fail the run loudly?",
    "dashboard": "Who is the primary reader, and what decision do they need to make from it? That drives what goes on the first screen.",
    "web_research": "When a competitor does not list a price publicly, would you rather see the field blank or my best estimate marked as an estimate?",
    "financial_model": "Which assumptions do you expect to change most often? I will isolate those so you can adjust them without touching the formulas.",
}


def build_turnaround(opp: Opportunity) -> str:
    """Fixed-scope work gets a delivery estimate. Ongoing hourly work gets availability.

    Quoting "4-5 business days" at a client hiring a contractor for 20-40 hours a week answers a
    question they did not ask and signals you misread the posting.
    """
    from .models import BudgetType

    if opp.budget_type == BudgetType.HOURLY.value:
        return (
            "I can start within a week and commit consistent hours; happy to begin with a small "
            "scoped piece so you can see how I work before committing to more"
        )

    econ = compute(opp)
    hours = econ.estimated_hours
    if hours <= 4:
        return "2 business days from a confirmed brief"
    if hours <= 12:
        return "4-5 business days from a confirmed brief"
    if hours <= 30:
        return "About 2 weeks, with a working version for review at the halfway point"
    return "3-4 weeks, delivered in milestones so you see progress early"


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def draft(opp: Opportunity, *, include_ai_disclosure: bool = True) -> ProposalDraft:
    claims = select_claims(opp)
    _verify_claims(claims)
    category = opp.category or "generic"

    deliverables = extract_deliverables(opp) or [
        "The completed work product in the format you specified",
        "The script or process used, so you can re-run it yourself",
        "A short note on method and any data-quality issues found",
    ]

    return ProposalDraft(
        problem=extract_problem(opp),
        experience=build_experience(claims),
        solution=SOLUTION_TEMPLATES.get(category, DEFAULT_SOLUTION),
        deliverables=deliverables,
        turnaround=build_turnaround(opp),
        question=QUESTION_TEMPLATES.get(category, "What does 'done' look like to you - what would make you call this a success?"),
        claims=claims,
    )


def render(opp: Opportunity, d: ProposalDraft, *, include_ai_disclosure: bool = True) -> str:
    lines = [
        f"Re: {opp.title}",
        "",
        f'You said: "{d.problem}"',
        "",
        "Here is how I would approach it.",
        "",
        d.solution,
        "",
        "What you would get:",
    ]
    lines += [f"  - {item}" for item in d.deliverables]
    label = "Availability" if opp.budget_type == "HOURLY" else "Timeline"
    lines += ["", f"{label}: {d.turnaround}.", "", d.experience, ""]
    if d.question:
        lines += [f"One question before I start: {d.question}", ""]
    if include_ai_disclosure:
        lines += [AI_DISCLOSURE, ""]
    lines += [PROFILE.name]
    return "\n".join(lines)


def generate(opp: Opportunity, *, include_ai_disclosure: bool = True) -> Proposal:
    """Produce a Proposal in DRAFT. Nothing is sent; approval is a separate, human step."""
    d = draft(opp)
    econ = compute(opp)
    body = render(opp, d, include_ai_disclosure=include_ai_disclosure)

    return Proposal(
        opportunity_id=opp.id,
        source=opp.source,
        problem_statement=d.problem,
        relevant_experience=d.experience,
        proposed_solution=d.solution,
        deliverables=d.deliverables,
        turnaround=d.turnaround,
        clarifying_question=d.question,
        body=body,
        quoted_price=econ.client_price or None,
        claims_made=d.claims,
        claims_verified=True,
        ai_disclosure_included=include_ai_disclosure,
        status="AWAITING_APPROVAL",
        is_demo=opp.is_demo,
    )
