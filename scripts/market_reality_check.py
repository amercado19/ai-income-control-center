#!/usr/bin/env python3
"""Measure whether the open project marketplace actually contains winnable work.

This exists because a claim like "Freelancer.com is too competitive" is worth nothing without
a number attached, and because the number changes: if the market improves, this should be able
to say so, and if it degrades further, the same. It is a measurement, not an opinion, and it is
re-runnable.

It walks the live public Freelancer.com project API across the operator's job categories and
applies the funnel in order, reporting how many survive each step:

    active projects -> priced in USD -> few enough bids to be winnable
                    -> budget above the operator's floor

No credentials, no cost, no scraping: this is the platform's own documented public endpoint,
read-only, and nothing is stored.

    python3 scripts/market_reality_check.py [--pages 5] [--json]
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from aicc.config import PROFILE  # noqa: E402
from aicc.connectors.base import ConnectorError, http_get_json  # noqa: E402
from aicc.connectors.freelancer_com import API, DEFAULT_JOB_IDS, MAX_BID_COUNT  # noqa: E402


def fetch(pages: int) -> list[dict[str, Any]]:
    jobs = "&".join(f"jobs[]={j}" for j in DEFAULT_JOB_IDS)
    rows: list[dict[str, Any]] = []
    for page in range(pages):
        url = f"{API}?limit=100&offset={page * 100}&{jobs}&job_details=true&full_description=true"
        try:
            payload = http_get_json(url)
        except ConnectorError as exc:
            print(f"  (stopped at page {page + 1}: {exc})")
            break
        batch = payload.get("result", {}).get("projects", [])
        if not batch:
            break
        rows.extend(batch)
        time.sleep(0.6)  # courtesy pacing on a public endpoint
    return rows


def bid_count(project: dict[str, Any]) -> int:
    return int((project.get("bid_stats") or {}).get("bid_count") or 0)


def budget(project: dict[str, Any], key: str) -> float:
    return float((project.get("budget") or {}).get(key) or 0.0)


def measure(rows: list[dict[str, Any]]) -> dict[str, Any]:
    floor = PROFILE.minimum_job_value
    usd = [p for p in rows if (p.get("currency") or {}).get("code") == "USD"]
    winnable = [p for p in usd if bid_count(p) <= MAX_BID_COUNT]
    above_floor = [p for p in winnable if budget(p, "maximum") >= floor]
    survivors = [p for p in above_floor if budget(p, "minimum") >= floor]

    bids = sorted(bid_count(p) for p in rows)
    budgets = sorted(budget(p, "maximum") for p in usd if budget(p, "maximum") > 0)
    currencies: dict[str, int] = {}
    for p in rows:
        code = (p.get("currency") or {}).get("code") or "?"
        currencies[code] = currencies.get(code, 0) + 1

    return {
        "sampled": len(rows),
        "funnel": {
            "active": len(rows),
            "priced_in_usd": len(usd),
            f"and_at_most_{MAX_BID_COUNT}_bids": len(winnable),
            f"and_max_budget_over_{floor:.0f}": len(above_floor),
            f"and_min_budget_over_{floor:.0f}": len(survivors),
        },
        "bids_per_project": {
            "median": statistics.median(bids) if bids else None,
            "p90": bids[int(len(bids) * 0.9)] if bids else None,
            "max": bids[-1] if bids else None,
        },
        "usd_max_budget": {
            "median": statistics.median(budgets) if budgets else None,
            "p90": budgets[int(len(budgets) * 0.9)] if budgets else None,
        },
        "currencies": dict(sorted(currencies.items(), key=lambda kv: -kv[1])[:8]),
        "survivors": [
            {
                "title": (p.get("title") or "")[:80],
                "min": budget(p, "minimum"),
                "max": budget(p, "maximum"),
                "bids": bid_count(p),
            }
            for p in survivors[:15]
        ],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pages", type=int, default=5, help="100 projects per page")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if not args.json:
        print(f"Sampling up to {args.pages * 100} active Freelancer.com projects in {len(DEFAULT_JOB_IDS)} job categories...")
    rows = fetch(args.pages)
    if not rows:
        print("No data returned. The API may be unreachable from here.")
        return 1
    result = measure(rows)

    if args.json:
        print(json.dumps(result, indent=2))
        return 0

    print(f"\nSampled {result['sampled']} active projects.\n")
    print("Funnel:")
    for label, count in result["funnel"].items():
        share = 100 * count / max(result["sampled"], 1)
        print(f"  {label.replace('_', ' '):38s} {count:5d}   ({share:5.1f}%)")
    b = result["bids_per_project"]
    print(f"\nBids per project : median {b['median']}, p90 {b['p90']}, max {b['max']}")
    u = result["usd_max_budget"]
    if u["median"] is not None:
        print(f"USD max budget   : median ${u['median']:,.0f}, p90 ${u['p90']:,.0f}")
    print(f"Currencies       : {result['currencies']}")

    print(f"\nSurvivors ({len(result['survivors'])}):")
    for s in result["survivors"]:
        print(f"  ${s['min']:>7,.0f}-${s['max']:>8,.0f}  {s['bids']:>3} bids  {s['title']}")
    if not result["survivors"]:
        print("  none")
    print(
        "\nRead this as a measurement of the CHANNEL, not of the operator. A median of ~78 bidders\n"
        "on a ~$250 job is a market where winning costs more in unpaid proposal writing than the\n"
        "job pays. Survivors still go through the full risk scoring - a listing surviving the\n"
        "funnel is not the same as a listing worth bidding on."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
