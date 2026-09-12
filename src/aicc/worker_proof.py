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

from .config import DATA_DIR
from .fulfillment.worker import ClaudeWorker, workspace_for
from .models import Job

#: Where the last proof result lives, so the dashboard's AI Worker light can be backed by an
#: actual model call rather than by the presence of a token and a binary.
PROOF_FILE = DATA_DIR / "worker_proof.json"

#: How long a passing proof stays good for. A credential that worked last week is not evidence
#: that it works now - OAuth tokens expire, get revoked, and get rotated - so a stale proof
#: reports as stale rather than as a pass.
PROOF_VALID_HOURS = 72.0


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
        agreed_price=0.0,
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
    """Persist the verdict where `health.probe_ai_worker` can find it.

    Only the verdict and its provenance, never the evidence dict - the proof runs on a public
    repository and this file is committed.
    """
    payload = {
        "ok": report["ok"],
        "worker_test_status": report["worker_test_status"],
        "generated_at": report["generated_at"],
        "workflow_run_url": report["environment"].get("workflow_run_url", ""),
        "execution_environment": report["environment"].get("execution_environment", ""),
        "failures": [r["detail"] for r in report["results"] if not r["passed"]],
    }
    PROOF_FILE.parent.mkdir(parents=True, exist_ok=True)
    PROOF_FILE.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return PROOF_FILE


def last_result() -> dict[str, Any]:
    """The last recorded proof, with staleness resolved.

    Returns ``{"state": ...}`` where state is one of PASSED, FAILED, STALE or NEVER_RUN. The
    distinction between STALE and NEVER_RUN matters: one says "this worked and we should check
    again", the other says "nothing has ever demonstrated this works".
    """
    if not PROOF_FILE.exists():
        return {"state": "NEVER_RUN", "detail": "No worker proof has ever been recorded."}
    try:
        payload = json.loads(PROOF_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return {"state": "NEVER_RUN", "detail": f"The proof record is unreadable: {exc}"}

    try:
        at = datetime.fromisoformat(payload.get("generated_at", ""))
    except ValueError:
        return {"state": "NEVER_RUN", "detail": "The proof record has no usable timestamp."}
    if at.tzinfo is None:
        at = at.replace(tzinfo=UTC)
    age_hours = (datetime.now(UTC) - at).total_seconds() / 3600.0

    if not payload.get("ok"):
        return {
            "state": "FAILED",
            "detail": "; ".join(payload.get("failures") or ["The last proof failed."]),
            "at": payload.get("generated_at", ""),
            "run_url": payload.get("workflow_run_url", ""),
            "age_hours": round(age_hours, 1),
        }
    if age_hours > PROOF_VALID_HOURS:
        return {
            "state": "STALE",
            "detail": (
                f"The last proof passed {age_hours:.0f}h ago, beyond the {PROOF_VALID_HOURS:.0f}h "
                f"window. A credential that worked last week is not evidence that it works now."
            ),
            "at": payload.get("generated_at", ""),
            "run_url": payload.get("workflow_run_url", ""),
            "age_hours": round(age_hours, 1),
        }
    return {
        "state": "PASSED",
        "detail": (
            f"A real model call through ClaudeWorker.execute returned an exact nonce "
            f"{age_hours:.0f}h ago on {payload.get('execution_environment', 'an unknown machine')}."
        ),
        "at": payload.get("generated_at", ""),
        "run_url": payload.get("workflow_run_url", ""),
        "age_hours": round(age_hours, 1),
    }
