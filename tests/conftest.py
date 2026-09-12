"""Test fixtures.

Every test runs against an isolated data directory. The modules read their paths from
``aicc.config`` at import time, so the fixture rebinds those module-level paths rather than
setting an environment variable after the fact.
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from aicc import config, storage

    data = tmp_path / "data"
    workspaces = tmp_path / "workspaces"
    for d in (data, workspaces):
        d.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(config, "DATA_DIR", data)
    monkeypatch.setattr(config, "WORKSPACE_ROOT", workspaces)
    monkeypatch.setattr(config, "STATE_FILE", data / "system_state.json")
    monkeypatch.setattr(config, "AUDIT_LOG", data / "audit_log.jsonl")
    monkeypatch.setattr(config, "COST_REQUESTS", data / "cost_requests.jsonl")
    monkeypatch.setattr(config, "RUNS_DIR", data / "runs")
    monkeypatch.setattr(config, "OPPORTUNITIES_DIR", data / "opportunities")
    monkeypatch.setattr(config, "PROPOSALS_DIR", data / "proposals")
    monkeypatch.setattr(config, "JOBS_DIR", data / "jobs")
    monkeypatch.setattr(config, "LEDGER_DIR", data / "ledger")

    # Modules that captured a path at import time.
    from aicc import audit as audit_mod
    from aicc import state as state_mod
    from aicc.fulfillment import worker as worker_mod

    monkeypatch.setattr(audit_mod, "AUDIT_LOG", data / "audit_log.jsonl")
    monkeypatch.setattr(state_mod, "STATE_FILE", data / "system_state.json")
    monkeypatch.setattr(worker_mod, "WORKSPACE_ROOT", workspaces)

    from aicc import capacity as capacity_mod
    from aicc import fiverr_kit as fiverr_mod

    monkeypatch.setattr(capacity_mod, "CAPACITY_FILE", data / "capacity.json")
    monkeypatch.setattr(fiverr_mod, "STATUS_FILE", data / "fiverr_status.json")

    from aicc import worker_proof as worker_proof_mod

    monkeypatch.setattr(worker_proof_mod, "PROOF_FILE", data / "worker_proof.json")

    monkeypatch.setattr(storage, "OPPORTUNITIES_FILE", data / "opportunities.json")
    monkeypatch.setattr(storage, "OPPORTUNITIES_ARCHIVE", data / "opportunities_archive.json")
    monkeypatch.setattr(storage, "PROPOSALS_FILE", data / "proposals.json")
    monkeypatch.setattr(storage, "JOBS_FILE", data / "jobs.json")
    monkeypatch.setattr(storage, "REVENUE_FILE", data / "revenue.json")
    monkeypatch.setattr(storage, "DATA_DIR", data)
    for coll, path in [
        (storage.opportunities, data / "opportunities.json"),
        (storage.opportunities_archive, data / "opportunities_archive.json"),
        (storage.proposals, data / "proposals.json"),
        (storage.jobs, data / "jobs.json"),
        (storage.revenue, data / "revenue.json"),
    ]:
        monkeypatch.setattr(coll, "path", path)

    return data


@pytest.fixture
def active_system():
    """A system in ACTIVE state, which most pipeline operations require."""
    from aicc import state

    state.start()
    return state.SystemState.load()


@pytest.fixture
def sample_opportunity():
    from aicc.models import BudgetType, Opportunity

    return Opportunity(
        source="hackernews",
        external_id="test_1",
        title="Consolidate monthly sales spreadsheets into one reporting workbook",
        description=(
            "We have 14 monthly Excel exports with slightly different column names. We need them "
            "consolidated into one clean workbook with a normalized schema and a summary tab. "
            "Deliverables: one xlsx, a summary tab, and a README describing the column mapping. "
            "Requirements: preserve every original row, flag rows that fail to map rather than "
            "dropping them, keep currency formatting consistent. We would like the mapping logic "
            "as a Python script so we can re-run it next quarter. Scope is fixed, timeline is one "
            "week. Please tell us how you would handle the duplicate order IDs in Q3. This is a "
            "straightforward data cleaning and consolidation task using pandas or similar."
        ),
        client="Test Client Ltd",
        budget_min=400.0,
        budget_max=600.0,
        budget_type=BudgetType.FIXED.value,
        skills=["excel", "python", "data cleaning"],
    )
