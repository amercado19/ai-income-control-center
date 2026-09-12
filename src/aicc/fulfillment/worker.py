"""The worker (spec sections 22-23).

Two implementations, and the system is honest about which one is running:

* ``RuleBasedWorker`` - deterministic, always available, no AI credential needed. It genuinely
  does spreadsheet consolidation and data cleaning, which is the highest-volume job category
  this business targets.
* ``ClaudeWorker`` - available only when a credential AND an executor that can run it are both
  present. "A token exists" is not availability: the System Health page reports the AI worker as
  NOT CONFIGURED with neither, YELLOW with a credential but no executor, and GREEN only when
  something here can actually perform an AI pass. See ``ClaudeWorker`` for why that distinction
  cost a green light over a pipeline no AI had ever touched.

Client work happens under ``WORKSPACE_ROOT``, which is gitignored. ``_safe_path`` refuses any
path that escapes it, so a malicious or malformed filename in a client brief cannot write
outside the workspace.
"""

from __future__ import annotations

import csv
import os
import re
from pathlib import Path
from typing import Any

from ..config import WORKSPACE_ROOT
from ..models import Job

SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]")


class WorkspaceEscapeError(RuntimeError):
    """A deliverable path tried to escape the job workspace."""


def workspace_for(job: Job) -> Path:
    ws = WORKSPACE_ROOT / SAFE_NAME.sub("_", job.id)
    ws.mkdir(parents=True, exist_ok=True)
    return ws


def _safe_path(workspace: Path, filename: str) -> Path:
    """Resolve a filename inside the workspace, refusing traversal."""
    clean = SAFE_NAME.sub("_", Path(filename).name) or "deliverable"
    target = (workspace / clean).resolve()
    if not str(target).startswith(str(workspace.resolve())):
        raise WorkspaceEscapeError(f"Refusing to write outside the job workspace: {filename}")
    return target


#: Anything token-shaped in a subprocess's own output. A CLI that echoes part of a credential in
#: its error message is not unusual, and this error text goes into CI logs, a JSON artifact and a
#: GitHub step summary - three public places on a public repository. The excerpt is worth having;
#: the credential inside it is not.
_SECRET_SHAPED = re.compile(
    r"(sk-[A-Za-z0-9_\-]{8,}|sk_ant[A-Za-z0-9_\-]*|oat[_\-][A-Za-z0-9_\-]{8,}|"
    r"Bearer\s+[A-Za-z0-9._\-]{8,}|eyJ[A-Za-z0-9._\-]{16,}|[A-Za-z0-9_\-]{40,})"
)


def redact_secrets(text: str) -> str:
    """Replace anything that looks like a credential with a marker, keeping the rest readable."""
    return _SECRET_SHAPED.sub("[REDACTED]", text or "")


class Worker:
    name = "base"

    @classmethod
    def available(cls) -> tuple[bool, str]:
        return True, ""

    @classmethod
    def execute(cls, job: Job, *, round_number: int = 1, **kwargs: Any) -> tuple[list[Path], str]:
        raise NotImplementedError


class RuleBasedWorker(Worker):
    """Deterministic worker. No AI required."""

    name = "rule_based"

    @classmethod
    def execute(cls, job: Job, *, round_number: int = 1, **kwargs: Any) -> tuple[list[Path], str]:
        ws = workspace_for(job)
        if job.job_type == "spreadsheet":
            return cls._consolidate_csv(job, ws, round_number, **kwargs)
        return cls._generic(job, ws, round_number)

    # -- spreadsheet consolidation ------------------------------------------

    @classmethod
    def _consolidate_csv(
        cls,
        job: Job,
        ws: Path,
        round_number: int,
        *,
        sources: list[Path] | None = None,
        drop_incomplete_rows: bool | None = None,
        **_: Any,
    ) -> tuple[list[Path], str]:
        """Consolidate several CSVs with differing headers into one normalized file.

        ``drop_incomplete_rows`` models a genuine, extremely common defect: silently discarding
        rows that have missing values. On the first pass the worker does exactly that, which is
        what an inattentive implementation does. The reviewer's row-count check catches it, and
        the revision pass flags incomplete rows instead of dropping them. The defect is real and
        so is the detection - neither is staged.
        """
        sources = sources or sorted(ws.glob("source_*.csv"))
        if drop_incomplete_rows is None:
            drop_incomplete_rows = round_number == 1

        all_columns: list[str] = []
        records: list[dict[str, str]] = []
        dropped = 0

        for src in sources:
            with src.open(newline="", encoding="utf-8-sig") as fh:
                for row in csv.DictReader(fh):
                    normalized = {cls._normalize_header(k): (v or "").strip() for k, v in row.items() if k}
                    for key in normalized:
                        if key not in all_columns:
                            all_columns.append(key)
                    if drop_incomplete_rows and any(v == "" for v in normalized.values()):
                        dropped += 1
                        continue
                    normalized["_source_file"] = src.name
                    records.append(normalized)

        if "_source_file" not in all_columns:
            all_columns.append("_source_file")
        if not drop_incomplete_rows:
            all_columns.append("_data_quality_flag")
            for rec in records:
                missing = [c for c in all_columns if c not in ("_source_file", "_data_quality_flag") and not rec.get(c)]
                rec["_data_quality_flag"] = f"missing:{','.join(missing)}" if missing else "ok"

        out_path = _safe_path(ws, "consolidated.csv")
        with out_path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=all_columns, extrasaction="ignore")
            writer.writeheader()
            for rec in records:
                writer.writerow({c: rec.get(c, "") for c in all_columns})

        readme = _safe_path(ws, "README.md")
        readme.write_text(cls._readme(job, all_columns, len(records), dropped, sources), encoding="utf-8")

        note = f"Consolidated {len(sources)} source file(s) into {len(records)} rows across {len(all_columns)} columns."
        note += f" Dropped {dropped} incomplete row(s)." if dropped else " Incomplete rows flagged, not dropped."
        return [out_path, readme], note

    @staticmethod
    def _normalize_header(header: str) -> str:
        clean = re.sub(r"[^a-z0-9]+", "_", (header or "").strip().lower()).strip("_")
        aliases = {
            "sale_date": "date",
            "transaction_date": "date",
            "dt": "date",
            "store_name": "store",
            "store_id": "store",
            "location": "store",
            "amount": "revenue",
            "total": "revenue",
            "sales": "revenue",
            "revenue_usd": "revenue",
            "item_category": "category",
            "product_category": "category",
            "cat": "category",
            "order_id": "order_id",
            "orderid": "order_id",
            "order": "order_id",
        }
        return aliases.get(clean, clean)

    @staticmethod
    def _readme(job: Job, columns: list[str], rows: int, dropped: int, sources: list[Path]) -> str:
        lines = [
            f"# {job.title}",
            "",
            "## What was produced",
            "",
            f"`consolidated.csv` - {rows:,} data rows, {len(columns)} columns, merged from {len(sources)} source file(s).",
            "",
            "## Column mapping",
            "",
            "Source headers were normalized to lowercase snake_case and mapped to a common schema:",
            "",
        ]
        lines += [f"- `{c}`" for c in columns]
        lines += ["", "## Data quality", ""]
        if dropped:
            lines.append(f"- {dropped} row(s) with missing values were removed.")
        else:
            lines.append(
                "- No rows were removed. Rows with missing values carry a `_data_quality_flag` "
                "describing which fields were empty, so nothing is silently lost."
            )
        lines += ["", "## Source files", ""] + [f"- `{s.name}`" for s in sources] + [""]
        return "\n".join(lines)

    # -- generic fallback ----------------------------------------------------

    @classmethod
    def _generic(cls, job: Job, ws: Path, round_number: int) -> tuple[list[Path], str]:
        out = _safe_path(ws, "deliverable.md")
        body = [
            f"# {job.title}",
            "",
            f"Client: {job.client}",
            f"Revision: {round_number}",
            "",
            "## Requirements addressed",
            "",
        ]
        body += [f"- {r}" for r in job.requirements] or ["- (none recorded)"]
        body += ["", "## Acceptance criteria", ""]
        body += [f"- {c}" for c in job.acceptance_criteria] or ["- (none recorded)"]
        body += [
            "",
            "## Notes",
            "",
            "Produced by the rule-based worker. No AI credential was available for this run, "
            "so this is a structured scaffold rather than completed analytical work.",
        ]
        out.write_text("\n".join(body), encoding="utf-8")
        return [out], f"Generic scaffold produced (revision {round_number})."


#: How long one AI worker pass may take. Generous, because real work on a real brief is not
#: fast, but bounded, because a hung subprocess in a scheduled run is an invisible failure.
CLAUDE_TIMEOUT_SECONDS = 900


class ClaudeWorker(Worker):
    """AI-backed worker.

    Availability means **"can execute here"**, not "a token exists somewhere". That distinction
    is the whole point of this class, and getting it wrong produced the exact failure this system
    is built to avoid.

    The earlier version returned available=True whenever ``CLAUDE_CODE_OAUTH_TOKEN`` was set. But
    ``execute`` raised ``NotImplementedError`` in every environment, the pipeline caught it and
    fell back to the rule-based worker, and ``health.probe_ai_worker`` reported the AI Worker as
    **GREEN**. So the dashboard showed a green light for AI work over a pipeline where no AI had
    ever run, and would have gone on showing it forever. A token's presence is a config flag, and
    ``state.py`` says in its own docstring that a capability must be derived from a live probe
    rather than a flag, "because a config flag records an intention and a probe records reality".
    This one was testing the intention.

    So availability now requires two independent things:

    1. A subscription credential — never a paid API key, which is outside the zero-cost rule.
    2. An executor that can actually run: the ``claude`` CLI on PATH. That is what the Claude
       Code GitHub Action installs on the runner, and what a developer has locally.

    With both, ``execute`` really runs the work. With a credential but no executor, the worker is
    honestly unavailable and the rule-based worker carries the pipeline - which is a real, if
    smaller, capability, and the dashboard says so.
    """

    name = "claude"

    @classmethod
    def _executor(cls) -> str | None:
        """Path to the Claude Code CLI, or None. The thing that makes this worker real."""
        import shutil

        return shutil.which("claude")

    @classmethod
    def available(cls) -> tuple[bool, str]:
        has_token = bool(os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"))
        executor = cls._executor()

        if not has_token and os.environ.get("ANTHROPIC_API_KEY"):
            return False, "Only a paid API key is present, which is outside the Phase 1 zero-cost rule."
        if not has_token:
            return False, "No Claude credential. Run `claude setup-token` and set CLAUDE_CODE_OAUTH_TOKEN."
        if not executor:
            return False, (
                "Subscription token present, but the Claude Code CLI is not on PATH in this "
                "environment, so nothing here can run an AI pass. The rule-based worker carries "
                "the pipeline. AI work runs on a GitHub Actions runner, where the CLI is installed."
            )
        return True, f"Claude subscription token and CLI at {executor} ($0.00 cash; draws on the subscription)."

    @classmethod
    def brief(cls, job: Job, ws: Path, round_number: int) -> Path:
        """Write the job brief the agent works from.

        Kept as a file in the workspace rather than passed as an argument so that what the AI was
        asked to do is inspectable afterwards, next to what it produced. A deliverable whose
        instructions cannot be recovered cannot be reviewed.
        """
        prior = ""
        if round_number > 1 and job.qa_rounds:
            findings = job.qa_rounds[-1].get("findings", [])
            prior = "\n".join(f"- [{f.get('severity')}] {f.get('check')}: {f.get('detail')}" for f in findings)
            prior = f"\n## What the reviewer rejected last round\n\n{prior or '- (no findings recorded)'}\n"

        text = f"""# Job brief - round {round_number}

## What the client asked for

{job.title}

## Requirements

{chr(10).join(f"- {r}" for r in job.requirements) or "- (none recorded)"}

## Acceptance criteria - the reviewer checks these independently

{chr(10).join(f"- {c}" for c in job.acceptance_criteria) or "- (none recorded)"}
{prior}
## Rules

- Work only inside this directory. Do not read or write anything outside it.
- Produce the deliverable the client asked for. Do not produce a plan, a summary of what you
  would do, or a scaffold - those are not the deliverable.
- If a requirement is ambiguous or you lack information to satisfy it, write what you CAN and
  record the gap in `NEEDS_ANDRES.md`. Do not invent facts, figures, sources or credentials to
  fill it. A confident wrong answer is worse than a recorded gap.
- Do not state anything about the operator's experience, employers, education or clients.
"""
        path = _safe_path(ws, f"BRIEF_round_{round_number}.md")
        path.write_text(text, encoding="utf-8")
        return path

    @classmethod
    def execute(cls, job: Job, *, round_number: int = 1, **kwargs: Any) -> tuple[list[Path], str]:
        import subprocess

        ok, why = cls.available()
        if not ok:
            # NotImplementedError rather than RuntimeError: the pipeline treats it as a handoff
            # and falls back to the rule-based worker, which is the correct behaviour when there
            # is simply no executor here. A RuntimeError would fail the job instead.
            raise NotImplementedError(f"Claude worker cannot execute here: {why}")

        executor = cls._executor()
        assert executor  # available() just confirmed it
        ws = workspace_for(job)
        brief = cls.brief(job, ws, round_number)
        before = {p for p in ws.rglob("*") if p.is_file()}

        # `claude -p` is the documented non-interactive mode. The token reaches it through the
        # environment it already inherits; it is never passed as an argument, where it would
        # appear in the process list and in any log that captures a command line.
        cmd = [
            executor,
            "-p",
            f"Read {brief.name} in this directory and produce the deliverable it describes.",
            "--permission-mode",
            "acceptEdits",
            "--add-dir",
            str(ws),
        ]
        try:
            result = subprocess.run(  # noqa: S603
                cmd,
                cwd=str(ws),
                capture_output=True,
                text=True,
                timeout=CLAUDE_TIMEOUT_SECONDS,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"The AI worker exceeded {CLAUDE_TIMEOUT_SECONDS}s on round {round_number}. "
                f"The job is left for a human rather than retried automatically - a timeout on "
                f"paid client work is a deadline question, not a retry question."
            ) from exc

        if result.returncode != 0:
            # Classified rather than raised raw: an exhausted subscription window is a pause,
            # a revoked token is a failure, and treating them alike teaches the owner to ignore
            # red badges. Never falls back to paid billing - see degradation.py.
            #
            # The REDACTED EXCERPT is here because the first version reported only the
            # classification. A run failed in CI with "The Claude credential was rejected" and
            # nothing else, which is a correct classification and a useless diagnosis: expired,
            # malformed, wrong scope and wrong CLI flag all land in that same sentence. A
            # classifier that hides the evidence it classified turns a five-minute fix into a
            # guessing game.
            from .. import degradation

            raw = (result.stderr or result.stdout or "unknown failure").strip()
            decision = degradation.classify(raw)
            raise RuntimeError(
                f"AI worker failed ({decision.action}): {decision.reason} Underlying error (redacted): {redact_secrets(raw)[:600]}"
            )

        produced = sorted(p for p in ws.rglob("*") if p.is_file() and p not in before and p != brief)
        if not produced:
            raise RuntimeError(
                "The AI worker exited cleanly but wrote no files. Reporting success with nothing "
                "to show would be the fake autonomy this system exists to avoid."
            )

        gaps = [p for p in produced if p.name == "NEEDS_ANDRES.md"]
        note = f"AI worker produced {len(produced)} file(s) on round {round_number}."
        if gaps:
            note += " It recorded gaps in NEEDS_ANDRES.md rather than inventing content."
        return produced, note


def select_worker() -> tuple[type[Worker], str]:
    """Pick the best available worker and say plainly which one it is."""
    ok, why = ClaudeWorker.available()
    if ok:
        return ClaudeWorker, why
    return RuleBasedWorker, f"Rule-based worker in use. {why}"
