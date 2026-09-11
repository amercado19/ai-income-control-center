"""What to do when the AI is unavailable mid-run (spec section 44).

The zero-cost design runs the AI worker on a **Claude subscription**, not metered API billing.
That is what makes it free, and it is also the thing that makes this module necessary: a
subscription has usage windows, and an unattended GitHub Actions run can walk into one at any
time. It then has to answer a question the run cannot ask a human: is this broken, or is it
simply not now?

Those two look identical in a red workflow badge and they are completely different facts. A
credential that has been revoked needs attention today. A five-hour usage window needs nothing
at all except waiting - and a system that cries wolf about it teaches its owner to ignore red
badges, which is the expensive failure.

So failures are classified, and each class carries a decision:

``RETRY_LATER``   the capacity will come back on its own. Do not fail the run; defer the work
                  and say when to try again.
``DEGRADE``       proceed now with the rule-based worker, and label the output as such.
``NEEDS_HUMAN``   nothing will fix this without a person - a revoked or expired token.
``FAIL``          an unrecognised error. Surface it loudly rather than guessing.

One rule has no exceptions and is enforced by a test: **exhausted subscription capacity never
falls back to a paid API key.** ``ANTHROPIC_API_KEY`` is metered billing, and reaching for it
the moment the free path runs dry is precisely how a $0.00 system starts costing money - at the
worst possible moment, unattended, with nobody watching the meter.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum


class Action(StrEnum):
    RETRY_LATER = "RETRY_LATER"
    DEGRADE = "DEGRADE"
    NEEDS_HUMAN = "NEEDS_HUMAN"
    FAIL = "FAIL"


@dataclass(frozen=True)
class Decision:
    action: Action
    reason: str
    """Plain-language explanation, written to be read in a workflow summary."""
    retry_after_seconds: int | None = None
    human_message: str = ""
    """Non-empty only when a person genuinely has to do something."""

    @property
    def should_fail_the_run(self) -> bool:
        """A red badge is a claim on someone's attention. Spend it only when it is warranted."""
        return self.action in (Action.NEEDS_HUMAN, Action.FAIL)

    def to_dict(self) -> dict[str, object]:
        return {
            "action": self.action.value,
            "reason": self.reason,
            "retry_after_seconds": self.retry_after_seconds,
            "human_message": self.human_message,
            "fails_run": self.should_fail_the_run,
        }


# Anthropic's own wording, plus the shapes Claude Code surfaces. Matched against the combined
# message and any status code, lowercased.
_RATE_LIMIT = re.compile(
    r"rate[ _-]?limit|usage limit|too many requests|quota exceeded|capacity|overloaded|"
    r"limit reached|out of (?:credits|capacity)|try again (?:later|in)|resets? at|429",
    re.I,
)
_AUTH_FAILURE = re.compile(
    r"invalid[ _-]?(?:token|credential|api[ _-]?key)|unauthor|forbidden|authentication|"
    r"expired[ _-]?token|token (?:has )?expired|revoked|401|403",
    re.I,
)
_TRANSIENT = re.compile(r"timeout|timed out|connection reset|temporarily unavailable|502|503|504", re.I)

# A subscription usage window. Anthropic's consumer plans reset on a rolling five-hour window,
# so this is the default wait when the error does not name one itself.
DEFAULT_WINDOW_SECONDS = 5 * 60 * 60
TRANSIENT_RETRY_SECONDS = 15 * 60

_RETRY_AFTER = re.compile(r"retry[- _]?after[\"':= ]+(\d+)", re.I)
_RESETS_IN = re.compile(r"(?:resets?|available|try again) in (\d+)\s*(second|minute|hour)s?", re.I)


def _stated_wait(text: str) -> int | None:
    """Prefer the provider's own number over our guess, when it gives one."""
    m = _RETRY_AFTER.search(text)
    if m:
        return int(m.group(1))
    m = _RESETS_IN.search(text)
    if m:
        value, unit = int(m.group(1)), m.group(2).lower()
        return value * {"second": 1, "minute": 60, "hour": 3600}[unit]
    return None


def classify(error: str | BaseException, *, status_code: int | None = None, has_credential: bool = True) -> Decision:
    """Decide what an AI failure means and what should happen next.

    ``has_credential`` distinguishes "never configured" from "configured and failing", which are
    different problems with different fixes and should never share a message.
    """
    if not has_credential:
        return Decision(
            action=Action.DEGRADE,
            reason="No Claude credential is configured, so the rule-based worker runs instead.",
            human_message=(
                "Set the CLAUDE_CODE_OAUTH_TOKEN repository secret to enable the AI worker. "
                "Run `claude setup-token` locally; it uses your subscription, not paid API billing."
            ),
        )

    text = f"{error} {status_code or ''}".strip().lower()
    if not text:
        return Decision(action=Action.FAIL, reason="An AI failure was reported with no detail, which is itself the problem.")

    # Auth is checked first. A revoked token can produce a 429-shaped message on some paths, and
    # misreading it as a rate limit would mean waiting five hours for something that will never
    # come back on its own.
    if _AUTH_FAILURE.search(text) or status_code in (401, 403):
        return Decision(
            action=Action.NEEDS_HUMAN,
            reason="The Claude credential was rejected. This does not recover on its own.",
            human_message=(
                "CLAUDE_CODE_OAUTH_TOKEN is invalid, expired or revoked. Tokens from "
                "`claude setup-token` last one year and do NOT auto-refresh. Generate a new one "
                "and update the repository secret. Do not substitute an API key - that is paid billing."
            ),
        )

    if _RATE_LIMIT.search(text) or status_code == 429:
        return Decision(
            action=Action.RETRY_LATER,
            reason=(
                "Claude subscription capacity is exhausted for now. This is a usage window, not a "
                "fault: it returns on its own, and nothing needs doing."
            ),
            retry_after_seconds=_stated_wait(text) or DEFAULT_WINDOW_SECONDS,
        )

    if _TRANSIENT.search(text) or (status_code or 0) >= 500:
        return Decision(
            action=Action.RETRY_LATER,
            reason="A transient network or upstream failure. Worth one more attempt shortly.",
            retry_after_seconds=TRANSIENT_RETRY_SECONDS,
        )

    return Decision(
        action=Action.FAIL,
        reason=f"Unrecognised AI failure, surfaced rather than guessed at: {str(error)[:200]}",
        human_message="This error is not one the degradation rules know. Read the run log before assuming it is harmless.",
    )


def workflow_summary(decision: Decision) -> str:
    """A GitHub step-summary block. The wording is the point: it must not read as breakage."""
    if decision.action is Action.RETRY_LATER:
        wait = decision.retry_after_seconds or DEFAULT_WINDOW_SECONDS
        hours = wait / 3600
        when = f"{hours:.0f}h" if hours >= 1 else f"{wait // 60}m"
        return (
            f"### AI worker: PAUSED (capacity), retry in ~{when}\n\n{decision.reason}\n\n"
            "The run did not fail. Work that needed the AI is deferred, not lost, and no output "
            "was fabricated in its place."
        )
    if decision.action is Action.DEGRADE:
        return f"### AI worker: DEGRADED to rule-based\n\n{decision.reason}\n\n{decision.human_message}"
    if decision.action is Action.NEEDS_HUMAN:
        return f"### AI worker: NEEDS YOU\n\n{decision.reason}\n\n{decision.human_message}"
    return f"### AI worker: FAILED\n\n{decision.reason}\n\n{decision.human_message}"


def never_falls_back_to_paid(decision: Decision) -> bool:
    """The invariant, exposed so a test can assert it rather than trusting the prose above."""
    blob = f"{decision.reason} {decision.human_message}".lower()
    encourages_paid = "anthropic_api_key" in blob and "do not" not in blob and "not paid" not in blob
    return not encourages_paid
