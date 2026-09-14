"""Fulfillment pipeline, QA detection, connectors, analytics and the dashboard build."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from aicc import analytics, storage
from aicc.connectors import LIVE_DISCOVERY_ORDER, NotPermittedError, get, registry
from aicc.fulfillment import pipeline, reviewer
from aicc.fulfillment.worker import RuleBasedWorker, workspace_for
from aicc.models import Job, JobStatus, QAReport, RevenueEntry


def _write_csv(path: Path, header: list[str], rows: list[list[str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(rows)


# ---------------------------------------------------------------- spreadsheet QA


def test_qa_detects_row_loss(tmp_path):
    src = tmp_path / "src.csv"
    out = tmp_path / "out.csv"
    _write_csv(src, ["a", "b"], [[str(i), "x"] for i in range(100)])
    _write_csv(out, ["a", "b"], [[str(i), "x"] for i in range(93)])

    scores, findings = reviewer.qa_spreadsheet(out, src)
    row_finding = next(f for f in findings if f["check"] == "row_count")
    assert row_finding["severity"] == "critical"
    assert "lost 7" in row_finding["detail"]
    assert scores["completeness"] == 0.0


def test_qa_accepts_preserved_rows(tmp_path):
    src = tmp_path / "src.csv"
    out = tmp_path / "out.csv"
    rows = [[str(i), "x"] for i in range(50)]
    _write_csv(src, ["a", "b"], rows)
    _write_csv(out, ["a", "b"], rows)
    _scores, findings = reviewer.qa_spreadsheet(out, src)
    assert not any(f["check"] == "row_count" for f in findings)


def test_qa_detects_missing_required_column(tmp_path):
    out = tmp_path / "out.csv"
    _write_csv(out, ["a", "b"], [["1", "2"]])
    scores, findings = reviewer.qa_spreadsheet(out, None, expected_columns=["a", "b", "revenue"])
    assert any(f["check"] == "columns" for f in findings)
    assert scores["requirements_satisfied"] < 50


def test_qa_detects_missing_file(tmp_path):
    scores, findings = reviewer.qa_spreadsheet(tmp_path / "nope.csv")
    assert findings[0]["check"] == "file_exists"
    assert all(v == 0.0 for v in scores.values())


def test_qa_detects_duplicates(tmp_path):
    out = tmp_path / "out.csv"
    _write_csv(out, ["a"], [["1"], ["1"], ["2"]])
    _scores, findings = reviewer.qa_spreadsheet(out)
    assert any(f["check"] == "duplicates" for f in findings)


# ------------------------------------------------------------------- research QA


def test_research_qa_catches_fabricated_sources():
    doc = "Our analysis shows growth. Source: https://example.com/fake-report " + "filler " * 120
    scores, findings = reviewer.qa_research(doc)
    assert any(f["check"] == "fabricated_sources" for f in findings)
    assert scores["source_verification"] == 0.0


def test_research_qa_requires_sources():
    scores, _ = reviewer.qa_research("A confident claim with no citation at all. " * 40)
    assert scores["source_verification"] < 60


def test_research_qa_flags_overclaiming():
    doc = (
        ("This is always true. Every company does this. All of them. It is guaranteed and proven. Never fails. " * 6)
        + " ".join(f"https://real{i}.org/page" for i in range(4))
        + " filler " * 80
    )
    _scores, findings = reviewer.qa_research(doc)
    assert any(f["check"] == "overclaiming" for f in findings)


# ----------------------------------------------------------------------- code QA


def test_code_qa_catches_syntax_errors(tmp_path):
    (tmp_path / "bad.py").write_text("def broken(:\n    pass\n", encoding="utf-8")
    scores, findings = reviewer.qa_code(tmp_path)
    assert any(f["check"] == "syntax" for f in findings)
    assert scores["accuracy"] == 0.0


def test_code_qa_flags_dangerous_patterns(tmp_path):
    (tmp_path / "risky.py").write_text("import subprocess\nsubprocess.run(cmd, shell=True)\n", encoding="utf-8")
    scores, findings = reviewer.qa_code(tmp_path)
    assert any(f["check"] == "security" for f in findings)
    assert scores["security"] <= 50


def test_code_qa_will_not_claim_working_without_tests(tmp_path):
    (tmp_path / "ok.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    scores, findings = reviewer.qa_code(tmp_path)
    note = next(f for f in findings if f["check"] == "tests")
    assert "unverified" in note["detail"].lower()
    assert scores["requirements_satisfied"] < 100


# ------------------------------------------------------------------ QA verdicts


@pytest.mark.parametrize("score,expected", [(95, "READY"), (80, "AUTO_REVISE"), (60, "HUMAN_REVIEW")])
def test_verdict_thresholds(score, expected):
    r = QAReport()
    for c in QAReport.CATEGORIES:
        setattr(r, c, float(score))
    r.compute_overall()
    assert r.decide() == expected


def test_security_failure_escalates_regardless_of_average():
    """You do not average your way past a security finding."""
    r = QAReport()
    for c in QAReport.CATEGORIES:
        setattr(r, c, 100.0)
    r.security = 40.0
    r.compute_overall()
    assert r.overall_score > 90
    assert r.decide() == "HUMAN_REVIEW"


# ------------------------------------------------------------------- pipeline


def _spreadsheet_job(rows_per_file=20, files=2, incomplete=5) -> tuple[Job, int]:
    job = Job(
        title="Consolidate CSVs",
        client="Test Co",
        job_type="spreadsheet",
        agreed_price=500.0,
        requirements=["Consolidate all source files", "Preserve every row"],
        acceptance_criteria=["Row count matches the source total", "No row is silently discarded"],
    )
    storage.jobs.put(job)
    ws = workspace_for(job)
    total = 0
    placed = 0
    for i in range(files):
        rows = []
        for r in range(rows_per_file):
            cat = "" if (placed < incomplete and r % 4 == 0) else "Kitchen"
            if cat == "":
                placed += 1
            rows.append([f"O{i}{r}", "2026-01-01", "Store", cat, "10.00"])
            total += 1
        _write_csv(ws / f"source_{i:02d}.csv", ["Order ID", "Sale Date", "Store Name", "Item Category", "Amount"], rows)
    return job, total


def test_pipeline_catches_and_fixes_row_loss(active_system):
    job, total = _spreadsheet_job()
    job = pipeline.run(job, worker_cls=RuleBasedWorker, expected_row_count=total)

    assert len(job.qa_rounds) == 2, "first pass must fail, second must pass"
    first = job.qa_rounds[0]
    assert any(f["check"] == "row_count" and f["severity"] == "critical" for f in first["findings"])
    assert job.qa_rounds[-1]["verdict"] == "READY"
    assert job.status == JobStatus.READY_TO_DELIVER.value

    out = Path(job.deliverables[0])
    with out.open(newline="", encoding="utf-8") as fh:
        assert len(list(csv.reader(fh))) - 1 == total, "revision must preserve every row"


def test_pipeline_stops_at_ready_never_delivers(active_system):
    job, total = _spreadsheet_job()
    job = pipeline.run(job, worker_cls=RuleBasedWorker, expected_row_count=total)
    assert job.status != JobStatus.DELIVERED.value


def test_pipeline_respects_the_revision_cap(active_system):
    """A job that can never pass must escalate, not loop forever."""
    job, total = _spreadsheet_job()
    # Demand a column the worker will never produce, so QA can never pass.
    job = pipeline.run(job, worker_cls=RuleBasedWorker, expected_row_count=total, expected_columns=["a_column_that_will_never_exist"])
    assert job.status == JobStatus.PROBLEM.value
    assert len(job.qa_rounds) <= job.max_auto_revisions + 1
    assert job.human_action_required


def test_pipeline_rejects_an_incomplete_brief(active_system):
    job = Job(title="Vague", client="", agreed_price=0.0)
    job = pipeline.run(job, worker_cls=RuleBasedWorker)
    assert job.status == JobStatus.PROBLEM.value
    assert "incomplete" in job.human_action_required.lower()


# ----------------------------------------------------------------- connectors


@pytest.mark.parametrize("name", ["upwork", "contra", "fiverr"])
def test_unautomatable_connectors_raise_rather_than_return_empty(name):
    """Returning [] would look like 'no jobs today'. Raising says 'this cannot be automated'."""
    with pytest.raises(NotPermittedError):
        get(name).discover(limit=1)


@pytest.mark.parametrize("name", ["upwork", "contra", "fiverr"])
def test_unautomatable_connectors_declare_themselves_honestly(name):
    caps = get(name).CAPS
    assert caps.discovery is False
    assert caps.allowed_automated_fetch is False
    assert caps.manual_approval_required is True
    assert caps.rules_evidence, "must cite why it cannot be automated"


def test_fiverr_is_inbound_only():
    assert get("fiverr").CAPS.automation_policy == "INBOUND_ONLY"


def test_live_sources_exclude_the_marketplaces():
    for name in ("upwork", "contra", "fiverr"):
        assert name not in LIVE_DISCOVERY_ORDER


def test_every_connector_cites_its_rules():
    for name, cls in registry().items():
        assert cls.CAPS.rules_evidence, f"{name} has no rules evidence"


def test_no_connector_claims_auto_delivery():
    for name, cls in registry().items():
        assert cls.CAPS.auto_delivery is False, f"{name} claims automatic delivery"


def test_demo_records_are_labelled():
    for o in get("demo").discover(limit=20):
        assert o.is_demo is True
        assert o.title.startswith("[DEMO]")


# -------------------------------------------------------------- rate extraction


@pytest.mark.parametrize(
    "text,lo,hi,kind",
    [
        ("paying $45-70 USD per hour for this", 45.0, 70.0, "HOURLY"),
        ("rate is $120/hr", 120.0, 120.0, "HOURLY"),
        ("budget is $500 for the project", 500.0, 500.0, "FIXED"),
        ("we pay $150,000 per year", None, None, "UNKNOWN"),
        ("no compensation mentioned here", None, None, "UNKNOWN"),
    ],
)
def test_rate_extraction(text, lo, hi, kind):
    from aicc.connectors.base import extract_rate

    assert extract_rate(text) == (lo, hi, kind)


def test_annual_salary_is_not_treated_as_a_project_budget():
    from aicc.connectors.feeds import _annual_to_hourly

    lo, hi = _annual_to_hourly(150_000, 200_000)
    assert 60 < lo < 80 and 90 < hi < 105


# ------------------------------------------------------------------ analytics


def test_insufficient_data_is_not_zero():
    m = analytics.compute_metrics()
    assert m["win_rate"] is None, "an unknown win rate must be None, not 0"
    assert m["avg_qa_score"] is None
    assert analytics.fmt(m["win_rate"]) == "Insufficient Data"


def test_win_rate_needs_enough_observations():
    from aicc.models import Opportunity, OpportunityStatus

    for i in range(3):
        storage.opportunities.put(Opportunity(external_id=f"w{i}", status=OpportunityStatus.WON.value))
    assert analytics.compute_metrics()["win_rate"] is None, "3 outcomes is not a rate"

    for i in range(3):
        storage.opportunities.put(Opportunity(external_id=f"l{i}", status=OpportunityStatus.LOST.value))
    assert analytics.compute_metrics()["win_rate"] == 50.0


def test_demo_revenue_is_excluded_from_real_totals():
    real = RevenueEntry(gross=100.0, is_demo=False)
    real.compute_net()
    fake = RevenueEntry(gross=9999.0, is_demo=True)
    fake.compute_net()
    storage.revenue.put(real)
    storage.revenue.put(fake)
    assert storage.real_revenue_entries() == [r for r in storage.revenue.all() if not r.is_demo]
    assert analytics.compute_metrics(include_demo=False)["revenue_gross_total"] == 100.0


def test_metrics_carry_provenance():
    m = analytics.compute_metrics()
    prov = analytics.describe_metrics(m)
    for key in ("win_rate", "avg_qa_score", "revenue_net_total"):
        assert prov[key]["formula"]
        assert prov[key]["source"]
        assert "computed_at" in prov[key]


def test_calibration_refuses_to_tune_on_noise():
    cal = analytics.scoring_calibration()
    assert cal["status"] == "Insufficient Data"
    assert cal["recommendation"]


# ------------------------------------------------------------------ dashboard


def test_dashboard_builds_and_verifies(tmp_path, active_system):
    from aicc.dashboard.build import build_site, verify_site

    build_site(tmp_path / "site")
    ok, report = verify_site(tmp_path / "site")
    assert ok, [c for c in report["checks"] if not c["passed"]]


def test_dashboard_is_self_contained(tmp_path, active_system):
    from aicc.dashboard.build import build_site

    html = build_site(tmp_path / "site").read_text(encoding="utf-8")
    assert "<script src=" not in html, "no external scripts"
    assert "window.DATA=" in html


def test_dashboard_never_shows_green_without_evidence(tmp_path, active_system):
    from aicc.dashboard.build import build_site

    build_site(tmp_path / "site")
    data = json.loads((tmp_path / "site" / "data.json").read_text(encoding="utf-8"))
    for cap in data["capabilities"]:
        if cap["light"] == "GREEN":
            assert cap["detail"], f"{cap['label']} is green with no supporting detail"


def test_dashboard_reports_zero_added_cost(tmp_path, active_system):
    from aicc.dashboard.build import build_site

    build_site(tmp_path / "site")
    data = json.loads((tmp_path / "site" / "data.json").read_text(encoding="utf-8"))
    assert data["cost"]["ceiling"] == 0.0


# ------------------------------------------------------------- full lifecycle


def test_full_demo_lifecycle_passes():
    from aicc.demo_lifecycle import run_full_lifecycle

    assert run_full_lifecycle(verbose=False) == 0


# ------------------------------------------------- the approval card must support its decision


def test_an_approval_card_says_what_it_is_asking_about() -> None:
    """Every card previously read "Proposal awaiting your approval" with no indication of WHICH
    opportunity, no score, and no way to read what would be sent. On a phone that is a one-click
    approval for something unread - the same fake autonomy this system refuses everywhere else,
    pointed the other way."""
    from aicc import proposals as proposals_mod
    from aicc import storage
    from aicc.connectors.base import make_opportunity
    from aicc.dashboard.build import _attention
    from aicc.scoring import score_opportunity

    # Seeded rather than read from the live store. An earlier version of this test skipped
    # whenever the store happened to hold no pending proposal, which meant the assertion it
    # exists to make - that an approval card is readable before it is approved - silently
    # stopped running exactly when the store was empty. A skipped check is not a passing check.
    opp = make_opportunity(
        source="hackernews",
        title="Acme Analytics - consolidate 40 monthly CSV exports into one reporting workbook",
        description=(
            "Freelance project. We have roughly forty monthly CSV exports with inconsistent "
            "headers and date formats. We need them consolidated into a single Excel workbook "
            "with a summary sheet. Python preferred. Fixed price, one-time project."
        ),
        skills=["python", "pandas", "excel"],
        budget_min=600,
        budget_max=900,
    )
    score_opportunity(opp)
    storage.opportunities.put(opp)
    proposal = proposals_mod.generate(opp)
    storage.proposals.put(proposal)

    cards = [c for c in _attention(storage.jobs.all(), storage.proposals.all()) if c.get("body")]
    assert cards, "No approval card carried the proposal body."
    for card in cards:
        assert card["title"] != "Proposal awaiting your approval", "The title must name the opportunity."
        assert card["body"], "He must be able to read what would be sent before approving it."
        assert "meta" in card


def test_a_partial_fit_is_flagged_on_the_card_where_the_decision_is_made() -> None:
    """A listing Andres only partly fits must say so on the card, not only in the score.

    Seeded, for the same reason as the test above: this previously keyed on one specific live
    listing by name, so it stopped running the moment that listing aged out of the store - which
    is precisely when a silent regression would have gone unnoticed.
    """
    from aicc import proposals as proposals_mod
    from aicc import scoring, storage
    from aicc.connectors.base import make_opportunity
    from aicc.dashboard.build import _attention

    opp = make_opportunity(
        source="hackernews",
        title="Noricum Data - contract engineer for a payments ledger",
        description=(
            "Contract, project-based. Python and SQL. Must have shipped: a double-entry ledger "
            "or equivalent money system in production, and a payment integration including "
            "webhook idempotency. Kubernetes experience required."
        ),
        skills=["python", "sql"],
        budget_min=4000,
        budget_max=6000,
    )
    scoring.score_opportunity(opp)
    storage.opportunities.put(opp)
    storage.proposals.put(proposals_mod.generate(opp))

    cards = _attention(storage.jobs.all(), storage.proposals.all())
    match = [c for c in cards if "Noricum" in c["title"]]
    assert match, "The seeded proposal did not produce an approval card."
    assert match[0]["caveat"], "A known requirements gap must be visible at the point of approval."


def test_requirement_phrases_are_tidy_wherever_a_human_reads_them() -> None:
    """Same defect, two places: the proposal text and the scoring evidence on the card."""
    from aicc.connectors.base import make_opportunity
    from aicc.scoring import score_opportunity

    text = (
        "Senior backend engineer. Python and SQL. "
        * 8
        + "Must have shipped: a double-entry ledger or equivalent money system in production, "
        "and a payment integration including webhook idempotency."
    )
    o = make_opportunity(source="hackernews", title="Role", description=text, skills=["python", "sql"])
    score_opportunity(o)
    for pen in o.score_breakdown.get("penalties", []):
        if pen["name"] == "Unmet stated requirements":
            assert '"shipped:' not in pen["evidence"], f"Mid-clause phrase reached the card: {pen['evidence']}"
            assert "a double-entry ledger" in pen["evidence"]


# ------------------------------------------------- the real-order chain, end to end


def test_a_real_order_travels_the_whole_chain(active_system):
    """Intake to revenue, on the path a real Fiverr order takes.

    `FiverrConnector.import_order` was documented as THE path from an order notification into the
    pipeline and had no callers. `pipeline.run` and `pipeline.deliver` had none either outside the
    demo. Every piece was verified and the front door did not exist - a buyer ordering would have
    left Andres working by hand next to a fulfillment system he could not put the order into.

    Runs the rule-based worker so the test costs no subscription capacity. What it proves is the
    chain and its gates, not the AI pass, which `worker-proof` proves on a runner.
    """
    from aicc import order_intake, storage
    from aicc.fulfillment import pipeline
    from aicc.fulfillment.worker import RuleBasedWorker
    from aicc.models import Actor, JobStatus

    # ORDER RECEIVED -> REQUIREMENTS CHECK -> CAPACITY CHECK -> ACCEPT
    verdict = order_intake.intake(
        order_id="FO123456",
        buyer="a_real_buyer",
        gig_title="I will clean and consolidate your messy excel or csv data",
        price=75.0,
        requirements=[
            "Three CSV exports, attached",
            "One clean output with a consistent date format",
            "Never drop a row - flag anything that fails",
        ],
        worker_minutes=120.0,
        job_type="spreadsheet",
        actor="SYSTEM",
    )
    assert verdict.decision == "ACCEPTED", f"{verdict.capacity_status}: {verdict.capacity_reason}"
    assert verdict.requirements_ok
    job = verdict.job
    assert job is not None
    assert job.status == JobStatus.RECEIVED.value
    assert storage.jobs.get(job.id) is not None, "an accepted order must be in the store"

    # WORKER -> REVIEWER -> REVISION -> FINAL QA -> READY_TO_DELIVER
    job = pipeline.run(job, worker_cls=RuleBasedWorker)
    assert job.qa_rounds, "the reviewer never ran"
    assert job.status in (JobStatus.READY_TO_DELIVER.value, JobStatus.PROBLEM.value)

    if job.status == JobStatus.PROBLEM.value:
        return  # a rule-based deliverable may legitimately need a human; the chain still held

    # NEEDS ANDRES: delivery refuses a non-human actor.
    refused, msg = pipeline.deliver(job, actor=Actor.CLAUDE)
    assert not refused and "human" in msg.lower()
    assert job.status == JobStatus.READY_TO_DELIVER.value

    # DELIVERY -> REVENUE RECORDING
    ok, _ = pipeline.deliver(job, actor=Actor.ANDRES, approved_by="ANDRES")
    assert ok
    entry = order_intake.record_revenue(job, actor="ANDRES", human_minutes=20.0)
    assert entry.gross == 75.0
    assert entry.net == 60.0, "Fiverr takes 20%"
    assert entry.ai_cash_cost == 0.0
    assert not entry.is_demo, "a real payment must count toward REAL REVENUE"
    assert entry.id in {r.id for r in storage.real_revenue_entries()}


def test_a_thin_brief_escalates_instead_of_starting_work(active_system):
    """The order clock runs from acceptance, so a brief nobody can satisfy has to stop at intake.
    Asking the buyer is free; guessing produces a revision at best."""
    from aicc import order_intake
    from aicc.models import JobStatus

    verdict = order_intake.intake(
        order_id="FO999",
        buyer="terse_buyer",
        gig_title="I will clean and consolidate your messy excel or csv data",
        price=30.0,
        requirements=["fix my sheet"],
        worker_minutes=45.0,
        actor="SYSTEM",
    )
    assert verdict.decision == "ESCALATE"
    assert not verdict.requirements_ok
    assert verdict.job is not None
    assert verdict.job.status == JobStatus.RECEIVED.value
    assert verdict.job.human_action_required, "escalation must say what a person has to do"


def test_intake_never_guesses_a_workload(active_system):
    """capacity.pre_job_check returns UNKNOWN for a zero estimate rather than inventing one, and
    intake must escalate on that rather than accepting work it cannot size."""
    from aicc import order_intake

    verdict = order_intake.intake(
        order_id="FO777",
        buyer="buyer",
        gig_title="I will clean and consolidate your messy excel or csv data",
        price=75.0,
        requirements=["a", "b", "c"],
        worker_minutes=0.0,
        actor="SYSTEM",
    )
    assert verdict.decision == "ESCALATE"
    assert verdict.capacity_status.startswith("UNKNOWN")


def test_an_escalated_order_does_not_reserve_capacity(active_system):
    """Reserving against work that may never start would hold capacity away from work that will."""
    from aicc import capacity, order_intake

    before = len(capacity.active_reservations())
    order_intake.intake(
        order_id="FO888",
        buyer="buyer",
        gig_title="I will clean and consolidate your messy excel or csv data",
        price=30.0,
        requirements=["too thin"],
        worker_minutes=45.0,
        actor="SYSTEM",
    )
    assert len(capacity.active_reservations()) == before


class TestTheReviewerCannotPassNothing:
    """Found by running a real Gig 4 order end to end, not by reading the code.

    Twice the pipeline reported READY_TO_DELIVER and invited a human to send the result to a
    paying buyer. The first time the deliverable was a 622-byte markdown file restating the
    buyer's own requirements back at them - scored 100.0/100. The second time it was a 14-byte
    `consolidated.csv`: one header, zero rows, "consolidated 0 source file(s)" - scored 95.6/100.

    Both passed because nothing had an opinion. The generic branch runs no type-specific QA, and
    the spreadsheet branch only compares row counts when an expectation is supplied. A QA gate
    that cannot tell zero rows from finished work manufactures confidence in nothing, which is
    worse than having no gate.
    """

    def test_a_csv_with_no_data_rows_is_never_acceptable(self, tmp_path):
        from aicc.fulfillment import reviewer

        out = tmp_path / "consolidated.csv"
        out.write_text("_source_file\n", encoding="utf-8")  # exactly what the worker produced
        scores, findings = reviewer.qa_spreadsheet(out)  # no source, no expected count
        assert scores["completeness"] == 0.0
        assert scores["accuracy"] == 0.0
        assert any(f["check"] == "not_empty" and f["severity"] == "critical" for f in findings)

    def test_a_deliverable_that_admits_it_is_a_scaffold_is_refused(self, tmp_path):
        from aicc.fulfillment import reviewer

        art = tmp_path / "deliverable.md"
        art.write_text(
            "# Job\n\n## Notes\n\nProduced by the rule-based worker. No AI credential was "
            "available for this run, so this is a structured scaffold rather than completed "
            "analytical work.\n",
            encoding="utf-8",
        )
        report = reviewer.review(
            job_id="j1",
            job_type="spreadsheet",
            requirements=["Clean the file"],
            acceptance_criteria=["Clean the file"],
            artifacts=[art],
        )
        assert report.verdict != "READY", f"a self-declared scaffold scored {report.overall_score}"
        assert any(f["check"] == "not_a_scaffold" for f in report.findings)

    def test_restating_the_brief_is_not_doing_the_work(self, tmp_path):
        from aicc.fulfillment import reviewer

        reqs = [
            "Consolidate and clean the attached CSV file into one output",
            "Two rows are the same record when customer name and email match",
        ]
        art = tmp_path / "deliverable.md"
        art.write_text(
            "# Job\n\n## Requirements addressed\n\n- " + "\n- ".join(reqs) + "\n\n## Acceptance criteria\n\n- " + "\n- ".join(reqs) + "\n",
            encoding="utf-8",
        )
        report = reviewer.review(
            job_id="j2",
            job_type="unknown_type",
            requirements=reqs,
            acceptance_criteria=reqs,
            artifacts=[art],
        )
        assert report.verdict != "READY", f"an echo of the brief scored {report.overall_score}"

    def test_real_work_still_passes(self, tmp_path):
        """The gate must not fire on a genuine deliverable, or it is just an outage."""
        from aicc.fulfillment import reviewer

        out = tmp_path / "invoices_clean.csv"
        out.write_text(
            "customer_name,email,amount_due,invoice_date\n"
            "Jose Kim,jose.kim@example.com,8923.00,2025-08-11\n"
            "Sara Silva,sara.silva@example.com,-301.00,2023-01-11\n",
            encoding="utf-8",
        )
        scores, findings = reviewer.qa_spreadsheet(out)
        assert scores["completeness"] == 100.0
        assert not any(f["check"] == "not_empty" for f in findings)
