"""The safety properties. If any of these regress, the system is dangerous rather than merely broken."""

from __future__ import annotations

import pytest

from aicc import state, storage
from aicc.config import MAX_NEW_MONTHLY_CASH_SPEND, CostGate, CostRequest
from aicc.fulfillment import pipeline, reviewer
from aicc.fulfillment.worker import RuleBasedWorker, WorkspaceEscapeError, workspace_for
from aicc.models import Actor, Job, JobStatus
from aicc.storage import UnsafeToCommitError, assert_safe_to_commit

# --------------------------------------------------------------------- cost gate


def test_cost_ceiling_is_zero():
    assert MAX_NEW_MONTHLY_CASH_SPEND == 0.00


@pytest.mark.parametrize("amount", [0.15, 7.0, 19.99, 100.0])
def test_cost_gate_declines_any_cash(amount):
    decision = CostGate().request(
        CostRequest(
            service="Test",
            reason="Test",
            monthly_estimate=amount,
            benefit="none",
            can_continue_without=True,
        )
    )
    assert decision.approved is False
    assert decision.requires_human is True


def test_cost_gate_allows_free_things():
    decision = CostGate().request(
        CostRequest(
            service="Free API",
            reason="Discovery",
            monthly_estimate=0.0,
            benefit="opportunities",
            can_continue_without=False,
        )
    )
    assert decision.approved is True


def test_cost_gate_has_no_override():
    """There must be no argument, flag, or attribute that forces approval."""
    import inspect

    sig = inspect.signature(CostGate.request)
    assert set(sig.parameters) == {"self", "req"}, "request() must take no override parameter"


def test_upwork_connect_spend_beyond_free_allowance_is_declined():
    from aicc.connectors.upwork import UpworkConnector

    quote = UpworkConnector.connect_spend_request(connects=16, expected_net=400.0, job_title="Test")
    assert quote["billable_connects"] == 6  # 16 requested, 10 free remaining
    assert quote["cash_cost"] == pytest.approx(0.90)
    assert quote["approved"] is False
    assert quote["requires_human"] is True


def test_upwork_connect_spend_within_free_allowance_is_allowed():
    from aicc.connectors.upwork import UpworkConnector

    quote = UpworkConnector.connect_spend_request(connects=8, expected_net=400.0, job_title="Test")
    assert quote["billable_connects"] == 0
    assert quote["cash_cost"] == 0.0
    assert quote["approved"] is True


# ------------------------------------------------------------------ emergency stop


def test_emergency_stop_blocks_the_pipeline(active_system):
    state.emergency_stop("test")
    with pytest.raises(pipeline.PipelineBlocked):
        pipeline.run(Job(title="should not run"))


def test_emergency_stop_disables_every_automation(active_system):
    state.emergency_stop("test")
    st = state.SystemState.load()
    assert all(not a["enabled"] for a in st.automations.values())


def test_start_refuses_while_emergency_stopped(active_system):
    state.emergency_stop("test")
    ok, msg = state.start()
    assert ok is False
    assert "emergency stop" in msg.lower()


def test_corrupt_state_file_fails_closed_to_off(isolated_data):
    state.start()
    state.STATE_FILE.write_text("{ this is not json", encoding="utf-8")
    assert state.SystemState.load().run_state == "OFF"


# --------------------------------------------------------------- human-only actions


def test_delivery_requires_a_human(active_system):
    job = Job(title="t", status=JobStatus.READY_TO_DELIVER.value)
    storage.jobs.put(job)
    ok, msg = pipeline.deliver(job, actor=Actor.SYSTEM)
    assert ok is False and "human" in msg.lower()

    ok, _ = pipeline.deliver(job, actor=Actor.ANDRES)
    assert ok is True


@pytest.mark.parametrize("actor", [Actor.CLAUDE, Actor.GITHUB_ACTIONS, Actor.SYSTEM])
def test_no_non_human_actor_can_deliver(active_system, actor):
    job = Job(title="t", status=JobStatus.READY_TO_DELIVER.value)
    storage.jobs.put(job)
    assert pipeline.deliver(job, actor=actor)[0] is False


def test_delivery_refused_unless_ready(active_system):
    job = Job(title="t", status=JobStatus.WORK.value)
    assert pipeline.deliver(job, actor=Actor.ANDRES)[0] is False


# ------------------------------------------------------------ reviewer independence


def test_reviewer_rejects_worker_self_assessment():
    with pytest.raises(reviewer.ReviewerContaminationError):
        reviewer.assert_reviewer_input_clean({"worker_notes": "I think this is great"})


@pytest.mark.parametrize("key", ["worker_notes", "worker_assessment", "worker_confidence", "self_score"])
def test_every_contamination_key_is_blocked(key):
    with pytest.raises(reviewer.ReviewerContaminationError):
        reviewer.assert_reviewer_input_clean({key: "anything"})


def test_review_signature_cannot_accept_a_job_object():
    """Structural guarantee: there is no parameter through which the Job (and therefore
    worker_notes) could reach the reviewer."""
    import inspect

    params = set(inspect.signature(reviewer.review).parameters)
    assert "job" not in params
    assert "worker_notes" not in params


def test_pipeline_does_not_pass_worker_notes_to_reviewer():
    """Read the source: the call site must not forward worker_notes."""
    import inspect

    src = inspect.getsource(pipeline.run)
    call = src[src.index("reviewer.review(") :]
    call = call[: call.index("\n        )")]
    # Strip comments - the call site carries an explanatory comment that names the field.
    code = "\n".join(line.split("#", 1)[0] for line in call.splitlines())
    assert "worker_notes" not in code


# ---------------------------------------------------------------- secret leak guard


@pytest.mark.parametrize(
    "payload",
    [
        {"note": "sk-ant-api03-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},
        {"note": "ghp_abcdefghijklmnopqrstuvwxyz0123456789"},
        {"token": "AKIAIOSFODNN7EXAMPLE"},
        {"key": "-----BEGIN RSA PRIVATE KEY-----"},
        {"card": "4111111111111111"},
        {"ssn": "123-45-6789"},
    ],
)
def test_storage_refuses_credentials_and_identifiers(payload):
    with pytest.raises(UnsafeToCommitError):
        assert_safe_to_commit(payload)


def test_storage_allows_ordinary_content():
    assert_safe_to_commit({"title": "Clean up a CSV", "budget": 500, "url": "https://example.com/job/1"})


def test_collection_put_enforces_the_guard():
    from aicc.models import Opportunity

    opp = Opportunity(title="x", description="my key is ghp_abcdefghijklmnopqrstuvwxyz0123456789")
    with pytest.raises(UnsafeToCommitError):
        storage.opportunities.put(opp)


# ------------------------------------------------------------- workspace containment


@pytest.mark.parametrize("bad", ["../../../etc/passwd", "/etc/passwd", "..\\..\\windows\\system32"])
def test_worker_cannot_write_outside_its_workspace(bad):
    from aicc.fulfillment.worker import _safe_path

    job = Job(id="job_test")
    ws = workspace_for(job)
    resolved = _safe_path(ws, bad)
    assert str(resolved).startswith(str(ws.resolve()))


def test_workspace_escape_error_exists():
    assert issubclass(WorkspaceEscapeError, RuntimeError)


def test_workspaces_are_gitignored():
    from pathlib import Path

    gitignore = (Path(__file__).resolve().parents[1] / ".gitignore").read_text(encoding="utf-8")
    assert "workspaces/" in gitignore


# ----------------------------------------------------------------- truthful claims


def test_proposal_refuses_unverifiable_claims(sample_opportunity):
    from aicc import proposals

    with pytest.raises(proposals.UnverifiableClaimError):
        proposals.build_experience(["ten years at goldman sachs"])


def test_every_generated_claim_is_backed_by_a_real_artifact(sample_opportunity):
    from aicc import proposals
    from aicc.config import PROFILE

    prop = proposals.generate(sample_opportunity)
    assert prop.claims_made
    for claim in prop.claims_made:
        assert claim in PROFILE.demonstrated


def test_proposal_includes_ai_disclosure_by_default(sample_opportunity):
    from aicc import proposals

    prop = proposals.generate(sample_opportunity)
    assert prop.ai_disclosure_included is True
    assert "AI" in prop.body


def test_proposal_is_not_generic(sample_opportunity):
    from aicc import proposals

    body = proposals.generate(sample_opportunity).body.lower()
    for banned in ["dear hiring manager", "i am excited", "i am the best candidate", "lorem"]:
        assert banned not in body


def test_rule_based_worker_never_claims_to_be_claude():
    from aicc.fulfillment.worker import ClaudeWorker, select_worker

    worker, note = select_worker()
    available, _ = ClaudeWorker.available()
    if not available:
        assert worker is RuleBasedWorker
        assert "rule-based" in note.lower()


# ------------------------------------------------------- third-party privacy


@pytest.mark.parametrize(
    "text",
    [
        "Email me at michael@example.io with your start date",
        "Apply: talent+hn@example.co. Short cover note matters.",
        "Or email me at jason [at] withclad [dot] com",
        "reach me at garen (at) overture (dot) business",
        "Call +1 555-123-4567 to discuss",
        "We coordinate on Telegram: @recruiter99",
    ],
)
def test_third_party_contact_details_are_redacted_before_storage(text):
    """The store is committed to a public repo and git history is permanent. A hiring manager
    posted their address on Hacker News, not into our repository."""
    from aicc.privacy import contains_contact_details, sanitize_for_storage

    clean, redactions = sanitize_for_storage(text)
    assert redactions >= 1
    assert not contains_contact_details(clean)


def test_redaction_does_not_eat_ordinary_numbers():
    from aicc.privacy import sanitize_for_storage

    clean, redactions = sanitize_for_storage("Budget is $500, order id 1234567890, posted 2026-09-11")
    assert redactions == 0
    assert "$500" in clean and "1234567890" in clean


def test_ingest_redacts_and_excerpts():
    from aicc.connectors.base import make_opportunity

    opp = make_opportunity(
        source="hackernews",
        title="Test role",
        description="Great contract role. Email me at hiring@example.com. " + "detail " * 400,
    )
    assert "hiring@example.com" not in opp.description
    assert "[contact removed]" in opp.description
    assert len(opp.description) < 2000, "only an excerpt is persisted, not the whole posting"
    # The unredacted text stays available in-process for scoring, but is not a dataclass field.
    assert "hiring@example.com" in opp.full_description
    assert "full_description" not in opp.to_dict()


def test_scoring_reads_the_full_text_not_the_excerpt():
    """Redaction protects the store; it must not blind the analysis."""
    from aicc.connectors.base import make_opportunity

    opp = make_opportunity(
        source="hackernews",
        title="Role",
        description=("Standard blurb. " * 300) + " We run AI detection on every submission.",
    )
    assert "AI detection" not in opp.description, "the tell is past the excerpt boundary"
    from aicc.models import RiskFlag
    from aicc.scoring import detect_risks

    assert RiskFlag.AI_PROHIBITED in detect_risks(opp), "scoring must still see it"


# ------------------------------------------------- provider timestamp normalization
#
# Found by a live scan, not by a unit test: Himalayas returns `pubDate` as a Unix epoch
# INTEGER while every other feed returns a string. `Opportunity.posted_time` is declared
# `str`, so the integer travelled all the way from the connector to the win-probability
# model before crashing on `.replace()` - a stack trace a long way from its cause, and it
# took down the whole scan rather than one listing.


def test_epoch_integers_from_providers_become_iso_strings() -> None:
    from aicc.connectors.base import normalize_timestamp

    # Seconds (Himalayas) and milliseconds (several JS-backed APIs) both appear in the wild.
    assert normalize_timestamp(1757600000).startswith("2025-")
    assert normalize_timestamp(1757600000000).startswith("2025-")
    assert normalize_timestamp("1757600000").startswith("2025-")


def test_normalize_timestamp_passes_strings_through_and_survives_junk() -> None:
    from aicc.connectors.base import normalize_timestamp

    assert normalize_timestamp("2026-09-11T12:00:00Z") == "2026-09-11T12:00:00Z"
    assert normalize_timestamp("Thu, 11 Sep 2026 12:00:00 GMT") == "Thu, 11 Sep 2026 12:00:00 GMT"
    for junk in (None, "", "   ", True, False, object(), float("nan"), 10**18):
        assert isinstance(normalize_timestamp(junk), str)


def test_make_opportunity_coerces_a_non_string_posted_time() -> None:
    """The boundary every connector funnels through, so no new connector can reintroduce this."""
    from aicc.connectors.base import make_opportunity

    opp = make_opportunity(source="himalayas", title="T", description="d", posted_time=1757600000)
    assert isinstance(opp.posted_time, str)
    assert opp.posted_time.startswith("2025-")


def test_scoring_survives_an_unparseable_posted_time() -> None:
    """Degrade to 'age unknown'. A scan must not die on one provider's bad date."""
    from aicc.classes import _age_days

    assert _age_days("not a date at all") is None
    assert _age_days(None) is None
    assert _age_days("") is None
    assert _age_days(1757600000) is not None  # coerced, not crashed


# ------------------------------------------------- HN header parsing / employment commitment
#
# Every case below is a real header from the September 2026 "Who is hiring?" thread.


HN_HEADERS = [
    # (header, expected commitment, expected contractor flag)
    (
        "Virtasant (1099 contractor for a client of ours) | Virtasant.com | Remote (USA)| full-time "
        "Hey, we are a finops/cloud optimization company looking for a Senior Data Engineer.",
        "FULL_TIME",
        True,
    ),
    ("Axmed | AI Engineer | REMOTE (preferred Spain, UK, Poland) | Full-time | apply: example", "FULL_TIME", False),
    ("AREO | Remote (EU) / on-site in Bremen, Germany | Full-Time | Equity | Roles: [Engineering Manager]", "FULL_TIME", False),
    ("We The Flywheel | AI-Native Engineers & Operators | REMOTE (worldwide) | Contract / Part-time (10-40 hrs/wk)", "PART_TIME", True),
    ("Duets Network | Founding Engineer | REMOTE (US-Based Only) | Equity + discretionary cash | ~10-15 hrs/wk", "PART_TIME", False),
    ("Greywatch | Co-Founder & CEO | REMOTE Greywatch is building an AI security and governance platform", "", False),
]


@pytest.mark.parametrize("header,commitment,contractor", HN_HEADERS)
def test_hn_header_separates_hours_from_tax_status(header: str, commitment: str, contractor: bool) -> None:
    """The bug this pins: "1099 contractor ... full-time" is BOTH, and it is still full-time.

    Treating "contractor" as evidence against full-time let a 40-hour salaried role through as
    freelance work, where it then scored higher than every real gig in the list.
    """
    from aicc.connectors.hackernews import parse_header

    parsed = parse_header(header)
    assert parsed.get("commitment", "") == commitment
    assert (parsed.get("contractor") == "true") is contractor


def test_hn_header_extracts_a_usable_role_title() -> None:
    from aicc.connectors.hackernews import parse_header

    assert parse_header("Axmed | AI Engineer | REMOTE | Full-time").get("role") == "AI Engineer"
    # Plural role words, and parentheticals stripped out of the middle rather than only the end.
    assert parse_header("We The Flywheel | AI-Native Engineers & Operators | REMOTE").get("role") == "AI-Native Engineers & Operators"
    assert "(" not in parse_header("Squoosh.AI | Full-Stack Engineer (full-time, REMOTE) + PhD Researcher (part-time) | url").get(
        "role", ""
    )


def test_hn_header_invents_nothing_when_the_posting_says_nothing() -> None:
    from aicc.connectors.hackernews import parse_header

    parsed = parse_header("Greywatch | Co-Founder & CEO | REMOTE building a platform")
    assert "commitment" not in parsed
    assert "contractor" not in parsed


def test_full_time_roles_are_penalised_but_not_hidden() -> None:
    """Downranked below any real gig, still visible. Rejecting outright is not the system's call."""
    from aicc import scoring
    from aicc.models import Opportunity, RiskFlag

    text = "Senior Data Engineer. Python, SQL and dbt pipelines for a healthcare data company. " * 6
    common = dict(
        source="hackernews",
        title="Co - Senior Data Engineer",
        description=text,
        skills=["python", "sql"],
        budget_min=80.0,
        budget_max=90.0,
        budget_type="HOURLY",
    )
    gig = Opportunity(**common)
    full = Opportunity(**common, engagement_type="FULL_TIME")

    scoring.score_opportunity(gig)
    scoring.score_opportunity(full)

    assert RiskFlag.FULL_TIME_EMPLOYMENT.value in full.risk_flags
    assert RiskFlag.FULL_TIME_EMPLOYMENT.value not in gig.risk_flags
    assert full.score < gig.score - 20, "The penalty must actually move it down the list."
    assert not full.score_breakdown.get("rejected"), "Penalised, not rejected - he decides."
    assert full.score_band != "STRONG"


def test_part_time_and_contract_are_not_penalised() -> None:
    from aicc import scoring
    from aicc.models import Opportunity, RiskFlag

    text = "Build and maintain scheduled Python data pipelines for our reporting stack. " * 6
    for commitment in ("PART_TIME", "CONTRACT", ""):
        opp = Opportunity(source="hackernews", title="Co - Data Engineer", description=text, skills=["python"], engagement_type=commitment)
        scoring.score_opportunity(opp)
        assert RiskFlag.FULL_TIME_EMPLOYMENT.value not in opp.risk_flags, commitment
