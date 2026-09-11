"""python.org Job Board connector - small, clean, and precisely on-target.

Verified September 2026. Twenty items in the feed, of which roughly a third carried contract or
hourly language in the audit. Low volume, but every listing is Python work, which is the operator's
actual stack - a better hit rate per listing than any of the large aggregators.

**Permitted**: ``python.org/robots.txt`` blocks only HTTrack, puf, MSIECrawler and Nutch; the
``User-agent: *`` block permits ``/jobs/``. The RSS feed is a published interface.

**Gotcha worth recording**: the feed lives at ``/jobs/feed/rss/``. The plausible-looking
``/jobs/feeds/rss/`` returns 404.

Item titles follow the convention ``"Role, Company"``, which is how the client name is recovered.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Any

from ..models import AutomationPolicy, OpportunityStatus
from .base import (
    Capabilities,
    Connector,
    ConnectorError,
    extract_rate,
    extract_skills,
    http_get,
    make_opportunity,
    register,
    strip_html,
)
from .feeds import _CONTRACT_HINT

FEED = "https://www.python.org/jobs/feed/rss/"


@register
class PythonJobsConnector(Connector):
    CAPS = Capabilities(
        name="python_jobs",
        label="python.org Jobs",
        discovery=True,
        api=False,
        allowed_automated_fetch=True,
        auto_proposal=True,
        manual_approval_required=True,
        auto_delivery=False,
        automation_policy=AutomationPolicy.FULL_AUTO.value,
        requires_credential=False,
        credential_present=True,
        cost_to_apply="Free - apply at the employer's own link",
        rules_evidence=(
            "python.org/robots.txt blocks only HTTrack, puf, MSIECrawler and Nutch; the "
            "User-agent: * block permits /jobs/. The RSS feed is a published interface."
        ),
        rules_url=FEED,
        notes=(
            "Small volume (about 20 live items) but every listing is Python work, so the hit rate "
            "per listing beats the large aggregators. Rates appear in the description text only."
        ),
    )

    @classmethod
    def discover(cls, limit: int = 50) -> list[Any]:
        raw = http_get(FEED)
        try:
            root = ET.fromstring(raw)  # noqa: S314 - trusted feed, stdlib parser
        except ET.ParseError as exc:
            raise ConnectorError(f"Malformed RSS from {FEED}: {exc}") from exc

        out: list[Any] = []
        for item in root.iter("item"):
            title = (item.findtext("title") or "").strip()
            description = strip_html(item.findtext("description") or "")
            text = f"{title} {description}"
            if not _CONTRACT_HINT.search(text):
                continue

            # python.org titles read "Role, Company".
            company = title.rsplit(",", 1)[-1].strip() if "," in title else ""

            lo, hi, btype = extract_rate(description)
            out.append(
                make_opportunity(
                    source="python_jobs",
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
