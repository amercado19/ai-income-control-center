#!/usr/bin/env python3
"""Estimate this repository's GitHub Actions consumption, and check it against the $0 rule.

The headline answer is that a **public** repository gets unlimited free Actions minutes on
GitHub-hosted standard runners, so minutes are not the binding constraint here. That is worth
computing anyway rather than asserting, for two reasons: the number tells you what this would
cost if the repository is ever made private (it would then draw against the 2,000 free
minutes/month shared with the NFL and MLB pipelines), and the *other* Actions limits are real
and are the ones that actually bite.

The limit that bites is not minutes. It is this:

    **A scheduled workflow is disabled automatically after 60 days of repository inactivity.**

A repository that only runs scheduled jobs generates no "activity", so a quiet project switches
its own automation off and says nothing. That is a silent failure of exactly the kind this
project refuses everywhere else, so it is called out here and in OPERATIONS.md.

    python3 scripts/actions_budget.py [--private] [--json]
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

WORKFLOWS = Path(".github/workflows")

FREE_PRIVATE_MINUTES = 2000  # GitHub Free tier, per month, for private repositories
LINUX_MULTIPLIER = 1.0  # ubuntu-latest bills 1 minute per minute
SCHEDULE_DISABLE_DAYS = 60
BILLING_ROUNDS_UP_TO_WHOLE_MINUTES = True


@dataclass
class WorkflowEstimate:
    name: str
    file: str
    crons: list[str]
    runs_per_month: float
    minutes_per_run: float
    minutes_per_month: float
    scheduled: bool


def cron_runs_per_month(expr: str) -> float:
    """Approximate monthly firings for a 5-field cron.

    Deliberately approximate and deliberately generous - it exists to size a budget, not to
    schedule anything. Where a field is ambiguous it rounds towards *more* runs, because a
    budget that under-counts is worse than one that over-counts.
    """
    parts = expr.split()
    if len(parts) != 5:
        return 0.0
    minute, hour, dom, _month, dow = parts
    days_per_month = 30.44

    def count(field: str, total: int) -> int:
        if field == "*":
            return total
        n = 0
        for chunk in field.split(","):
            if chunk.startswith("*/"):
                step = int(chunk[2:])
                n += max(1, total // step)
            elif "-" in chunk:
                lo, hi = chunk.split("-")[:2]
                step = 1
                if "/" in hi:
                    hi, step_s = hi.split("/")
                    step = int(step_s)
                n += max(1, (int(hi) - int(lo) + 1) // step)
            else:
                n += 1
        return n

    per_day = count(minute, 60) * count(hour, 24)
    if dow != "*":
        days = days_per_month * (count(dow, 7) / 7)
    elif dom != "*":
        days = float(count(dom, 31))
    else:
        days = days_per_month
    return per_day * days


def read_workflows() -> list[WorkflowEstimate]:
    out: list[WorkflowEstimate] = []
    for path in sorted(WORKFLOWS.glob("*.yml")):
        text = path.read_text(encoding="utf-8")
        name_match = re.search(r"^name:\s*(.+)$", text, re.M)
        name = (name_match.group(1).strip() if name_match else path.stem).strip("\"'")
        crons = [m.group(1).strip().strip("\"'") for m in re.finditer(r"^\s*-\s*cron:\s*(.+?)\s*(?:#.*)?$", text, re.M)]
        timeouts = [int(m.group(1)) for m in re.finditer(r"timeout-minutes:\s*(\d+)", text)]

        # A reusable workflow called by another is billed under its caller, so counting it
        # separately would double-count every run.
        if path.name.startswith("_"):
            continue

        # Timeout is a ceiling, not a duration. These jobs are stdlib-only with a pip cache;
        # a third of the ceiling is a deliberately conservative working estimate.
        minutes_per_run = round((max(timeouts) if timeouts else 10) / 3.0, 1)
        runs = sum(cron_runs_per_month(c) for c in crons)
        if not crons:
            # push/PR triggered. Assume an active month of development.
            runs = 40.0
        out.append(
            WorkflowEstimate(
                name=name,
                file=path.name,
                crons=crons,
                runs_per_month=round(runs, 1),
                minutes_per_run=minutes_per_run,
                minutes_per_month=round(runs * minutes_per_run * LINUX_MULTIPLIER, 1),
                scheduled=bool(crons),
            )
        )
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--private", action="store_true", help="Cost this as if the repository were private")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if not WORKFLOWS.exists():
        print(f"No {WORKFLOWS}/ directory here. Run this from the repository root.")
        return 2

    rows = read_workflows()
    total = round(sum(r.minutes_per_month for r in rows), 1)
    scheduled_total = round(sum(r.minutes_per_month for r in rows if r.scheduled), 1)

    result = {
        "workflows": [asdict(r) for r in rows],
        "total_minutes_per_month": total,
        "scheduled_minutes_per_month": scheduled_total,
        "public_repo_cost_usd": 0.0,
        "private_repo_free_minutes": FREE_PRIVATE_MINUTES,
        "private_repo_headroom": round(FREE_PRIVATE_MINUTES - total, 1),
        "binding_constraint": (
            f"Not minutes. A scheduled workflow is disabled after {SCHEDULE_DISABLE_DAYS} days of "
            "repository inactivity, and a repo that only runs scheduled jobs generates no activity - "
            "so a quiet project switches its own automation off without saying anything."
        ),
    }

    if args.json:
        print(json.dumps(result, indent=2))
        return 0

    print("GITHUB ACTIONS BUDGET\n")
    print(f"  {'Workflow':22s} {'Runs/mo':>8s} {'Min/run':>8s} {'Min/mo':>8s}  Schedule")
    for r in rows:
        sched = ", ".join(r.crons) if r.crons else "on push / PR"
        print(f"  {r.name[:22]:22s} {r.runs_per_month:8.1f} {r.minutes_per_run:8.1f} {r.minutes_per_month:8.1f}  {sched}")
    print(f"\n  {'TOTAL':22s} {'':8s} {'':8s} {total:8.1f} minutes/month")

    print("\nCost:")
    print("  PUBLIC repository  : $0.00 - unlimited minutes on GitHub-hosted standard runners.")
    headroom = result["private_repo_headroom"]
    verdict = "fits" if headroom >= 0 else "DOES NOT FIT"
    print(f"  If ever made PRIVATE: {total:.0f} of {FREE_PRIVATE_MINUTES} free minutes/month - {verdict}, {headroom:.0f} left.")
    print("                        That allowance is shared across the whole account, so it would")
    print("                        compete with the NFL and MLB pipelines rather than adding to them.")

    print(f"\nThe limit that actually bites:\n  {result['binding_constraint']}")
    print("\n  Mitigation: any commit to the repository resets the clock. The pipeline's own data")
    print("  commits count, so an actively running system keeps itself alive - but a paused one")
    print("  does not, and that is exactly when you would least notice.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
