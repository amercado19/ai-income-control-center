"""Free notifications (spec section 43).

The constraint that shapes this: **$0.00**. No email service, no Pushover, no Slack paid tier,
no Twilio. The only notification channel that is both free and reliable here is one GitHub
already runs - it emails the repository owner about workflow failures and about activity on
issues they are subscribed to. So the system speaks through GitHub rather than around it.

The design decision worth stating is the **rolling issue**. A naive implementation opens an
issue per event, which produces a notification per event, which produces an inbox the owner
stops reading - and a notification channel nobody reads is worse than none, because it creates
the belief that they would have been told. Instead there is exactly one issue, titled
``NEEDS_YOU_TITLE``. It is created when something needs attention, **edited** while the list
changes, and **closed** when the list empties. GitHub emails on creation and on close; edits
are quiet. So the owner hears about the transition into needing them and the transition out,
and nothing in between.

The second decision: this never invents urgency. If nothing needs a human, the issue closes and
no message is sent. A system that pings daily to say "all clear" has trained its owner to
ignore it by the time something is actually wrong.

Everything here degrades to printing. Without a token it writes to stdout and the workflow
summary, which is still a real notification - a failed run emails the owner anyway.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

NEEDS_YOU_TITLE = "Needs you"
MARKER = "<!-- aicc:needs-you -->"
"""Identifies the rolling issue. Searching by title alone would collide with anything a human
happened to name the same thing."""

API = "https://api.github.com"


@dataclass
class Notification:
    title: str
    body: str
    urgent: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {"title": self.title, "body": self.body, "urgent": self.urgent}


def collect() -> list[Notification]:
    """Everything currently waiting on a human, newest concern first.

    Reads the same ``_attention`` list the dashboard renders, so the notification and the screen
    can never disagree - a notification channel that reports something the dashboard does not
    show is a bug that costs trust in both.
    """
    from . import storage
    from .dashboard.build import _attention

    items = _attention(storage.jobs.all(), storage.proposals.all())
    out = [Notification(title=i["title"], body=i.get("detail", ""), urgent=i.get("severity") == "critical") for i in items]

    st = _system_state()
    if st is not None and st.run_state == "EMERGENCY_STOP":
        out.insert(0, Notification("Emergency stop is engaged", st.why_blocked(), urgent=True))
    return out


def _system_state() -> Any:
    try:
        from .state import SystemState

        return SystemState.load()
    except Exception:  # noqa: BLE001 - a notification must never be the thing that crashes
        return None


def render(notifications: list[Notification]) -> str:
    """The issue body. Written to be read on a phone, in a notification preview."""
    if not notifications:
        return f"{MARKER}\nNothing needs you right now."
    lines = [MARKER, ""]
    urgent = [n for n in notifications if n.urgent]
    normal = [n for n in notifications if not n.urgent]
    if urgent:
        lines.append("## Blocked")
        lines += [f"- **{n.title}**  \n  {n.body[:300]}" for n in urgent]
        lines.append("")
    if normal:
        lines.append("## Waiting for you")
        lines += [f"- **{n.title}**  \n  {n.body[:300]}" for n in normal]
        lines.append("")
    lines.append("---")
    lines.append("Open the dashboard, or run `python -m aicc status`.")
    lines.append("")
    lines.append("This issue is edited in place rather than reopened per event, and closes itself when nothing is waiting.")
    return "\n".join(lines)


# --------------------------------------------------------------------------- GitHub transport


class GitHubUnavailable(RuntimeError):
    """No token, no repository, or the API refused. Never fatal - notification is best-effort."""


def _request(method: str, path: str, token: str, payload: dict[str, Any] | None = None) -> Any:
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        f"{API}{path}",
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
            "User-Agent": "ai-income-control-center",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:  # noqa: S310 - fixed https host
            return json.loads(resp.read().decode() or "null")
    except urllib.error.HTTPError as exc:
        # Deliberately does not include the response body: an API error can echo request
        # headers, and this string ends up in logs.
        raise GitHubUnavailable(f"GitHub API {exc.code} on {method} {path}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise GitHubUnavailable(f"GitHub API unreachable: {exc}") from exc


def _find_rolling_issue(repo: str, token: str) -> dict[str, Any] | None:
    issues = _request("GET", f"/repos/{repo}/issues?state=open&per_page=50", token) or []
    for issue in issues:
        if MARKER in (issue.get("body") or ""):
            return issue
    return None


def sync_issue(notifications: list[Notification], *, repo: str | None = None, token: str | None = None) -> str:
    """Reconcile the rolling issue with what is actually waiting. Returns what it did.

    Idempotent by construction: it reads the current issue, compares the body it would write,
    and writes nothing when they match. Re-running on every scheduled run is therefore silent,
    which is the only way a per-run notifier is tolerable.
    """
    repo = repo or os.environ.get("GITHUB_REPOSITORY", "")
    token = token or os.environ.get("GITHUB_TOKEN", "")
    if not repo or not token:
        raise GitHubUnavailable("No GITHUB_REPOSITORY/GITHUB_TOKEN. Falling back to printing, which is still a notification.")

    existing = _find_rolling_issue(repo, token)
    body = render(notifications)

    if not notifications:
        if existing is None:
            return "Nothing waiting; no issue open. Nothing to do."
        _request("PATCH", f"/repos/{repo}/issues/{existing['number']}", token, {"state": "closed", "body": body})
        return f"Closed #{existing['number']} - nothing needs you any more."

    if existing is None:
        created = _request("POST", f"/repos/{repo}/issues", token, {"title": NEEDS_YOU_TITLE, "body": body})
        return f"Opened #{created['number']} with {len(notifications)} item(s)."

    if (existing.get("body") or "").strip() == body.strip():
        return f"#{existing['number']} already says exactly this. Left alone, so nobody is pinged twice."
    _request("PATCH", f"/repos/{repo}/issues/{existing['number']}", token, {"body": body})
    return f"Updated #{existing['number']} to {len(notifications)} item(s) - edits are quiet, so no new email."


def summary_line(notifications: list[Notification]) -> str:
    if not notifications:
        return "Nothing needs you."
    urgent = sum(1 for n in notifications if n.urgent)
    if urgent:
        return f"{len(notifications)} item(s) need you, {urgent} blocking."
    return f"{len(notifications)} item(s) need you."
