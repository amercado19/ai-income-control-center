"""Portfolio case studies: every claim must be backed, and cold proposals get only public proof.

Fabricating portfolio projects is a hard prohibition and a marketplace-suspension offence, so
these tests treat an unbacked claim as a build failure rather than a style issue.
"""

from __future__ import annotations

import pytest

from aicc import portfolio
from aicc.config import PROFILE

ALL = portfolio.all_studies()


def test_there_are_case_studies_and_their_keys_are_unique() -> None:
    assert ALL
    keys = [s.key for s in ALL]
    assert len(keys) == len(set(keys))


@pytest.mark.parametrize("study", ALL, ids=lambda s: s.key)
def test_every_study_is_substantive(study: portfolio.CaseStudy) -> None:
    assert study.approach and study.outcome and study.stack
    assert len(study.problem) > 80, "A problem statement this short is a headline, not a case study."
    assert study.sells, f"{study.key} names no category it is proof for, so nothing can cite it."


@pytest.mark.parametrize("study", ALL, ids=lambda s: s.key)
def test_every_piece_of_evidence_says_where_to_check_it(study: portfolio.CaseStudy) -> None:
    for ev in study.public_evidence + study.private_evidence:
        assert ev.claim.strip(), f"{study.key}: evidence with no claim"
        assert ev.where.strip(), f"{study.key}: evidence with no location - unverifiable by construction"


@pytest.mark.parametrize("study", ALL, ids=lambda s: s.key)
def test_no_evidence_links_anywhere(study: portfolio.CaseStudy) -> None:
    """Rule reversed on 2026-09-14, deliberately.

    This used to require that public evidence carry a resolvable URL. Those URLs pointed at the
    repositories the private internal projects publish, which is precisely what must never reach a
    buyer - and the module is read by the public dashboard. Nothing here links anywhere now.
    """
    for ev in list(study.public_evidence) + list(study.private_evidence):
        assert not ev.url, f"{study.key}: evidence still carries a link ({ev.url!r})"


@pytest.mark.parametrize("study", ALL, ids=lambda s: s.key)
def test_citable_claims_are_only_the_publicly_backed_ones(study: portfolio.CaseStudy) -> None:
    """A cold proposal may not assert something the reader has no way to check."""
    citable = study.citable_claims()
    private_claims = {e.claim for e in study.private_evidence}
    assert not (set(citable) & private_claims)
    assert citable == [e.claim for e in study.public_evidence]


def test_every_study_has_at_least_one_publicly_verifiable_claim() -> None:
    """A study with no public proof cannot be used in a proposal, so it would be dead weight."""
    unbacked = [s.key for s in ALL if not s.public_evidence]
    assert not unbacked, f"{unbacked} have no public evidence and could never be cited."


def test_no_link_points_at_a_private_repository() -> None:
    """nfl-pipeline and mlb-pipeline are private. A link to either is a 404 to a client."""
    private_repos = ("github.com/amercado19/nfl-pipeline", "github.com/amercado19/mlb-pipeline")
    for study in ALL:
        for url in study.links():
            for repo in private_repos:
                assert repo not in url, f"{study.key} links {url}, which a client cannot open."


@pytest.mark.parametrize("study", ALL, ids=lambda s: s.key)
def test_studies_claim_nothing_outside_the_operator_profile(study: portfolio.CaseStudy) -> None:
    """The profile is the single source of truth for what may be claimed (spec section 20)."""
    known = PROFILE.skill_set()
    # Tools named in a case study should be things the profile already covers, or plainly
    # generic infrastructure. An unrecognised entry means either the profile is stale or the
    # case study has drifted into claiming something unproven - both worth failing on.
    generic = {
        "github actions",
        "cron",
        "github pages",
        "ci/cd",
        "bash",
        "monitoring",
        "model calibration",
        "backtesting",
        "reporting",
        "pytest",
        "ruff",
        "mypy",
    }
    for tool in study.stack:
        assert tool.lower() in known or tool.lower() in generic, (
            f"{study.key} claims {tool!r}, which is neither in the operator profile nor generic infrastructure."
        )


def test_no_private_project_is_named_or_described_anywhere() -> None:
    """The invariant that replaced the disclaimer.

    A disclaimer explaining what the private projects are about was itself the disclosure. The
    rule now is that they are not named, linked or described by subject at all.
    """
    import json
    import re

    blob = json.dumps(portfolio.summary()) + json.dumps([portfolio.to_markdown(s) for s in ALL])
    banned = re.compile(r"(?i)\b(nfl|mlb|betting|sportsbook|wager|parlay|picks)\b|github\.com/amercado19|amercado19\.github\.io")
    hits = [m.group(0) for m in banned.finditer(blob)]
    assert not hits, f"portfolio output still exposes private projects: {sorted(set(hits))}"


def test_case_studies_do_not_tout() -> None:
    for study in ALL:
        blob = " ".join([study.one_line, study.problem] + study.approach + study.outcome).lower()
        for touting in ("guaranteed", "risk-free", "sure thing", "free money", "you should bet", "profitable picks"):
            assert touting not in blob, f"{study.key} contains {touting!r}"


def test_visibility_note_names_no_repository() -> None:
    note = portfolio.summary()["visibility_note"].lower()
    assert "private" in note, "the note must still say the evidence is private"
    for name in ("nfl", "mlb", "github.com", "amercado19"):
        assert name not in note, f"visibility note still names {name!r}"


def test_for_category_routes_to_relevant_proof() -> None:
    assert portfolio.for_category("automation")
    assert portfolio.for_category("scheduled_automation")
    assert portfolio.for_category("nonexistent-category-xyz") == []
    # Case and whitespace should not change the answer.
    assert portfolio.for_category("  AUTOMATION ") == portfolio.for_category("automation")


def test_get_returns_a_study_or_none() -> None:
    assert portfolio.get("failure_alerting") is not None
    assert portfolio.get("no-such-study") is None


def test_summary_counts_match_the_studies() -> None:
    s = portfolio.summary()
    assert s["count"] == len(ALL)
    assert s["with_public_proof"] == sum(1 for x in ALL if x.public_evidence)
