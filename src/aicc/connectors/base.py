"""Connector framework (spec section 15).

Every acquisition channel is a module that declares, in code, exactly what it is permitted to
do. The declaration is not aspirational: each field is backed by a citation in
``docs/MARKETPLACE_RULES.md``, and a connector that cannot legitimately discover work says so by
setting ``DISCOVERY = False`` rather than by quietly returning an empty list.

The cardinal rule of this package: **a connector never fakes a capability it does not have.**
If a platform offers no permitted programmatic read, the honest implementation raises
``NotPermittedError`` and the dashboard shows the channel as MANUAL. It does not scrape.
"""

from __future__ import annotations

import gzip
import json
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from typing import Any

from ..models import AutomationPolicy, Opportunity

USER_AGENT = (
    "ai-income-control-center/0.1 (+https://github.com/amercado19/ai-income-control-center) "
    "personal job-search pipeline; contact via GitHub"
)
DEFAULT_TIMEOUT = 20


class ConnectorError(RuntimeError):
    """A connector failed to fetch. Always surfaced, never swallowed into a green light."""


class NotPermittedError(ConnectorError):
    """This action is not permitted by the source's current published rules.

    Raised deliberately, with the rule quoted, so the failure is self-documenting.
    """


@dataclass
class Capabilities:
    """What this connector may do. Spec section 15."""

    name: str
    label: str
    discovery: bool = False
    api: bool = False
    allowed_automated_fetch: bool = False
    auto_proposal: bool = False
    manual_approval_required: bool = True
    auto_delivery: bool = False
    automation_policy: str = AutomationPolicy.MANUAL_IMPORT.value

    requires_credential: bool = False
    credential_present: bool = False
    cost_to_apply: str = "Free"
    rules_evidence: str = ""
    rules_url: str = ""
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def status_light(self) -> str:
        """Honest light for the dashboard (spec section 47/48)."""
        if not self.discovery:
            return "WHITE"
        if self.requires_credential and not self.credential_present:
            return "YELLOW"
        if self.allowed_automated_fetch:
            return "GREEN"
        return "YELLOW"


class Connector:
    """Base class. Subclasses override ``CAPS`` and ``discover``."""

    CAPS: Capabilities

    @classmethod
    def capabilities(cls) -> Capabilities:
        return cls.CAPS

    @classmethod
    def discover(cls, limit: int = 50) -> list[Opportunity]:
        raise NotPermittedError(f"{cls.CAPS.label} has no permitted automated discovery surface. {cls.CAPS.rules_evidence}")

    @classmethod
    def probe(cls) -> tuple[bool, str]:
        """Cheap liveness check. Returns (ok, detail). Must never raise."""
        if not cls.CAPS.discovery:
            return False, "No discovery surface by design."
        try:
            found = cls.discover(limit=1)
            return True, f"Reachable; returned {len(found)} record(s)."
        except Exception as exc:  # noqa: BLE001
            return False, f"{type(exc).__name__}: {exc}"


# ---------------------------------------------------------------------------
# HTTP helper - stdlib only
# ---------------------------------------------------------------------------


def http_get(url: str, *, timeout: int = DEFAULT_TIMEOUT, headers: dict[str, str] | None = None) -> bytes:
    """GET a URL and return raw bytes. Transparent gzip. Raises ConnectorError on any failure."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept-Encoding": "gzip", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - https URLs only, built in code
            raw = resp.read()
            if resp.headers.get("Content-Encoding") == "gzip":
                raw = gzip.decompress(raw)
            return raw
    except urllib.error.HTTPError as exc:
        raise ConnectorError(f"HTTP {exc.code} from {url}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise ConnectorError(f"Network failure for {url}: {exc}") from exc


def http_get_json(url: str, *, timeout: int = DEFAULT_TIMEOUT, headers: dict[str, str] | None = None) -> Any:
    raw = http_get(url, timeout=timeout, headers=headers)
    try:
        return json.loads(raw.decode("utf-8", errors="replace"))
    except json.JSONDecodeError as exc:
        raise ConnectorError(f"Malformed JSON from {url}: {exc}") from exc


# ---------------------------------------------------------------------------
# Shared parsing helpers
# ---------------------------------------------------------------------------

import re  # noqa: E402 - kept next to the parsers that use it

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")

_ENTITIES = {
    "&amp;": "&",
    "&lt;": "<",
    "&gt;": ">",
    "&quot;": '"',
    "&#x27;": "'",
    "&#39;": "'",
    "&#x2F;": "/",
    "&nbsp;": " ",
    "&apos;": "'",
}


def strip_html(text: str) -> str:
    if not text:
        return ""
    out = text.replace("<p>", "\n\n").replace("<br>", "\n").replace("<br/>", "\n").replace("<br />", "\n")
    out = _TAG_RE.sub(" ", out)
    for entity, char in _ENTITIES.items():
        out = out.replace(entity, char)
    out = re.sub(r"&#(\d+);", lambda mm: chr(int(mm.group(1))), out)
    return _WS_RE.sub(" ", out).strip()


# Rate extraction. The structured salary fields on aggregator APIs are ANNUAL figures for
# full-time roles; real freelance rates live in free text. This is the only reliable way to get
# them, and it is deliberately conservative: a number it cannot parse confidently is left None
# rather than guessed at.
# Real listings write rates as "$45-70 USD or 180-280 PLN per hour" - the unit sits well after
# the figure, and a second currency is often quoted alongside. So: capture an optional currency
# code, allow bounded filler before the unit, and reject anything not quoted in USD rather than
# silently treating 280 PLN as $280.
_HOURLY_RANGE = re.compile(
    r"\$?\s?(\d{1,3})\s?(?:-|–|—|to)\s?\$?\s?(\d{1,3})\s*([A-Za-z]{3})?[^.\n]{0,40}?(?:/|\s|per\s*)(?:hr|hour|h\b)",
    re.I,
)
_HOURLY_SINGLE = re.compile(r"\$\s?(\d{1,3})\s*([A-Za-z]{3})?\s*(?:/|\s?per\s?)\s?(?:hr|hour|h\b)", re.I)
_ACCEPTED_CURRENCIES = {None, "", "usd"}
_FIXED_RANGE = re.compile(r"\$\s?(\d{1,3}(?:,\d{3})*|\d{3,6})\s?(?:-|–|—|to)\s?\$?\s?(\d{1,3}(?:,\d{3})*|\d{3,6})\b")
_FIXED_SINGLE = re.compile(r"\$\s?(\d{1,3}(?:,\d{3})*|\d{3,6})(?:\s?(?:USD|budget|fixed|total))?", re.I)
_ANNUAL_HINT = re.compile(r"\b(per year|/yr|annually|annual salary|k\s?-\s?\d+k)\b", re.I)


def extract_rate(text: str) -> tuple[float | None, float | None, str]:
    """Best-effort rate extraction from free text.

    Returns (min, max, budget_type). Returns (None, None, "UNKNOWN") when nothing parses
    confidently - an unknown budget is reported as unknown, never as zero.
    """
    if not text:
        return None, None, "UNKNOWN"

    for m in _HOURLY_RANGE.finditer(text):
        currency = (m.group(3) or "").lower() or None
        if currency not in _ACCEPTED_CURRENCIES:
            continue  # a rate quoted in PLN is not a dollar rate
        lo, hi = float(m.group(1)), float(m.group(2))
        if 5 <= lo <= hi <= 500:
            return lo, hi, "HOURLY"

    for m in _HOURLY_SINGLE.finditer(text):
        currency = (m.group(2) or "").lower() or None
        if currency not in _ACCEPTED_CURRENCIES:
            continue
        rate = float(m.group(1))
        if 5 <= rate <= 500:
            return rate, rate, "HOURLY"

    # Only look for fixed amounts when the text is not obviously quoting an annual salary.
    if not _ANNUAL_HINT.search(text):
        fixed_range = _FIXED_RANGE.search(text)
        if fixed_range:
            lo = float(fixed_range.group(1).replace(",", ""))
            hi = float(fixed_range.group(2).replace(",", ""))
            if 50 <= lo <= hi <= 100_000:
                return lo, hi, "FIXED"
        fixed_single = _FIXED_SINGLE.search(text)
        if fixed_single:
            amount = float(fixed_single.group(1).replace(",", ""))
            if 50 <= amount <= 100_000:
                return amount, amount, "FIXED"

    return None, None, "UNKNOWN"


SKILL_VOCAB = [
    "python",
    "pandas",
    "numpy",
    "sql",
    "postgres",
    "postgresql",
    "mysql",
    "sqlite",
    "snowflake",
    "bigquery",
    "dbt",
    "airflow",
    "etl",
    "excel",
    "vba",
    "csv",
    "google sheets",
    "api",
    "rest",
    "graphql",
    "fastapi",
    "flask",
    "django",
    "scraping",
    "selenium",
    "playwright",
    "aws",
    "gcp",
    "azure",
    "docker",
    "kubernetes",
    "terraform",
    "github actions",
    "ci/cd",
    "automation",
    "cron",
    "webhook",
    "zapier",
    "dashboard",
    "tableau",
    "power bi",
    "looker",
    "metabase",
    "grafana",
    "streamlit",
    "javascript",
    "typescript",
    "react",
    "node",
    "html",
    "css",
    "machine learning",
    "data science",
    "forecasting",
    "statistics",
    "analytics",
    "pdf",
    "ocr",
    "data entry",
    "data cleaning",
    "web research",
    "financial modeling",
]


def extract_skills(text: str, extra: list[str] | None = None) -> list[str]:
    low = (text or "").lower()
    found = [s for s in SKILL_VOCAB if s in low]
    for tag in extra or []:
        t = tag.strip().lower()
        if t and t not in found:
            found.append(t)
    return found[:20]


def make_opportunity(**kwargs: Any) -> Opportunity:
    """Build an Opportunity with description/title normalized."""
    kwargs["title"] = strip_html(kwargs.get("title", ""))[:300]
    kwargs["description"] = strip_html(kwargs.get("description", ""))[:8000]
    return Opportunity(**kwargs)


_REGISTRY: dict[str, type[Connector]] = {}


def register(cls: type[Connector]) -> type[Connector]:
    _REGISTRY[cls.CAPS.name] = cls
    return cls


def registry() -> dict[str, type[Connector]]:
    return dict(_REGISTRY)


def get(name: str) -> type[Connector] | None:
    return _REGISTRY.get(name)
