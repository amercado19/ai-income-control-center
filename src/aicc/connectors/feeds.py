"""Aggregator feed connectors: Himalayas, RemoteOK, WeWorkRemotely.

All three are free, keyless, and permitted. Each was verified live in September 2026.

An important caveat that shapes every parser here: the structured ``salary_min`` / ``maxSalary``
fields on these APIs are **annual figures for full-time roles**. They are not freelance rates.
Treating them as project budgets would inflate every profitability estimate by roughly two
orders of magnitude, so the parsers below convert annual salary into an implied hourly figure
and mark the budget type accordingly, rather than passing the raw number through.

Sources deliberately excluded, and why:
* **Remotive** - its robots.txt contains ``Disallow: /api/*``. The API is otherwise open, but
  the rule is explicit and this system does not argue with a robots directive.
* **Authentic Jobs** - RSS feed is robots-disallowed.
* **Reddit r/forhire** - robots.txt is a blanket ``Disallow: /``, and unauthenticated .json
  endpoints now return 403. The OAuth Data API is the only legal route and its commercial-use
  terms need a human decision first. See docs/MARKETPLACE_RULES.md.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Any

from ..models import AutomationPolicy, BudgetType, OpportunityStatus
from .base import (
    Capabilities,
    Connector,
    ConnectorError,
    extract_rate,
    extract_skills,
    http_get,
    http_get_json,
    make_opportunity,
    register,
    strip_html,
)

# Annual salary -> implied hourly, for comparability only. 2,080 = 52 weeks x 40 hours.
WORK_HOURS_PER_YEAR = 2080.0

_CONTRACT_HINT = re.compile(
    r"\b(contract|contractor|freelance|freelancer|1099|b2b|part[- ]time|consulting|fractional|project[- ]based)\b",
    re.I,
)


def _annual_to_hourly(lo: float | None, hi: float | None) -> tuple[float | None, float | None]:
    def conv(v: float | None) -> float | None:
        if v is None or v <= 0:
            return None
        # Anything under 500 is already an hourly rate, not an annual salary.
        return round(v / WORK_HOURS_PER_YEAR, 2) if v >= 500 else v

    return conv(lo), conv(hi)


@register
class HimalayasConnector(Connector):
    CAPS = Capabilities(
        name="himalayas",
        label="Himalayas",
        discovery=True,
        api=True,
        allowed_automated_fetch=True,
        auto_proposal=True,
        manual_approval_required=True,
        automation_policy=AutomationPolicy.FULL_AUTO.value,
        cost_to_apply="Free - apply at the employer's own link",
        rules_evidence="Public keyless JSON API at himalayas.app/jobs/api with cursor pagination.",
        rules_url="https://himalayas.app/jobs/api",
        notes=(
            "The employmentType query parameter is silently ignored by the API, so contract "
            "filtering happens client-side. Salary fields are annual and frequently null."
        ),
    )

    ENDPOINT = "https://himalayas.app/jobs/api?limit={limit}"

    @classmethod
    def discover(cls, limit: int = 50) -> list[Any]:
        data = http_get_json(cls.ENDPOINT.format(limit=min(limit * 4, 100)))
        jobs = data.get("jobs") or data.get("data") or []
        out = []
        for job in jobs:
            etype = (job.get("employmentType") or "").lower()
            text = f"{job.get('title', '')} {job.get('excerpt', '')} {job.get('description', '')}"
            is_contract = "contract" in etype or "part" in etype or bool(_CONTRACT_HINT.search(text))
            if not is_contract:
                continue

            lo, hi = _annual_to_hourly(job.get("minSalary"), job.get("maxSalary"))
            btype = BudgetType.HOURLY.value if lo else BudgetType.UNKNOWN.value

            out.append(
                make_opportunity(
                    source="himalayas",
                    external_id=str(job.get("guid") or job.get("id") or job.get("applicationLink", ""))[:200],
                    title=job.get("title", ""),
                    description=job.get("description") or job.get("excerpt") or "",
                    client=job.get("companyName", ""),
                    url=job.get("applicationLink", ""),
                    budget_min=lo,
                    budget_max=hi,
                    budget_type=btype,
                    currency=job.get("currency") or "USD",
                    posted_time=job.get("pubDate", ""),
                    deadline=job.get("expiryDate", ""),
                    skills=extract_skills(text, job.get("categories") or []),
                    automation_policy=AutomationPolicy.FULL_AUTO.value,
                    status=OpportunityStatus.NEW.value,
                )
            )
            if len(out) >= limit:
                break
        return out


@register
class RemoteOKConnector(Connector):
    """RemoteOK.

    Attribution is a contractual condition of this API, stated in the API response itself:
    a followed link back to RemoteOK is required if results are surfaced publicly. The
    dashboard renders that attribution wherever a RemoteOK row appears.
    """

    CAPS = Capabilities(
        name="remoteok",
        label="RemoteOK",
        discovery=True,
        api=True,
        allowed_automated_fetch=True,
        auto_proposal=True,
        manual_approval_required=True,
        automation_policy=AutomationPolicy.FULL_AUTO.value,
        cost_to_apply="Free - apply at the employer's own link",
        rules_evidence=(
            "robots.txt is Allow: / with Crawl-delay: 1 and no /api disallow. The API's own first "
            "element states the attribution requirement, which this system honours on the dashboard."
        ),
        rules_url="https://remoteok.com/api",
        notes="First array element is a legal/ToS object, not a job. It is skipped.",
    )

    ENDPOINT = "https://remoteok.com/api"

    @classmethod
    def discover(cls, limit: int = 50) -> list[Any]:
        data = http_get_json(cls.ENDPOINT)
        if not isinstance(data, list):
            raise ConnectorError("RemoteOK returned an unexpected payload shape.")

        out = []
        for entry in data:
            if not isinstance(entry, dict) or "legal" in entry or not entry.get("position"):
                continue  # index 0 is the legal object
            text = f"{entry.get('position', '')} {entry.get('description', '')} {' '.join(entry.get('tags') or [])}"
            if not _CONTRACT_HINT.search(text):
                continue

            lo, hi = _annual_to_hourly(entry.get("salary_min"), entry.get("salary_max"))
            btype = BudgetType.HOURLY.value if lo else BudgetType.UNKNOWN.value

            out.append(
                make_opportunity(
                    source="remoteok",
                    external_id=str(entry.get("id") or entry.get("slug", ""))[:200],
                    title=entry.get("position", ""),
                    description=entry.get("description", ""),
                    client=entry.get("company", ""),
                    url=entry.get("url") or entry.get("apply_url", ""),
                    budget_min=lo,
                    budget_max=hi,
                    budget_type=btype,
                    posted_time=entry.get("date", ""),
                    skills=extract_skills(text, entry.get("tags") or []),
                    automation_policy=AutomationPolicy.FULL_AUTO.value,
                    status=OpportunityStatus.NEW.value,
                )
            )
            if len(out) >= limit:
                break
        return out


@register
class WeWorkRemotelyConnector(Connector):
    CAPS = Capabilities(
        name="weworkremotely",
        label="We Work Remotely",
        discovery=True,
        api=False,
        allowed_automated_fetch=True,
        auto_proposal=True,
        manual_approval_required=True,
        automation_policy=AutomationPolicy.FULL_AUTO.value,
        cost_to_apply="Free - apply at the employer's own link",
        rules_evidence=(
            "Published RSS feeds. robots.txt is Allow: / with disallows limited to account and "
            "admin paths; the category RSS feeds are permitted."
        ),
        rules_url="https://weworkremotely.com/categories/remote-programming-jobs.rss",
        notes="No salary element in the feed; rates are parsed from the description text only.",
    )

    FEEDS = [
        "https://weworkremotely.com/categories/remote-programming-jobs.rss",
        "https://weworkremotely.com/categories/remote-back-end-programming-jobs.rss",
        "https://weworkremotely.com/categories/remote-devops-sysadmin-jobs.rss",
    ]

    @classmethod
    def discover(cls, limit: int = 50) -> list[Any]:
        out: list[Any] = []
        errors: list[str] = []
        for feed in cls.FEEDS:
            if len(out) >= limit:
                break
            try:
                out.extend(cls._parse_feed(feed, limit - len(out)))
            except ConnectorError as exc:
                errors.append(str(exc))
        if not out and errors:
            raise ConnectorError("; ".join(errors))
        return out[:limit]

    @classmethod
    def _parse_feed(cls, url: str, limit: int) -> list[Any]:
        raw = http_get(url)
        try:
            root = ET.fromstring(raw)  # noqa: S314 - trusted feed, stdlib parser, no entity expansion used
        except ET.ParseError as exc:
            raise ConnectorError(f"Malformed RSS from {url}: {exc}") from exc

        out = []
        for item in root.iter("item"):
            title = (item.findtext("title") or "").strip()
            description = strip_html(item.findtext("description") or "")
            text = f"{title} {description}"
            if not _CONTRACT_HINT.search(text):
                continue

            lo, hi, btype = extract_rate(description)
            company = title.split(":", 1)[0].strip() if ":" in title else ""

            out.append(
                make_opportunity(
                    source="weworkremotely",
                    external_id=(item.findtext("guid") or item.findtext("link") or title)[:200],
                    title=title,
                    description=description,
                    client=company,
                    url=(item.findtext("link") or "").strip(),
                    budget_min=lo,
                    budget_max=hi,
                    budget_type=btype,
                    posted_time=(item.findtext("pubDate") or "").strip(),
                    skills=extract_skills(text),
                    automation_policy=AutomationPolicy.FULL_AUTO.value,
                    status=OpportunityStatus.NEW.value,
                )
            )
            if len(out) >= limit:
                break
        return out
