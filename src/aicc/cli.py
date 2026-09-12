"""Command line interface. Every scheduled workflow calls this, never a script inline.

python -m aicc <command> [options]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from . import audit, fiverr_kit, health, money, proposals, scoring, state, storage
from .config import BRAND_NAME, MAX_NEW_MONTHLY_CASH_SPEND, RUNS_DIR, ensure_dirs
from .connectors import LIVE_DISCOVERY_ORDER, get, registry
from .models import Actor, JobStatus, Opportunity, OpportunityStatus


def _print(msg: str = "") -> None:
    sys.stdout.write(msg + "\n")


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def cmd_discover(args: argparse.Namespace) -> int:
    ensure_dirs()
    st = state.SystemState.load()
    if not st.external_actions_allowed() and not args.force:
        _print(f"REFUSED: {st.why_blocked()}")
        _print("Use --force only for a manual diagnostic run.")
        return 2

    sources = args.sources or (["demo"] if st.mode == "DEMO" else LIVE_DISCOVERY_ORDER)
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    record: dict[str, object] = {"run_id": run_id, "sources": {}, "started_at": state.utcnow()}

    all_found: list[Opportunity] = []
    for name in sources:
        connector = get(name)
        if connector is None:
            record["sources"][name] = {"status": "unknown_connector"}  # type: ignore[index]
            continue
        try:
            found = connector.discover(limit=args.limit)
            for opp in found:
                opp.discovered_by_run = run_id
            all_found.extend(found)
            record["sources"][name] = {"status": "ok", "found": len(found)}  # type: ignore[index]
            _print(f"  {name:16s} {len(found):3d} found")
        except Exception as exc:  # noqa: BLE001
            record["sources"][name] = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}  # type: ignore[index]
            _print(f"  {name:16s} FAILED  {type(exc).__name__}: {str(exc)[:90]}")

    new, dupes = storage.upsert_opportunities(all_found)
    scored = 0
    archived = 0
    for opp in storage.opportunities.all():
        if opp.status in (OpportunityStatus.NEW.value, OpportunityStatus.SCORING.value):
            scoring.score_opportunity(opp)
            storage.opportunities.put(opp)
            scored += 1
            if opp.score_breakdown.get("rejected") and args.archive_rejected:
                storage.archive(opp)
                archived += 1

    record.update(
        {
            "finished_at": state.utcnow(),
            "found": len(all_found),
            "new": new,
            "duplicates": dupes,
            "scored": scored,
            "archived": archived,
        }
    )
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    (RUNS_DIR / f"{run_id}.json").write_text(json.dumps(record, indent=2), encoding="utf-8")

    audit.record(
        "opportunity_scan",
        actor=Actor.GITHUB_ACTIONS if args.ci else Actor.SYSTEM,
        object_type="run",
        object_id=run_id,
        after={"found": len(all_found), "new": new, "duplicates": dupes, "archived": archived},
    )
    _print(f"\n{len(all_found)} found | {new} new | {dupes} duplicates | {scored} scored | {archived} archived")
    return 0


# ---------------------------------------------------------------------------
# Proposals
# ---------------------------------------------------------------------------


def cmd_draft(args: argparse.Namespace) -> int:
    candidates = [
        o
        for o in storage.opportunities.all()
        if o.status in (OpportunityStatus.STRONG_MATCH.value, OpportunityStatus.REVIEW.value) and o.score >= args.min_score
    ]
    candidates.sort(key=lambda o: o.score, reverse=True)
    candidates = candidates[: args.limit]

    existing = {p.opportunity_id for p in storage.proposals.all()}
    drafted = 0
    for opp in candidates:
        if opp.id in existing:
            continue
        try:
            prop = proposals.generate(opp)
        except proposals.UnverifiableClaimError as exc:
            _print(f"  SKIPPED {opp.title[:50]}: {exc}")
            continue
        storage.proposals.put(prop)
        opp.status = OpportunityStatus.PROPOSAL_DRAFTED.value
        storage.opportunities.put(opp)
        audit.record(
            "proposal_drafted",
            actor=Actor.CLAUDE,
            object_type="proposal",
            object_id=prop.id,
            after={"opportunity": opp.id, "score": opp.score},
            source=opp.source,
        )
        drafted += 1
        _print(f"  drafted  [{opp.score:5.1f}] {opp.title[:62]}")

    _print(f"\n{drafted} proposal(s) drafted and awaiting your approval.")
    return 0


def cmd_approve(args: argparse.Namespace) -> int:
    prop = storage.proposals.get(args.proposal_id)
    if prop is None:
        _print(f"No proposal {args.proposal_id}")
        return 1
    if prop.source == "upwork":
        from .connectors.upwork import UpworkConnector

        quote = UpworkConnector.connect_spend_request(args.connects, prop.quoted_price or 0.0, prop.problem_statement[:60])
        _print(json.dumps(quote, indent=2))
        if not quote["approved"]:
            _print("\nCOST APPROVAL REQUIRED - not submitted.")
            return 3

    prop.status = "APPROVED"
    prop.approved_by = "ANDRES"
    prop.approved_at = state.utcnow()
    storage.proposals.put(prop)
    # Recorded as whoever ran it. If the system ever approved a proposal, an audit row saying
    # CLAUDE is the honest record of a rule being broken - writing ANDRES would hide it.
    audit.record("proposal_approved", actor=_actor(), object_type="proposal", object_id=prop.id, source=prop.source)
    _print(f"APPROVED {prop.id}. Submit it through the source's own interface, then run `python -m aicc mark-submitted {prop.id}`.")
    return 0


def cmd_mark_submitted(args: argparse.Namespace) -> int:
    prop = storage.proposals.get(args.proposal_id)
    if prop is None or prop.status != "APPROVED":
        _print("Refused: proposal must be APPROVED first.")
        return 1
    prop.status = "SUBMITTED"
    prop.submitted_at = state.utcnow()
    prop.submit_cost_units = args.connects
    storage.proposals.put(prop)
    opp = storage.opportunities.get(prop.opportunity_id)
    if opp:
        opp.status = OpportunityStatus.SUBMITTED.value
        storage.opportunities.put(opp)
    audit.record(
        "proposal_submitted",
        actor=_actor(),
        object_type="proposal",
        object_id=prop.id,
        source=prop.source,
        after={"connects": args.connects},
    )
    _print(f"Recorded as submitted: {prop.id}")
    return 0


# ---------------------------------------------------------------------------
# System control
# ---------------------------------------------------------------------------


def cmd_start(_: argparse.Namespace) -> int:
    ok, msg = state.start()
    _print(msg)
    if ok:
        _print("")
        for cap in state.probe_capabilities():
            _print(f"  {cap.light:6s} {cap.label:24s} {cap.detail[:70]}")
    return 0 if ok else 2


def cmd_stop(_: argparse.Namespace) -> int:
    _print(state.stop()[1])
    return 0


def cmd_pause(_: argparse.Namespace) -> int:
    _print(state.pause()[1])
    return 0


def cmd_resume(_: argparse.Namespace) -> int:
    _print(state.resume()[1])
    return 0


def cmd_emergency_stop(args: argparse.Namespace) -> int:
    _print(state.emergency_stop(args.reason)[1])
    return 0


def cmd_health(_: argparse.Namespace) -> int:
    caps = state.probe_capabilities()
    status, light = health.overall_status()
    _print(f"{BRAND_NAME}\nSYSTEM: {light} {status}\n")
    for cap in caps:
        _print(f"  {cap.light:6s} {cap.label:26s} {cap.detail[:72]}")
        if cap.blocking_reason:
            _print(f"         {'':26s} blocked: {cap.blocking_reason[:72]}")
    _print(f"\nADDITIONAL MONTHLY COST: ${MAX_NEW_MONTHLY_CASH_SPEND:.2f}")
    return 0


def cmd_status(_: argparse.Namespace) -> int:
    st = state.SystemState.load()
    opps = storage.opportunities.all()
    props = storage.proposals.all()
    jobs = storage.jobs.all()
    real = storage.real_revenue_entries()
    _print(f"{BRAND_NAME}")
    _print(f"  state          {st.run_state}   mode {st.mode}")
    _print(f"  opportunities  {len(opps)} ({sum(1 for o in opps if o.score_band in ('EXCELLENT', 'STRONG'))} strong)")
    _print(f"  proposals      {len(props)} ({sum(1 for p in props if p.status == 'AWAITING_APPROVAL')} awaiting approval)")
    _print(f"  jobs           {len(jobs)} ({sum(1 for j in jobs if j.status == JobStatus.READY_TO_DELIVER.value)} ready to deliver)")
    _print(f"  REAL revenue   ${sum(r.net for r in real):,.2f} across {len(real)} entr(y/ies)")
    _print(f"  added cost     ${MAX_NEW_MONTHLY_CASH_SPEND:.2f}/month")
    return 0


def cmd_connectors(_: argparse.Namespace) -> int:
    _print(f"{'SOURCE':<16}{'LIGHT':<8}{'DISCOVERY':<11}{'APPLY COST':<52}POLICY")
    _print("-" * 120)
    for name, cls in registry().items():
        c = cls.CAPS
        _print(f"{name:<16}{c.status_light():<8}{str(c.discovery):<11}{c.cost_to_apply[:50]:<52}{c.automation_policy}")
    return 0


def cmd_audit(args: argparse.Namespace) -> int:
    for ev in audit.read_all(limit=args.limit):
        _print(f"{ev.timestamp}  {ev.actor:<15}{ev.action:<28}{ev.object_type}/{ev.object_id}  {ev.result}")
    return 0


def cmd_costs(_: argparse.Namespace) -> int:
    from .config import COST_REQUESTS

    if not COST_REQUESTS.exists():
        _print("No cost requests have been made. Additional monthly cost: $0.00")
        return 0
    for line in COST_REQUESTS.read_text(encoding="utf-8").splitlines():
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        r = d["request"]
        _print(f"{'APPROVED' if d['approved'] else 'DECLINED':<10}${r['monthly_estimate']:>8.2f}/mo  {r['service']}: {r['reason'][:60]}")
    return 0


def cmd_build(args: argparse.Namespace) -> int:
    from .dashboard.build import build_fragment, build_site

    out = Path(args.out)
    if args.fragment:
        # Body-only output for hosts that supply their own document skeleton. Same data, same
        # assets - the difference is only the wrapper, so a fragment preview and the Pages
        # deployment can never show different numbers.
        dest = Path(args.fragment)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(build_fragment(), encoding="utf-8")
        _print(f"Fragment written to {dest}")
        return 0
    build_site(out)
    _print(f"Dashboard built at {out}")
    return 0


def cmd_verify_site(args: argparse.Namespace) -> int:
    from .dashboard.build import verify_site

    ok, report = verify_site(Path(args.site))
    _print(json.dumps(report, indent=2))
    return 0 if ok else 1


def cmd_demo(args: argparse.Namespace) -> int:
    from .demo_lifecycle import run_full_lifecycle

    return run_full_lifecycle(verbose=not args.quiet)


def _actor() -> str:
    """Who is actually running this command.

    The CLI used to assume ANDRES whenever it was not CI. That was true when a person at a
    terminal was the only caller, and stopped being true the moment the system started driving
    its own CLI - at which point every `selftest`, `clear-demo` and `discover` the system ran
    was written into the audit log under his name.

    The audit log exists to answer "who did what". A false answer there is the least recoverable
    kind, so the actor is read rather than assumed.

    The first fix kept ANDRES as the fallback for "not CI", on the reasoning that the remaining
    case was a person at a terminal. It wasn't: the agent driving this CLI from a container is
    also not CI, and every selftest it ran went on being signed with his name. The fallback was
    the bug, not the CI branch.

    So ANDRES now requires positive evidence, and there are only two kinds: something declared it
    (`AICC_ACTOR`), or the command is attached to an interactive terminal, which a person typing
    has and no headless caller does. Everything else is SYSTEM - an honest "some automation",
    which is recoverable, where a wrong name is not.
    """
    import os
    import sys

    declared = (os.environ.get("AICC_ACTOR") or "").strip().upper()
    if declared:
        try:
            return Actor(declared).value
        except ValueError:
            return Actor.SYSTEM.value
    if os.environ.get("GITHUB_ACTIONS") == "true":
        return Actor.GITHUB_ACTIONS.value
    try:
        at_a_keyboard = sys.stdin.isatty() and sys.stdout.isatty()
    except (AttributeError, ValueError):  # a closed or replaced stream is not a keyboard
        at_a_keyboard = False
    return Actor.ANDRES.value if at_a_keyboard else Actor.SYSTEM.value


def cmd_clear_demo(_: argparse.Namespace) -> int:
    removed = sum(
        c.clear_demo() for c in (storage.opportunities, storage.opportunities_archive, storage.proposals, storage.jobs, storage.revenue)
    )
    audit.record("demo_data_cleared", actor=_actor(), after={"removed": removed})
    _print(f"Removed {removed} demo record(s).")
    return 0


def cmd_top(args: argparse.Namespace) -> int:
    opps = sorted(
        [o for o in storage.opportunities.all() if not o.score_breakdown.get("rejected")],
        key=lambda o: o.score,
        reverse=True,
    )[: args.limit]
    for i, o in enumerate(opps, 1):
        econ = money.compute(o)
        _print(f"\n{i}. [{o.score:5.1f} {o.score_band}] {o.title}")
        _print(
            f"   source={o.source}  budget={o.budget_display()}  net=${econ.expected_net_profit:,.0f}  "
            f"human={econ.estimated_human_hours:.1f}h  automation={money.automation_pct(econ)}%"
        )
        _print(f"   {o.url}")
        for name, f in o.score_breakdown.get("factors", {}).items():
            _print(f"     {name:26s} {f['awarded']:5.1f}/{f['available']:<4.0f} {f['evidence'][:84]}")
    return 0


def cmd_fiverr(args: argparse.Namespace) -> int:
    """Inspect the gig kit and mark a gig ready.

    ``ready`` deliberately stops one step short of publishing. Fiverr has no seller API, so
    there is nothing to call even if it were permitted - and the category locks permanently the
    moment a gig is saved, which makes the last step one a human should take with their eyes open.
    """
    kit = fiverr_kit.summary()
    if args.action == "check":
        for gig in kit["gigs"]:
            problems = gig["validation"]
            mark = "OK  " if gig["valid"] else "FAIL"
            where = "BENCH" if gig.get("bench") else gig["status"]
            _print(f"{mark} {gig['key']:22s} {gig['title_chars']:>2}/80 title  {gig['description_chars']:>4}/1200 desc  [{where}]")
            for p_ in problems:
                _print(f"       - {p_}")
            outlook = {row["package"]: row for row in gig.get("capacity_outlook", [])}
            for pkg in gig["packages"]:
                hourly = pkg["implied_hourly"]
                cap_row = outlook.get(pkg["name"])
                cap_note = ""
                if cap_row:
                    fits = "" if cap_row["fits_delivery_window"] else "  <-- WILL NOT FIT THE PROMISED WINDOW"
                    cap_note = f"  {cap_row['claude_minutes']:>4.0f}m claude  [{cap_row['status']}]{fits}"
                _print(
                    f"       {pkg['name']:9s} ${pkg['price']:>7,.0f} list  ${pkg['net_after_commission']:>7,.0f} net  "
                    f"{pkg['est_human_hours']:>4.2f}h you  ${hourly:>6,.2f}/h{cap_note}"
                )
        for entry in kit["below_floor"]:
            _print(f"\nBELOW FLOOR (declared): {entry['key']}\n  {entry['reason']}")
        _print(f"\n{kit['slots_used']} of {kit['slots_available']} new-seller slots used.")
        for b in kit.get("bench", []):
            _print(f"BENCH: {b['key']} - ready to swap in for whichever gig gets no impressions in six weeks.")
        _print(kit["publishing_note"])
        return 0 if kit["all_valid"] else 1

    if args.action == "wizard":
        from . import fiverr_wizard

        _print(fiverr_wizard.render(args.key))
        return 0

    if args.action == "ready" and args.key in (None, "", "all"):
        # Marking every validated gig ready is preparation, not publishing, so the system may do
        # it - and the audit records CLAUDE, because writing it down as ANDRES would be a false
        # statement about who did what in the one log that exists to answer that question.
        failures = 0
        for g in fiverr_kit.all_gigs():
            if g.bench:
                continue
            ok, msg = fiverr_kit.mark_ready(g.key, actor=Actor.CLAUDE.value)
            _print(("  " if ok else "  ") + msg)
            failures += 0 if ok else 1
        _print("\nRun 'python -m aicc fiverr wizard' for the publishing sequence.")
        return 1 if failures else 0

    ok, msg = fiverr_kit.mark_ready(args.key, actor=Actor.CLAUDE.value)
    _print(msg)
    if not ok:
        return 1
    _print("Publish it yourself at https://www.fiverr.com/manage_gigs - there is no seller API,")
    _print("and the category cannot be changed after you save.")
    return 0


def cmd_notify(args: argparse.Namespace) -> int:
    """Tell Andres what needs him, through the only channel that is free and reliable.

    Never fails the run. A notifier that can take down the pipeline has inverted its purpose.
    """
    from . import notify

    notifications = notify.collect()
    _print(notify.summary_line(notifications))
    for n in notifications:
        _print(f"  {'BLOCKING' if n.urgent else 'waiting '}  {n.title}")
        if n.body:
            _print(f"            {n.body[:110]}")

    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        with open(step_summary, "a", encoding="utf-8") as fh:
            fh.write(f"### {notify.summary_line(notifications)}\n\n{notify.render(notifications)}\n")

    if args.issue:
        try:
            _print("\n" + notify.sync_issue(notifications))
        except notify.GitHubUnavailable as exc:
            _print(f"\nIssue not synced: {exc}")
    return 0


def cmd_selftest(args: argparse.Namespace) -> int:
    """Attempt a safety violation against the live system and confirm it is refused.

    Distinct from the test suite on purpose: pytest proves the code was right when written,
    against fixtures. This runs against the deployed configuration as it stands right now, and
    is the thing to run before leaving the system unattended.
    """
    from . import selftest

    report = selftest.run_all()
    if args.json:
        _print(json.dumps(report.to_dict(), indent=2))
    else:
        _print(selftest.format_report(report))

    audit.record(
        "safety_selftest",
        actor=Actor.GITHUB_ACTIONS.value if args.ci else _actor(),
        object_type="system",
        result="ok" if report.ok else "error",
        after={"passed": len(report.passed), "failed": [c.name for c in report.failed], "skipped": len(report.skipped)},
    )
    return 0 if report.ok else 1


def cmd_ai_status(args: argparse.Namespace) -> int:
    """Classify an AI failure and tell the workflow what to do about it.

    Called from CI with the failing step's output. Exit code carries the decision so the
    workflow can branch without parsing text:

        0  carry on (paused for capacity, degraded, or retry shortly)
        1  a person is needed, or the error is unrecognised

    The point of the 0 is that an exhausted usage window must not paint the run red. A red
    badge is a claim on someone's attention, and spending it on something that fixes itself in
    five hours teaches the owner to ignore red badges.
    """
    import os

    from .degradation import classify, workflow_summary

    decision = classify(
        args.error or "",
        status_code=args.status,
        has_credential=bool(os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")),
    )
    summary = workflow_summary(decision)
    _print(summary)

    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        with open(step_summary, "a", encoding="utf-8") as fh:
            fh.write(summary + "\n")

    audit.record(
        "ai.degraded",
        actor=Actor.GITHUB_ACTIONS if os.environ.get("GITHUB_ACTIONS") else Actor.SYSTEM,
        object_type="ai_worker",
        result="refused" if decision.should_fail_the_run else "ok",
        after=decision.to_dict(),
    )
    return 1 if decision.should_fail_the_run else 0


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def cmd_queue(args: argparse.Namespace) -> int:
    """The profit queue: what the system believes should happen next, and why.

    The same data the dashboard renders, in the terminal, so a decision can be checked without
    a browser. Deliberately prints the reason for every row - a queue that says what but not
    why is a queue nobody can correct.
    """
    from .dashboard.build import _collect

    d = _collect()
    pf = d.get("profit", {})
    cap_ = d.get("capacity", {})

    print("PROFIT QUEUE")
    print(f"  Expected captured   ${pf.get('expected_captured_profit', 0):,.0f}")
    print(f"  Expected missed     ${pf.get('expected_missed_profit', 0):,.0f}")
    print(f"  Total available     ${pf.get('total_available_profit', 0):,.0f} across {pf.get('profitable_count', 0)} profitable listings")
    print(f"  Capacity            {cap_.get('status', 'UNKNOWN')} - {cap_.get('safe_new_work_display', 'UNKNOWN')} safe for new work")
    print(f"  Utilisation         {pf.get('capacity_utilization_pct', 0):.0f}% of the planning horizon")
    print()
    rows = d.get("profit_queue", [])
    if not rows:
        print("  Nothing queued. Usually the market or the capacity window, not a bug.")
        return 0
    for r in rows[: args.limit]:
        print(f"  [{r['position']:<14}] ${r['gross']:>8,.0f}  {int(r['claude_minutes']):>5} min AI  {r['title'][:56]}")
        print(f"                    {r['lane']} · {r['capacity_status']}")
        print(f"                    {r['why']}")
        print()
    for note in d.get("plan_notes", []):
        print(f"  NOTE: {note}")
    return 0


def cmd_capacity(args: argparse.Namespace) -> int:
    """Claude subscription capacity, with its confidence attached to every figure."""
    from . import capacity as capmod

    snap = capmod.snapshot()
    if args.json:
        print(json.dumps(snap, indent=2))
        return 0
    print(f"CLAUDE CAPACITY: {snap['status']}  [{snap['confidence']}]")
    print(f"  Window            {snap['window_minutes']:.0f} min, resets {snap['next_reset']}")
    print(f"  Used this window  {snap['used_minutes']:.0f} min")
    print(f"  Reserved          {snap['reserved_minutes']:.0f} min across {snap['reserved_job_count']} accepted job(s)")
    print(f"  Safe for new work {snap['safe_new_work_display']}")
    print(f"  Utilisation       {snap['utilization_pct']:.0f}%")
    print(f"  Paid API fallback {snap['paid_api_fallback']}")
    if snap.get("exhausted_until"):
        print(f"  EXHAUSTED until   {snap['exhausted_until']} - AI work is queued, never billed elsewhere.")
    for line in snap.get("shed", []):
        print(f"  PAUSED: {line}")
    print()
    print(f"  {snap['basis']}")
    print(f"  {snap['telemetry_note']}")
    return 0


def cmd_compliance(args: argparse.Namespace) -> int:
    """The nine safety indicators, each probed live rather than read from a constant."""
    from . import compliance as compmod

    panel = compmod.panel()
    if args.json:
        print(json.dumps(panel, indent=2))
        return 0
    print("SAFETY & COMPLIANCE")
    for i in panel["indicators"]:
        mark = "ok " if i["ok"] else "BAD"
        want = f"(want {i['desired']})" if i["desired"] else ""
        print(f"  {mark} {i['label']:<26} {i['value']:<26} {want}")
        print(f"      {i['detail']}")
    print()
    if panel["all_ok"]:
        print("  All nine indicators are in their desired state.")
        return 0
    print(f"  DRIFTED: {', '.join(panel['drifted'])}. Do not leave the system running unattended.")
    return 1


def cmd_worker_proof(args: argparse.Namespace) -> int:
    """Prove the Claude worker runs, through the path real client work uses.

    Exits non-zero on failure so a workflow fails loudly rather than printing a sad paragraph
    and going green.
    """
    from . import worker_proof

    report = worker_proof.run_all(include_pipeline=not args.worker_only)
    if args.json:
        _print(json.dumps(report, indent=2))
    else:
        _print(worker_proof.format_report(report))
    # Recorded unconditionally: a FAILED proof is exactly as important to the dashboard as a
    # passing one, and only writing the good ones is how a light gets stuck on green.
    worker_proof.record_result(report)
    if args.out:
        from pathlib import Path

        worker_proof.write_report(report, Path(args.out))
        _print(f"\nReport written to {args.out}")
    if args.summary and os.environ.get("GITHUB_STEP_SUMMARY"):
        env = report["environment"]
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as fh:
            fh.write(f"## Claude worker proof: {report['worker_test_status']}\n\n")
            fh.write("| Field | Value |\n|---|---|\n")
            fh.write(f"| Worker test status | **{report['worker_test_status']}** |\n")
            fh.write(f"| Workflow run id | {env['workflow_run_id'] or 'n/a'} |\n")
            fh.write(f"| Execution environment | {env['execution_environment']} |\n")
            fh.write(f"| Subscription auth | {env['subscription_auth']} |\n")
            fh.write(f"| ANTHROPIC_API_KEY | {env['anthropic_api_key']} |\n")
            fh.write("| Paid fallback | DISABLED |\n")
            fh.write(f"| Mac required for job execution | {env['mac_required_for_job_execution']} |\n\n")
            for r in report["results"]:
                fh.write(f"- {'PASS' if r['passed'] else 'FAIL'} **{r['name']}** - {r['detail']}\n")
    return 0 if report["ok"] else 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="aicc", description=BRAND_NAME)
    sub = p.add_subparsers(dest="command", required=True)

    d = sub.add_parser("discover", help="Scan permitted sources for opportunities")
    d.add_argument("--sources", nargs="*", default=None)
    d.add_argument("--limit", type=int, default=50)
    d.add_argument("--force", action="store_true", help="Run even when the system is not ACTIVE")
    d.add_argument("--ci", action="store_true")
    d.add_argument("--archive-rejected", action="store_true", default=True)
    d.set_defaults(func=cmd_discover)

    dr = sub.add_parser("draft", help="Draft proposals for high-scoring opportunities")
    dr.add_argument("--min-score", type=float, default=65.0)
    dr.add_argument("--limit", type=int, default=5)
    dr.set_defaults(func=cmd_draft)

    ap = sub.add_parser("approve", help="Approve a drafted proposal")
    ap.add_argument("proposal_id")
    ap.add_argument("--connects", type=int, default=0)
    ap.set_defaults(func=cmd_approve)

    ms = sub.add_parser("mark-submitted")
    ms.add_argument("proposal_id")
    ms.add_argument("--connects", type=int, default=0)
    ms.set_defaults(func=cmd_mark_submitted)

    for name, fn, helptext in [
        ("start", cmd_start, "START BUSINESS"),
        ("stop", cmd_stop, "Stop the system"),
        ("pause", cmd_pause, "Pause all automation"),
        ("resume", cmd_resume, "Resume"),
        ("health", cmd_health, "Probe every capability"),
        ("status", cmd_status, "One-screen summary"),
        ("connectors", cmd_connectors, "Show each connector's real capabilities"),
        ("costs", cmd_costs, "Every cost request and its decision"),
        ("clear-demo", cmd_clear_demo, "Remove all demo records"),
    ]:
        s = sub.add_parser(name, help=helptext)
        s.set_defaults(func=fn)

    es = sub.add_parser("emergency-stop", help="Disable all external actions immediately")
    es.add_argument("--reason", default="")
    es.set_defaults(func=cmd_emergency_stop)

    au = sub.add_parser("audit")
    au.add_argument("--limit", type=int, default=40)
    au.set_defaults(func=cmd_audit)

    b = sub.add_parser("build", help="Build the static dashboard")
    b.add_argument("--out", default="site")
    b.add_argument("--fragment", default=None, help="Write body-only HTML to this path instead")
    b.set_defaults(func=cmd_build)

    vs = sub.add_parser("verify-site", help="Refuse to publish a broken build")
    vs.add_argument("--site", default="site")
    vs.set_defaults(func=cmd_verify_site)

    dm = sub.add_parser("demo", help="Run the full demo lifecycle (spec section 52)")
    dm.add_argument("--quiet", action="store_true")
    dm.set_defaults(func=cmd_demo)

    t = sub.add_parser("top", help="Top opportunities with full score reasoning")
    t.add_argument("--limit", type=int, default=10)
    t.set_defaults(func=cmd_top)

    nt = sub.add_parser("notify", help="Report what needs a human; optionally sync the rolling GitHub issue")
    nt.add_argument("--issue", action="store_true", help="Create, update or close the rolling 'Needs you' issue")
    nt.set_defaults(func=cmd_notify)

    stst = sub.add_parser("selftest", help="Attempt safety violations against the live system and confirm each is refused")
    stst.add_argument("--json", action="store_true")
    stst.add_argument("--ci", action="store_true")
    stst.set_defaults(func=cmd_selftest)

    ai = sub.add_parser("ai-status", help="Classify an AI failure and decide whether it should fail the run")
    ai.add_argument("--error", default="", help="The failing step's message")
    ai.add_argument("--status", type=int, default=None, help="HTTP status, if known")
    ai.set_defaults(func=cmd_ai_status)

    q = sub.add_parser("queue", help="The profit queue: what to work on next, and why")
    q.add_argument("--limit", type=int, default=10)
    q.set_defaults(func=cmd_queue)

    cp = sub.add_parser("capacity", help="Claude subscription capacity, with its confidence attached")
    cp.add_argument("--json", action="store_true")
    cp.set_defaults(func=cmd_capacity)

    cm = sub.add_parser("compliance", help="Probe the nine safety indicators against the live system")
    cm.add_argument("--json", action="store_true")
    cm.set_defaults(func=cmd_compliance)

    wp = sub.add_parser("worker-proof", help="Prove the Claude worker runs, through the production code path")
    wp.add_argument("--json", action="store_true")
    wp.add_argument("--out", default="", help="Write the JSON report to this path")
    wp.add_argument("--summary", action="store_true", help="Append a table to GITHUB_STEP_SUMMARY")
    wp.add_argument("--worker-only", action="store_true", help="Skip the reviewer pipeline leg")
    wp.set_defaults(func=cmd_worker_proof)

    fv = sub.add_parser("fiverr", help="Inspect the Fiverr gig kit; mark a gig ready to publish")
    fv.add_argument("action", choices=["check", "ready", "wizard"])
    fv.add_argument("key", nargs="?", help="Gig key. Omit with 'ready' to mark all four; omit with 'wizard' for the full sequence.")
    fv.set_defaults(func=cmd_fiverr)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
