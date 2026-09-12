"""Order intake: the path from a real Fiverr order to a delivered, recorded job.

Why this exists
---------------
`FiverrConnector.import_order` is documented as *the* path from an order notification into the
pipeline. It had **no callers** - not a CLI command, not a workflow, nothing outside its own
docstring. `pipeline.run` and `pipeline.deliver` had none either, outside the demo lifecycle.

So the verified fulfillment system had no front door. Every piece worked and was proved to work
end to end by `worker-proof`, on a fixture job the proof built itself. A real buyer ordering
tonight would have left Andres with a verified pipeline he could not put an order into, doing the
work by hand instead - which is the outcome the whole system exists to prevent.

This module is the surface, and only the surface. It adds no fulfillment logic: intake calls
`import_order`, the requirements check calls `pipeline.validate`, the capacity check calls
`capacity.pre_job_check`, the work calls `pipeline.run`, delivery calls `pipeline.deliver`.
Everything it chains was already built, tested, and proved.

The chain, and where a human stands in it
-----------------------------------------
    ORDER RECEIVED      import_order, from a notification Andres already has
    REQUIREMENTS CHECK  pipeline.validate - an incomplete brief stops here
    CAPACITY CHECK      capacity.pre_job_check - worker + reviewer + revision + reserve
    ACCEPT / ESCALATE   accept reserves capacity; escalate says what a person must decide
    WORKER -> REVIEWER -> REVISION -> FINAL QA        pipeline.run
    READY_TO_DELIVER    terminal for the machine
    NEEDS ANDRES        delivery requires a human actor, by design and by test
    DELIVERY            pipeline.deliver
    REVENUE RECORDING   a real RevenueEntry, and the storefront ledger

Nothing here contacts Fiverr. Fiverr has no seller API, orders arrive by email and push, and
delivery is performed by Andres in Fiverr's own interface. This records and fulfils; it does not
transact.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from . import audit, capacity, storage
from .models import Job, JobStatus, RevenueEntry

#: Fiverr's commission. Used to derive net from gross when Andres does not state net himself.
FIVERR_COMMISSION = 0.20

#: A brief with fewer than this many requirements is almost never workable. Fiverr's own
#: questionnaire asks five questions per gig, so an order arriving with one line means the buyer
#: skipped it - and starting work on a brief nobody can satisfy is how a late delivery begins.
MIN_REQUIREMENTS = 2


@dataclass
class IntakeVerdict:
    """What intake decided, and what a person has to do about it."""

    decision: str  # ACCEPTED | ESCALATE | REFUSED
    job: Job | None
    requirements_ok: bool
    requirement_problems: list[str]
    capacity_status: str
    capacity_reason: str
    needs_human: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision,
            "job_id": self.job.id if self.job else "",
            "requirements_ok": self.requirements_ok,
            "requirement_problems": self.requirement_problems,
            "capacity_status": self.capacity_status,
            "capacity_reason": self.capacity_reason,
            "needs_human": self.needs_human,
        }


def intake(
    *,
    order_id: str,
    buyer: str,
    gig_title: str,
    price: float,
    requirements: list[str],
    worker_minutes: float,
    deadline: str = "",
    job_type: str = "generic",
    actor: str,
) -> IntakeVerdict:
    """Take a real order through the requirements and capacity gates, then store it.

    Deliberately does NOT start work. Accepting an order and doing it are different decisions,
    and a command that did both would spend subscription capacity before anyone had looked at
    what arrived. `order run` is the second step.

    ``worker_minutes`` is required and not guessed. `capacity.pre_job_check` returns UNKNOWN for a
    zero estimate rather than inventing one, and that is the correct behaviour - so the caller has
    to supply it, from the gig tier the buyer actually purchased.
    """
    from .connectors.fiverr import FiverrConnector

    job = FiverrConnector.import_order(
        order_id=order_id,
        buyer=buyer,
        gig_title=gig_title,
        price=price,
        requirements=requirements,
        deadline=deadline,
        job_type=job_type,
    )

    # --- REQUIREMENTS CHECK -------------------------------------------------
    from .fulfillment import pipeline

    ok, problems = pipeline.validate(job)
    if len(requirements) < MIN_REQUIREMENTS:
        ok = False
        problems.append(
            f"Only {len(requirements)} requirement(s) supplied. Fiverr's questionnaire asks five; "
            f"this brief is too thin to work from, and the order clock is already running."
        )

    # --- CAPACITY CHECK ----------------------------------------------------
    verdict = capacity.pre_job_check(worker_minutes=worker_minutes, deadline=deadline or None)

    # --- ACCEPT / ESCALATE -------------------------------------------------
    if not ok:
        decision = "ESCALATE"
        needs_human = (
            "Ask the buyer for the missing information before starting. The delivery clock does "
            "not start until the questionnaire is answered, so asking is free and guessing is not."
        )
    elif verdict.needs_human or verdict.status not in ("SAFE TO START", "SAFE_TO_START"):
        decision = "ESCALATE"
        needs_human = f"Capacity says {verdict.status}. {verdict.reason}"
    else:
        decision = "ACCEPTED"
        needs_human = ""

    job.status = JobStatus.RECEIVED.value
    if decision == "ESCALATE":
        job.human_action_required = needs_human
    storage.jobs.put(job)

    # Reserve only on acceptance. Reserving for an order that was escalated would hold capacity
    # against work that may never start.
    if decision == "ACCEPTED":
        capacity.reserve(
            job.id,
            worker_minutes=worker_minutes,
            category=job_type,
            basis=f"Fiverr order {order_id}, {gig_title}",
        )

    audit.record(
        "order_intake",
        actor=actor,
        object_type="job",
        object_id=job.id,
        source="fiverr",
        after={
            "decision": decision,
            "order_id": order_id,
            "price": price,
            "capacity": verdict.status,
        },
        error=needs_human,
    )

    return IntakeVerdict(
        decision=decision,
        job=job,
        requirements_ok=ok,
        requirement_problems=problems,
        capacity_status=verdict.status,
        capacity_reason=verdict.reason,
        needs_human=needs_human,
    )


def record_revenue(job: Job, *, actor: str, gross: float | None = None, human_minutes: float = 0.0) -> RevenueEntry:
    """Record a real banked payment for a delivered job, and update the storefront ledger.

    `is_demo` is left False deliberately and that is the whole point of the field: every REAL
    REVENUE figure on the dashboard reads only rows where it is False, so this is the one place
    that can move the first-$100 milestone. Nothing calls it except a human-approved delivery.

    `ai_cash_cost` is 0.00 because the work runs on the subscription token. If that ever changes,
    this is one of the places the change becomes visible.
    """
    amount = job.agreed_price if gross is None else gross
    fee = round(amount * FIVERR_COMMISSION, 2)
    entry = RevenueEntry(
        job_id=job.id,
        source=job.source or "fiverr",
        client=job.client,
        description=job.title,
        gross=amount,
        platform_fee=fee,
        ai_cash_cost=0.0,
        ai_usage_units=job.ai_usage_units,
        human_minutes=human_minutes,
        net=round(amount - fee, 2),
    )
    storage.revenue.put(entry)

    audit.record(
        "revenue_recorded",
        actor=actor,
        object_type="revenue",
        object_id=entry.id,
        source=entry.source,
        after={"gross": entry.gross, "net": entry.net, "job": job.id},
    )

    # Mirror into the storefront ledger so the funnel and the money agree. Best-effort: a
    # bookkeeping mirror must never be the reason a recorded payment fails to record.
    try:
        from . import fiverr_kit, storefront

        gig = next((g for g in fiverr_kit.GIGS if g.title == job.title), None)
        if gig is not None:
            row = storefront.get(gig.key)
            if row is not None:
                storefront.observe(
                    gig.key,
                    actor=actor,
                    orders=row.orders + 1,
                    gross_revenue=round(row.gross_revenue + entry.gross, 2),
                    net_revenue=round(row.net_revenue + entry.net, 2),
                    claude_actual_minutes=(row.claude_actual_minutes or 0) + int(round(job.ai_usage_units * 60)),
                    andres_active_minutes=row.andres_active_minutes + int(round(human_minutes)),
                )
    except Exception as exc:  # noqa: BLE001 - the payment is recorded either way
        audit.record(
            "storefront_mirror_failed",
            actor=actor,
            object_type="revenue",
            object_id=entry.id,
            result="error",
            error=f"{type(exc).__name__}: {exc}",
        )

    return entry


def format_intake(v: IntakeVerdict) -> str:
    lines = [
        "ORDER INTAKE",
        "",
        f"  DECISION           {v.decision}",
        f"  JOB                {v.job.id if v.job else '(none)'}",
        f"  REQUIREMENTS       {'OK' if v.requirements_ok else 'INCOMPLETE'}",
        f"  CAPACITY           {v.capacity_status}",
        "",
    ]
    for p in v.requirement_problems:
        lines.append(f"  - {p}")
    if v.requirement_problems:
        lines.append("")
    lines.append(f"  {v.capacity_reason}")
    if v.needs_human:
        lines.append("")
        lines.append(f"  NEEDS ANDRES: {v.needs_human}")
    if v.decision == "ACCEPTED" and v.job is not None:
        lines.append("")
        lines.append(f"  Capacity reserved. Start the work with:  python -m aicc order run {v.job.id}")
    return "\n".join(lines)


def format_jobs() -> str:
    jobs = [j for j in storage.jobs.all() if not j.is_demo]
    if not jobs:
        return "No real jobs. An order arrives via `python -m aicc order import`."
    lines = ["REAL JOBS", ""]
    for j in sorted(jobs, key=lambda x: x.created_at or ""):
        lines.append(f"  [{j.status}] {j.id}  ${j.agreed_price:,.2f}  {j.client}")
        lines.append(f"        {j.title[:70]}")
        if j.qa_rounds:
            last = j.qa_rounds[-1]
            lines.append(f"        QA {len(j.qa_rounds)} round(s), latest {last.get('verdict')} at {last.get('overall_score')}/100")
        if j.human_action_required:
            lines.append(f"        NEEDS ANDRES: {j.human_action_required[:90]}")
        lines.append("")
    return "\n".join(lines)


def utcnow() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
