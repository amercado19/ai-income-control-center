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
