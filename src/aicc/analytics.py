"""Analytics (spec sections 40, 41).

Every metric here is computed from records that actually exist. Nothing is estimated, assumed,
seeded, or back-filled. When there are not enough observations to compute a rate, the value is
``None`` and the dashboard renders **Insufficient Data** rather than a zero or a placeholder -
a zero win rate and an unknown win rate are different facts, and conflating them is how a
dashboard starts lying to you.

Every metric also carries its own metadata: the formula, the number of observations behind it,
and the timestamp it was computed. ``describe_metrics()`` returns that, and the dashboard shows
it on hover, so no figure on screen is unfalsifiable.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from . import storage
from .models import JobStatus, OpportunityStatus

MIN_OBSERVATIONS_FOR_RATE = 5
"""Below this many observations a rate is noise. 1 win from 1 proposal is not a 100% win rate."""


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _parse(ts: str) -> datetime | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    except ValueError:
        return None


def _within(ts: str, days: int) -> bool:
    dt = _parse(ts)
    return dt is not None and dt >= _utc_now() - timedelta(days=days)


def compute_metrics(*, include_demo: bool = False) -> dict[str, Any]:
    """Full metric set. ``include_demo`` is False everywhere the dashboard shows REAL figures."""
    opps = [o for o in storage.opportunities.all() + storage.opportunities_archive.all() if include_demo or not o.is_demo]
    props = [p for p in storage.proposals.all() if include_demo or not p.is_demo]
    jobs = [j for j in storage.jobs.all() if include_demo or not j.is_demo]
    rev = [r for r in storage.revenue.all() if include_demo or not r.is_demo]

    submitted = [p for p in props if p.status == "SUBMITTED"]
    won = [o for o in opps if o.status == OpportunityStatus.WON.value]
    lost = [o for o in opps if o.status == OpportunityStatus.LOST.value]
    interviews = [o for o in opps if o.status == OpportunityStatus.INTERVIEW.value]
    completed = [j for j in jobs if j.status == JobStatus.DELIVERED.value]

    decided = len(won) + len(lost)
    win_rate = round(100 * len(won) / decided, 1) if decided >= MIN_OBSERVATIONS_FOR_RATE else None

    qa_scores = [s for j in jobs for s in [j.latest_qa_score()] if s is not None]
    avg_qa = round(sum(qa_scores) / len(qa_scores), 1) if qa_scores else None

    revised = [j for j in jobs if j.revision_count > 0]
    revision_rate = round(100 * len(revised) / len(jobs), 1) if len(jobs) >= MIN_OBSERVATIONS_FOR_RATE else None

    human_minutes = [j.human_minutes_spent for j in completed if j.human_minutes_spent > 0]
    avg_human_minutes = round(sum(human_minutes) / len(human_minutes), 1) if human_minutes else None

    net_total = round(sum(r.net for r in rev), 2)
    gross_total = round(sum(r.gross for r in rev), 2)
    margin = round(100 * net_total / gross_total, 1) if gross_total > 0 else None
    avg_order = round(gross_total / len(rev), 2) if rev else None

    return {
        # volume
        #
        # `discovered` is CUMULATIVE - active plus archived, everything ever seen. `active` is
        # what is in the store right now. The two differ by the archive, and an overview that
        # showed 115 next to a sidebar badge of 72 and a CLI count of 85, all labelled
        # "opportunities" and none explained, is how a dashboard loses the reader's trust in
        # every other number on it.
        "opportunities_discovered": len(opps),
        "opportunities_active": len([o for o in storage.opportunities.all() if include_demo or not o.is_demo]),
        "opportunities_archived": len([o for o in storage.opportunities_archive.all() if include_demo or not o.is_demo]),
        "opportunities_qualified": sum(1 for o in opps if o.score_band in ("EXCELLENT", "STRONG")),
        "opportunities_rejected": sum(1 for o in opps if o.score_breakdown.get("rejected")),
        "proposals_drafted": len(props),
        "proposals_awaiting_approval": sum(1 for p in props if p.status == "AWAITING_APPROVAL"),
        "proposals_submitted": len(submitted),
        "interviews": len(interviews),
        "jobs_won": len(won),
        "jobs_lost": len(lost),
        "active_jobs": sum(1 for j in jobs if j.status not in (JobStatus.DELIVERED.value, JobStatus.PROBLEM.value)),
        "jobs_ready_to_deliver": sum(1 for j in jobs if j.status == JobStatus.READY_TO_DELIVER.value),
        "jobs_problem": sum(1 for j in jobs if j.status == JobStatus.PROBLEM.value),
        "jobs_completed": len(completed),
        # rates - None means Insufficient Data, which is not the same as zero
        "win_rate": win_rate,
        "avg_qa_score": avg_qa,
        "revision_rate": revision_rate,
        "avg_human_minutes_per_job": avg_human_minutes,
        # money
        "revenue_today": round(sum(r.net for r in rev if _within(r.received_at, 1)), 2),
        "revenue_week": round(sum(r.net for r in rev if _within(r.received_at, 7)), 2),
        "revenue_month": round(sum(r.net for r in rev if _within(r.received_at, 30)), 2),
        "revenue_gross_total": gross_total,
        "revenue_net_total": net_total,
        "profit_margin_pct": margin,
        "avg_order_value": avg_order,
        "ai_cash_cost_total": round(sum(r.ai_cash_cost for r in rev), 2),
        "ai_usage_units_total": round(sum(r.ai_usage_units for r in rev), 2),
        # breakdowns
        "by_source": _by_source(opps, props, rev),
        "by_category": _by_category(opps, rev),
        "computed_at": _utc_now().isoformat(timespec="seconds"),
        "includes_demo": include_demo,
    }


def _by_source(opps: list[Any], props: list[Any], rev: list[Any]) -> dict[str, dict[str, Any]]:
    sources = sorted({o.source for o in opps} | {p.source for p in props} | {r.source for r in rev})
    out = {}
    for src in sources:
        s_opps = [o for o in opps if o.source == src]
        out[src] = {
            "opportunities": len(s_opps),
            "strong_matches": sum(1 for o in s_opps if o.score_band in ("EXCELLENT", "STRONG")),
            "proposals": sum(1 for p in props if p.source == src),
            "interviews": sum(1 for o in s_opps if o.status == OpportunityStatus.INTERVIEW.value),
            "won": sum(1 for o in s_opps if o.status == OpportunityStatus.WON.value),
            "revenue": round(sum(r.net for r in rev if r.source == src), 2),
        }
    return out


def _by_category(opps: list[Any], rev: list[Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for opp in opps:
        cat = opp.category or "generic"
        bucket = out.setdefault(cat, {"opportunities": 0, "won": 0, "revenue": 0.0, "avg_score": 0.0, "_scores": []})
        bucket["opportunities"] += 1
        bucket["_scores"].append(opp.score)
        if opp.status == OpportunityStatus.WON.value:
            bucket["won"] += 1
    for bucket in out.values():
        scores = bucket.pop("_scores")
        bucket["avg_score"] = round(sum(scores) / len(scores), 1) if scores else 0.0
    return out


# ---------------------------------------------------------------------------
# Metric provenance
# ---------------------------------------------------------------------------


def describe_metrics(metrics: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Formula, source, observation count and timestamp for every metric that has one.

    This is what makes a figure on the dashboard inspectable rather than asserted.
    """
    ts = metrics["computed_at"]
    n_decided = metrics["jobs_won"] + metrics["jobs_lost"]
    return {
        "win_rate": {
            "formula": "jobs_won / (jobs_won + jobs_lost) x 100",
            "source": "opportunity records with status WON or LOST",
            "observations": n_decided,
            "sufficient": n_decided >= MIN_OBSERVATIONS_FOR_RATE,
            "note": f"Requires at least {MIN_OBSERVATIONS_FOR_RATE} decided outcomes. "
            "Below that the figure is noise and shows Insufficient Data.",
            "computed_at": ts,
        },
        "avg_qa_score": {
            "formula": "mean of the final QA overall_score across all jobs with a QA round",
            "source": "QAReport records produced by the independent reviewer",
            "observations": metrics["jobs_completed"],
            "sufficient": metrics["avg_qa_score"] is not None,
            "computed_at": ts,
        },
        "revenue_net_total": {
            "formula": "sum(gross - platform_fee - payment_fee - other_cost - ai_cash_cost)",
            "source": "RevenueEntry records confirmed by a human",
            "observations": metrics["jobs_completed"],
            "sufficient": True,
            "note": "Demo rows are structurally excluded from REAL figures.",
            "computed_at": ts,
        },
        "profit_margin_pct": {
            "formula": "net_total / gross_total x 100",
            "source": "RevenueEntry records",
            "observations": metrics["jobs_completed"],
            "sufficient": metrics["profit_margin_pct"] is not None,
            "computed_at": ts,
        },
        "ai_cash_cost_total": {
            "formula": "sum(ai_cash_cost)",
            "source": "RevenueEntry records",
            "observations": metrics["jobs_completed"],
            "sufficient": True,
            "note": "$0.00 while the worker runs on a Claude subscription OAuth token. "
            "Usage draw is tracked separately as ai_usage_units_total.",
            "computed_at": ts,
        },
        "revision_rate": {
            "formula": "jobs with revision_count > 0 / all jobs x 100",
            "source": "Job records",
            "observations": metrics["active_jobs"] + metrics["jobs_completed"],
            "sufficient": metrics["revision_rate"] is not None,
            "computed_at": ts,
        },
    }


def fmt(value: Any, *, money_fmt: bool = False, pct: bool = False) -> str:
    """Render a metric, showing Insufficient Data rather than a misleading zero."""
    if value is None:
        return "Insufficient Data"
    if money_fmt:
        return f"${value:,.2f}"
    if pct:
        return f"{value:.1f}%"
    return f"{value:,}" if isinstance(value, int) else str(value)


# ---------------------------------------------------------------------------
# Learning system (spec section 41)
# ---------------------------------------------------------------------------


def scoring_calibration() -> dict[str, Any]:
    """Compare scores against outcomes so weights can be adjusted deliberately.

    This deliberately does NOT modify scoring weights automatically. Spec section 41 requires
    changes to be visible and reversible, so this returns a recommendation and a human edits
    ``scoring.WEIGHTS`` in a reviewable commit.
    """
    opps = [o for o in storage.opportunities.all() + storage.opportunities_archive.all() if not o.is_demo]
    decided = [o for o in opps if o.status in (OpportunityStatus.WON.value, OpportunityStatus.LOST.value)]

    if len(decided) < MIN_OBSERVATIONS_FOR_RATE:
        return {
            "status": "Insufficient Data",
            "observations": len(decided),
            "required": MIN_OBSERVATIONS_FOR_RATE,
            "recommendation": "Keep the current weights. There is not enough outcome data to "
            "justify changing anything, and tuning on noise makes scoring worse.",
        }

    won = [o for o in decided if o.status == OpportunityStatus.WON.value]
    lost = [o for o in decided if o.status == OpportunityStatus.LOST.value]
    avg_won = round(sum(o.score for o in won) / len(won), 1) if won else None
    avg_lost = round(sum(o.score for o in lost) / len(lost), 1) if lost else None

    separation = (avg_won - avg_lost) if (avg_won is not None and avg_lost is not None) else None
    if separation is None:
        rec = "Only one outcome class observed so far. Keep the current weights."
    elif separation > 10:
        rec = f"Scoring separates winners from losers by {separation:.1f} points. Keep the current weights."
    elif separation > 0:
        rec = (
            f"Weak separation ({separation:.1f} points). Scoring is only slightly better than "
            "chance at predicting wins. Review which factors differ between won and lost jobs."
        )
    else:
        rec = (
            f"Scoring is INVERTED ({separation:.1f} points): losing opportunities score higher "
            "than winning ones. Do not trust the score until this is investigated."
        )

    return {
        "status": "ok",
        "observations": len(decided),
        "avg_score_won": avg_won,
        "avg_score_lost": avg_lost,
        "separation": separation,
        "recommendation": rec,
        "applies_automatically": False,
        "note": "Weight changes are never applied automatically. Edit scoring.WEIGHTS in a commit.",
    }
