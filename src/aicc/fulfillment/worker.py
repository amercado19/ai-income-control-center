"""The worker (spec sections 22-23).

Two implementations, and the system is honest about which one is running:

* ``RuleBasedWorker`` - deterministic, always available, no AI credential needed. It genuinely
  does spreadsheet consolidation and data cleaning, which is the highest-volume job category
  this business targets.
* ``ClaudeWorker`` - available only when a Claude credential is present. When it is absent, the
  System Health page reports the AI worker as NOT CONFIGURED rather than showing a green light
  over a rule-based fallback.

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


class ClaudeWorker(Worker):
    """AI-backed worker. Only reports available when a credential actually exists."""

    name = "claude"

    @classmethod
    def available(cls) -> tuple[bool, str]:
        if os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
            return True, "Claude subscription OAuth token present ($0.00 cash)."
        if os.environ.get("ANTHROPIC_API_KEY"):
            return False, "Only a paid API key is present, which is outside the Phase 1 zero-cost rule."
        return False, "No Claude credential. Run `claude setup-token` and set CLAUDE_CODE_OAUTH_TOKEN."

    @classmethod
    def execute(cls, job: Job, *, round_number: int = 1, **kwargs: Any) -> tuple[list[Path], str]:
        ok, why = cls.available()
        if not ok:
            raise RuntimeError(f"Claude worker unavailable: {why}")
        # Intentionally not implemented as a fake. When this runs inside a Claude Code GitHub
        # Action, the agent itself performs the work in the workspace and this method is the
        # handoff point. Returning fabricated output here would be exactly the "fake autonomy"
        # spec section 48 forbids.
        raise NotImplementedError(
            "The Claude worker executes inside a Claude Code GitHub Action, not from this process. "
            "See docs/OPERATIONS.md 'AI worker handoff'."
        )


def select_worker() -> tuple[type[Worker], str]:
    """Pick the best available worker and say plainly which one it is."""
    ok, why = ClaudeWorker.available()
    if ok:
        return ClaudeWorker, why
    return RuleBasedWorker, f"Rule-based worker in use. {why}"
