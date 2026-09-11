"""Independent reviewer (spec sections 23-26).

The reviewer's independence is structural, not a matter of discipline. ``review()`` takes the
client requirements, the acceptance criteria, and the produced artifacts. It does **not** take
the Job object, so there is no path by which the worker's own assessment (``Job.worker_notes``)
can reach it. ``assert_reviewer_input_clean`` enforces that at runtime, and a test asserts it.

This matters because a reviewer told "the worker thinks this is done" grades the claim rather
than the artifact, which is how AI review theatre happens: two models agreeing with each other
and neither checking.

Verdicts follow spec section 23:
    90+     READY
    75-89   AUTO_REVISE   (at most 2 automatic loops, then escalate)
    <75     HUMAN_REVIEW
A hard failure in security or file integrity escalates to a human regardless of the average.
"""

from __future__ import annotations

import ast
import csv
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from ..models import QAReport

FORBIDDEN_REVIEWER_KEYS = {"worker_notes", "worker_assessment", "worker_confidence", "self_score"}


class ReviewerContaminationError(RuntimeError):
    """The reviewer was handed the worker's own opinion. That destroys the independence."""


def assert_reviewer_input_clean(payload: dict[str, Any]) -> None:
    leaked = FORBIDDEN_REVIEWER_KEYS & set(payload)
    if leaked:
        raise ReviewerContaminationError(
            f"Reviewer input contains worker self-assessment: {sorted(leaked)}. "
            "The reviewer must evaluate the artifact, not the worker's claim about it."
        )


# ---------------------------------------------------------------------------
# Spreadsheet / CSV QA (spec section 25)
# ---------------------------------------------------------------------------


def _read_csv(path: Path) -> tuple[list[str], list[list[str]]]:
    with path.open(newline="", encoding="utf-8-sig") as fh:
        rows = list(csv.reader(fh))
    if not rows:
        return [], []
    return rows[0], rows[1:]


def qa_spreadsheet(
    output_path: Path, source_path: Path | None = None, expected_columns: list[str] | None = None, expected_row_count: int | None = None
) -> tuple[dict[str, float], list[dict[str, Any]]]:
    """Verify row counts, column counts, nulls, duplicates, and unexpected data loss.

    ``expected_row_count`` covers the multi-source case, where the reviewer knows how many input
    rows existed in total but there is no single source file to diff against.
    """
    findings: list[dict[str, Any]] = []
    scores = {
        "requirements_satisfied": 100.0,
        "accuracy": 100.0,
        "completeness": 100.0,
        "formatting": 100.0,
        "file_integrity": 100.0,
    }

    if not output_path.exists():
        findings.append({"severity": "critical", "check": "file_exists", "detail": f"{output_path.name} was not produced."})
        return dict.fromkeys(scores, 0.0), findings

    try:
        header, rows = _read_csv(output_path)
    except (OSError, UnicodeDecodeError, csv.Error) as exc:
        findings.append({"severity": "critical", "check": "readable", "detail": f"Unreadable: {exc}"})
        return dict.fromkeys(scores, 0.0), findings

    if not header:
        findings.append({"severity": "critical", "check": "header", "detail": "No header row."})
        scores["file_integrity"] = 0.0

    # Row-count preservation is the single most valuable spreadsheet check: silent row loss is
    # the most common and most damaging defect in data-cleaning work.
    expected: int | None = expected_row_count
    if expected is None and source_path and source_path.exists():
        expected = len(_read_csv(source_path)[1])

    if expected is not None:
        if len(rows) != expected:
            lost = expected - len(rows)
            sev = "critical" if lost > 0 else "major"
            findings.append(
                {
                    "severity": sev,
                    "check": "row_count",
                    "detail": f"Expected {expected:,} data rows, output has {len(rows):,} "
                    f"({'lost ' + str(lost) if lost > 0 else 'gained ' + str(-lost)} rows). "
                    "Silent row loss is the most damaging defect in data-cleaning work.",
                }
            )
            scores["completeness"] = 0.0 if lost > 0 else 60.0
            scores["accuracy"] = 40.0

    if expected_columns:
        missing = [c for c in expected_columns if c not in header]
        if missing:
            findings.append(
                {
                    "severity": "critical",
                    "check": "columns",
                    "detail": f"Missing required column(s): {', '.join(missing)}.",
                }
            )
            scores["requirements_satisfied"] = 30.0

    # Ragged rows
    ragged = [i for i, r in enumerate(rows, start=2) if len(r) != len(header)]
    if ragged:
        findings.append(
            {
                "severity": "major",
                "check": "ragged_rows",
                "detail": f"{len(ragged)} row(s) have a different field count than the header (first at line {ragged[0]}).",
            }
        )
        scores["file_integrity"] = min(scores["file_integrity"], 55.0)

    # Nulls and duplicates are reported, not failed - they are often legitimate.
    null_counts = {h: 0 for h in header}
    for r in rows:
        for idx, h in enumerate(header):
            if idx < len(r) and r[idx].strip() == "":
                null_counts[h] += 1
    heavy_nulls = {h: n for h, n in null_counts.items() if rows and n / len(rows) > 0.5}
    if heavy_nulls:
        findings.append(
            {
                "severity": "minor",
                "check": "nulls",
                "detail": "Columns more than 50% empty: " + ", ".join(f"{h} ({n})" for h, n in heavy_nulls.items()),
            }
        )
        scores["completeness"] = min(scores["completeness"], 80.0)

    seen: set[str] = set()
    dupes = 0
    for r in rows:
        key = "\x1f".join(r)
        if key in seen:
            dupes += 1
        seen.add(key)
    if dupes:
        findings.append(
            {
                "severity": "minor",
                "check": "duplicates",
                "detail": f"{dupes} fully duplicated row(s).",
            }
        )
        scores["accuracy"] = min(scores["accuracy"], 85.0)

    findings.append(
        {
            "severity": "info",
            "check": "summary",
            "detail": f"{len(rows):,} data rows x {len(header)} columns; {dupes} duplicates; {sum(null_counts.values())} empty cells.",
        }
    )
    return scores, findings


# ---------------------------------------------------------------------------
# Code QA (spec section 24)
# ---------------------------------------------------------------------------


def qa_code(workspace: Path) -> tuple[dict[str, float], list[dict[str, Any]]]:
    """Run real checks. Never claim code works without executing something that proves it."""
    findings: list[dict[str, Any]] = []
    scores = {"requirements_satisfied": 100.0, "accuracy": 100.0, "formatting": 100.0, "security": 100.0}

    py_files = list(workspace.rglob("*.py"))
    if not py_files:
        findings.append({"severity": "major", "check": "files", "detail": "No Python files found."})
        return dict.fromkeys(scores, 40.0), findings

    # Syntax must parse. This is non-negotiable and needs no external tool.
    for path in py_files:
        try:
            ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError as exc:
            findings.append(
                {
                    "severity": "critical",
                    "check": "syntax",
                    "detail": f"{path.name} line {exc.lineno}: {exc.msg}",
                }
            )
            scores["accuracy"] = 0.0

    scores.update(_run_tool_checks(workspace, findings))
    _scan_dangerous_patterns(py_files, findings, scores)
    return scores, findings


def _run_tool_checks(workspace: Path, findings: list[dict[str, Any]]) -> dict[str, float]:
    out: dict[str, float] = {}

    ruff = _run([sys.executable, "-m", "ruff", "check", str(workspace)], workspace)
    if ruff is None:
        findings.append({"severity": "info", "check": "lint", "detail": "ruff not available; lint skipped."})
    elif ruff.returncode != 0:
        count = len([ln for ln in ruff.stdout.splitlines() if ":" in ln])
        findings.append({"severity": "minor", "check": "lint", "detail": f"ruff reported {count} finding(s)."})
        out["formatting"] = 70.0
    else:
        findings.append({"severity": "info", "check": "lint", "detail": "ruff clean."})

    if list(workspace.rglob("test_*.py")) or list(workspace.rglob("*_test.py")):
        tests = _run([sys.executable, "-m", "pytest", "-q", str(workspace)], workspace, timeout=180)
        if tests is None:
            findings.append({"severity": "info", "check": "tests", "detail": "pytest not available."})
        elif tests.returncode != 0:
            findings.append(
                {
                    "severity": "critical",
                    "check": "tests",
                    "detail": "Tests FAILED: " + (tests.stdout.strip().splitlines() or ["no output"])[-1][:200],
                }
            )
            out["requirements_satisfied"] = 20.0
            out["accuracy"] = 20.0
        else:
            findings.append({"severity": "info", "check": "tests", "detail": "All tests passed."})
    else:
        findings.append(
            {
                "severity": "major",
                "check": "tests",
                "detail": "No tests present. Correctness is unverified - this cannot be claimed as working.",
            }
        )
        out["requirements_satisfied"] = 65.0

    return out


DANGEROUS = [
    (re.compile(r"\beval\s*\("), "eval() on untrusted input is a code-execution risk"),
    (re.compile(r"\bexec\s*\("), "exec() is a code-execution risk"),
    (re.compile(r"shell\s*=\s*True"), "subprocess with shell=True invites injection"),
    (re.compile(r"verify\s*=\s*False"), "TLS verification disabled"),
    (re.compile(r"pickle\.loads?\("), "pickle deserialization of untrusted data is unsafe"),
    (re.compile(r"(?i)(api[_-]?key|secret|password|token)\s*=\s*[\"'][A-Za-z0-9_\-]{16,}[\"']"), "hardcoded credential"),
]


def _scan_dangerous_patterns(py_files: list[Path], findings: list[dict[str, Any]], scores: dict[str, float]) -> None:
    for path in py_files:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for pattern, why in DANGEROUS:
            if pattern.search(text):
                findings.append({"severity": "major", "check": "security", "detail": f"{path.name}: {why}."})
                scores["security"] = min(scores.get("security", 100.0), 50.0)


def _run(cmd: list[str], cwd: Path, timeout: int = 90) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, check=False)  # noqa: S603
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None


# ---------------------------------------------------------------------------
# Research QA (spec section 26)
# ---------------------------------------------------------------------------

_URL = re.compile(r"https?://[^\s\)\]]+")
_CITATION_MARKER = re.compile(r"\[(?:\d+|source|ref)\]|\(\s*source:", re.I)
_HEDGE = re.compile(r"\b(approximately|estimated|roughly|reportedly|appears to|may be|unverified|as of)\b", re.I)
_ABSOLUTE = re.compile(r"\b(always|never|all|every|none|guaranteed|proven|certainly)\b", re.I)
# Fabricated-citation tells: a URL that is obviously a placeholder.
_FAKE_URL = re.compile(r"(example\.(com|org|invalid)|placeholder|lorem|\bTODO\b|\bXXX\b)", re.I)


def qa_research(document: str, min_sources: int = 3) -> tuple[dict[str, float], list[dict[str, Any]]]:
    findings: list[dict[str, Any]] = []
    scores = {
        "requirements_satisfied": 100.0,
        "accuracy": 100.0,
        "completeness": 100.0,
        "source_verification": 100.0,
        "professional_quality": 100.0,
    }

    urls = _URL.findall(document)
    unique = sorted(set(urls))
    if len(unique) < min_sources:
        findings.append(
            {
                "severity": "major",
                "check": "sources",
                "detail": f"Only {len(unique)} distinct source(s); {min_sources} required.",
            }
        )
        scores["source_verification"] = 45.0

    fake = [u for u in unique if _FAKE_URL.search(u)]
    if fake:
        findings.append(
            {
                "severity": "critical",
                "check": "fabricated_sources",
                "detail": f"Placeholder or fabricated URL(s): {', '.join(fake[:3])}.",
            }
        )
        scores["source_verification"] = 0.0
        scores["accuracy"] = 30.0

    if not _CITATION_MARKER.search(document) and len(unique) < 2:
        findings.append(
            {
                "severity": "major",
                "check": "attribution",
                "detail": "Claims are not attributed to sources.",
            }
        )
        scores["source_verification"] = min(scores["source_verification"], 55.0)

    absolutes = len(_ABSOLUTE.findall(document))
    hedges = len(_HEDGE.findall(document))
    if absolutes > 5 and hedges == 0:
        findings.append(
            {
                "severity": "minor",
                "check": "overclaiming",
                "detail": f"{absolutes} absolute claims and no uncertainty language. Facts and inference are not separated.",
            }
        )
        scores["accuracy"] = min(scores["accuracy"], 80.0)

    if len(document.strip()) < 400:
        findings.append({"severity": "major", "check": "length", "detail": "Document is very short."})
        scores["completeness"] = 50.0

    findings.append(
        {
            "severity": "info",
            "check": "summary",
            "detail": f"{len(document.split()):,} words; {len(unique)} distinct sources; {hedges} hedges; {absolutes} absolutes.",
        }
    )
    return scores, findings


# ---------------------------------------------------------------------------
# Top-level review
# ---------------------------------------------------------------------------


def review(
    *,
    job_id: str,
    job_type: str,
    requirements: list[str],
    acceptance_criteria: list[str],
    artifacts: list[Path],
    round_number: int = 1,
    source_file: Path | None = None,
    expected_columns: list[str] | None = None,
    expected_row_count: int | None = None,
    document_text: str = "",
    extra_context: dict[str, Any] | None = None,
) -> QAReport:
    """Grade the artifacts. Deliberately takes no Job object - see module docstring."""
    assert_reviewer_input_clean(extra_context or {})

    report = QAReport(job_id=job_id, round=round_number, reviewer="rule_based")
    scores: dict[str, float] = {c: 100.0 for c in QAReport.CATEGORIES}
    findings: list[dict[str, Any]] = []

    if not artifacts and not document_text:
        for c in QAReport.CATEGORIES:
            scores[c] = 0.0
        findings.append({"severity": "critical", "check": "deliverables", "detail": "No deliverable was produced."})
    else:
        if job_type == "spreadsheet":
            for art in artifacts:
                if art.suffix.lower() in (".csv", ".tsv"):
                    s, f = qa_spreadsheet(art, source_file, expected_columns, expected_row_count)
                    _merge(scores, s)
                    findings += f
        elif job_type == "code":
            for art in artifacts:
                if art.is_dir():
                    s, f = qa_code(art)
                    _merge(scores, s)
                    findings += f
        elif job_type == "research":
            text = document_text or _read_text(artifacts)
            s, f = qa_research(text)
            _merge(scores, s)
            findings += f

        # Universal checks, whatever the job type.
        _check_requirements_mentioned(requirements, acceptance_criteria, artifacts, document_text, scores, findings)
        _check_file_hygiene(artifacts, scores, findings)

    for category, value in scores.items():
        setattr(report, category, round(value, 1))
    report.findings = findings
    report.compute_overall()
    report.decide()
    return report


def _merge(target: dict[str, float], incoming: dict[str, float]) -> None:
    """Worst score wins. Averaging away a critical failure is how bad work ships."""
    for k, v in incoming.items():
        if k in target:
            target[k] = min(target[k], v)


def _read_text(artifacts: list[Path]) -> str:
    parts = []
    for art in artifacts:
        if art.is_file() and art.suffix.lower() in (".md", ".txt", ".html"):
            try:
                parts.append(art.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError):
                continue
    return "\n\n".join(parts)


def _check_requirements_mentioned(
    requirements: list[str],
    acceptance: list[str],
    artifacts: list[Path],
    document_text: str,
    scores: dict[str, float],
    findings: list[dict[str, Any]],
) -> None:
    """Weak but honest: checks that each acceptance criterion's key terms appear somewhere in the
    deliverable. It cannot verify semantics, and the finding says so rather than implying it can."""
    if not acceptance:
        return
    corpus = (document_text + " " + " ".join(a.name for a in artifacts)).lower()
    for art in artifacts:
        if art.is_file() and art.stat().st_size < 2_000_000:
            try:
                corpus += " " + art.read_text(encoding="utf-8", errors="ignore").lower()
            except OSError:
                continue

    unmet = []
    for crit in acceptance:
        terms = [t for t in re.findall(r"[a-z]{4,}", crit.lower()) if t not in _STOPWORDS][:4]
        if terms and not any(t in corpus for t in terms):
            unmet.append(crit)

    if unmet:
        findings.append(
            {
                "severity": "major",
                "check": "acceptance_criteria",
                "detail": f"{len(unmet)} acceptance criterion/criteria have no textual trace in the "
                f"deliverable (keyword check only, not a semantic check): " + "; ".join(c[:70] for c in unmet[:3]),
            }
        )
        scores["requirements_satisfied"] = min(scores["requirements_satisfied"], 60.0)


_STOPWORDS = {"must", "should", "with", "that", "this", "from", "have", "will", "each", "into", "them", "then", "than", "when", "your"}


def _check_file_hygiene(artifacts: list[Path], scores: dict[str, float], findings: list[dict[str, Any]]) -> None:
    for art in artifacts:
        if art.is_file():
            if art.stat().st_size == 0:
                findings.append({"severity": "critical", "check": "empty_file", "detail": f"{art.name} is empty."})
                scores["file_integrity"] = 0.0
            if art.name.startswith(".") or " " in art.name:
                findings.append(
                    {
                        "severity": "minor",
                        "check": "filename",
                        "detail": f"'{art.name}' is not a clean deliverable filename.",
                    }
                )
                scores["formatting"] = min(scores["formatting"], 85.0)
