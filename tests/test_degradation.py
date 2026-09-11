"""AI degradation: a usage window is not a fault, and running dry never starts costing money."""

from __future__ import annotations

import pytest

from aicc.degradation import Action, classify, never_falls_back_to_paid, workflow_summary

RATE_LIMITED = [
    "Rate limit exceeded",
    "429 Too Many Requests",
    "You have reached your usage limit for this 5 hour window",
    "Claude is currently overloaded, please try again later",
    "quota exceeded for this plan",
]

AUTH_FAILED = [
    "401 Unauthorized",
    "invalid api key provided",
    "OAuth token has expired",
    "Authentication failed: token revoked",
]


@pytest.mark.parametrize("message", RATE_LIMITED)
def test_capacity_exhaustion_waits_rather_than_failing(message: str) -> None:
    d = classify(message)
    assert d.action is Action.RETRY_LATER
    assert d.retry_after_seconds and d.retry_after_seconds > 0
    assert not d.should_fail_the_run, "A usage window must not spend a red badge."
    assert not d.human_message, "Nothing for a person to do means nothing said to them."


@pytest.mark.parametrize("message", AUTH_FAILED)
def test_a_rejected_credential_asks_for_a_human(message: str) -> None:
    d = classify(message)
    assert d.action is Action.NEEDS_HUMAN
    assert d.should_fail_the_run
    assert d.human_message


def test_auth_failure_wins_over_a_rate_limit_shaped_message() -> None:
    """A revoked token can surface as a 429 on some paths.

    Reading that as a usage window would mean waiting five hours for capacity that is never
    coming back, so the auth check deliberately runs first.
    """
    d = classify("429: token revoked, too many requests", status_code=429)
    assert d.action is Action.NEEDS_HUMAN


def test_a_stated_wait_beats_our_default() -> None:
    assert classify("rate limited; retry-after: 900").retry_after_seconds == 900
    assert classify("usage limit reached, try again in 2 hours").retry_after_seconds == 7200
    assert classify("rate limit exceeded, resets in 45 minutes").retry_after_seconds == 2700


def test_missing_credential_degrades_and_explains_the_free_route() -> None:
    d = classify("", has_credential=False)
    assert d.action is Action.DEGRADE
    assert not d.should_fail_the_run
    assert "setup-token" in d.human_message
    assert "subscription" in d.human_message.lower()


def test_transient_failures_retry_soon_rather_than_waiting_a_window() -> None:
    d = classify("connection reset by peer")
    assert d.action is Action.RETRY_LATER
    assert d.retry_after_seconds is not None
    assert d.retry_after_seconds < 3600, "A dropped connection is not a five-hour usage window."
    assert classify("503 Service Unavailable", status_code=503).action is Action.RETRY_LATER


def test_an_unrecognised_error_is_surfaced_not_guessed_at() -> None:
    d = classify("ValueError: unexpected schema in tool response")
    assert d.action is Action.FAIL
    assert d.should_fail_the_run
    assert "Unrecognised" in d.reason


def test_an_empty_error_is_itself_a_failure() -> None:
    assert classify("").action is Action.FAIL


# ------------------------------------------------------------------ the money invariant


@pytest.mark.parametrize("message", RATE_LIMITED + AUTH_FAILED + ["", "weird unknown error"])
def test_running_dry_never_reaches_for_paid_billing(message: str) -> None:
    """The whole system is $0.00. Falling back to metered billing the moment the free path
    runs dry - unattended, with nobody watching the meter - is the exact failure the cost gate
    exists to prevent, and it would happen at the worst possible time."""
    d = classify(message) if message else classify(message, has_credential=False)
    assert never_falls_back_to_paid(d)
    assert d.action is not Action.DEGRADE or "api key" not in d.reason.lower()


def test_the_expired_token_message_explicitly_warns_against_an_api_key() -> None:
    d = classify("OAuth token has expired")
    assert "do not substitute an api key" in d.human_message.lower()


# ------------------------------------------------------------------ how it reads


def test_a_paused_run_does_not_read_as_breakage() -> None:
    text = workflow_summary(classify("usage limit reached"))
    assert "PAUSED" in text
    assert "did not fail" in text
    assert "fabricated" in text, "It must say output was not invented in the AI's place."
    for alarming in ("ERROR", "BROKEN", "FAILED"):
        assert alarming not in text.upper().replace("NOT FAIL", "")


def test_each_action_produces_a_distinguishable_summary() -> None:
    seen = {
        workflow_summary(classify("usage limit reached")),
        workflow_summary(classify("", has_credential=False)),
        workflow_summary(classify("401 unauthorized")),
        workflow_summary(classify("ValueError: nonsense")),
    }
    assert len(seen) == 4


def test_decision_serialises_for_the_dashboard() -> None:
    d = classify("rate limit exceeded").to_dict()
    assert d["action"] == "RETRY_LATER"
    assert d["fails_run"] is False
    assert d["retry_after_seconds"]
