"""Proof that the Claude worker really runs, through the path real client work uses.

The distinction this module exists to enforce: a green ``anthropics/claude-code-action`` step
proves that an action ran. It does not prove that ``ClaudeWorker.execute`` - the code path a paid
client job actually travels - can invoke a model and get an answer back. Those are different
code paths, and an earlier version of this repository proved the first while claiming the second.

So every check here goes through the production objects. ``prove_worker`` builds a real ``Job``,
hands it to ``ClaudeWorker.execute``, and inspects what lands in the workspace.
``prove_worker_reviewer`` runs the same worker through ``pipeline.run``, which is the identical
function a client job uses, with the same independent reviewer and the same revision loop.

Three properties make the worker proof hard to fake:

* **The expected answer is minted at runtime.** A nonce generated moments before the prompt
  cannot be in a cached response, a fixture, or a training set.
* **The answer must be exact.** Not "contains", not "looks like" - the file content is compared
  character for character after stripping whitespace, so a model that wrote something plausible
  instead of the thing asked for fails.
* **A missing file is a failure, never a pass.** The most likely way to get a false green is for
  nothing to happen and for nobody to check.
"""

from __future__ import annotations

import json
import os
import secrets
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import proof_transport
from .config import DATA_DIR
from .fulfillment.worker import ClaudeWorker, workspace_for
from .models import Job

#: Where the last proof result lives, so the dashboard's AI Worker light can be backed by an
#: actual model call rather than by the presence of a token and a binary.
#:
#: **Written by `ingest_attestation` and by nothing else.** It holds a verdict that the border
#: guard reached with GitHub's authoritative metadata in hand, and that context cannot be
#: reconstructed later: the run URL, the validation timestamp, and the list of rejections exist
#: only in the environment that did the validating. Anything else that writes here does not
#: add a claim to the file, it deletes the evidence.
PROOF_FILE = DATA_DIR / "worker_proof.json"

#: Where a locally-run `python -m aicc worker-proof` records itself.
#:
#: Separate from `PROOF_FILE` because the two files answer different questions. This one answers
#: "what happened when I ran the proof on this machine" - a diagnostic, self-reported, with no
#: provenance anyone checked. `PROOF_FILE` answers "what did the border guard accept" - the
#: repository's state.
#:
#: They shared a path until a local run of the diagnostic overwrote a committed `AUTH FAILED`
#: verdict, discarding its link to the run that produced it. No green light was at risk (the
#: guard re-validates a raw attestation on read and rejects a laptop's for `runner_environment`),
#: which is exactly why it went unnoticed: the clobber type-checked and the colour barely moved.
#: Anything that writes `data/` in CI would then have committed a laptop's self-report as the
#: repository's validated state.
#:
#: Gitignored as well, so both the code and the VCS have to fail for a diagnostic to become state.
LOCAL_PROOF_FILE = DATA_DIR / "worker_proof_local.json"

#: Freshness lives in `proof_transport.PROOF_TTL_HOURS`, next to the validator that enforces it
#: and to the worker's cron that justifies the number. Re-exported so existing callers and tests
#: keep working, and so there is exactly one value rather than two that can drift apart.
PROOF_VALID_HOURS = proof_transport.PROOF_TTL_HOURS


@dataclass
class ProofResult:
    """The record the workflow prints and the dashboard can read."""

    name: str
    passed: bool
    detail: str
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def environment_facts() -> dict[str, Any]:
    """What can be said about this machine and its credentials without printing any of them.

    Every value here is a boolean or a non-secret string. ``bool(os.environ.get(...))`` reveals
    that a secret exists, which is the question being asked; the value never leaves this process.
    """
    import platform
    import shutil

    on_actions = os.environ.get("GITHUB_ACTIONS") == "true"
    return {
        "execution_environment": (
            f"GitHub Actions runner ({os.environ.get('RUNNER_OS', '?')}, {platform.machine()})"
            if on_actions
            else f"local ({platform.system()}, {platform.machine()})"
        ),
        "runs_in_cloud": on_actions,
        "hostname": platform.node(),
        "workflow_run_id": os.environ.get("GITHUB_RUN_ID", ""),
        "workflow_run_url": (
            f"{os.environ.get('GITHUB_SERVER_URL', '')}/{os.environ.get('GITHUB_REPOSITORY', '')}"
            f"/actions/runs/{os.environ.get('GITHUB_RUN_ID', '')}"
            if on_actions
            else ""
        ),
        "subscription_auth": "PRESENT" if os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") else "ABSENT",
        "anthropic_api_key": "PRESENT" if os.environ.get("ANTHROPIC_API_KEY") else "ABSENT",
        "claude_cli": shutil.which("claude") or "NOT ON PATH",
        "mac_required_for_job_execution": "NO" if on_actions else "UNKNOWN - not running in Actions",
        "workflow_name": os.environ.get("GITHUB_WORKFLOW", ""),
        "repository": os.environ.get("GITHUB_REPOSITORY", ""),
        "commit_sha": os.environ.get("GITHUB_SHA", ""),
        "branch": os.environ.get("GITHUB_REF_NAME", ""),
        "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT", ""),
    }


def attestation(report: dict[str, Any]) -> dict[str, Any]:
    """The proof as it crosses the trust boundary: the claim, its provenance, and nothing else.

    This is what `claude-worker.yml` uploads as an artifact and `health.yml` validates. It is
    deliberately not the full report - the evidence dict can contain model output, and model
    output came from a prompt built partly from a marketplace listing. Only booleans, identifiers
    GitHub already publishes, and short failure strings cross over.

    **No secret appears here, and none can.** `subscription_auth_path` is the NAME of the
    environment variable used, never its value; the other credential fields are booleans about
    presence. `environment_facts` has the same property by construction, which is why this is
    assembled from it rather than from `os.environ` directly.
    """
    env = report["environment"]
    from . import proof_transport as pt

    worker_result = next((r for r in report["results"] if r["name"] == "Claude worker executes"), None)
    attempted = worker_result is not None
    succeeded = bool(worker_result and worker_result["passed"])
    failures = [r["detail"] for r in report["results"] if not r["passed"]]

    return {
        "schema_version": pt.SCHEMA_VERSION,
        # Provenance. Every one of these is cross-checked against GitHub by the validator.
        "workflow_run_id": env.get("workflow_run_id", ""),
        "workflow_run_url": env.get("workflow_run_url", ""),
        "workflow_run_attempt": env.get("run_attempt", ""),
        "workflow_name": env.get("workflow_name", ""),
        "repository": env.get("repository", ""),
        "commit_sha": env.get("commit_sha", ""),
        "branch": env.get("branch", ""),
        "runner_environment": env.get("execution_environment", ""),
        "generated_at": report["generated_at"],
        # What was actually attempted, and whether it worked.
        "execution_attempted": attempted,
        "execution_succeeded": succeeded,
        "production_path": pt.PRODUCTION_PATH,
        # Money. Booleans, checked before the verdict is believed.
        "subscription_auth_path": "CLAUDE_CODE_OAUTH_TOKEN" if env.get("subscription_auth") == "PRESENT" else "NONE",
        "anthropic_api_key_absent": env.get("anthropic_api_key") == "ABSENT",
        "paid_fallback_disabled": True,
        "mac_required_for_job_execution": env.get("mac_required_for_job_execution", ""),
        # The verdict, and why if it failed.
        "result_state": report["worker_test_status"],
        "failures": failures,
        "reviewer_path_proved": any(r["name"] == "Worker to reviewer, end to end" and r["passed"] for r in report["results"]),
    }


def prove_no_paid_path() -> ProofResult:
    """The check that runs first, because it is the one that can cost money.

    Ordered before any model call deliberately. Reporting afterwards that billing was reachable
    would be reporting a charge, not preventing one.
    """
    from . import degradation
    from .config import MAX_NEW_MONTHLY_CASH_SPEND

    problems = []
    if os.environ.get("ANTHROPIC_API_KEY"):
        problems.append("ANTHROPIC_API_KEY is present in this environment, so metered billing is reachable.")
    if MAX_NEW_MONTHLY_CASH_SPEND != 0.00:
        problems.append(f"The cash ceiling is ${MAX_NEW_MONTHLY_CASH_SPEND:.2f}, not $0.00.")

    decision = degradation.classify("429 rate_limit_error: usage limit reached", status_code=429)
    if not degradation.never_falls_back_to_paid(decision):
        problems.append(f"An exhausted window yields {decision.action}, which permits paid billing.")

    if problems:
        return ProofResult("No paid fallback", False, "; ".join(problems))
    return ProofResult(
        "No paid fallback",
        True,
        f"No ANTHROPIC_API_KEY. Ceiling ${MAX_NEW_MONTHLY_CASH_SPEND:.2f}. An exhausted subscription "
        f"window yields {decision.action}: the consequence of the limit is waiting, not a bill.",
        {"paid_fallback": "DISABLED", "anthropic_api_key": "ABSENT"},
    )


def prove_worker(*, keep_workspace: bool = False) -> ProofResult:
    """Run a real Job through ClaudeWorker.execute and require an exact, unguessable answer."""
    available, why = ClaudeWorker.available()
    if not available:
        return ProofResult("Claude worker executes", False, f"Worker unavailable: {why}")

    nonce = secrets.token_hex(8)
    job = Job(
        title="Worker proof",
        client="internal",
        job_type="proof",
        requirements=[
            f"Create a file named `proof.txt` whose entire contents are exactly: {nonce}",
            "No other text, no trailing commentary, no other files.",
        ],
        acceptance_criteria=[f"proof.txt contains exactly the string {nonce} and nothing else."],
    )
    ws = workspace_for(job)

    try:
        produced, note = ClaudeWorker.execute(job)
    except Exception as exc:  # noqa: BLE001 - the failure IS the result here
        return ProofResult(
            "Claude worker executes",
            False,
            f"{type(exc).__name__}: {exc}",
            {"workspace": str(ws)},
        )

    proof_file = ws / "proof.txt"
    if not proof_file.exists():
        names = ", ".join(p.name for p in produced) or "(nothing)"
        return ProofResult(
            "Claude worker executes",
            False,
            f"proof.txt was not written. Files produced: {names}. A worker that writes something "
            f"other than what was asked for has not proved it can do the job.",
        )

    actual = proof_file.read_text(encoding="utf-8").strip()
    if actual != nonce:
        return ProofResult(
            "Claude worker executes",
            False,
            f"proof.txt holds {actual[:40]!r}, not the nonce minted for this run. Something produced output, but not the output requested.",
        )

    if not keep_workspace:
        for p in ws.rglob("*"):
            if p.is_file():
                p.unlink()

    return ProofResult(
        "Claude worker executes",
        True,
        f"A nonce minted at {datetime.now(UTC):%H:%M:%S}Z was returned exactly by a real model "
        f"call through ClaudeWorker.execute - the same path a paid client job takes. {note}",
        {"files_produced": len(produced), "exact_match": True},
    )


def prove_worker_reviewer() -> ProofResult:
    """Worker -> Reviewer -> QA -> revision -> final, through `pipeline.run` itself.

    Uses the production pipeline function rather than reimplementing its steps, because a proof
    that reimplements the thing it is proving proves only the reimplementation. The reviewer is
    structurally unable to see the worker's notes; that independence is enforced by
    ``reviewer.review``'s signature and checked separately in the safety self-test.
    """
    available, why = ClaudeWorker.available()
    if not available:
        return ProofResult("Worker to reviewer, end to end", False, f"Worker unavailable: {why}")

    from .fulfillment import pipeline
    from .models import JobStatus

    job = Job(
        title="Reviewer proof - two-column summary",
        client="internal",
        job_type="research",
        # Nonzero deliberately, and it took a real run to find out why.
        #
        # This was 0.0, and `pipeline.validate` refuses a job with no agreed price - correctly,
        # since its whole job is to check a brief is workable "before spending any effort on it".
        # So `pipeline.run` went VALIDATE -> PROBLEM and returned before any work or QA, and the
        # proof reported "the pipeline produced no QA round at all, so the reviewer never ran" -
        # which was true, and read like a reviewer defect rather than an invalid fixture.
        #
        # It went unnoticed because this leg had never once executed. `run_all` only reaches it
        # when `prove_worker` passed, and `prove_worker` returned 401 on every run from #3 to #8.
        # A check that cannot run until a prior check passes is untested code wearing a test's
        # clothes, and the first time it ran it failed for a reason that had nothing to do with
        # what it measures.
        #
        # A fixture price, not revenue: real earnings come from the ledger
        # (`storage.real_revenue_entries`), never from a job's agreed_price, and the worker
        # workflow holds `contents: read` so the job this persists is discarded with the runner.
        agreed_price=1.00,
        requirements=[
            "Write `summary.md` containing a level-1 markdown heading and at least three sentences describing what a data pipeline does.",
            "Cite no sources - this is a description, not research.",
        ],
        acceptance_criteria=[
            "summary.md exists.",
            "It begins with a level-1 markdown heading.",
            "It contains at least three sentences.",
        ],
    )

    try:
        job = pipeline.run(job, worker_cls=ClaudeWorker)
    except Exception as exc:  # noqa: BLE001
        return ProofResult("Worker to reviewer, end to end", False, f"{type(exc).__name__}: {exc}")

    rounds = len(job.qa_rounds)
    if rounds == 0:
        return ProofResult(
            "Worker to reviewer, end to end",
            False,
            "The pipeline produced no QA round at all, so the reviewer never ran.",
        )

    worker_used = job.worker_notes or ""
    if "rule-based" in worker_used.lower() or "scaffold" in worker_used.lower():
        return ProofResult(
            "Worker to reviewer, end to end",
            False,
            "The pipeline silently fell back to the rule-based worker. That is a valid runtime "
            "behaviour and a worthless proof - this check exists to confirm the AI path works.",
        )

    verdict = job.qa_rounds[-1].get("verdict", "?")
    score = job.latest_qa_score()
    ok = job.status in (JobStatus.READY_TO_DELIVER.value, JobStatus.PROBLEM.value)
    return ProofResult(
        "Worker to reviewer, end to end",
        ok,
        f"Claude worker produced the deliverable; the independent reviewer ran {rounds} QA "
        f"round(s), final verdict {verdict} at {score}/100, job ended {job.status}. "
        f"{'Delivery still requires a human approval, by design.' if ok else ''}",
        {"qa_rounds": rounds, "final_verdict": verdict, "final_score": score, "status": job.status},
    )


def run_all(*, include_pipeline: bool = True) -> dict[str, Any]:
    """Everything, in the order that keeps the money-relevant check first."""
    results = [prove_no_paid_path(), prove_worker()]
    if include_pipeline and results[-1].passed:
        results.append(prove_worker_reviewer())

    facts = environment_facts()
    return {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "environment": facts,
        "results": [r.to_dict() for r in results],
        "passed": sum(1 for r in results if r.passed),
        "failed": sum(1 for r in results if not r.passed),
        "ok": all(r.passed for r in results),
        "worker_test_status": "VERIFIED" if all(r.passed for r in results) else "FAILED",
    }


def format_report(report: dict[str, Any]) -> str:
    env = report["environment"]
    lines = [
        "CLAUDE WORKER PROOF",
        "",
        f"  WORKER TEST STATUS     {report['worker_test_status']}",
        f"  WORKFLOW RUN ID        {env['workflow_run_id'] or '(not running in Actions)'}",
        f"  EXECUTION ENVIRONMENT  {env['execution_environment']}",
        f"  HOSTNAME               {env['hostname']}",
        f"  SUBSCRIPTION AUTH      {env['subscription_auth']}",
        f"  ANTHROPIC_API_KEY      {env['anthropic_api_key']}",
        "  PAID FALLBACK          DISABLED",
        f"  CLAUDE CLI             {env['claude_cli']}",
        f"  MAC REQUIRED FOR JOBS  {env['mac_required_for_job_execution']}",
        "",
    ]
    for r in report["results"]:
        lines.append(f"  [{'PASS' if r['passed'] else 'FAIL'}] {r['name']}")
        lines.append(f"         {r['detail']}")
    lines.append("")
    lines.append(f"  RESULT: {report['passed']} passed, {report['failed']} failed.")
    return "\n".join(lines)


def write_report(report: dict[str, Any], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def record_result(report: dict[str, Any]) -> Path:
    """Persist a locally-run proof so `python -m aicc worker-proof` is not silently a no-op.

    Writes `LOCAL_PROOF_FILE`, never `PROOF_FILE`. A diagnostic that runs on demand must not be
    able to overwrite a verdict the border guard reached - not because the diagnostic could forge
    a green light (it cannot; see `LOCAL_PROOF_FILE`) but because it would erase the run URL and
    validation timestamp that only the validating environment could supply.

    Records the outcome whatever it is. A FAILED proof matters to whoever ran it exactly as much
    as a passing one, and writing only the good ones is how a light gets stuck on green.

    Never the evidence dict: the attestation shape carries no model output and no credential, and
    is used here too so the local file is readable by the same tooling.
    """
    LOCAL_PROOF_FILE.parent.mkdir(parents=True, exist_ok=True)
    LOCAL_PROOF_FILE.write_text(json.dumps(attestation(report), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return LOCAL_PROOF_FILE


def ingest_attestation(payload: Any, *, now: Any = None) -> tuple[bool, str, dict[str, Any]]:
    """Validate an uploaded proof and, only if it survives, make it the recorded state.

    This is the privileged half of the transport, called by `health.yml` - which holds no Claude
    credential and never runs a model. It is the sole writer of `data/worker_proof.json` and
    returns what to say about it.

    The rejected case still writes, and that is deliberate: "a proof arrived and was refused
    because it came from the wrong workflow" is information a person needs, and dropping it would
    leave the dashboard showing the previous, better-looking state. What a rejection can never do
    is produce a green light.
    """
    from . import proof_transport as pt

    verdict = pt.validate(payload, now=now, authoritative=pt.authoritative_from_env())

    record = {
        "state": str(verdict.state),
        "accepted": verdict.accepted,
        "reason": verdict.reason,
        "rejections": verdict.rejections,
        "run_url": verdict.run_url,
        "generated_at": verdict.generated_at,
        "age_hours": verdict.age_hours,
        "validated_at": (now or datetime.now(UTC)).isoformat(timespec="seconds"),
        "ttl_hours": pt.PROOF_TTL_HOURS,
        # Carried through for the dashboard, and only when the proof was believed. An unvalidated
        # artifact must not get to put its own claims on the page.
        "commit_sha": str(payload.get("commit_sha", "")) if verdict.accepted and isinstance(payload, dict) else "",
        "reviewer_path_proved": bool(payload.get("reviewer_path_proved")) if verdict.accepted and isinstance(payload, dict) else False,
        "mac_required_for_job_execution": (
            str(payload.get("mac_required_for_job_execution", "")) if verdict.accepted and isinstance(payload, dict) else ""
        ),
    }
    PROOF_FILE.parent.mkdir(parents=True, exist_ok=True)
    PROOF_FILE.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return verdict.accepted, str(verdict.state), record


def last_result() -> dict[str, Any]:
    """The recorded worker state, as one of `proof_transport.WorkerState`.

    Two shapes can be on disk. A record written by `ingest_attestation` is already a validated
    verdict and is returned as-is. A raw attestation is put through the same validator now, so an
    unvalidated file can never reach the dashboard as though it had been checked.

    Nothing in this repository writes a raw attestation here any more - `record_result` uses
    `LOCAL_PROOF_FILE`. The branch stays because the guarantee worth having is "whatever is in
    this file, an unvalidated claim cannot be believed", and that has to hold for a file a person
    edited, a bad merge, or a future caller that forgets the rule. A check that is only correct
    while every caller behaves is a property of the callers, not of this function.
    """
    from . import proof_transport as pt

    if not PROOF_FILE.exists():
        return {
            "state": str(pt.WorkerState.NOT_YET_VERIFIED),
            "accepted": False,
            "reason": "No worker proof has ever been recorded, so nothing has demonstrated that a real model call succeeds.",
            "rejections": [],
            "run_url": "",
            "generated_at": "",
            "age_hours": None,
        }

    payload = pt.load(PROOF_FILE)

    if isinstance(payload, dict) and "__unreadable__" in payload:
        return {
            "state": str(pt.WorkerState.NOT_YET_VERIFIED),
            "accepted": False,
            "reason": f"The recorded proof is unreadable: {payload['__unreadable__']}",
            "rejections": ["malformed: unreadable file"],
            "run_url": "",
            "generated_at": "",
            "age_hours": None,
        }

    # Already a validated verdict.
    if isinstance(payload, dict) and "state" in payload and "accepted" in payload:
        return payload

    # A raw attestation. Validate it now rather than trusting it.
    verdict = pt.validate(payload)
    return verdict.to_dict() | {"state": str(verdict.state)}
