"""Moving a worker proof across a trust boundary, as data rather than as a permission.

The problem this module exists for. The Claude worker workflow holds `CLAUDE_CODE_OAUTH_TOKEN`
and runs `claude -p` against a job brief - and briefs originate in marketplace listings, which
this project treats as untrusted external input everywhere else. The dashboard needs that
workflow's result. The lazy way to connect them is to grant the worker `contents: write` so it
can commit its own verdict, which would put credential handling, untrusted-text execution and
repository write authority in one job. A prompt that talked a worker into writing a file would
then be a prompt that writes to the repository.

So the proof crosses as an **artifact**, and a second workflow that holds no credential decides
whether to believe it:

    claude-worker.yml   contents: read   token, claude -p, writes an artifact, commits nothing
            |
            |  artifact: a JSON attestation, no secrets
            v
    health.yml          contents: write  no token, validates, writes data/worker_proof.json

The direction matters. The privileged-write side never runs the model and never sees the
credential; the credential side can never write. Neither half can be talked into doing the
other's job, because it does not have the permission to.

`validate` is the border guard, and it is written to distrust its input. The attestation is a
file an arbitrary workflow run uploaded: every field in it is a claim, not a fact. Where GitHub
can be asked directly - which workflow produced this run, on which repository, at which commit -
the answer from GitHub wins and a disagreement is itself a rejection, because a proof whose
self-description does not match its provenance is the exact shape of a forged one.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

# ---------------------------------------------------------------------------
# What the dashboard is allowed to say
# ---------------------------------------------------------------------------


class WorkerState(StrEnum):
    """Seven states, because GREEN/RED cannot express what a person needs to do next.

    "Broken" and "never tried" call for different actions, and so do "the credential is
    rejected" and "the subscription window is spent" - one needs a human with a browser, the
    other needs an hour of patience. Collapsing them into RED throws away the only part of the
    signal that tells anyone what to do.
    """

    HEALTHY = "HEALTHY"
    """A real `claude -p` call through the production path succeeded, on a runner, recently."""

    DEGRADED = "DEGRADED"
    """It ran and something is wrong that is neither auth nor capacity."""

    AUTH_FAILED = "AUTH FAILED"
    """The credential was rejected. Does not recover on its own; needs a person."""

    CAPACITY_LIMITED = "CAPACITY LIMITED"
    """The subscription window is spent. Recovers by itself; needs no one."""

    STALE_PROOF = "STALE PROOF"
    """It worked, but too long ago to still be evidence. Tokens expire and get revoked."""

    NOT_YET_VERIFIED = "NOT YET VERIFIED"
    """No valid proof has ever arrived. The honest state before the first successful run."""

    CONFIGURED_NOT_OPERATIONAL = "CONFIGURED BUT NOT OPERATIONAL"
    """The pieces are present and the thing does not work. A credential merely existing lands
    here, never in HEALTHY - which is the whole reason this module exists."""


#: Which states may show a green lamp. Exactly one, and it is asserted at import.
GREEN_STATES = frozenset({WorkerState.HEALTHY})

assert len(GREEN_STATES) == 1, "Only a verified execution may be green."


# ---------------------------------------------------------------------------
# Freshness
# ---------------------------------------------------------------------------

#: How long a proof stays evidence.
#:
#: Tied to the worker's schedule rather than picked for feel. `claude-worker.yml` runs daily at
#: 11:00 UTC, twenty-five minutes ahead of `health.yml`, so a healthy system refreshes this every
#: day and 48 hours leaves exactly one missed run of margin before the light stops claiming.
#:
#: The failure this bounds: a token that expires on Tuesday must not leave the dashboard green
#: through Friday. A week-long TTL would do that, and a token expiring quietly is the single most
#: likely way this system breaks.
PROOF_TTL_HOURS = 48.0

#: What the worker's cron says, kept here so a change to one is visibly a change to the other.
WORKER_SCHEDULE_CRON = "0 11 * * *"


# ---------------------------------------------------------------------------
# What a valid attestation has to contain
# ---------------------------------------------------------------------------

#: Every field is non-secret by construction: booleans, enum-ish strings, and identifiers GitHub
#: already publishes. The token's *name* appears; its value never does, anywhere.
REQUIRED_FIELDS: tuple[tuple[str, type | tuple[type, ...]], ...] = (
    ("schema_version", int),
    ("workflow_run_id", str),
    ("workflow_name", str),
    ("repository", str),
    ("commit_sha", str),
    ("branch", str),
    ("runner_environment", str),
    ("generated_at", str),
    ("execution_attempted", bool),
    ("execution_succeeded", bool),
    ("subscription_auth_path", str),
    ("anthropic_api_key_absent", bool),
    ("paid_fallback_disabled", bool),
    ("production_path", str),
    ("result_state", str),
)

SCHEMA_VERSION = 1

#: The call chain a paid client job travels. An attestation naming anything else did not exercise
#: the production path, whatever else it proved.
PRODUCTION_PATH = "ClaudeWorker.execute -> claude -p"

#: The only workflow whose proofs are believed, and the only branch they may come from.
EXPECTED_WORKFLOW = "Claude worker"
EXPECTED_REPOSITORY = "amercado19/ai-income-control-center"
ACCEPTED_BRANCHES = frozenset({"main"})


@dataclass
class Verdict:
    """Accepted or rejected, with the state to display and the reason in words."""

    accepted: bool
    state: str
    reason: str
    rejections: list[str] = field(default_factory=list)
    run_url: str = ""
    generated_at: str = ""
    age_hours: float | None = None

    @property
    def green(self) -> bool:
        return self.state in GREEN_STATES

    def to_dict(self) -> dict[str, Any]:
        return {
            "accepted": self.accepted,
            "state": str(self.state),
            "reason": self.reason,
            "rejections": list(self.rejections),
            "run_url": self.run_url,
            "generated_at": self.generated_at,
            "age_hours": self.age_hours,
        }


# ---------------------------------------------------------------------------
# The border guard
# ---------------------------------------------------------------------------


def _classify_failure(payload: dict[str, Any]) -> tuple[str, str]:
    """Which kind of failure, from what the attestation reports.

    Separated out because the three failures need three different responses. Auth needs a person
    at a browser; capacity needs nobody at all, just time; anything else is a real defect.
    """
    blob = json.dumps(payload.get("failures", []) + [payload.get("result_detail", "")]).lower()

    if "401" in blob or "oauth" in blob or "authenticate" in blob or "credential was rejected" in blob:
        return (
            WorkerState.AUTH_FAILED,
            "The Claude credential was rejected. This does not recover on its own - it needs a new token from `claude setup-token`.",
        )
    if "rate_limit" in blob or "usage limit" in blob or "429" in blob or "retry_later" in blob:
        return (
            WorkerState.CAPACITY_LIMITED,
            "The subscription window is spent. This recovers by itself when the window resets; the consequence is waiting, never a bill.",
        )
    return (
        WorkerState.DEGRADED,
        "The worker ran and failed for a reason that is neither authentication nor capacity.",
    )


def validate(
    payload: Any,
    *,
    now: datetime | None = None,
    authoritative: dict[str, Any] | None = None,
    ttl_hours: float = PROOF_TTL_HOURS,
    expected_repository: str = EXPECTED_REPOSITORY,
    expected_workflow: str = EXPECTED_WORKFLOW,
    accepted_branches: frozenset[str] = ACCEPTED_BRANCHES,
) -> Verdict:
    """Decide whether to believe an uploaded attestation.

    ``authoritative`` is run metadata fetched from GitHub's API by the *validating* workflow -
    not supplied by the artifact. When present it overrules the attestation's own account of
    itself, and any disagreement is a rejection: a file claiming to come from a run whose real
    metadata says otherwise is the shape of a forgery, and there is no benign reading of it.

    Every rejection is collected rather than short-circuiting, so one run tells you everything
    that is wrong instead of one thing at a time.
    """
    now = now or datetime.now(UTC)
    rejections: list[str] = []

    # ---- malformed ---------------------------------------------------------
    if not isinstance(payload, dict):
        return Verdict(
            False,
            WorkerState.NOT_YET_VERIFIED,
            "The proof artifact is not a JSON object, so nothing in it can be checked.",
            ["malformed: not an object"],
        )

    for name, kind in REQUIRED_FIELDS:
        if name not in payload:
            rejections.append(f"malformed: missing {name!r}")
        elif not isinstance(payload[name], kind):
            rejections.append(f"malformed: {name!r} is {type(payload[name]).__name__}, expected {kind}")

    if payload.get("schema_version") != SCHEMA_VERSION:
        rejections.append(f"malformed: schema_version {payload.get('schema_version')!r}, this validator understands {SCHEMA_VERSION}")

    if rejections:
        return Verdict(
            False,
            WorkerState.NOT_YET_VERIFIED,
            "The proof artifact is malformed, so no claim in it is usable. A proof that cannot be "
            "checked is not weak evidence; it is no evidence.",
            rejections,
        )

    # ---- provenance, GitHub's answer beating the artifact's --------------
    repository = str(payload["repository"])
    workflow_name = str(payload["workflow_name"])
    branch = str(payload["branch"])
    run_id = str(payload["workflow_run_id"])

    if authoritative:
        for field_name, claimed in (
            ("repository", repository),
            ("workflow_name", workflow_name),
            ("branch", branch),
            ("workflow_run_id", run_id),
        ):
            truth = authoritative.get(field_name)
            # An absent or empty authoritative value means "GitHub was not asked about this",
            # not "GitHub says it is empty". Conflating the two rejected every valid proof
            # whenever the validating environment had not set the variable - a check that fails
            # closed on its own missing configuration, which is the wrong kind of strict.
            if truth and str(truth) != claimed:
                rejections.append(f"provenance: the artifact claims {field_name}={claimed!r} but GitHub reports {str(truth)!r}")
        # Prefer the authoritative values from here on, where there are any.
        repository = str(authoritative.get("repository") or repository)
        workflow_name = str(authoritative.get("workflow_name") or workflow_name)
        branch = str(authoritative.get("branch") or branch)
        run_id = str(authoritative.get("workflow_run_id") or run_id)

    if repository != expected_repository:
        rejections.append(f"wrong repository: {repository!r}, expected {expected_repository!r}")
    if workflow_name != expected_workflow:
        rejections.append(f"unexpected workflow: {workflow_name!r}, expected {expected_workflow!r}")
    if branch not in accepted_branches:
        rejections.append(f"unacceptable branch: {branch!r}, accepted {sorted(accepted_branches)}")
    if not run_id:
        rejections.append("provenance: no workflow run id, so this did not come from a workflow run")

    # ---- the runner, not somebody's laptop -------------------------------
    runner = str(payload["runner_environment"])
    if "github actions" not in runner.lower():
        rejections.append(
            f"not from a runner: runner_environment={runner!r}. A pass on a laptop proves that "
            f"laptop's credential, not the repository secret Actions uses."
        )

    # ---- money, checked before anything is believed ----------------------
    if not payload["anthropic_api_key_absent"]:
        rejections.append("paid API configuration detected: ANTHROPIC_API_KEY was present in the proving run")
    if not payload["paid_fallback_disabled"]:
        rejections.append("paid fallback was not disabled in the proving run")

    # ---- the production path, not a stand-in -----------------------------
    if str(payload["production_path"]) != PRODUCTION_PATH:
        rejections.append(f"production path not exercised: {payload['production_path']!r}, expected {PRODUCTION_PATH!r}")
    if not payload["execution_attempted"]:
        rejections.append("the worker execution was never attempted, so nothing was proved either way")

    # ---- freshness -------------------------------------------------------
    age_hours: float | None = None
    try:
        stamped = datetime.fromisoformat(str(payload["generated_at"]))
        if stamped.tzinfo is None:
            stamped = stamped.replace(tzinfo=UTC)
        age_hours = round((now - stamped).total_seconds() / 3600.0, 1)
        if age_hours < -1.0:
            rejections.append(f"malformed: generated_at is {abs(age_hours):.0f}h in the future")
    except (ValueError, TypeError):
        rejections.append("malformed: generated_at is not a timestamp")

    run_url = str(payload.get("workflow_run_url", "") or "")

    # Anything above means the proof is not trustworthy, whatever it says about the worker.
    if rejections:
        return Verdict(
            False,
            WorkerState.NOT_YET_VERIFIED,
            "The proof was rejected, so the worker is unverified. Rejecting is the safe outcome: "
            "an unverified worker shows as unverified, never as healthy.",
            rejections,
            run_url=run_url,
            generated_at=str(payload.get("generated_at", "")),
            age_hours=age_hours,
        )

    # ---- the proof is trustworthy. Now: what does it say? ----------------
    if not payload["execution_succeeded"]:
        state, reason = _classify_failure(payload)
        return Verdict(
            False,
            state,
            reason,
            [f"execution failed: {state}"],
            run_url=run_url,
            generated_at=str(payload["generated_at"]),
            age_hours=age_hours,
        )

    if age_hours is not None and age_hours > ttl_hours:
        return Verdict(
            False,
            WorkerState.STALE_PROOF,
            f"The last real execution succeeded {age_hours:.0f}h ago, beyond the {ttl_hours:.0f}h "
            f"window. A credential that worked two days ago is not evidence that it works now - "
            f"tokens expire, get revoked and get rotated.",
            ["stale"],
            run_url=run_url,
            generated_at=str(payload["generated_at"]),
            age_hours=age_hours,
        )

    return Verdict(
        True,
        WorkerState.HEALTHY,
        f"A real `{PRODUCTION_PATH}` call succeeded {age_hours:.0f}h ago on a GitHub Actions "
        f"runner, at commit {str(payload['commit_sha'])[:8]} on {branch}, authenticated by "
        f"{payload['subscription_auth_path']} with no paid API key present.",
        run_url=run_url,
        generated_at=str(payload["generated_at"]),
        age_hours=age_hours,
    )


def authoritative_from_env() -> dict[str, Any]:
    """Run metadata as GitHub reports it to the *validating* job, for cross-checking.

    Read from the validating workflow's own environment rather than from the artifact, which is
    the point: these values are set by GitHub for this job and the uploader cannot influence
    them. `GITHUB_REPOSITORY` is the repository the validation is running in, so an artifact
    claiming a different one is rejected on that basis alone.
    """
    import os

    # Only non-empty values. An unset variable means "not asked", and `validate` must not read
    # that as GitHub asserting an empty string.
    facts = {"repository": os.environ.get("GITHUB_REPOSITORY", "")}
    return {k: v for k, v in facts.items() if v}


def load(path: Any) -> Any:
    """Read an artifact file without letting a bad one raise.

    A malformed proof must reach `validate` as data and be rejected with a reason, not blow up
    the validating workflow - a crash here would take out the health commit the rest of the
    dashboard depends on.
    """
    from pathlib import Path

    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        return {"__unreadable__": f"{type(exc).__name__}: {exc}"}
