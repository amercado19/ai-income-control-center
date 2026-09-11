"""The full demo lifecycle (spec section 52).

Twenty-four steps from SYSTEM OFF to a delivered job with revenue booked and an audit trail.

The QA step is the one worth understanding. Step 15 requires that "QA catches an intentional
error". The error here is not a flag flipped to make the demo look good: the worker's first pass
silently drops rows that contain missing values, which is a real and extremely common defect in
data-cleaning work. The reviewer catches it by counting rows and comparing against the known
input count. The revision pass flags incomplete rows instead of discarding them, and the same
check then passes. Both the defect and the detection are genuine.
"""

from __future__ import annotations

import csv
import random
import sys

from . import audit, proposals, scoring, state, storage
from .config import ensure_dirs
from .connectors import get
from .fulfillment import pipeline
from .fulfillment.worker import RuleBasedWorker, workspace_for
from .models import Actor, Job, JobStatus, OpportunityStatus, RevenueEntry

STORES = ["Riverside", "Oakfield", "Hillcrest", "Lakeview"]
CATEGORIES = ["Homeware", "Garden", "Kitchen", "Lighting"]

# Header spellings differ per file on purpose - that is the actual problem the client has.
HEADER_VARIANTS = [
    ["Order ID", "Sale Date", "Store Name", "Item Category", "Amount"],
    ["order_id", "transaction_date", "store_id", "product_category", "total"],
    ["OrderID", "dt", "location", "cat", "revenue_usd"],
]


def _seed_sources(job: Job, *, files: int = 3, rows_per_file: int = 40, incomplete: int = 7) -> int:
    """Write messy source CSVs into the job workspace. Returns the total data-row count."""
    ws = workspace_for(job)
    rng = random.Random(20260911)
    total = 0
    blanks_placed = 0

    for idx in range(files):
        header = HEADER_VARIANTS[idx % len(HEADER_VARIANTS)]
        path = ws / f"source_{idx:02d}.csv"
        with path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(header)
            for r in range(rows_per_file):
                row = [
                    f"ORD-{idx:02d}{r:04d}",
                    f"2026-0{(idx % 9) + 1}-{(r % 27) + 1:02d}",
                    rng.choice(STORES),
                    rng.choice(CATEGORIES),
                    f"{rng.uniform(12, 480):.2f}",
                ]
                # Deliberate missing values - the trap the naive first pass falls into.
                if blanks_placed < incomplete and r % 6 == 0:
                    row[3] = ""
                    blanks_placed += 1
                writer.writerow(row)
                total += 1
    return total


def run_full_lifecycle(verbose: bool = True) -> int:  # noqa: PLR0915 - a linear script by design
    ensure_dirs()
    failures: list[str] = []
    step = 0

    def say(msg: str, ok: bool = True) -> None:
        nonlocal step
        step += 1
        if verbose:
            sys.stdout.write(f"  {step:2d}. {'PASS' if ok else 'FAIL'}  {msg}\n")
        if not ok:
            failures.append(msg)

    def check(condition: bool, msg: str) -> None:
        say(msg, bool(condition))

    if verbose:
        sys.stdout.write("\nDEMO LIFECYCLE - spec section 52\n" + "=" * 76 + "\n")

    # 1 - system off
    state.stop()
    st = state.SystemState.load()
    st.mode = "DEMO"
    st.save()
    check(st.run_state == "OFF", "System is OFF")

    # 2 - start
    ok, msg = state.start()
    check(ok and state.SystemState.load().run_state == "ACTIVE", f"START BUSINESS -> {msg}")

    # 3 + 4 - opportunities arrive and are normalized
    demo_connector = get("demo")
    if demo_connector is None:
        say("Demo connector is not registered", ok=False)
        return _finish(failures, verbose)
    found = demo_connector.discover(limit=20)
    new, _dupes = storage.upsert_opportunities(found)
    check(len(found) > 0, f"Demo opportunities arrived ({len(found)} listings, {new} new)")
    check(all(o.source == "demo" and o.is_demo for o in found), "Opportunities normalized to one schema and labelled DEMO")

    # 5 - scored
    stored = [o for o in storage.opportunities.all() if o.is_demo]
    for o in stored:
        scoring.score_opportunity(o)
        storage.opportunities.put(o)
    check(all(o.score_breakdown for o in stored), f"Scores calculated for {len(stored)} opportunities, each with a factor breakdown")

    # 5b - rejections actually fire
    rejected = [o for o in stored if o.score_breakdown.get("rejected")]
    reasons = {r.score_breakdown["rejection_reason"].split(".")[0] for r in rejected}
    check(len(rejected) >= 3, f"Non-negotiable rejections fired ({len(rejected)}): {'; '.join(sorted(reasons))}")

    # 6 - strong match highlighted
    strong = sorted([o for o in stored if o.score_band in ("EXCELLENT", "STRONG")], key=lambda o: o.score, reverse=True)
    check(bool(strong), f"Strong match highlighted: {strong[0].title[:50] if strong else 'NONE'} ({strong[0].score if strong else 0})")
    if not strong:
        return _finish(failures, verbose)
    target = strong[0]

    # 7 - proposal drafted
    prop = proposals.generate(target)
    storage.proposals.put(prop)
    target.status = OpportunityStatus.PROPOSAL_DRAFTED.value
    storage.opportunities.put(target)
    audit.record("proposal_drafted", actor=Actor.CLAUDE, object_type="proposal", object_id=prop.id, source="demo")
    check(
        len(prop.body) > 400 and prop.claims_verified and "Dear hiring manager" not in prop.body,
        "Proposal drafted: specific, claims verified against real artifacts, no generic opener",
    )

    # 8 - awaiting approval
    check(prop.status == "AWAITING_APPROVAL", "Approval notification appears (proposal AWAITING_APPROVAL)")

    # 9 + 10 - approve, submit
    prop.status = "APPROVED"
    prop.approved_by = "ANDRES"
    prop.approved_at = state.utcnow()
    storage.proposals.put(prop)
    audit.record("proposal_approved", actor=Actor.ANDRES, object_type="proposal", object_id=prop.id, source="demo")
    prop.status = "SUBMITTED"
    prop.submitted_at = state.utcnow()
    storage.proposals.put(prop)
    audit.record("proposal_submitted", actor=Actor.ANDRES, object_type="proposal", object_id=prop.id, source="demo")
    check(prop.status == "SUBMITTED", "Approved by Andres, then marked SUBMITTED")

    # 11 - won
    target.status = OpportunityStatus.WON.value
    storage.opportunities.put(target)
    job = Job(
        opportunity_id=target.id,
        proposal_id=prop.id,
        source="demo",
        client=target.client,
        title=target.title.replace("[DEMO] ", "[DEMO] "),
        job_type="spreadsheet",
        agreed_price=float(prop.quoted_price or 500.0),
        requirements=[
            "Consolidate all source files into one workbook",
            "Normalize the differing column headers to a single schema",
            "Preserve every source row; flag rows that fail to map rather than dropping them",
            "Provide a README describing the column mapping",
        ],
        acceptance_criteria=[
            "Row count in the output matches the total row count across the source files",
            "A README documents the column mapping",
            "No source row is silently discarded",
        ],
        status=JobStatus.RECEIVED.value,
        is_demo=True,
    )
    storage.jobs.put(job)
    audit.record("job_won", actor=Actor.SYSTEM, object_type="job", object_id=job.id, source="demo")
    check(job.status == JobStatus.RECEIVED.value, f"Job WON and created (${job.agreed_price:,.0f})")

    # 12 - worker begins
    expected_rows = _seed_sources(job)
    check(expected_rows > 0, f"Worker begins: {expected_rows} source rows staged across 3 files with mismatched headers")

    # 13-17 - work, QA, catch, fix, pass
    job = pipeline.run(
        job,
        worker_cls=RuleBasedWorker,
        expected_row_count=expected_rows,
        expected_columns=["order_id", "date", "store", "category", "revenue"],
    )
    rounds = job.qa_rounds
    check(bool(job.deliverables), f"Deliverable generated ({len(job.deliverables)} file(s))")
    check(len(rounds) >= 1, f"Reviewer performed QA independently (round 1 score {rounds[0]['overall_score'] if rounds else 0})")

    caught = any(
        f.get("check") == "row_count" and f.get("severity") == "critical" for f in (rounds[0].get("findings", []) if rounds else [])
    )
    detail = (
        next((f["detail"] for f in rounds[0].get("findings", []) if f.get("check") == "row_count"), "not detected")
        if rounds
        else "no QA round"
    )
    check(caught, f"QA caught the intentional error -> {detail[:88]}")

    check(len(rounds) >= 2, f"Worker revised and QA re-ran ({len(rounds)} round(s))")
    final_score = rounds[-1]["overall_score"] if rounds else 0
    check(
        rounds[-1]["verdict"] == "READY" if rounds else False,
        f"Final QA passed: {final_score}/100 verdict {rounds[-1]['verdict'] if rounds else 'NONE'}",
    )

    # 18 - delivery waits
    check(job.status == JobStatus.READY_TO_DELIVER.value, "Delivery waits for approval (READY_TO_DELIVER, not DELIVERED)")

    blocked, why = pipeline.deliver(job, actor=Actor.SYSTEM)
    check(not blocked, f"Automated delivery correctly refused -> {why}")

    # 19 + 20 - approve and deliver
    delivered, msg = pipeline.deliver(job, actor=Actor.ANDRES, approved_by="ANDRES")
    check(delivered and job.status == JobStatus.DELIVERED.value, f"Andres approved -> {msg}")

    # 21 - revenue
    entry = RevenueEntry(
        job_id=job.id,
        source="demo",
        client=job.client,
        description=job.title,
        gross=job.agreed_price,
        platform_fee=round(job.agreed_price * 0.10, 2),
        ai_cash_cost=0.0,
        ai_usage_units=job.ai_usage_units,
        human_minutes=18.0,
        confirmed_by="ANDRES",
        is_demo=True,
    )
    entry.compute_net()
    storage.revenue.put(entry)
    audit.record(
        "revenue_recorded",
        actor=Actor.ANDRES,
        object_type="revenue",
        object_id=entry.id,
        after={"net": entry.net, "is_demo": True},
        source="demo",
    )
    check(entry.net > 0, f"Revenue recorded: gross ${entry.gross:,.0f} - fee ${entry.platform_fee:,.0f} = net ${entry.net:,.0f}")

    # 22 - real revenue stays zero
    real_total = sum(r.net for r in storage.real_revenue_entries())
    check(real_total == 0.0, f"REAL REVENUE correctly excludes demo rows (${real_total:,.2f})")

    # 23 - analytics
    from .analytics import compute_metrics

    metrics = compute_metrics(include_demo=True)
    check(
        metrics["jobs_completed"] >= 1 and metrics["avg_qa_score"] is not None,
        f"Analytics updated: {metrics['jobs_completed']} completed, avg QA {metrics['avg_qa_score']}, "
        f"win rate {metrics['win_rate'] if metrics['win_rate'] is not None else 'Insufficient Data'}",
    )

    # 24 - audit trail
    actions = {e.action for e in audit.read_all()}
    required = {
        "system_started",
        "proposal_drafted",
        "proposal_approved",
        "proposal_submitted",
        "job_won",
        "qa_completed",
        "job_delivered",
        "revenue_recorded",
        "job_status_changed",
    }
    missing = required - actions
    check(not missing, f"Audit log contains every action ({len(actions)} distinct){'' if not missing else ' MISSING: ' + str(missing)}")

    # emergency stop must actually stop things
    state.emergency_stop("Demo lifecycle verification")
    stopped = state.SystemState.load()
    try:
        pipeline.run(Job(title="should not run", is_demo=True))
        blocked_ok = False
    except pipeline.PipelineBlocked:
        blocked_ok = True
    check(
        stopped.run_state == "EMERGENCY_STOP" and blocked_ok,
        "EMERGENCY STOP engaged and the pipeline refuses to run",
    )
    state.resume()

    return _finish(failures, verbose)


def _finish(failures: list[str], verbose: bool) -> int:
    if verbose:
        sys.stdout.write("=" * 76 + "\n")
        if failures:
            sys.stdout.write(f"RESULT: {len(failures)} FAILURE(S)\n")
            for f in failures:
                sys.stdout.write(f"   - {f}\n")
        else:
            sys.stdout.write("RESULT: ALL STEPS PASSED\n")
    return 1 if failures else 0
