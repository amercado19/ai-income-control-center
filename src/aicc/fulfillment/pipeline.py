"""Job fulfillment pipeline (spec section 22).

    RECEIVED -> VALIDATE -> PLAN -> WORK -> VERIFY -> QA -> FIX -> FINAL_QA
             -> READY_TO_DELIVER -> [human approval] -> DELIVERED

Two hard rules:

* **At most two automatic revision loops.** After that the job goes to PROBLEM with a
  human_action_required note. A system that loops forever on a job it cannot fix burns
  subscription allowance and hides the failure.
* **Delivery is never automatic.** READY_TO_DELIVER is a terminal state for the machine.
  Only ``deliver()``, called with a human actor, moves a job to DELIVERED.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .. import audit, storage
from ..models import Actor, Job, JobStatus, utcnow
from ..state import SystemState
from . import reviewer
from .worker import RuleBasedWorker, Worker, select_worker, workspace_for


class PipelineBlocked(RuntimeError):
    """The pipeline refused to act because the system is not in a state that permits it."""


def _advance(job: Job, status: JobStatus, *, note: str = "") -> None:
    before = job.status
    job.status = status.value
    job.updated_at = utcnow()
    storage.jobs.put(job)
    audit.record(
        "job_status_changed",
        actor=Actor.SYSTEM,
        object_type="job",
        object_id=job.id,
        before=before,
        after=job.status,
        source=job.source,
        error=note,
    )


def validate(job: Job) -> tuple[bool, list[str]]:
    """Check the brief is workable before spending any effort on it."""
    problems: list[str] = []
    if not job.requirements:
        problems.append("No requirements recorded.")
    if not job.acceptance_criteria:
        problems.append("No acceptance criteria recorded - 'done' is undefined.")
    if job.agreed_price <= 0:
        problems.append("No agreed price.")
    if not job.client:
        problems.append("No client recorded.")
    return (not problems), problems


def run(
    job: Job,
    *,
    worker_cls: type[Worker] | None = None,
    max_auto_revisions: int | None = None,
    **worker_kwargs: Any,
) -> Job:
    """Drive a job from RECEIVED to READY_TO_DELIVER or PROBLEM.

    Never proceeds to DELIVERED. That transition requires a human (spec section 5, Level 2).
    """
    state = SystemState.load()
    if not state.external_actions_allowed():
        raise PipelineBlocked(f"Pipeline refused: {state.why_blocked()}")

    job.workspace = str(workspace_for(job))
    cap = max_auto_revisions if max_auto_revisions is not None else job.max_auto_revisions

    # --- VALIDATE ---------------------------------------------------------
    _advance(job, JobStatus.VALIDATE)
    ok, problems = validate(job)
    if not ok:
        job.human_action_required = "Brief is incomplete: " + "; ".join(problems)
        _advance(job, JobStatus.PROBLEM, note=job.human_action_required)
        return job

    # --- PLAN -------------------------------------------------------------
    _advance(job, JobStatus.PLAN)
    worker, worker_note = (worker_cls, "explicitly supplied") if worker_cls else select_worker()
    audit.record(
        "worker_selected",
        actor=Actor.SYSTEM,
        object_type="job",
        object_id=job.id,
        after=worker.name,
        error=worker_note,
    )

    round_number = 1
    while True:
        # --- WORK ---------------------------------------------------------
        _advance(job, JobStatus.WORK if round_number == 1 else JobStatus.FIX)
        try:
            artifacts, note = worker.execute(job, round_number=round_number, **worker_kwargs)
        except NotImplementedError as exc:
            # The AI worker hands off to a GitHub Action rather than running here. Fall back to
            # the rule-based worker rather than producing nothing, and say so.
            audit.record(
                "worker_handoff",
                actor=Actor.SYSTEM,
                object_type="job",
                object_id=job.id,
                result="refused",
                error=str(exc)[:200],
            )
            worker = RuleBasedWorker
            artifacts, note = worker.execute(job, round_number=round_number, **worker_kwargs)
        except Exception as exc:  # noqa: BLE001
            job.human_action_required = f"Worker failed: {type(exc).__name__}: {exc}"
            _advance(job, JobStatus.PROBLEM, note=job.human_action_required)
            return job

        job.worker_notes = note
        job.deliverables = [str(p) for p in artifacts]
        job.ai_usage_units += 1.0 if worker.name == "claude" else 0.0

        # --- VERIFY -------------------------------------------------------
        _advance(job, JobStatus.VERIFY)
        missing = [p for p in artifacts if not Path(p).exists()]
        if missing:
            job.human_action_required = f"Worker reported files that do not exist: {missing}"
            _advance(job, JobStatus.PROBLEM, note=job.human_action_required)
            return job

        # --- QA -----------------------------------------------------------
        _advance(job, JobStatus.QA if round_number == 1 else JobStatus.FINAL_QA)
        report = reviewer.review(
            job_id=job.id,
            job_type=job.job_type,
            requirements=job.requirements,
            acceptance_criteria=job.acceptance_criteria,
            artifacts=[Path(p) for p in job.deliverables],
            round_number=round_number,
            source_file=worker_kwargs.get("qa_source_file"),
            expected_columns=worker_kwargs.get("expected_columns"),
            expected_row_count=worker_kwargs.get("expected_row_count"),
            document_text=worker_kwargs.get("document_text", ""),
            # Deliberately NOT passing job.worker_notes. See reviewer module docstring.
        )
        job.qa_rounds.append(report.to_dict())
        storage.jobs.put(job)
        audit.record(
            "qa_completed",
            actor=Actor.SYSTEM,
            object_type="job",
            object_id=job.id,
            after={"round": round_number, "score": report.overall_score, "verdict": report.verdict},
            source=job.source,
        )

        if report.verdict == "READY":
            job.revision_count = round_number - 1
            job.human_action_required = "Ready for delivery. Approve to send."
            _advance(job, JobStatus.READY_TO_DELIVER, note=f"QA {report.overall_score}")
            return job

        if report.verdict == "HUMAN_REVIEW" or round_number > cap:
            job.revision_count = round_number - 1
            reason = "QA below the auto-revise threshold" if report.verdict == "HUMAN_REVIEW" else f"exhausted {cap} automatic revision(s)"
            criticals = [f for f in report.findings if f.get("severity") == "critical"]
            job.human_action_required = f"Needs your review: {reason}. QA {report.overall_score}/100. " + (
                f"Critical: {criticals[0]['detail']}" if criticals else ""
            )
            _advance(job, JobStatus.PROBLEM, note=job.human_action_required)
            return job

        round_number += 1


def deliver(job: Job, *, actor: Actor = Actor.ANDRES, approved_by: str = "") -> tuple[bool, str]:
    """Mark a job delivered. Requires an explicit human approval (spec section 5, Level 2)."""
    if job.status != JobStatus.READY_TO_DELIVER.value:
        return False, f"Refused: job is {job.status}, not READY_TO_DELIVER."
    if actor not in (Actor.ANDRES,):
        audit.record(
            "delivery_refused",
            actor=actor,
            object_type="job",
            object_id=job.id,
            result="refused",
            error="Delivery requires a human approver.",
        )
        return False, "Refused: delivery requires a human approver."

    job.delivered_at = utcnow()
    job.human_action_required = ""
    _advance(job, JobStatus.DELIVERED, note=f"approved_by={approved_by or actor.value}")
    audit.record(
        "job_delivered",
        actor=actor,
        object_type="job",
        object_id=job.id,
        after={"price": job.agreed_price, "qa": job.latest_qa_score()},
        source=job.source,
    )
    return True, "DELIVERED"


def needs_attention() -> list[Job]:
    """Everything waiting on Andres (spec section 29)."""
    return [
        j for j in storage.jobs.all() if j.status in (JobStatus.READY_TO_DELIVER.value, JobStatus.PROBLEM.value) or j.human_action_required
    ]
