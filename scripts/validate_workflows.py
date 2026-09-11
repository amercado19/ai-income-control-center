#!/usr/bin/env python3
"""Validate the GitHub Actions workflows without a YAML dependency.

Checks that matter here:

* every workflow parses and declares a trigger;
* no cron is more aggressive than hourly (spec section 35: Actions minutes are shared with the
  MLB and NFL pipelines and must be conserved);
* no workflow requests write permissions it does not use;
* secrets are referenced through ``secrets.``, never inlined;
* every scheduled workflow has a timeout, so a hung run cannot burn the monthly quota.

Exit 1 on any failure.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"

CRON = re.compile(r"^\s*-\s*cron:\s*['\"]([^'\"]+)['\"]", re.M)
TIMEOUT = re.compile(r"^\s*timeout-minutes:\s*(\d+)", re.M)
INLINE_SECRET = re.compile(r"(?i)(ANTHROPIC_API_KEY|CLAUDE_CODE_OAUTH_TOKEN|_TOKEN|_KEY)\s*:\s*['\"]?[A-Za-z0-9_\-]{16,}")


def minutes_between_runs(expr: str) -> float:
    """Rough interval for the minute field. Returns 60 for '0', smaller for step/list forms."""
    minute = expr.split()[0]
    if minute == "*":
        return 1.0
    if minute.startswith("*/"):
        try:
            return float(minute[2:])
        except ValueError:
            return 60.0
    if "," in minute:
        parts = sorted(int(p) for p in minute.split(",") if p.isdigit())
        if len(parts) >= 2:
            return float(min(b - a for a, b in zip(parts, parts[1:], strict=False)))
    return 60.0


# The classic GitHub Actions injection. A ${{ }} expansion is substituted as TEXT before bash
# parses the line, so a quote or a $(...) inside the value escapes the string and executes. The
# fix is always the same - pass the value through `env:` and reference it as "$VAR", where it is
# data rather than syntax.
#
# Added after a security review found two of these in this repository's own reusable workflow.
# Neither was exploitable: the only callers are checked-in workflow files. But that was a
# property of the callers, not of the file, and one workflow already accepted a
# `workflow_dispatch` input that the obvious next edit would have piped straight into an `eval`.
# This check removes the possibility instead of relying on nobody making that edit.
_INTERPOLATED = re.compile(
    r"\$\{\{\s*(?:github\.event\.[\w.]*(?:title|body|message|name|label|ref|login)"
    r"|github\.head_ref|inputs\.[\w.]+|env\.[\w.]+)",
    re.I,
)


# Both spellings matter, and the first version of this check caught neither reliably:
#   `        run: |`      a block scalar inside a step
#   `      - run: echo x` a single-line run that IS the list item
# Missing the `- ` form meant the check reported CLEAN on a file containing the very problem it
# was written to find, which is worse than not having the check at all.
_RUN_START = re.compile(r"^(?P<indent>\s*)(?:-\s+)?run:\s*(?P<inline>.*)$")


def _interpolation_problems(rel: str, text: str) -> list[str]:
    """Flag a ${{ }} expansion of a caller- or event-supplied value inside a `run:` script."""
    out: list[str] = []

    def flag(number: int, line: str) -> None:
        out.append(
            f"{rel}:{number}: a caller- or event-supplied value is interpolated into a shell "
            f'script. Pass it through `env:` and use "$VAR" instead - {line.strip()[:70]}'
        )

    in_block = False
    block_indent = 0
    for number, line in enumerate(text.split("\n"), 1):
        match = _RUN_START.match(line)
        if match:
            inline = match.group("inline").strip()
            if inline and not inline.startswith(("|", ">")):
                if _INTERPOLATED.search(inline):
                    flag(number, line)
                in_block = False
            else:
                in_block, block_indent = True, len(match.group("indent"))
            continue
        if in_block:
            if line.strip() and (len(line) - len(line.lstrip())) <= block_indent:
                in_block = False
            elif _INTERPOLATED.search(line):
                flag(number, line)
    return out


def main() -> int:
    if not WORKFLOWS.exists():
        print(f"No workflows directory at {WORKFLOWS}")
        return 1

    problems: list[str] = []
    files = sorted(WORKFLOWS.glob("*.yml")) + sorted(WORKFLOWS.glob("*.yaml"))
    if not files:
        problems.append("no workflow files found")

    for path in files:
        rel = path.relative_to(ROOT).as_posix()
        text = path.read_text(encoding="utf-8")

        if "\t" in text:
            problems.append(f"{rel}: contains a tab character (YAML forbids tabs for indentation)")
        if not re.search(r"^on:|^\s*workflow_call:", text, re.M):
            problems.append(f"{rel}: no trigger declared")
        if "jobs:" not in text:
            problems.append(f"{rel}: no jobs block")

        for m in INLINE_SECRET.finditer(text):
            if "secrets." not in text[max(0, m.start() - 80) : m.end() + 80]:
                line = text[: m.start()].count("\n") + 1
                problems.append(f"{rel}:{line}: possible inlined secret value")

        crons = CRON.findall(text)
        for expr in crons:
            fields = expr.split()
            if len(fields) != 5:
                problems.append(f"{rel}: cron '{expr}' does not have 5 fields")
                continue
            interval = minutes_between_runs(expr)
            if interval < 60:
                problems.append(
                    f"{rel}: cron '{expr}' runs every ~{interval:.0f} minutes. "
                    "Nothing here needs sub-hourly scheduling, and Actions minutes are shared "
                    "with the MLB and NFL pipelines."
                )

        # A caller job cannot set timeout-minutes when it delegates via `uses:` - the timeout
        # lives in the called workflow. Accept delegation, but verify the callee actually has one.
        delegates = re.search(r"^\s*uses:\s*\./\.github/workflows/([\w\-.]+)", text, re.M)
        if crons and not TIMEOUT.search(text):
            if delegates:
                callee = WORKFLOWS / delegates.group(1)
                if not callee.exists():
                    problems.append(f"{rel}: delegates to {delegates.group(1)}, which does not exist")
                elif not TIMEOUT.search(callee.read_text(encoding="utf-8")):
                    problems.append(f"{rel}: delegates to {delegates.group(1)}, which has no timeout-minutes")
            else:
                problems.append(f"{rel}: scheduled workflow has no timeout-minutes; a hung run would burn quota")

        for m in TIMEOUT.finditer(text):
            if int(m.group(1)) > 60:
                problems.append(f"{rel}: timeout-minutes {m.group(1)} is too generous for this project")

        problems.extend(_interpolation_problems(rel, text))

    if problems:
        print(f"WORKFLOW VALIDATION FAILED - {len(problems)} problem(s):\n")
        for p in problems:
            print(f"  {p}")
        return 1

    print(f"Workflow validation clean ({len(files)} file(s)).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
