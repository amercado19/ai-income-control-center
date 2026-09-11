"""The self-test must actually detect violations, not just report green.

A safety report that cannot fail is worse than no report, because it gets acted on. So these
tests do two things: confirm the checks pass against the real system, and then break each
invariant deliberately and confirm the corresponding check notices.
"""

from __future__ import annotations

import pytest

from aicc import selftest


def test_every_check_passes_against_the_live_system() -> None:
    report = selftest.run_all()
    assert report.ok, [f"{c.name}: {c.detail}" for c in report.failed]
    assert len(report.passed) == len(selftest.CHECKS)


def test_every_check_says_what_would_be_true_if_it_failed() -> None:
    """The consequence, not the mechanism. Someone reads this deciding whether to leave it running."""
    for name, invariant, _fn in selftest.CHECKS:
        assert invariant, f"{name} states no consequence"
        assert len(invariant) > 25, f"{name}: {invariant!r} is too thin to act on"


# ------------------------------------------------------------------ the checks must bite


def test_cost_gate_check_fails_when_the_ceiling_is_raised(monkeypatch: pytest.MonkeyPatch) -> None:
    from aicc import config

    monkeypatch.setattr(config, "MAX_NEW_MONTHLY_CASH_SPEND", 25.0)
    ok, detail = selftest._check_cost_gate_fails_closed()
    assert not ok
    assert "25.00" in detail


def test_cost_gate_override_check_fails_when_an_override_is_added(monkeypatch: pytest.MonkeyPatch) -> None:
    from aicc import config

    class Permissive:
        def request(self, req, force: bool = False):  # noqa: ANN001, ANN201, FBT001, FBT002
            return None

    monkeypatch.setattr(config, "CostGate", Permissive)
    ok, detail = selftest._check_cost_gate_has_no_override()
    assert not ok
    assert "force" in detail


def test_reviewer_independence_check_fails_when_the_reviewer_takes_a_job(monkeypatch: pytest.MonkeyPatch) -> None:
    from aicc.fulfillment import reviewer
    from aicc.models import Job

    def leaky_review(job: Job, **kwargs):  # noqa: ANN003, ANN202
        return None

    monkeypatch.setattr(reviewer, "review", leaky_review)
    ok, detail = selftest._check_reviewer_cannot_see_the_job()
    assert not ok
    assert "Job" in detail


def test_redaction_check_fails_when_redaction_is_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    from aicc import privacy

    monkeypatch.setattr(privacy, "sanitize_for_storage", lambda text: (text, 0))
    ok, detail = selftest._check_contact_details_are_redacted()
    assert not ok
    assert "survived" in detail or "did not see" in detail


def test_injection_check_fails_when_the_scanner_goes_blind(monkeypatch: pytest.MonkeyPatch) -> None:
    from aicc import untrusted

    # `suspicious` is derived from findings, not a field, so a blind scanner is simply one that
    # reports nothing - which is exactly how this would fail in practice if a pattern regressed.
    monkeypatch.setattr(untrusted, "scan_for_injection", lambda text: untrusted.InjectionScan())
    ok, detail = selftest._check_injection_tripwire_fires()
    assert not ok
    assert "not flagged" in detail


def test_insufficient_data_check_fails_on_a_rate_computed_from_noise(monkeypatch: pytest.MonkeyPatch) -> None:
    from aicc import analytics

    monkeypatch.setattr(analytics, "compute_metrics", lambda **kw: {"win_rate": 100.0, "decided_outcomes": 1})
    ok, detail = selftest._check_insufficient_data_is_not_zero()
    assert not ok
    assert "noise" in detail


def test_a_check_that_raises_is_reported_as_a_failure_not_a_crash() -> None:
    """A safety check that dies tells you nothing. Dying silently tells you something false."""

    def explodes() -> tuple[bool, str]:
        raise RuntimeError("boom")

    check = selftest._run("exploding check", "Something bad.", explodes)
    assert check.status == "FAIL"
    assert "RuntimeError" in check.detail


# ------------------------------------------------------------------ reporting


def test_skips_are_never_counted_as_passes() -> None:
    report = selftest.Report(
        checks=[
            selftest.Check("a", "PASS", "ok"),
            selftest.Check("b", "SKIPPED", "could not run"),
        ]
    )
    assert report.ok, "A skip should not fail the run..."
    assert len(report.passed) == 1, "...but it must not inflate the pass count either."
    assert len(report.skipped) == 1


def test_a_failure_report_says_not_to_leave_it_running() -> None:
    report = selftest.Report(checks=[selftest.Check("x", "FAIL", "broke", "Money could be spent.")])
    text = selftest.format_report(report)
    assert not report.ok
    assert "unattended" in text
    assert "Money could be spent." in text


def test_report_serialises_for_ci() -> None:
    d = selftest.run_all().to_dict()
    assert d["ok"] is True
    assert d["passed"] == len(selftest.CHECKS)
    assert all({"name", "status", "detail", "invariant"} <= set(c) for c in d["checks"])


# ------------------------------------------------------------------ actions budget


def _budget_module():
    import importlib.util
    import sys
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "actions_budget", Path(__file__).resolve().parent.parent / "scripts" / "actions_budget.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # Register before executing: @dataclass resolves its module through sys.modules, and a
    # module missing from there makes every dataclass in the file fail to build.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "expr,expected",
    [
        ("0 */6 * * *", 121.8),  # four times a day
        ("25 11 * * *", 30.4),  # once a day
        ("0 0 * * 1", 4.3),  # weekly
        ("0 * * * *", 730.6),  # hourly
    ],
)
def test_cron_estimates_are_in_the_right_order_of_magnitude(expr: str, expected: float) -> None:
    mod = _budget_module()
    assert mod.cron_runs_per_month(expr) == pytest.approx(expected, rel=0.02)


def test_a_malformed_cron_counts_as_zero_rather_than_guessing() -> None:
    mod = _budget_module()
    assert mod.cron_runs_per_month("not a cron") == 0.0
    assert mod.cron_runs_per_month("0 0 *") == 0.0


def test_reusable_workflows_are_not_double_counted() -> None:
    """A reusable workflow is billed under its caller. Counting it separately doubles every run."""
    mod = _budget_module()
    files = {w.file for w in mod.read_workflows()}
    assert not any(f.startswith("_") for f in files), files


def test_the_whole_schedule_fits_the_free_private_allowance() -> None:
    """It costs nothing while the repo is public. This guards the day it is not."""
    mod = _budget_module()
    total = sum(w.minutes_per_month for w in mod.read_workflows())
    assert total < mod.FREE_PRIVATE_MINUTES, f"{total:.0f} min/mo exceeds the {mod.FREE_PRIVATE_MINUTES} free private minutes."


# ------------------------------------------------- the live checklist has three states


def test_the_cron_authorship_item_is_unverifiable_not_failing() -> None:
    """It depends on who last touched a cron line on GitHub, which cannot be probed from here.

    Rendering an unverifiable condition as FAIL is the same dishonesty as a green light with
    nothing behind it, only pointed the other way - and it would permanently block live mode
    on something the system can never confirm.
    """
    from aicc.health import live_mode_checklist

    items = {i["name"]: i for i in live_mode_checklist()}
    cron = next((v for k, v in items.items() if "cron" in k.lower()), None)
    assert cron is not None, "The cron-authorship reminder is missing from the checklist."
    assert cron["passing"] is None, "It must be None (unverifiable), not False (failing)."
    assert "docs/DEPLOYMENT.md" in cron["detail"]


def test_every_other_checklist_item_is_a_real_boolean() -> None:
    """Only genuinely unprobeable conditions may be None. Everything else must commit."""
    from aicc.health import live_mode_checklist

    for item in live_mode_checklist():
        if item["passing"] is None:
            assert "not verifiable" in item["detail"].lower() or "cannot" in item["detail"].lower(), (
                f"{item['name']} is None but does not say why it cannot be checked."
            )
        else:
            assert isinstance(item["passing"], bool), f"{item['name']} has a non-boolean passing value."
