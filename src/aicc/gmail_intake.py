"""Gmail -> order intake: turning a Fiverr notification into a Job, safely.

Why this exists
---------------
Fiverr has no seller API, and its Community Standards prohibit scraping. ``robots.txt`` disallows
``/inbox/`` and ``/orders/timeline/*`` - every path an order-watcher would want. So the system
cannot ask Fiverr whether an order exists.

What it *can* do is read Andres's own mailbox. ``docs/MARKETPLACE_RULES.md`` identifies that as the
only compliant intake path: Fiverr sends an order notification by email, and parsing mail you
already received touches no Fiverr system. This module is that parser, and only that parser. It
adds no fulfillment logic - it hands the extracted fields to ``order_intake.intake``, which was
already built and tested.

What this module refuses to do
------------------------------
**It does not trust the email.** A notification is external input that arrives unauthenticated at
the application layer, addressed to someone whose business is doing what strangers ask. Every body
goes through ``untrusted.scan_for_injection`` before a single field is read, and nothing in a body
is ever executed, followed, or treated as an instruction.

**It fails closed.** If the sender is not on the allowlist, if a required field is missing, or if a
number does not parse, the message is escalated for a human to read rather than imported with a
guess. An order imported with the wrong price or the wrong deadline is worse than no import,
because the rest of the chain will believe it.

**It stores only what intake needs.** Order id, gig title, price, deadline, buyer handle. Not the
body, not the thread, not anything else in the mailbox. A notification that is not an order
notification is counted and discarded.

A standing caveat about the body patterns
-----------------------------------------
The sender allowlist below is verified: ``noreply@e.fiverr.com`` is the address Fiverr's
transactional mail actually arrives from in this mailbox, confirmed against a real message on
2026-09-13. ``Team@announce.fiverr.com`` is marketing and is deliberately excluded.

**The body field patterns are provisional.** No real Fiverr *order* notification has been seen yet
- nothing is published, so none exists. The patterns below are written to be tolerant and to fail
closed, and they are marked ``PROVISIONAL`` where that matters. The first genuine order email is
the specification; tighten these against it rather than trusting them in advance. Until then the
honest status of this path is: sender validation proven, extraction unproven.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from . import untrusted
from .config import DATA_DIR

# ---------------------------------------------------------------------------
# Sender validation. VERIFIED against real mail in this mailbox, 2026-09-13.
# ---------------------------------------------------------------------------

#: Domains Fiverr's transactional mail actually arrives from. An order notification from anywhere
#: else is a forgery or a forward, and either way is not evidence of an order.
TRUSTED_SENDER_DOMAINS = ("e.fiverr.com", "fiverr.com")

#: Marketing and announcement mail. Real Fiverr, but never an order, so never intake.
IGNORED_SENDER_DOMAINS = ("announce.fiverr.com",)

#: The Gmail search for the import path. Deliberately narrow: sender-scoped first, then
#: subject-scoped, so the query cannot match unrelated personal mail even by accident.
GMAIL_QUERY = (
    'from:(noreply@e.fiverr.com OR no-reply@fiverr.com) subject:(order OR purchased OR "new order" OR "order started") newer_than:30d'
)

#: The Gmail search for the *watch*, which is a different job from the import.
#:
#: ``GMAIL_QUERY`` above answers "is there an order to import", and is narrow on purpose. But a
#: buyer inquiry, a brief Fiverr routed to us, a cancellation, a review request and a dispute are
#: all revenue events too, and none of them say "order" in the subject. Anything matching only the
#: narrow query would leave those invisible - and on Fiverr an unanswered first message is the
#: first order lost, because response time is ranked.
#:
#: So the watch is sender-scoped only. It cannot match personal mail (the sender list is Fiverr's
#: transactional addresses), it parses nothing, and it imports nothing. Its whole output is a
#: three-way split: a known account notice is dropped, an order goes to the strict parser, and
#: **anything else is surfaced for a human to read**. Fail closed for the machine, fail open for
#: Andres - the opposite default from the importer, for the opposite reason.
WATCH_QUERY = "from:(noreply@e.fiverr.com OR no-reply@fiverr.com OR notifications@fiverr.com) newer_than:7d"

#: Subjects that indicate an order rather than an account notice. PROVISIONAL - see module docstring.
ORDER_SUBJECT_PATTERNS = (
    re.compile(r"\byou (?:have|'ve) (?:a |an )?new order\b", re.I),
    re.compile(r"\bnew order\b", re.I),
    re.compile(r"\border\s+(?:#|FO)\w+\s+(?:has )?started\b", re.I),
    re.compile(r"\bpurchased your\b", re.I),
)

#: Subjects that are definitely NOT orders, checked first so they can never fall through.
NON_ORDER_SUBJECT_PATTERNS = (
    re.compile(r"\bForm W-9\b", re.I),
    re.compile(r"\bphone number\b", re.I),
    re.compile(r"\bwelcome to fiverr\b", re.I),
    re.compile(r"\bpassword\b", re.I),
    re.compile(r"\bverif(?:y|ication)\b", re.I),
    re.compile(r"\bnewsletter\b", re.I),
)

#: Account notices seen in this mailbox that are known to carry no revenue event. Matching one is
#: the ONLY way mail from a trusted Fiverr sender gets dropped without a human seeing it, so this
#: list stays short and every entry is a subject actually observed, not a guess. Everything else a
#: trusted sender sends is surfaced.
KNOWN_NOTICE_PATTERNS = NON_ORDER_SUBJECT_PATTERNS + (
    re.compile(r"\byou look like you mean business\b", re.I),
    re.compile(r"\bcompliant with W-9\b", re.I),
    re.compile(r"\bneeds a W-9 form\b", re.I),
    re.compile(r"\blet'?s get started\b", re.I),
)


# ---------------------------------------------------------------------------
# Field extraction. PROVISIONAL - see module docstring.
# ---------------------------------------------------------------------------

#: Fiverr order identifiers observed in the wild take the form FO + alphanumerics. Anchored on the
#: FO prefix so a stray number in a body cannot be mistaken for an order id.
ORDER_ID_RE = re.compile(r"\b(FO[0-9A-Z]{6,20})\b")

#: A price in USD. Requires the currency marker; a bare number is never read as a price.
PRICE_RE = re.compile(r"(?:US)?\$\s?([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]{2})?)")

SEEN_FILE = DATA_DIR / "gmail_intake_seen.json"

#: Fields intake cannot run without.
REQUIRED_FIELDS = ("order_id", "gig_title", "price")


@dataclass
class ExtractionResult:
    """What was found in one message, and what was not.

    ``ok`` is true only when every required field parsed. Anything else escalates rather than
    importing, because a half-read order is worse than an unread one.
    """

    ok: bool
    reason: str = ""
    order_id: str = ""
    gig_title: str = ""
    price: float | None = None
    deadline: str = ""
    buyer: str = ""
    missing: list[str] = field(default_factory=list)
    injection_findings: list[str] = field(default_factory=list)
    message_id: str = ""
    received_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "reason": self.reason,
            "order_id": self.order_id,
            "gig_title": self.gig_title,
            "price": self.price,
            "deadline": self.deadline,
            "buyer": self.buyer,
            "missing": list(self.missing),
            "injection_findings": list(self.injection_findings),
            "message_id": self.message_id,
            "received_at": self.received_at,
        }


def sender_domain(sender: str) -> str:
    """The domain of an RFC-5322 sender string, lowercased. Empty when unparseable."""
    m = re.search(r"<?([^<>@\s]+)@([^<>@\s]+?)>?$", (sender or "").strip())
    return m.group(2).lower() if m else ""


def sender_is_trusted(sender: str) -> tuple[bool, str]:
    """Whether this sender may be treated as Fiverr transactional mail.

    Ignored domains are rejected explicitly rather than by omission, so that adding a domain to
    ``TRUSTED_SENDER_DOMAINS`` can never accidentally admit marketing mail.
    """
    dom = sender_domain(sender)
    if not dom:
        return False, "sender address could not be parsed"
    if dom in IGNORED_SENDER_DOMAINS:
        return False, f"{dom} is Fiverr marketing mail, never an order"
    if dom in TRUSTED_SENDER_DOMAINS:
        return True, dom
    return False, f"{dom} is not a Fiverr transactional sender"


def looks_like_order(subject: str) -> tuple[bool, str]:
    """Whether a subject line indicates an order. Non-order patterns win."""
    s = subject or ""
    for pat in NON_ORDER_SUBJECT_PATTERNS:
        if pat.search(s):
            return False, "subject is an account notice, not an order"
    for pat in ORDER_SUBJECT_PATTERNS:
        if pat.search(s):
            return True, "subject matches an order notification"
    return False, "subject does not match any known order notification"


def _load_seen() -> dict[str, Any]:
    if not SEEN_FILE.exists():
        return {"order_ids": [], "message_ids": []}
    try:
        data = json.loads(SEEN_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"order_ids": [], "message_ids": []}
    data.setdefault("order_ids", [])
    data.setdefault("message_ids", [])
    return data


def already_imported(order_id: str, message_id: str = "") -> bool:
    """Deduplication.

    Keyed on the Fiverr order id, which is stable across resends, forwards and Gmail's own
    threading. The Gmail message id is a secondary key for the case where a message was processed
    before an order id could be read from it.
    """
    seen = _load_seen()
    if order_id and order_id in seen["order_ids"]:
        return True
    if message_id and message_id in seen["message_ids"]:
        return True
    return False


def mark_imported(order_id: str, message_id: str = "") -> None:
    """Record an order id as handled. Called only after intake has actually accepted it."""
    seen = _load_seen()
    if order_id and order_id not in seen["order_ids"]:
        seen["order_ids"].append(order_id)
    if message_id and message_id not in seen["message_ids"]:
        seen["message_ids"].append(message_id)
    SEEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    SEEN_FILE.write_text(json.dumps(seen, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def extract(
    *,
    sender: str,
    subject: str,
    body: str,
    message_id: str = "",
    received_at: str = "",
    known_gig_titles: tuple[str, ...] = (),
) -> ExtractionResult:
    """Read one message into the fields intake needs, or explain why it cannot.

    The order of checks matters and is deliberate: sender, then subject, then injection scan, then
    fields. A body is never read from a sender that failed validation.
    """
    res = ExtractionResult(ok=False, message_id=message_id, received_at=received_at)

    trusted, why = sender_is_trusted(sender)
    if not trusted:
        res.reason = f"REJECTED: {why}"
        return res

    is_order, why = looks_like_order(subject)
    if not is_order:
        res.reason = f"IGNORED: {why}"
        return res

    # The body is external input. Scan before reading, and carry the findings forward so a human
    # sees them even when extraction otherwise succeeds.
    scan = untrusted.scan_for_injection(body or "")
    # Category and severity, not the whole dataclass repr. This line is read by a person deciding
    # in a hurry whether an order email is hostile; a wall of repr is the same as no finding.
    res.injection_findings = [f"{getattr(f, 'category', 'finding')} ({getattr(f, 'severity', '?')})" for f in getattr(scan, "findings", [])]
    clean = untrusted.strip_invisible(body or "")

    m = ORDER_ID_RE.search(clean) or ORDER_ID_RE.search(subject or "")
    if m:
        res.order_id = m.group(1)
    else:
        res.missing.append("order_id")

    # The gig title is matched against gigs we actually published rather than parsed out of prose.
    # A title we do not recognise is a reason to escalate, not to invent one.
    for title in known_gig_titles:
        if title and title.lower() in clean.lower():
            res.gig_title = title
            break
    if not res.gig_title:
        res.missing.append("gig_title")

    pm = PRICE_RE.search(clean)
    if pm:
        try:
            res.price = float(pm.group(1).replace(",", ""))
        except ValueError:
            res.missing.append("price")
    else:
        res.missing.append("price")

    bm = re.search(r"\bfrom\s+@?([A-Za-z0-9_.-]{3,40})\b", clean)
    if bm:
        res.buyer = bm.group(1)

    dm = re.search(r"\b(\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?Z?)?)\b", clean)
    if dm:
        res.deadline = dm.group(1)

    if res.missing:
        res.reason = "ESCALATE: could not read " + ", ".join(res.missing)
        return res

    # Fields parsed cleanly - but a notification carrying an active injection attempt is not a
    # normal order, whatever its fields say. The scanner already did its job by refusing to treat
    # the body as instructions; this is the second half of that job. Something is wrong with a
    # message that tries to reassign your role or exfiltrate a token, and a person should see it
    # before the pipeline reserves capacity against it.
    if any(getattr(f, "severity", "") == "high" for f in getattr(scan, "findings", [])):
        res.reason = (
            "ESCALATE: fields read, but the body contains a high-severity injection attempt. "
            "Not imported automatically. A human should read this message."
        )
        return res

    res.ok = True
    res.reason = "READY: all required fields read from a verified Fiverr sender"
    return res


# ---------------------------------------------------------------------------
# The watch. A different job from the import, with the opposite default.
# ---------------------------------------------------------------------------

#: What the watch decided about one message.
#:   DROP    - a trusted sender, but a subject on the known-notice list. No revenue event.
#:   ORDER   - looks like an order. Hand it to `extract` and then to order intake.
#:   SURFACE - anything else from a trusted sender. A human reads this one.
#:   REJECT  - the sender is not Fiverr transactional mail. Not evidence of anything.
WATCH_DROP, WATCH_ORDER, WATCH_SURFACE, WATCH_REJECT = "DROP", "ORDER", "SURFACE", "REJECT"


def classify(sender: str, subject: str) -> tuple[str, str]:
    """Route one message, on sender and subject alone. The body is not read here.

    The asymmetry is the point. ``extract`` fails closed because importing a wrong order is worse
    than importing none. This fails *open*: an unrecognised subject from a real Fiverr address is
    surfaced rather than dropped, because the cost of missing a buyer inquiry is a lost first order
    and the cost of showing Andres one extra email is a glance.
    """
    trusted, why = sender_is_trusted(sender)
    if not trusted:
        return WATCH_REJECT, why

    s = subject or ""
    for pat in KNOWN_NOTICE_PATTERNS:
        if pat.search(s):
            return WATCH_DROP, "known account notice, carries no revenue event"
    for pat in ORDER_SUBJECT_PATTERNS:
        if pat.search(s):
            return WATCH_ORDER, "subject matches an order notification"
    return WATCH_SURFACE, "from Fiverr, not a known notice - a human should read this"


# ---------------------------------------------------------------------------
# Tier resolution. Which package was bought, read off the live listing.
# ---------------------------------------------------------------------------


def resolve_tier(gig_title: str, price: float) -> tuple[str, str]:
    """Which package that price corresponds to, from the published prices.

    Returns ``(tier, detail)``; tier is "" when it cannot be determined. This exists so that
    ``--worker-minutes`` is *looked up* rather than invented: `order import` already refuses to
    guess it, and the operator needs to know which tier was actually bought to supply it.

    Prices come from ``data/storefront_ledger.json`` - what the listing publicly shows - not from
    the kit, so a price the buyer could not have seen resolves to nothing rather than to the tier
    it would have matched before a change.
    """
    from .storefront import LEDGER_FILE

    try:
        ledger = json.loads(LEDGER_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return "", f"could not read the storefront ledger: {type(exc).__name__}"

    listings = ledger.get("listings")
    rows = list(listings.values()) if isinstance(listings, dict) else list(listings or [])
    wanted = (gig_title or "").strip().lower()

    for row in rows:
        if str(row.get("service", "")).strip().lower() != wanted:
            continue
        prices = row.get("package_prices") or {}
        for tier, listed in prices.items():
            if listed is not None and abs(float(listed) - float(price)) < 0.01:
                return tier, f"{tier} on {row.get('gig_key', '?')} at ${float(listed):.2f}"
        shown = ", ".join(f"{k} ${float(v):.0f}" for k, v in sorted(prices.items()) if v is not None)
        return "", f"gig found, but ${price:.2f} matches no published price ({shown})"
    return "", f"no live listing titled {gig_title!r}"
