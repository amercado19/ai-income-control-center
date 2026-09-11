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


# Rate extraction.
#
# The structured salary fields on aggregator APIs are ANNUAL figures for full-time roles; real
# freelance rates live in free text. This is the only reliable way to get them, and it is
# deliberately conservative: a figure it cannot parse confidently is left None rather than guessed.
#
# Three defects found against real September 2026 listings drove this design:
#
#   "270-300 zl/hr"            was read as $270-300/hr. That is Polish zloty - roughly a 4x
#                              overvaluation, and exactly the kind of error that sends you chasing
#                              the wrong job. A currency check that only understood three-letter
#                              codes did not catch a symbol.
#   "30-40 hours/week"         parsed as a rate of $30-40/hr. It is a duration, not a price.
#   "45-70 USD or 180-280 PLN" needs the USD figure, not the first or the largest one.
#
# So rather than one dense regex, the numbers are matched loosely and the surrounding text is
# validated in Python, where the rules are legible.

_NON_USD = re.compile(
    r"(z\u0142|\bzl\b|\bPLN\b|\u20ac|\bEUR\b|\u00a3|\bGBP\b|\u20b9|\bINR\b|\bCAD\b|\bAUD\b|\bNZD\b|"
    r"R\$|\bBRL\b|\u00a5|\bJPY\b|\bCNY\b|\bRMB\b|\bSEK\b|\bNOK\b|\bDKK\b|\bCHF\b|\bMXN\b|\bZAR\b|"
    r"\bSGD\b|\bHKD\b|\u20bd|\bRUB\b|\bTRY\b|\bILS\b|\bAED\b)",
    re.I,
)
_USD_MARK = re.compile(r"(\$|\bUSD\b)", re.I)

# A duration ("30-40 hours/week"), not a price. The unit word is the same, so only what follows
# separates them.
_PER_PERIOD = re.compile(r"^\s*(?:/|\s|per\s+|a\s+)(?:week|wk|month|mo|year|yr|day)\b", re.I)

_HOUR_UNIT = re.compile(r"(?:/|\s|per\s*|an\s+)(hr|hrs|hour|hours)\b", re.I)

_RANGE = re.compile(
    r"(?P<pre>[$\u20ac\u00a3]|z\u0142|R\$)?\s?(?P<lo>\d{1,3})\s?(?:-|\u2013|\u2014|to)\s?"
    r"(?P<mid>[$\u20ac\u00a3]|z\u0142)?\s?(?P<hi>\d{1,3})(?=(?P<tail>[^.\n]{0,45}))"
)
_SINGLE = re.compile(r"(?P<pre>[$\u20ac\u00a3]|z\u0142)?\s?(?P<amt>\d{1,3})(?=(?P<tail>[^.\n]{0,30}))")

_FIXED_RANGE = re.compile(r"\$\s?(\d{1,3}(?:,\d{3})*|\d{3,6})\s?(?:-|\u2013|\u2014|to)\s?\$?\s?(\d{1,3}(?:,\d{3})*|\d{3,6})\b")
_FIXED_SINGLE = re.compile(r"\$\s?(\d{1,3}(?:,\d{3})*|\d{3,6})(?:\s?(?:USD|budget|fixed|total))?", re.I)
_ANNUAL_HINT = re.compile(r"\b(per year|/yr|annually|annual salary|k\s?-\s?\d+k)\b", re.I)


def _hourly_from(match: re.Match[str], text: str = "") -> tuple[float, float] | None:
    """Validate one numeric match as a USD hourly rate, or reject it.

    ``text`` is the full source string, used to look backwards for a currency marker that
    attaches to this figure without immediately preceding it - "\u20ac60-80 per hour" puts the symbol
    on the first number, but the second is the one the range parser lands on.
    """
    tail = match.group("tail") or ""

    unit = _HOUR_UNIT.search(tail)
    if not unit:
        return None

    before_unit = tail[: unit.start()]
    after_unit = tail[unit.end() :]

    # "30-40 hours/week" is a time commitment, not a price.
    if _PER_PERIOD.match(after_unit):
        return None

    # Any non-USD marker attached to this figure disqualifies it. Checked on the prefix symbols
    # and on everything between the number and the unit.
    prefix = (match.groupdict().get("pre") or "") + (match.groupdict().get("mid") or "")
    context = prefix + before_unit

    # Whichever currency marker comes FIRST belongs to this figure. In
    # "45-70 USD or 180-280 PLN per hour" the USD sits with 45-70 and the PLN with the
    # alternative quote, so presence alone is the wrong test - position is the right one.
    # Look back a short way for a currency marker attached to this figure.
    lookback = text[max(0, match.start() - 14) : match.start()] if text else ""
    if _NON_USD.search(lookback) and not _USD_MARK.search(lookback):
        return None

    usd = _USD_MARK.search(context)
    non_usd = _NON_USD.search(context)
    if non_usd and (usd is None or non_usd.start() < usd.start()):
        return None

    # A bare number with no dollar mark near it is too weak to trust unless the unit follows
    # almost immediately.
    if usd is None and len(before_unit.strip()) > 6:
        return None

    lo = float(match.group("lo")) if "lo" in match.groupdict() else float(match.group("amt"))
    hi = float(match.group("hi")) if "hi" in match.groupdict() else lo
    if 5 <= lo <= hi <= 500:
        return lo, hi
    return None


def extract_rate(text: str) -> tuple[float | None, float | None, str]:
    """Best-effort rate extraction from free text.

    Returns (min, max, budget_type). Returns (None, None, "UNKNOWN") when nothing parses
    confidently - an unknown budget is reported as unknown, never as zero.
    """
    if not text:
        return None, None, "UNKNOWN"

    for m in _RANGE.finditer(text):
        found = _hourly_from(m, text)
        if found:
            return found[0], found[1], "HOURLY"

    for m in _SINGLE.finditer(text):
        found = _hourly_from(m, text)
        if found:
            return found[0], found[1], "HOURLY"

    # Only look for fixed amounts when the text is not obviously quoting an annual salary, and
    # never when the figure carries a non-USD marker.
    if not _ANNUAL_HINT.search(text):
        fixed_range = _FIXED_RANGE.search(text)
        if fixed_range and not _NON_USD.search(text[fixed_range.start() : fixed_range.end() + 12]):
            lo = float(fixed_range.group(1).replace(",", ""))
            hi = float(fixed_range.group(2).replace(",", ""))
            if 50 <= lo <= hi <= 100_000:
                return lo, hi, "FIXED"
        fixed_single = _FIXED_SINGLE.search(text)
        if fixed_single and not _NON_USD.search(text[fixed_single.start() : fixed_single.end() + 12]):
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
