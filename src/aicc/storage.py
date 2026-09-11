"""Persistence layer.

Phase 1 storage strategy (spec section 33): plain JSON committed to the repository, for
NON-SENSITIVE operational data only. This is deliberate, not lazy.

* It is genuinely free and genuinely durable - git history is the backup, and GitHub keeps it.
* It survives Render's ephemeral filesystem, which cannot be trusted for persistence.
* It is diffable, so every change to operational data is reviewable in a commit.

What must never be stored here: client files, client-confidential content, credentials, session
cookies, payment data. Those live under ``WORKSPACE_ROOT``, which is gitignored, and the
sensitive-client-job feature stays disabled until there is revenue to pay for real infrastructure.
``assert_safe_to_commit`` below is the programmatic half of that guarantee.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Protocol

from .config import DATA_DIR, ensure_dirs
from .models import Job, Opportunity, Proposal, RevenueEntry


class Record(Protocol):
    """Every stored model: an id, a dict round-trip."""

    id: str

    def to_dict(self) -> dict[str, Any]: ...

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Any: ...


OPPORTUNITIES_FILE = DATA_DIR / "opportunities.json"
OPPORTUNITIES_ARCHIVE = DATA_DIR / "opportunities_archive.json"
PROPOSALS_FILE = DATA_DIR / "proposals.json"
JOBS_FILE = DATA_DIR / "jobs.json"
REVENUE_FILE = DATA_DIR / "revenue.json"


# ---------------------------------------------------------------------------
# Low-level atomic JSON
# ---------------------------------------------------------------------------


def _read(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _write(path: Path, payload: dict[str, Any]) -> None:
    ensure_dirs()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
    tmp.replace(path)


# ---------------------------------------------------------------------------
# Secret leak guard
# ---------------------------------------------------------------------------

_SECRET_PATTERNS = [
    (re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}"), "Anthropic API key"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"), "GitHub token"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "AWS access key id"),
    (re.compile(r"-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----"), "Private key"),
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}\b"), "Slack token"),
    (re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b"), "JWT"),
    (re.compile(r"\b\d{13,16}\b(?=[^\d]|$)"), "Possible payment card number"),
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "Possible US SSN"),
]


class UnsafeToCommitError(RuntimeError):
    """Raised when a record looks like it carries a credential or personal identifier."""


def assert_retention_permitted(record: Any) -> None:
    """Refuse to persist content whose source contractually forbids it.

    Upwork's API & MCP Terms cap caching at 24 hours and require permanent deletion after it. A
    committed git store keeps history forever, so Upwork content simply cannot live here.
    """
    from .connectors.upwork import assert_not_persisted

    assert_not_persisted(record)


def assert_safe_to_commit(payload: Any, *, where: str = "record") -> None:
    """Refuse to persist anything that pattern-matches a credential.

    This runs on every write, not only in CI, because the CI secret scan catches a leak after it
    has already been written to disk. This catches it before.
    """
    blob = json.dumps(payload, default=str)
    for pattern, label in _SECRET_PATTERNS:
        if pattern.search(blob):
            raise UnsafeToCommitError(
                f"Refusing to write {where}: content matches {label}. "
                "Sensitive material belongs in the gitignored workspace, not in committed data."
            )


# ---------------------------------------------------------------------------
# Collections
# ---------------------------------------------------------------------------


class Collection:
    """A dict-of-records JSON file keyed by record id."""

    def __init__(self, path: Path, model: type[Record]) -> None:
        self.path = path
        self.model = model

    def all(self) -> list[Any]:
        return [self.model.from_dict(v) for v in _read(self.path).values()]

    def get(self, record_id: str) -> Any | None:
        raw = _read(self.path).get(record_id)
        return self.model.from_dict(raw) if raw else None

    def put(self, record: Any) -> Any:
        data = _read(self.path)
        assert_retention_permitted(record)
        payload = record.to_dict()
        assert_safe_to_commit(payload, where=f"{self.model.__name__} {record.id}")
        data[record.id] = payload
        _write(self.path, data)
        return record

    def put_many(self, records: list[Any]) -> int:
        if not records:
            return 0
        data = _read(self.path)
        for record in records:
            assert_retention_permitted(record)
            payload = record.to_dict()
            assert_safe_to_commit(payload, where=f"{self.model.__name__} {record.id}")
            data[record.id] = payload
        _write(self.path, data)
        return len(records)

    def delete(self, record_id: str) -> bool:
        data = _read(self.path)
        if record_id not in data:
            return False
        del data[record_id]
        _write(self.path, data)
        return True

    def clear_demo(self) -> int:
        """Remove every demo record. Used when switching DEMO -> LIVE so that synthetic rows can
        never be mistaken for real ones."""
        data = _read(self.path)
        demo_ids = [k for k, v in data.items() if v.get("is_demo")]
        for k in demo_ids:
            del data[k]
        _write(self.path, data)
        return len(demo_ids)


opportunities = Collection(OPPORTUNITIES_FILE, Opportunity)
opportunities_archive = Collection(OPPORTUNITIES_ARCHIVE, Opportunity)
proposals = Collection(PROPOSALS_FILE, Proposal)
jobs = Collection(JOBS_FILE, Job)
revenue = Collection(REVENUE_FILE, RevenueEntry)


# ---------------------------------------------------------------------------
# Opportunity-specific helpers
# ---------------------------------------------------------------------------


def existing_dedupe_keys() -> set[str]:
    """Every dedupe key already seen, active or archived. A re-scan must not resurrect a
    listing Andres already skipped."""
    keys: set[str] = set()
    for coll in (opportunities, opportunities_archive):
        for opp in coll.all():
            keys.add(opp.dedupe_key())
    return keys


def upsert_opportunities(found: list[Opportunity]) -> tuple[int, int]:
    """Insert only genuinely new opportunities. Returns (new, duplicates_skipped)."""
    seen = existing_dedupe_keys()
    fresh: list[Opportunity] = []
    duplicates = 0
    batch_keys: set[str] = set()
    for opp in found:
        key = opp.dedupe_key()
        if key in seen or key in batch_keys:
            duplicates += 1
            continue
        batch_keys.add(key)
        fresh.append(opp)
    opportunities.put_many(fresh)
    return len(fresh), duplicates


def archive(opp: Opportunity) -> None:
    """Move a rejected or expired opportunity out of the active set (spec section 6, Level 1)."""
    opportunities_archive.put(opp)
    opportunities.delete(opp.id)


def real_revenue_entries() -> list[RevenueEntry]:
    """REAL revenue only. Demo rows are structurally excluded, not filtered by convention."""
    return [r for r in revenue.all() if not r.is_demo]
