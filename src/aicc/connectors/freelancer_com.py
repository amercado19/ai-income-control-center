"""Freelancer.com connector - the only real project marketplace with an open read API.

Verified September 2026. This is the one genuinely new discovery channel found in the source
audit, and the only true *project* marketplace (as opposed to a job board) that can be read
without authentication.

**Why it is permitted.** ``freelancer.com/robots.txt`` blocks exactly two named crawlers
(``JoobleBot`` and ``PetalBot``); the ``User-agent: *`` block contains **no Disallow lines at
all**. The API answers unauthenticated and returns its own rate-limit headers, which is a
deliberate public interface rather than an oversight:

    RateLimit-Limit: 200, 200;window=60, 1000;window=3600

**Why the filters are aggressive, and this is the important part.** Freelancer.com is a bidding
bloodbath. The audit observed 251 bids on a single $750-1,500 project, 202 on a GBP 250-750 one,
and a long tail of hourly listings at **$2-8/hour**. Ingesting it naively would flood the
dashboard with work that is not worth winning and would drag every average down.

So this connector filters hard, at the source, before anything reaches scoring:

* **USD only.** A budget quoted in INR or IDR is not a US freelance rate, and the platform is
  dominated by low-cost-market bids.
* **Minimum budget.** Below the operator's floor there is no point competing.
* **Maximum bid count.** A project with 80 existing bids is decided; arriving 81st is not a
  strategy. This is the single most useful filter the API offers, because ``bid_stats`` is
  returned with every project and no other source exposes competition directly.

**Bidding costs bid credits**, the same structural trap as Upwork Connects. This connector is a
*lead-detection feed*, never an auto-apply channel: ``auto_proposal`` drafts only, and submission
is human-gated like everywhere else.
"""

from __future__ import annotations

from datetime import UTC
from typing import Any

from ..config import PROFILE
from ..models import AutomationPolicy, BudgetType, OpportunityStatus
from .base import (
    Capabilities,
    Connector,
    ConnectorError,
    extract_skills,
    http_get_json,
    make_opportunity,
    register,
)

API = "https://www.freelancer.com/api/projects/0.1/projects/active/"

# Freelancer.com job-category ids relevant to this operator's skills.
JOB_IDS = {
    13: "Python",
    3: "PHP",  # kept out of the default query; here for reference
    116: "Data Processing",
    198: "Excel",
    237: "Web Scraping",
    305: "Data Entry",
    331: "Software Architecture",
    500: "Data Mining",
    591: "Data Analytics",
    1381: "Data Science",
}
DEFAULT_JOB_IDS = [13, 116, 198, 237, 500, 591, 1381]

MAX_BID_COUNT = 25
"""Above this, the project is effectively decided.

Arriving as the 80th bidder is not a strategy, it is a lottery ticket that costs bid credits.
"""

ACCEPTED_CURRENCIES = {"USD"}


@register
class FreelancerComConnector(Connector):
    CAPS = Capabilities(
        name="freelancer_com",
        label="Freelancer.com",
        discovery=True,
        api=True,
        allowed_automated_fetch=True,
        auto_proposal=True,  # drafting only
        manual_approval_required=True,
        auto_delivery=False,
        automation_policy=AutomationPolicy.FULL_AUTO.value,
        requires_credential=False,
        credential_present=True,
        cost_to_apply="Free to read. Bidding consumes bid credits (limited free monthly allowance).",
        rules_evidence=(
            "robots.txt blocks only JoobleBot and PetalBot; the User-agent: * block has no Disallow "
            "lines. The REST API answers unauthenticated and publishes its own rate-limit headers "
            "(200/min, 1000/hour), which is a deliberate public read interface."
        ),
        rules_url="https://developers.freelancer.com/",
        notes=(
            "Heavy competition: 200+ bids on popular projects and an hourly long tail at $2-8. "
            "Filtered hard at source on currency, budget floor and existing bid count. Read-only "
            "feed; bidding costs credits and is always human-approved."
        ),
    )

    @classmethod
    def _endpoint(cls, limit: int, job_ids: list[int] | None = None) -> str:
        jobs = "&".join(f"jobs[]={j}" for j in (job_ids or DEFAULT_JOB_IDS))
        return f"{API}?{jobs}&limit={min(limit * 4, 100)}&job_details=true&full_description=true&sort_field=time_updated"

    @classmethod
    def discover(cls, limit: int = 50) -> list[Any]:
        data = http_get_json(cls._endpoint(limit))
        if data.get("status") != "success":
            raise ConnectorError(f"Freelancer.com returned status {data.get('status')!r}")

        projects = (data.get("result") or {}).get("projects") or []
        out: list[Any] = []

        for proj in projects:
            currency = ((proj.get("currency") or {}).get("code") or "").upper()
            if currency not in ACCEPTED_CURRENCIES:
                continue  # a rate quoted in another currency is not a US freelance rate

            bids = (proj.get("bid_stats") or {}).get("bid_count") or 0
            if bids > MAX_BID_COUNT:
                continue  # effectively decided

            budget = proj.get("budget") or {}
            lo = budget.get("minimum")
            hi = budget.get("maximum")
            is_hourly = (proj.get("type") or "").lower() == "hourly"
            btype = BudgetType.HOURLY.value if is_hourly else BudgetType.FIXED.value

            floor = PROFILE.minimum_hourly if is_hourly else PROFILE.minimum_job_value
            if lo is not None and float(lo) < floor:
                continue

            description = proj.get("description") or proj.get("preview_description") or ""
            skills = [j.get("name", "") for j in (proj.get("jobs") or []) if j.get("name")]
            seo = proj.get("seo_url") or ""

            out.append(
                make_opportunity(
                    source="freelancer_com",
                    external_id=str(proj.get("id") or seo),
                    title=proj.get("title", ""),
                    description=description,
                    client=f"Freelancer.com client ({bids} bid{'s' if bids != 1 else ''} so far)",
                    url=f"https://www.freelancer.com/projects/{seo}" if seo else "https://www.freelancer.com/",
                    budget_min=float(lo) if lo is not None else None,
                    budget_max=float(hi) if hi is not None else None,
                    budget_type=btype,
                    currency=currency,
                    posted_time=_epoch_to_iso(proj.get("submitdate")),
                    skills=extract_skills(f"{proj.get('title', '')} {description}", skills),
                    automation_policy=AutomationPolicy.FULL_AUTO.value,
                    status=OpportunityStatus.NEW.value,
                )
            )
            if len(out) >= limit:
                break
        return out


def _epoch_to_iso(epoch: Any) -> str:
    from datetime import datetime

    try:
        return datetime.fromtimestamp(int(epoch), tz=UTC).isoformat(timespec="seconds")
    except (TypeError, ValueError, OSError):
        return ""
