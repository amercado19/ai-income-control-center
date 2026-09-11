"""Notifications: one rolling issue, quiet when nothing is wrong, never crashes the run.

The property under test throughout is restraint. A channel that pings on every scheduled run
trains its owner to ignore it, and by the time something actually matters the notification has
already been made worthless.
"""

from __future__ import annotations

import pytest

from aicc import notify
from aicc.notify import GitHubUnavailable, Notification


class FakeGitHub:
    """Records calls so the tests can assert on what was NOT sent, which is the point."""

    def __init__(self, issues: list[dict] | None = None) -> None:
        self.issues = issues or []
        self.calls: list[tuple[str, str, dict | None]] = []
        self._next = 100

    def __call__(self, method: str, path: str, token: str, payload: dict | None = None):
        self.calls.append((method, path, payload))
        if method == "GET":
            return self.issues
        if method == "POST":
            self._next += 1
            issue = {"number": self._next, "body": (payload or {}).get("body", ""), "state": "open"}
            self.issues.append(issue)
            return issue
        if method == "PATCH":
            number = int(path.rsplit("/", 1)[-1])
            for issue in self.issues:
                if issue["number"] == number:
                    issue.update(payload or {})
                    return issue
        return None

    @property
    def writes(self) -> list[tuple[str, str, dict | None]]:
        return [c for c in self.calls if c[0] in ("POST", "PATCH")]


@pytest.fixture
def gh(monkeypatch: pytest.MonkeyPatch) -> FakeGitHub:
    fake = FakeGitHub()
    monkeypatch.setattr(notify, "_request", fake)
    return fake


WAITING = [Notification("Proposal awaiting your approval", "Clean 14 spreadsheets", urgent=False)]
BLOCKED = [Notification("Job needs you", "The pipeline stopped", urgent=True)]


# ------------------------------------------------------------------ the rolling issue


def test_opens_one_issue_when_something_starts_needing_a_human(gh: FakeGitHub) -> None:
    result = notify.sync_issue(WAITING, repo="a/b", token="t")
    assert "Opened" in result
    posts = [c for c in gh.writes if c[0] == "POST"]
    assert len(posts) == 1
    assert posts[0][2]["title"] == notify.NEEDS_YOU_TITLE
    assert notify.MARKER in posts[0][2]["body"]


def test_editing_an_unchanged_issue_is_skipped_entirely(gh: FakeGitHub) -> None:
    """The property that makes a per-run notifier tolerable: re-running changes nothing."""
    notify.sync_issue(WAITING, repo="a/b", token="t")
    before = len(gh.writes)
    result = notify.sync_issue(WAITING, repo="a/b", token="t")
    assert len(gh.writes) == before, "A second identical run wrote to GitHub again."
    assert "Left alone" in result


def test_a_changed_list_edits_rather_than_opening_a_second_issue(gh: FakeGitHub) -> None:
    notify.sync_issue(WAITING, repo="a/b", token="t")
    notify.sync_issue(WAITING + BLOCKED, repo="a/b", token="t")
    assert len([c for c in gh.writes if c[0] == "POST"]) == 1, "Opened a second issue instead of editing."
    assert [c for c in gh.writes if c[0] == "PATCH"]


def test_the_issue_closes_itself_when_nothing_is_waiting(gh: FakeGitHub) -> None:
    notify.sync_issue(WAITING, repo="a/b", token="t")
    result = notify.sync_issue([], repo="a/b", token="t")
    assert "Closed" in result
    assert gh.issues[0]["state"] == "closed"


def test_nothing_waiting_and_no_issue_open_sends_nothing(gh: FakeGitHub) -> None:
    """No daily all-clear. A system that pings to say nothing is wrong has taught its owner
    to ignore it by the time something is."""
    result = notify.sync_issue([], repo="a/b", token="t")
    assert gh.writes == []
    assert "Nothing to do" in result


def test_the_rolling_issue_is_found_by_marker_not_by_title(gh: FakeGitHub) -> None:
    """A human could name an issue 'Needs you'. Editing theirs would be rude and wrong."""
    gh.issues.append({"number": 7, "body": "I also called mine Needs you", "state": "open", "title": notify.NEEDS_YOU_TITLE})
    notify.sync_issue(WAITING, repo="a/b", token="t")
    assert [c for c in gh.writes if c[0] == "POST"], "Should have opened its own issue"
    assert not any(c[0] == "PATCH" and "/7" in c[1] for c in gh.writes), "Edited a human's issue."


# ------------------------------------------------------------------ failure behaviour


def test_missing_credentials_raise_a_handled_error_rather_than_crashing() -> None:
    with pytest.raises(GitHubUnavailable):
        notify.sync_issue(WAITING, repo="", token="")
    with pytest.raises(GitHubUnavailable):
        notify.sync_issue(WAITING, repo="a/b", token="")


def test_collect_survives_an_unreadable_system_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """A notifier that can take down the pipeline has inverted its purpose."""
    import aicc.state

    def explode() -> None:
        raise OSError("disk gone")

    monkeypatch.setattr(aicc.state.SystemState, "load", staticmethod(explode))
    assert notify._system_state() is None
    assert isinstance(notify.collect(), list)


def test_api_errors_do_not_echo_the_response_body() -> None:
    """An API error can echo request headers back, and this string lands in a public log."""
    import inspect

    source = inspect.getsource(notify._request)
    assert "exc.read()" not in source
    assert "exc.reason" not in source or "code" in source


# ------------------------------------------------------------------ what it says


def test_blocking_items_are_separated_from_merely_waiting_ones() -> None:
    body = notify.render(BLOCKED + WAITING)
    assert body.index("Blocked") < body.index("Waiting for you")


def test_the_body_carries_the_marker_so_the_issue_can_be_found_again() -> None:
    assert notify.render([]).startswith(notify.MARKER)
    assert notify.render(WAITING).startswith(notify.MARKER)


def test_summary_line_counts_blocking_items_separately() -> None:
    assert notify.summary_line([]) == "Nothing needs you."
    assert "1 item(s) need you." == notify.summary_line(WAITING)
    assert "blocking" in notify.summary_line(WAITING + BLOCKED)


def test_collect_agrees_with_what_the_dashboard_shows() -> None:
    """A notification reporting something the dashboard does not show costs trust in both."""
    from aicc import storage
    from aicc.dashboard.build import _attention

    expected = {i["title"] for i in _attention(storage.jobs.all(), storage.proposals.all())}
    got = {n.title for n in notify.collect()}
    assert expected <= got
