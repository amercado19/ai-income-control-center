"""Reading the safety panel must not change what it reports on.

`aicc compliance` proves the spending ceiling holds the only honest way: it attempts a charge
against the real gate and shows that it was refused. Every refusal is logged, so every read of the
panel appended a record byte-identical to the last one, and `git status` came back dirty after a
command that only looked at things.

That is worse than untidy. This project's rule for knowing whether work actually shipped is
`git diff --name-only origin/main`. A diagnostic that dirties the tree on every run makes that
check answer "yes, something is outstanding" when nothing is, and the habit that follows is
ignoring it.

The fix is `CostGate.probe()` - the same `_decide` as `request`, minus the probe's own log write.
It is a separate method rather than a flag because `test_cost_gate_has_no_override` pins
`request`'s signature to `(self, req)` so nobody can add a parameter to it, and that test is
right; a change that only fits by editing a safety assertion is the wrong change.

These tests pin the three things that matter - the probe still runs the real decision path, the
tree stays clean when it is read twice, and `probe` cannot be used to make spending invisible.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from aicc import compliance, config, selftest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _probe(monthly: float = 9.99) -> config.CostRequest:
    return config.CostRequest(
        service="test probe",
        reason="attempt a charge to prove the ceiling holds",
        monthly_estimate=monthly,
        benefit="none - this exists to be refused",
        can_continue_without=True,
    )


def _snapshot(root: Path) -> dict[str, str]:
    """Content hash of every file under `root`, keyed by relative path.

    Compares what persists, not mtimes - a file rewritten with identical bytes has not changed
    any state a later session could read.
    """
    out: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            out[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


class TestTheReadOnlyProbe:
    def test_a_refused_probe_writes_nothing(self):
        assert not config.COST_REQUESTS.exists()
        decision = config.CostGate().probe(_probe())
        assert not decision.approved
        assert not config.COST_REQUESTS.exists(), "a read-only probe left a footprint"

    def test_request_still_records_everything(self):
        """The real path is untouched. Nothing became silent."""
        config.CostGate().request(_probe())
        assert config.COST_REQUESTS.exists()
        assert len(config.COST_REQUESTS.read_text(encoding="utf-8").splitlines()) == 1

    def test_probe_and_request_reach_the_identical_verdict(self):
        """One `_decide`, so the panel cannot end up testing a copy of the gate."""
        loud = config.CostGate().request(_probe())
        quiet = config.CostGate().probe(_probe())
        assert loud.approved == quiet.approved is False
        assert loud.reason == quiet.reason
        assert loud.requires_human == quiet.requires_human is True

    def test_an_approval_is_recorded_even_through_the_probe(self):
        """The hole this method could have opened, closed.

        A $0 request is the one thing the gate approves. It is written whichever entrance it came
        through, so `probe` can never be the reason a spend went unlogged.
        """
        decision = config.CostGate().probe(_probe(monthly=0.0))
        assert decision.approved
        assert config.COST_REQUESTS.exists(), "an approved request was not recorded"
        written = json.loads(config.COST_REQUESTS.read_text(encoding="utf-8").splitlines()[0])
        assert written["approved"] is True

    def test_neither_entrance_takes_an_override_parameter(self):
        """`request` is pinned by test_safety. `probe` is new, so it is pinned here too."""
        import inspect

        banned = ("force", "override", "approve", "bypass", "allow", "skip")
        for method in (config.CostGate.request, config.CostGate.probe):
            params = set(inspect.signature(method).parameters)
            assert params == {"self", "req"}, f"{method.__name__}{tuple(params)}"
            assert not {p for p in params if any(w in p.lower() for w in banned)}


class TestDiagnosticsAreIdempotent:
    def test_reading_the_compliance_panel_twice_changes_no_persistent_state(self, isolated_data: Path):
        compliance.panel()
        before = _snapshot(isolated_data)
        compliance.panel()
        compliance.panel()
        assert _snapshot(isolated_data) == before

    def test_the_spending_lock_indicator_still_proves_itself(self):
        """Idempotence is worthless if it was bought by not probing."""
        ind = next(i for i in compliance.panel()["indicators"] if i["key"] == "spending_lock")
        assert ind["ok"] is True
        assert ind["value"] == "$0"
        assert "refused" in ind["detail"].lower()

    def test_running_the_selftest_twice_changes_no_persistent_state(self, isolated_data: Path):
        selftest.run_all()
        before = _snapshot(isolated_data)
        selftest.run_all()
        assert _snapshot(isolated_data) == before

    def test_the_selftest_cost_gate_checks_still_pass(self):
        """Idempotence is worthless if it was bought by not probing."""
        report = selftest.run_all()
        by_name = {c.name: c for c in report.passed + report.failed}
        for name in ("Cost gate fails closed", "Cost gate has no override"):
            assert by_name[name].status == "PASS", f"{name}: {by_name[name].detail}"


class TestTheCommandsThemselves:
    """The unit tests above patch module paths. This runs the real commands in a real process.

    It is the only form of this test that would have caught the original defect, because the
    defect was a command dirtying a directory - not a function returning the wrong value.
    """

    @staticmethod
    def _env(tmp_path: Path) -> dict[str, str]:
        return {
            **os.environ,
            "AICC_DATA_DIR": str(tmp_path / "data"),
            "AICC_WORKSPACE_ROOT": str(tmp_path / "workspaces"),
            "PYTHONPATH": str(REPO_ROOT / "src"),
        }

    @staticmethod
    def _run(command: list[str], env: dict[str, str]) -> None:
        # Exit status is not the point - some probes legitimately report a problem in a bare
        # directory. What is being tested is the footprint, not the verdict.
        subprocess.run(
            [sys.executable, "-m", "aicc", *command],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            timeout=180,
        )

    @pytest.mark.parametrize("command", [["compliance"], ["status"]])
    def test_a_second_run_leaves_the_data_directory_byte_identical(self, command, tmp_path: Path):
        data = tmp_path / "data"
        env = self._env(tmp_path)

        self._run(command, env)  # the first run may legitimately create files
        before = _snapshot(data) if data.exists() else {}
        self._run(command, env)
        after = _snapshot(data) if data.exists() else {}

        changed = {k for k in before.keys() | after.keys() if before.get(k) != after.get(k)}
        assert not changed, f"`aicc {' '.join(command)}` mutated {sorted(changed)} on a repeat read"

    def test_selftest_records_that_it_ran_but_not_a_duplicate_cost_request(self, tmp_path: Path):
        """`selftest` is deliberately not on the list above, and this is the line between them.

        "The safety selftest ran at 14:32 and 24 checks passed" is real history - it is the
        evidence that the system was verified before being left unattended, and CI reads it. A
        fourth byte-identical declined $9.99 request is not history, it is the probe's own
        footprint. So the audit entry stays and the cost-ledger line goes.

        If a future session sees this command dirty the tree and reaches for the audit write: it
        is not the same bug. Silencing it would delete a legitimate record.
        """
        data = tmp_path / "data"
        env = self._env(tmp_path)

        self._run(["selftest"], env)
        self._run(["selftest"], env)

        audit_lines = (data / "audit_log.jsonl").read_text(encoding="utf-8").splitlines()
        selftest_events = [ln for ln in audit_lines if json.loads(ln).get("action") == "safety_selftest"]
        assert len(selftest_events) == 2, "each run must leave exactly one record that it happened"

        cost_ledger = data / "cost_requests.jsonl"
        assert not cost_ledger.exists(), f"the selftest cost probes wrote {cost_ledger.name}; they are read-only and must not"
