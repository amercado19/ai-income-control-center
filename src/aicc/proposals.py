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


class PolicyBlockedError(RuntimeError):
    """A proposal was requested for work the standing rules forbid pursuing.

    The consequence that made this necessary was already on the dashboard: a proposal for a
    "Contract to permanent" engineering role sat in NEEDS ME, awaiting approval, one tap from
    being sent. Approving it would have started a conversation about a job that costs seven
    years of PSLF-qualifying payments.

    Drafting is where this has to stop. A proposal that exists is a proposal someone can approve.
    """


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
_ACCEPTANCE_LINE = re.compile(r"(?:acceptance criteria|definition of done|success looks like)[:\s]+([^.\n]{10,300})", re.I)

# Things the CANDIDATE must already have. These are emphatically NOT deliverables, and the
# distinction is not pedantic - it produced the worst defect this generator has had.
#
# A real draft for a Senior Backend Engineer role rendered:
#
#     What you would get:
#       - Have shipped: a double-entry ledger or equivalent money system in production
#       - A payment integration including webhook idempotency
#
# Those are the client's hiring requirements, echoed back as things Andres offers. Read
# plainly, that is a claim to have shipped a production double-entry ledger. He has not. It
# would have gone out as fabricated experience - the one thing this system exists to refuse -
# and the claim verifier never saw it, because it guards the experience section and this text
# arrived through the deliverables list.
#
# So candidate requirements are matched only to be EXCLUDED. Nothing matching this may ever
# reach a proposal body.
_CANDIDATE_REQUIREMENT = re.compile(
    r"\b(?:requirements?|qualifications?|must have|should have|you have|you['’]ll have|"
    r"have shipped|experience (?:with|in)|\d\+?\s*years?|we require|ideal candidate|"
    r"you are|nice to have|bonus points|about you)\b",
    re.I,
)


def extract_problem(opp: Opportunity, *, quotable_only: bool = False) -> str:
    """Name the client's actual problem in their own words where possible.

    ``quotable_only`` returns "" rather than falling back to the first sentence. The fallback
    is fine for a project proposal, where the opening line is usually the brief - but a job
    ad's first sentence is its pipe-delimited header, and quoting
    "Company | Role | REMOTE | $120-160/hr" back at the person who wrote it, under the words
    "you wrote", reads as a mail merge.
    """
    text = getattr(opp, "full_description", None) or opp.description or ""
    pains = [m.group(1).strip() for m in _PAIN.finditer(text)]
    needs = [m.group(1).strip() for m in _NEED.finditer(text)]
    if pains:
        return pains[0][:280]
    if needs:
        return needs[0][:280]
    if quotable_only:
        return ""
    first = re.split(r"(?<=[.!?])\s+", text.strip())
    return (first[0] if first else opp.title)[:280]


def extract_deliverables(opp: Opportunity) -> list[str]:
    """What the CLIENT will receive - never what they are asking the candidate to already be.

    Returns an empty list rather than guessing. The caller falls back to honest generic
    deliverables, which is always better than echoing the job description back as an offer.
    """
    body = getattr(opp, "full_description", None) or opp.description or ""
    out: list[str] = []
    for pattern in (_DELIVERABLE_LINE, _ACCEPTANCE_LINE):
        for m in pattern.finditer(body):
            chunk = m.group(1).strip()
            # The matched line itself may be a requirements heading that happens to contain
            # the word "output" or "deliver". Drop the whole chunk in that case.
            if _CANDIDATE_REQUIREMENT.search(chunk):
                continue
            for part in re.split(r",\s+(?=[a-z])|;\s*", chunk):
                part = part.strip(" .")
                if not (8 <= len(part) <= 160):
                    continue
                if _CANDIDATE_REQUIREMENT.search(part):
                    continue
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
    """Two registers, because a role and a gig are not the same document.

    A project proposal says "here is how I would build the thing you described, and one
    question before I start". Sent to a company hiring an engineer, that reads as someone who
    has misread the advert - they are not commissioning a deliverable, they are choosing a
    person, and there is no "before I start" to ask a question ahead of.

    So an ONGOING opportunity gets an application: what of their stated need is already
    demonstrable, what is not, and an offer to talk. The honesty rule is the same in both, and
    the ONGOING form adds one of its own - it states plainly where the fit is partial, because
    a hiring manager will find that out in ten minutes and finding it out from the applicant
    first is worth more than the sentence costs.
    """
    from .classes import OpportunityClass

    if opp.opportunity_class == OpportunityClass.ONGOING.value:
        return _render_application(opp, d, include_ai_disclosure=include_ai_disclosure)

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
    body = "\n".join(lines)
    _assert_no_echoed_requirements(body, d.deliverables)
    return body


def _render_application(opp: Opportunity, d: ProposalDraft, *, include_ai_disclosure: bool = True) -> str:
    """An application for an ongoing role, not a proposal for a project."""
    from .scoring import _unmet_hard_requirements

    quoted = extract_problem(opp, quotable_only=True)
    lines = [f"Re: {opp.title}", ""]
    if quoted:
        lines += [f'You wrote: "{quoted}"', "", "That is the part I can speak to directly.", ""]
    else:
        lines += ["Here is what I would bring to it, and where I would not.", ""]
    lines += [d.experience, ""]

    # Naming the gap. A hiring manager finds it in ten minutes anyway, and hearing it from the
    # applicant first is worth more than the sentence costs - it is also the only version of
    # this document that is true.
    unmet = [t for t in (_tidy_requirement(u) for u in _unmet_hard_requirements(opp)) if t]
    if unmet:
        lines += [
            "Where I would be starting from less: you asked for "
            + "; ".join(unmet[:2])
            + ". I have not built that specific thing, and I would rather say so now than have "
            "you find out in week two. What I would bring is the habit the list above describes - "
            "failure cases handled first, and work that reconciles.",
            "",
        ]

    lines += [
        f"Availability: {d.turnaround}.",
        "",
        "Happy to talk it through whenever suits you.",
        "",
    ]
    if include_ai_disclosure:
        lines += [AI_DISCLOSURE, ""]
    lines += [PROFILE.name]
    body = "\n".join(lines)
    _assert_no_echoed_requirements(body, d.deliverables)
    return body


# The requirement extractor matches on the lead-in phrase, so what it captures can begin
# mid-clause: "must have shipped: a double-entry ledger" yields "shipped: a double-entry
# ledger". Quoting that back reads as carelessness, which is a bad look in the one paragraph
# whose entire purpose is to sound candid.
_REQ_LEAD_IN = re.compile(r"^(?:shipped|built|worked|experience|expertise|knowledge|a background)\b[:\s,-]*(?:with|in|on|of)?\s*", re.I)


def _tidy_requirement(phrase: str) -> str:
    cleaned = _REQ_LEAD_IN.sub("", phrase).strip(" ,:;-")
    return cleaned[:90] if len(cleaned) >= 8 else ""


def _assert_no_echoed_requirements(body: str, deliverables: list[str]) -> None:
    """Last line of defence against offering the client their own hiring criteria back.

    The specific failure this exists to stop: a Senior Backend Engineer listing's
    "must have shipped a double-entry ledger in production" appearing under "What you would
    get", which reads as a claim to have done it. Filtering at extraction should already
    prevent it; this checks the finished text, because any future path into the deliverables
    list would otherwise reintroduce it silently.

    Raises rather than sanitising. A proposal quietly stripped of a sentence is a proposal
    nobody reviewed, and this is the class of error that gets an account suspended.
    """
    for item in deliverables:
        if _CANDIDATE_REQUIREMENT.search(item):
            raise UnverifiableClaimError(
                f"Refusing to send a proposal offering {item!r} as a deliverable: that is the "
                "client's requirement of the candidate, not something being delivered. Presenting "
                "it as an offer is a claim to have already done it."
            )
    if "What you would get:" in body:
        offered = body.split("What you would get:", 1)[1].split("\n\n", 1)[0]
        if _CANDIDATE_REQUIREMENT.search(offered):
            raise UnverifiableClaimError(
                "Refusing to send a proposal whose deliverables section restates the job's "
                f"candidate requirements: {offered.strip()[:160]!r}"
            )


def generate(opp: Opportunity, *, include_ai_disclosure: bool = True) -> Proposal:
    """Produce a Proposal in DRAFT. Nothing is sent; approval is a separate, human step.

    The standing rules are checked first, before any text is written. "Approval is a separate
    human step" is only a safeguard if what reaches the human is worth approving - a drafted
    proposal for work the rules forbid is a trap with Andres's own approval button on it.
    """
    from . import policy

    verdict = policy.evaluate(opp)
    if not verdict.allowed:
        gate = verdict.gates[0]
        raise PolicyBlockedError(f"{gate['gate']}: {gate['detail']}")

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
