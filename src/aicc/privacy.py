"""Privacy protection for harvested third-party content.

Why this module exists
----------------------

The operational store in ``data/`` is committed to the repository, and the repository is public.
That combination is fine for our own bookkeeping and for public job listings. It is **not** fine
for two things that a naive ingest would sweep up:

1. **Third-party contact details.** Hacker News hiring posts routinely carry a named person's
   direct email - `firstname@company.com`, `talent+hn@company.co`, or an obfuscated
   `jason [at] withclad [dot] com`. Those people posted them on Hacker News, not in a GitHub
   repository that a scheduled job re-publishes every six hours. Harvesting them into a public
   git history is a privacy problem regardless of where they were first posted, and git history
   makes it permanent.

2. **Verbatim republication.** Storing the entire text of somebody else's post, forever, in a
   public repo is republication, not indexing. An excerpt plus a link back to the source is what
   an aggregator should keep, and it is all the scoring engine needs.

So: scoring runs against the **full text in memory**, and only a redacted excerpt is ever
persisted. The source URL is always kept, so the full posting is one click away from where it
actually lives.

This runs inside ``make_opportunity``, which every connector uses, so a new connector inherits
the protection rather than having to remember it.
"""

from __future__ import annotations

import re

EXCERPT_CHARS = 1500
"""How much of a third-party posting is kept.

Long enough that re-scoring an archived record still sees the signal that mattered (real
postings run 600-1,200 characters); short enough that the store is an index, not a mirror.
"""

# Plain addresses.
_EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")

# Obfuscated addresses: "jason [at] withclad [dot] com", "garen (at) overture (dot) business".
#
# BOTH halves must be obfuscated. An earlier version accepted a bare "." as the dot, which made
# any ordinary "word. Word" sentence boundary look like an address - it flagged "gate. Detailed"
# and the plain URL "news.ycombinator.com". Requiring a spelled-out or bracketed "dot" is what
# distinguishes deliberate obfuscation from English.
_AT_FORM = r"(?:\[\s*at\s*\]|\(\s*at\s*\)|\{\s*at\s*\}|<\s*at\s*>|\s+at\s+)"
_DOT_FORM = r"(?:\[\s*dot\s*\]|\(\s*dot\s*\)|\{\s*dot\s*\}|<\s*dot\s*>|\s+dot\s+)"
_OBFUSCATED_EMAIL = re.compile(
    rf"\b[A-Za-z0-9._%+-]{{2,}}\s*{_AT_FORM}\s*[A-Za-z0-9-]{{2,}}\s*{_DOT_FORM}\s*[A-Za-z]{{2,}}",
    re.I,
)

# Phone numbers, international and US forms. Deliberately conservative: a bare run of digits is
# far more often an order id, a salary or a year than a phone number, so a separator or a country
# code is required.
_PHONE = re.compile(r"(?<![\w.])(?:\+\d{1,3}[\s.-]?)?(?:\(\d{2,4}\)[\s.-]?)?\d{3}[\s.-]\d{3,4}[\s.-]\d{3,4}(?![\w.])")

# Direct-message handles people leave for contact.
# A named handle, not a bare mention of the app. "Telegram: @recruiter99" is a contact detail;
# "Telegram only" is a scam signal that the risk detector wants to keep reading.
_HANDLE = re.compile(r"\b(?:telegram|whatsapp|signal|skype|discord)\s*(?::\s*@?|\s+@)\s*[A-Za-z0-9_.+-]{3,}", re.I)

REDACTED_EMAIL = "[contact removed]"
REDACTED_PHONE = "[phone removed]"


def redact_contacts(text: str) -> tuple[str, int]:
    """Strip third-party contact details. Returns (clean_text, number_of_redactions).

    Order matters: the obfuscated pattern is greedier, so plain addresses go first and the
    obfuscated sweep then catches what is left.
    """
    if not text:
        return "", 0

    count = 0

    def _sub(pattern: re.Pattern[str], replacement: str, s: str) -> str:
        nonlocal count
        s, n = pattern.subn(replacement, s)
        count += n
        return s

    text = _sub(_EMAIL, REDACTED_EMAIL, text)
    text = _sub(_OBFUSCATED_EMAIL, REDACTED_EMAIL, text)
    text = _sub(_PHONE, REDACTED_PHONE, text)
    text = _sub(_HANDLE, REDACTED_EMAIL, text)
    return text, count


def excerpt(text: str, limit: int = EXCERPT_CHARS) -> str:
    """Trim to an excerpt on a word boundary, marking that it is one."""
    if not text or len(text) <= limit:
        return text
    cut = text[:limit]
    space = cut.rfind(" ")
    if space > limit * 0.8:
        cut = cut[:space]
    return cut.rstrip() + " … [excerpt - see source link for the full posting]"


def sanitize_for_storage(text: str) -> tuple[str, int]:
    """Redact contact details, then excerpt. The only function callers need."""
    clean, redactions = redact_contacts(text)
    return excerpt(clean), redactions


def contains_contact_details(text: str) -> bool:
    """True when text still carries anything that looks like a direct contact.

    Used by the pre-publish audit so a leak is caught by a check rather than by a person.
    """
    if not text:
        return False
    return bool(_EMAIL.search(text) or _OBFUSCATED_EMAIL.search(text) or _HANDLE.search(text))
