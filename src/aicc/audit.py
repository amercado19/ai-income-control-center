"""Append-only audit log (spec section 32).

JSONL, one event per line, never rewritten. Appending is atomic enough for a single-writer
system (GitHub Actions serializes runs through a concurrency group) and survives partial writes
because a torn final line is simply dropped on read rather than corrupting the file.
"""

from __future__ import annotations

import json
from typing import Any

from .config import AUDIT_LOG
from .models import Actor, AuditEvent


def record(
    action: str,
    *,
    actor: Actor | str = Actor.SYSTEM,
    object_type: str = "",
    object_id: str = "",
    before: Any = None,
    after: Any = None,
    source: str = "",
    result: str = "ok",
    error: str = "",
) -> AuditEvent:
    """Append one event. Never raises: a logging failure must not abort real work."""
    event = AuditEvent(
        actor=actor.value if isinstance(actor, Actor) else str(actor),
        action=action,
        object_type=object_type,
        object_id=object_id,
        before=_summarize(before),
        after=_summarize(after),
        source=source,
        result=result,
        error=error,
    )
    try:
        AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
        with AUDIT_LOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event.to_dict(), default=str) + "\n")
    except OSError:
        pass
    return event


def _summarize(value: Any) -> Any:
    """Keep the log readable and keep large payloads out of it.

    Also a leak guard: a full client-file payload must never end up in an audit line, because
    the audit log IS committed to the repository.
    """
    if value is None or isinstance(value, (int, float, bool)):
        return value
    if isinstance(value, str):
        return value if len(value) <= 300 else value[:297] + "..."
    if isinstance(value, dict):
        return {k: _summarize(v) for k, v in list(value.items())[:25]}
    if isinstance(value, (list, tuple)):
        return [_summarize(v) for v in list(value)[:25]]
    return str(value)[:300]


def read_all(limit: int | None = None) -> list[AuditEvent]:
    """Read events newest-first. Silently skips malformed lines."""
    if not AUDIT_LOG.exists():
        return []
    events: list[AuditEvent] = []
    with AUDIT_LOG.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(AuditEvent.from_dict(json.loads(line)))
            except (json.JSONDecodeError, TypeError):
                continue
    events.reverse()
    return events[:limit] if limit else events
