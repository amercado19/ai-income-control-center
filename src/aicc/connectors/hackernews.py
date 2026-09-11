"""Hacker News connector - the primary discovery channel.

Why this is first rather than Upwork or Fiverr:

* The Algolia API is public, documented, needs no key, and no robots rule restricts it.
* Applying is a direct email to the poster. It costs nothing, and there is no reputation gate,
  which matters enormously for an account with no marketplace history.
* Verified September 2026: the monthly "Who is hiring?" thread carries real contract listings
  with published rates in the $45-160/hour range.

**Only the "Who is hiring?" thread is read.** The dedicated freelancer thread was measured
across ten monthly threads (January-September 2026) and abandoned on the evidence:

    191 SEEKING WORK posts against 4 SEEKING FREELANCER posts - and two of those four were
    mis-tagged supply. September 2026: 0 demand, 22 supply.

That is not a channel, it is a queue of competitors. Reading it cost a request per run and
returned roughly two real leads in nine months, so it was removed rather than left in to pad the
source count. ``find_freelancer_thread`` is kept for the record and is no longer called.

"Who is hiring?" by contrast carried 389 comments in September 2026, a consistent minority of
them contract with rates stated.
"""

from __future__ import annotations

import re
from typing import Any

from ..models import AutomationPolicy, OpportunityStatus
from .base import (
    Capabilities,
    Connector,
    ConnectorError,
    extract_rate,
    extract_skills,
    http_get_json,
    make_opportunity,
    register,
    strip_html,
)

ALGOLIA = "https://hn.algolia.com/api/v1"

# A comment is demand (a client hiring) rather than supply (a freelancer advertising) if it
# looks like one of these. Order matters: SEEKING WORK is checked first and excludes.
_SEEKING_WORK = re.compile(r"\bSEEKING\s+WORK\b", re.I)
_SEEKING_FREELANCER = re.compile(r"\bSEEKING\s+FREELANCER\b", re.I)

# Contract signals inside the "Who is hiring?" thread.
_CONTRACT = re.compile(
    r"\b(contract|contractor|freelance|freelancer|1099|b2b|part[- ]time|consulting|"
    r"contract[- ]to[- ]hire|short[- ]term|project[- ]based|fractional)\b",
    re.I,
)
# Explicit exclusions - full-time-only language that overrides a weak contract hit.
_FULLTIME_ONLY = re.compile(r"\b(full[- ]time only|no contractors|w2 only|fte only)\b", re.I)

_REMOTE = re.compile(r"\bremote\b", re.I)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def _company_from_comment(text: str) -> str:
    """HN convention is 'Company | Role | Location | REMOTE | Salary' on the first line."""
    first = text.strip().split("\n", 1)[0]
    if "|" in first:
        return first.split("|")[0].strip()[:120]
    return first[:120].strip()


def _find_threads(query: str, *, author: str | None = None, pages: int = 1) -> list[dict[str, Any]]:
    url = f"{ALGOLIA}/search_by_date?tags=story{',author_' + author if author else ''}&hitsPerPage={pages * 10}"
    if query:
        url += f"&query={urllib_quote(query)}"
    data = http_get_json(url)
    return data.get("hits", [])


def urllib_quote(text: str) -> str:
    from urllib.parse import quote

    return quote(text)


def _thread_comments(story_id: str, limit: int = 200) -> list[dict[str, Any]]:
    url = f"{ALGOLIA}/search?tags=comment,story_{story_id}&hitsPerPage={min(limit, 1000)}"
    data = http_get_json(url)
    return data.get("hits", [])


@register
class HackerNewsConnector(Connector):
    CAPS = Capabilities(
        name="hackernews",
        label="Hacker News",
        discovery=True,
        api=True,
        allowed_automated_fetch=True,
        auto_proposal=True,  # drafting only; sending is always human-approved
        manual_approval_required=True,
        auto_delivery=False,
        automation_policy=AutomationPolicy.FULL_AUTO.value,
        requires_credential=False,
        credential_present=True,
        cost_to_apply="Free - direct email to the poster",
        rules_evidence=(
            "Public Algolia search API over Hacker News, no authentication required. "
            "news.ycombinator.com/robots.txt restricts crawling of the site's own dynamic pages; "
            "this connector reads the Algolia API instead, which is a separate documented service "
            "intended for programmatic use."
        ),
        rules_url="https://hn.algolia.com/api",
        notes=(
            "The 'Who is hiring?' thread is the productive one. The dedicated freelancer thread "
            "is mostly freelancers advertising, not clients hiring."
        ),
    )

    @classmethod
    def find_hiring_thread(cls) -> dict[str, Any] | None:
        """Most recent 'Ask HN: Who is hiring?' story."""
        for hit in _find_threads("", author="whoishiring", pages=1):
            if "who is hiring" in (hit.get("title") or "").lower():
                return hit
        return None

    @classmethod
    def find_freelancer_thread(cls) -> dict[str, Any] | None:
        """Most recent freelancer thread, found by title because the poster changed."""
        best: dict[str, Any] | None = None
        for hit in _find_threads("Freelancer Seeking freelancer", pages=3):
            title = (hit.get("title") or "").lower()
            if "freelancer" in title and "seeking freelancer" in title:
                # Prefer the thread with the most comments - duplicate posts happen.
                if best is None or (hit.get("num_comments") or 0) > (best.get("num_comments") or 0):
                    best = hit
        return best

    @classmethod
    def discover(cls, limit: int = 50) -> list[Any]:
        found: list[Any] = []
        errors: list[str] = []

        try:
            found.extend(cls._from_hiring_thread(limit))
        except ConnectorError as exc:
            errors.append(f"hiring thread: {exc}")

        # The freelancer thread is deliberately NOT read - see the module docstring for the
        # nine-month supply/demand measurement that retired it.

        if not found and errors:
            raise ConnectorError("; ".join(errors))
        return found[:limit]

    # -- thread readers ------------------------------------------------------

    @classmethod
    def _from_hiring_thread(cls, limit: int) -> list[Any]:
        thread = cls.find_hiring_thread()
        if not thread:
            raise ConnectorError("Could not locate a current 'Who is hiring?' thread.")
        story_id = str(thread.get("objectID"))
        month = thread.get("title", "Who is hiring?")

        out = []
        for comment in _thread_comments(story_id, limit=500):
            text = strip_html(comment.get("comment_text") or "")
            if len(text) < 80:
                continue
            if not _CONTRACT.search(text) or _FULLTIME_ONLY.search(text):
                continue

            lo, hi, btype = extract_rate(text)
            company = _company_from_comment(text)
            out.append(_build(comment, company, text, lo, hi, btype, month, remote=bool(_REMOTE.search(text))))
            if len(out) >= limit:
                break
        return out

    @classmethod
    def _from_freelancer_thread(cls, limit: int) -> list[Any]:
        thread = cls.find_freelancer_thread()
        if not thread:
            return []
        story_id = str(thread.get("objectID"))
        month = thread.get("title", "Freelancer thread")

        out = []
        for comment in _thread_comments(story_id, limit=300):
            text = strip_html(comment.get("comment_text") or "")
            if len(text) < 60:
                continue
            # Demand only. A SEEKING WORK post is another freelancer, not a client.
            if _SEEKING_WORK.search(text) and not _SEEKING_FREELANCER.search(text):
                continue
            if not _SEEKING_FREELANCER.search(text):
                continue

            lo, hi, btype = extract_rate(text)
            company = _company_from_comment(text)
            out.append(_build(comment, company, text, lo, hi, btype, month, remote=bool(_REMOTE.search(text))))
            if len(out) >= limit:
                break
        return out


def _build(
    comment: dict[str, Any], company: str, text: str, lo: float | None, hi: float | None, btype: str, month: str, *, remote: bool
) -> Any:
    cid = comment.get("objectID")
    label = "contract role" if "hiring" in month.lower() else "freelance brief"
    return make_opportunity(
        source="hackernews",
        external_id=f"hn_{cid}",
        title=f"{company} - {label}"[:300],
        description=text,
        client=company,
        url=f"https://news.ycombinator.com/item?id={cid}",
        budget_min=lo,
        budget_max=hi,
        budget_type=btype,
        posted_time=comment.get("created_at", ""),
        skills=extract_skills(text) + (["remote"] if remote else []),
        ai_allowed=None,
        automation_policy=AutomationPolicy.FULL_AUTO.value,
        status=OpportunityStatus.NEW.value,
    )
