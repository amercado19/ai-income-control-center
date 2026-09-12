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


def test_full_time_roles_are_rejected_and_the_reason_is_visible() -> None:
    """Superseded policy, kept as a record of why it changed.

    This originally asserted that full-time roles were PENALISED (30 points) but NOT rejected,
    on the reasoning that a good job is a career decision the system should not make for him.
    That reasoning was sound and the conclusion was still wrong, because it was missing a fact:
    Andres needs roughly seven more years at a 501(c)(3) or government employer for PSLF, and
    every full-time role these sources carry is a for-profit company. It is not a career
    decision with a rate attached - it is the forfeiture of seven years of loan forgiveness.
    Leaving it visible-but-ranked invited exactly the mistake it could not afford.
    """
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
    assert full.score_breakdown["rejected"] is True
    assert gig.score > 0 and not gig.score_breakdown.get("rejected")
    assert "PSLF" in full.score_breakdown["rejection_reason"]


def test_part_time_and_contract_are_not_penalised() -> None:
    from aicc import scoring
    from aicc.models import Opportunity, RiskFlag

    text = "Build and maintain scheduled Python data pipelines for our reporting stack. " * 6
    for commitment in ("PART_TIME", "CONTRACT", ""):
        opp = Opportunity(source="hackernews", title="Co - Data Engineer", description=text, skills=["python"], engagement_type=commitment)
        scoring.score_opportunity(opp)
        assert RiskFlag.FULL_TIME_EMPLOYMENT.value not in opp.risk_flags, commitment


# ------------------------------------------------- echoing a job's requirements back as an offer
#
# The worst defect this generator has had, found by reading a real drafted proposal rather than
# by any test. A Senior Backend Engineer listing produced:
#
#     What you would get:
#       - Have shipped: a double-entry ledger or equivalent money system in production
#       - A payment integration including webhook idempotency
#
# Those are the CLIENT'S hiring requirements, echoed back as things on offer. Read plainly it
# claims a production double-entry ledger he has never built. That is fabricated experience -
# the single thing this system exists to refuse - and the claim verifier never saw it, because
# it guards the experience section while this text arrived through the deliverables list.

# The real listing, kept verbatim because it is what these behaviours were written against.
# It is ALSO a PSLF conflict - "Contract to permanent" - which `test_policy.py` covers. The tests
# below are about prose, not policy, so they use the project-shaped variant underneath: putting a
# blocked listing through `generate` now raises before any prose is written, which would make
# these tests fail for a reason that has nothing to do with what they check.
NORICUM = (
    "Noricum | Senior Backend Engineer, Payments, Ledger & Provable Fairness | REMOTE | "
    "Contract to permanent | $120-160/hr. We build money infrastructure. "
    "Requirements: have shipped a double-entry ledger or equivalent money system in production, "
    "a payment integration including webhook idempotency, auth and sessions you built and operated, "
    "and infrastructure you owned end to end. "
    "You have 5+ years of backend experience. Nice to have: Rust."
)

NORICUM_AS_A_PROJECT = NORICUM.replace("Contract to permanent | ", "Fixed-price project | ")


def test_candidate_requirements_never_become_deliverables() -> None:
    from aicc.connectors.base import make_opportunity
    from aicc.proposals import extract_deliverables

    opp = make_opportunity(source="hackernews", title="Noricum - Senior Backend Engineer", description=NORICUM, skills=["python"])
    for item in extract_deliverables(opp):
        low = item.lower()
        for phrase in ("have shipped", "double-entry ledger", "years of", "you built and operated", "nice to have"):
            assert phrase not in low, f"Offered the client their own requirement back: {item!r}"


def test_a_listing_with_only_requirements_yields_honest_generic_deliverables() -> None:
    """Empty beats invented. The caller falls back to what is actually always true."""
    from aicc.connectors.base import make_opportunity
    from aicc.proposals import extract_deliverables, generate

    opp = make_opportunity(
        source="hackernews", title="Noricum - Senior Backend Engineer", description=NORICUM_AS_A_PROJECT, skills=["python"]
    )
    assert extract_deliverables(opp) == []
    body = generate(opp).body
    assert "double-entry ledger" not in body.lower()
    assert "The completed work product in the format you specified" in body


def test_genuine_deliverables_are_still_extracted() -> None:
    """The fix must not make the generator generic for listings that do state deliverables."""
    from aicc.connectors.base import make_opportunity
    from aicc.proposals import extract_deliverables

    text = (
        "We have 14 monthly sales spreadsheets that do not line up. "
        "Deliverables: one consolidated workbook, a reconciliation tab, and the script used. "
        "Budget $400-600."
    )
    opp = make_opportunity(source="demo", title="Consolidate spreadsheets", description=text, skills=["excel"])
    got = " ".join(extract_deliverables(opp)).lower()
    assert "consolidated workbook" in got
    assert "reconciliation tab" in got


def test_the_body_guard_refuses_rather_than_silently_stripping() -> None:
    """A proposal quietly stripped of a sentence is a proposal nobody reviewed."""
    import pytest as _pytest

    from aicc.proposals import ProposalDraft, UnverifiableClaimError, _assert_no_echoed_requirements

    assert ProposalDraft  # the dataclass the guard protects
    with _pytest.raises(UnverifiableClaimError):
        _assert_no_echoed_requirements("body", ["Have shipped a production ledger"])
    with _pytest.raises(UnverifiableClaimError):
        _assert_no_echoed_requirements(
            "What you would get:\n  - 5+ years of backend experience\n\nAvailability: soon.",
            ["something harmless"],
        )
    # An honest body passes untouched.
    _assert_no_echoed_requirements(
        "What you would get:\n  - A consolidated workbook\n\nAvailability: soon.",
        ["A consolidated workbook"],
    )


# ------------------------------------------------- role applications vs project proposals


def _role(description: str, title: str = "Co - Senior Engineer"):
    from aicc.classes import OpportunityClass
    from aicc.connectors.base import make_opportunity

    o = make_opportunity(source="hackernews", title=title, description=description, skills=["python"], budget_type="HOURLY")
    o.opportunity_class = OpportunityClass.ONGOING.value
    return o


ROLE_TEXT = (
    "Noricum | Senior Backend Engineer | REMOTE | $120-160/hr. We build money infrastructure. "
    "Must have shipped: a double-entry ledger or equivalent money system in production, and a "
    "payment integration including webhook idempotency. "
)


def test_an_ongoing_role_gets_an_application_not_a_project_proposal() -> None:
    """'One question before I start' sent to a company hiring an engineer reads as a misread advert."""
    from aicc.proposals import generate

    body = generate(_role(ROLE_TEXT)).body
    assert "One question before I start" not in body
    assert "What you would get:" not in body
    assert "Availability:" in body


def test_an_application_states_the_gap_rather_than_hiding_it() -> None:
    from aicc.proposals import generate

    body = generate(_role(ROLE_TEXT)).body
    assert "starting from less" in body
    assert "double-entry ledger" in body
    assert "I have not built that specific thing" in body


def test_quoted_requirements_do_not_start_mid_clause() -> None:
    """ "shipped: a double-entry ledger" reads as carelessness in the one paragraph whose
    entire purpose is to sound candid."""
    from aicc.proposals import _tidy_requirement, generate

    assert _tidy_requirement("shipped: a double-entry ledger in production").startswith("a double-entry")
    assert _tidy_requirement("experience with kubernetes at scale").startswith("kubernetes")
    assert _tidy_requirement("shipped") == ""
    assert "for shipped:" not in generate(_role(ROLE_TEXT)).body


def test_a_job_ad_header_is_never_quoted_back_as_if_it_were_a_need() -> None:
    """A job ad's first sentence is 'Company | Role | REMOTE | $rate'. Quoting that under
    'you wrote' is a mail merge with the seams showing."""
    from aicc.proposals import generate

    header_only = "Acme | Staff Engineer | REMOTE (EU) | Fixed-price project | $100-140/hr. We are growing fast. " * 3
    body = generate(_role(header_only, title="Acme - Staff Engineer")).body
    assert "You wrote:" not in body
    assert "Here is what I would bring to it" in body


def test_a_real_need_statement_is_still_quoted() -> None:
    from aicc.proposals import generate

    text = "Acme | Engineer | REMOTE. We want someone who can independently debug and validate what they ship. " * 3
    body = generate(_role(text, title="Acme - Engineer")).body
    assert "You wrote:" in body
    assert "independently debug" in body


def test_a_gig_keeps_the_project_register() -> None:
    from aicc.classes import OpportunityClass
    from aicc.connectors.base import make_opportunity
    from aicc.proposals import generate

    text = "We have 14 monthly Excel exports with slightly different column names. Deliverables: one workbook. " * 3
    o = make_opportunity(
        source="demo", title="Consolidate spreadsheets", description=text, skills=["excel"], budget_min=400.0, budget_max=600.0
    )
    assert o.opportunity_class != OpportunityClass.ONGOING.value
    body = generate(o).body
    assert "What you would get:" in body


def test_neither_register_ever_drops_the_ai_disclosure() -> None:
    from aicc.connectors.base import make_opportunity
    from aicc.proposals import AI_DISCLOSURE, generate

    gig = make_opportunity(source="demo", title="Gig", description="Clean this data. " * 30, skills=["excel"], budget_min=300.0)
    assert AI_DISCLOSURE in generate(_role(ROLE_TEXT)).body
    assert AI_DISCLOSURE in generate(gig).body


# ------------------------------------------------- PSLF: full-time employment is disqualifying
#
# The most consequential constraint in the profile, and the easiest to miss because it is not a
# skill or a rate. Andres needs ~7 more years at a 501(c)(3) or government employer for Public
# Service Loan Forgiveness. Taking a full-time role at a for-profit company does not merely
# compete for his hours - it ends qualifying employment and forfeits seven years of progress.
# No hourly figure on a job board compensates for that, so this is a reject, not a score.


def test_full_time_employment_is_rejected_not_merely_penalised() -> None:
    from aicc.connectors.base import make_opportunity
    from aicc.models import RiskFlag
    from aicc.scoring import score_opportunity

    text = "Senior Data Engineer. Python, SQL, dbt. Excellent benefits. " * 8
    o = make_opportunity(
        source="hackernews",
        title="Co - Senior Data Engineer",
        description=text,
        skills=["python", "sql"],
        budget_min=80.0,
        budget_max=90.0,
        budget_type="HOURLY",
        engagement_type="FULL_TIME",
    )
    score_opportunity(o)
    assert RiskFlag.FULL_TIME_EMPLOYMENT.value in o.risk_flags
    assert o.score_breakdown["rejected"] is True
    assert o.score == 0.0
    assert o.score_band == "SKIP"


def test_the_rejection_reason_explains_pslf_rather_than_just_saying_no() -> None:
    """A reject he does not understand is a reject he will override."""
    from aicc.connectors.base import make_opportunity
    from aicc.scoring import score_opportunity

    o = make_opportunity(source="hackernews", title="Role", description="Engineer. " * 40, skills=["python"], engagement_type="FULL_TIME")
    score_opportunity(o)
    reason = o.score_breakdown["rejection_reason"].lower()
    assert "pslf" in reason
    assert "forgiveness" in reason
    assert "contract" in reason and "part-time" in reason, "It must say which arrangements are still fine."


def test_contract_and_part_time_work_is_untouched_by_the_pslf_rule() -> None:
    """PSLF turns on the full-time employer. Freelance work alongside it is the whole point of
    this system, and a rule that blocked it would defeat the project."""
    from aicc.connectors.base import make_opportunity
    from aicc.models import RiskFlag
    from aicc.scoring import score_opportunity

    text = "Build scheduled Python data pipelines for our reporting stack. " * 8
    for engagement in ("PART_TIME", "CONTRACT", ""):
        o = make_opportunity(
            source="hackernews",
            title="Role",
            description=text,
            skills=["python"],
            engagement_type=engagement,
            budget_min=90.0,
            budget_type="HOURLY",
        )
        score_opportunity(o)
        assert RiskFlag.FULL_TIME_EMPLOYMENT.value not in o.risk_flags, engagement
        assert not o.score_breakdown.get("rejected"), engagement
        assert o.score > 0, engagement


def test_the_profile_records_the_pslf_constraint_itself() -> None:
    from aicc.config import PROFILE

    assert PROFILE.pslf_qualifying_employment_required is True
    assert PROFILE.pslf_years_remaining >= 1


# ------------------------------------------------- emergency stop scope (amendment 14)


def test_emergency_stop_halts_every_kind_of_new_work() -> None:
    from dataclasses import replace

    from aicc.state import HALTED_BY_EMERGENCY_STOP, RunState, SystemState

    stopped = replace(SystemState.load(), run_state=RunState.EMERGENCY_STOP.value, emergency_stop_reason="probe")
    for activity in sorted(HALTED_BY_EMERGENCY_STOP):
        allowed, why = stopped.activity_allowed(activity)
        assert not allowed, f"{activity} still ran under emergency stop."
        assert "EMERGENCY STOP" in why


def test_emergency_stop_never_disables_its_own_oversight() -> None:
    """A stop that silenced the audit log would destroy the record of why it was pressed."""
    from dataclasses import replace

    from aicc.state import NEVER_HALTED, RunState, SystemState

    stopped = replace(SystemState.load(), run_state=RunState.EMERGENCY_STOP.value, emergency_stop_reason="probe")
    for control in sorted(NEVER_HALTED):
        allowed, _ = stopped.activity_allowed(control)
        assert allowed, f"{control} was disabled by the stop button. Safety controls are not work."


def test_the_two_lists_do_not_overlap() -> None:
    from aicc.state import HALTED_BY_EMERGENCY_STOP, NEVER_HALTED

    assert not (HALTED_BY_EMERGENCY_STOP & NEVER_HALTED)


def test_an_unclassified_activity_is_treated_as_work_not_as_oversight() -> None:
    from dataclasses import replace

    from aicc.state import RunState, SystemState

    stopped = replace(SystemState.load(), run_state=RunState.EMERGENCY_STOP.value)
    allowed, _ = stopped.activity_allowed("something_nobody_classified")
    assert not allowed, "An unknown activity must fail safe, not run."


def test_emergency_stop_deletes_nothing_and_says_so() -> None:
    from dataclasses import replace

    from aicc.state import RunState, SystemState

    stopped = replace(SystemState.load(), run_state=RunState.EMERGENCY_STOP.value, emergency_stop_reason="probe")
    scope = stopped.emergency_stop_scope()
    assert scope["engaged"] is True
    assert scope["deletes_nothing"] is True
    assert scope["halts"] and scope["never_halts"]
    assert "audit_log" in scope["never_halts"]


def test_a_paused_system_still_runs_its_safety_controls() -> None:
    from dataclasses import replace

    from aicc.state import RunState, SystemState

    paused = replace(SystemState.load(), run_state=RunState.PAUSED.value)
    assert paused.activity_allowed("audit_log")[0]
    assert not paused.activity_allowed("opportunity_scan")[0]


# ------------------------------------------------- the live gate reads three states, not two


def test_an_unverifiable_checklist_item_does_not_block_live_mode(monkeypatch) -> None:
    """The original filter was `if not c["passing"]`, which treats None as False - so live mode
    was permanently blocked on a question the system can never answer (who last edited a cron
    schedule on GitHub). A gate that can never open is not a safety feature."""
    from aicc import health, state
    from aicc.models import Actor

    monkeypatch.setattr(
        health,
        "live_mode_checklist",
        lambda: [
            {"name": "a real check", "passing": True, "detail": "ok"},
            {"name": "cannot be verified from here", "passing": None, "detail": "not verifiable"},
        ],
    )
    ok, msg = state.acknowledge_live_mode(actor=Actor.CLAUDE)
    assert ok, msg
    assert "cannot be verified from here" in msg, "The unverifiable item must still be surfaced."


def test_a_genuinely_failing_item_still_blocks_live_mode(monkeypatch) -> None:
    from aicc import health, state
    from aicc.models import Actor

    monkeypatch.setattr(
        health,
        "live_mode_checklist",
        lambda: [{"name": "demo data cleared", "passing": False, "detail": "synthetic rows present"}],
    )
    ok, msg = state.acknowledge_live_mode(actor=Actor.CLAUDE)
    assert not ok
    assert "demo data cleared" in msg


def test_live_mode_records_who_actually_enabled_it(monkeypatch) -> None:
    """An AI action recorded under Andres's name is a false statement about who did what, and
    the audit log is where that would be least recoverable."""
    from aicc import audit, health, state
    from aicc.models import Actor

    monkeypatch.setattr(health, "live_mode_checklist", lambda: [{"name": "ok", "passing": True, "detail": ""}])
    state.acknowledge_live_mode(actor=Actor.CLAUDE)
    events = [e for e in audit.read_all(50) if e.action == "live_mode_enabled"]
    assert events, "No audit event was written."
    assert events[0].actor == Actor.CLAUDE.value


def _pretend_a_person_is_typing(monkeypatch, *, yes: bool) -> None:
    """A terminal is the one thing a person at a keyboard has and a headless caller does not."""
    import sys

    for stream in ("stdin", "stdout"):
        monkeypatch.setattr(getattr(sys, stream), "isatty", lambda: yes, raising=False)


def test_the_cli_records_who_actually_ran_it(monkeypatch) -> None:
    """The CLI used to assume ANDRES whenever it was not CI. That stopped being true the moment
    the system started driving its own CLI, and every run the system made was then written into
    the audit log under his name.

    The first fix removed the CI case and kept ANDRES as the fallback. That was still wrong, and
    the audit log proved it: selftest runs made by the agent from a container went on being signed
    ANDRES, because a container is not CI either. The fallback itself was the bug.
    """
    from aicc.cli import _actor
    from aicc.models import Actor

    monkeypatch.delenv("AICC_ACTOR", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)

    _pretend_a_person_is_typing(monkeypatch, yes=False)
    assert _actor() == Actor.SYSTEM.value, "A headless caller with no declaration is SYSTEM, not Andres."

    _pretend_a_person_is_typing(monkeypatch, yes=True)
    assert _actor() == Actor.ANDRES.value, "A person at a terminal is genuinely Andres."

    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    assert _actor() == Actor.GITHUB_ACTIONS.value

    monkeypatch.setenv("AICC_ACTOR", "CLAUDE")
    assert _actor() == Actor.CLAUDE.value, "A declared actor wins over the environment guess."

    monkeypatch.setenv("AICC_ACTOR", "not-a-real-actor")
    assert _actor() == Actor.SYSTEM.value, "An unrecognised actor must not silently become ANDRES."


# ------------------------------------------------- the AI worker light must mean something


def test_a_token_alone_does_not_turn_the_ai_worker_light_green(monkeypatch) -> None:
    """The bug this exists for: the probe reported HEALTHY whenever the token was set, while
    `execute` raised in every environment and the pipeline silently fell back to the rule-based
    worker. The dashboard showed a green AI Worker light over a pipeline where no AI had ever
    run, and would have gone on showing it forever."""
    from aicc import health
    from aicc.fulfillment import worker as worker_mod
    from aicc.proof_transport import WorkerState
    from aicc.state import Health

    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "not-a-real-token")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(worker_mod.ClaudeWorker, "_executor", classmethod(lambda cls: None))

    cap = health.probe_ai_worker()
    assert cap.health != Health.HEALTHY.value, "Green with no executor is a config flag, not a probe."
    assert cap.health == Health.DEGRADED.value
    # The state names the situation exactly: the pieces are here and the thing does not work.
    assert str(WorkerState.CONFIGURED_NOT_OPERATIONAL) in cap.detail
    assert "runner" in cap.blocking_reason


# The green half of this pair used to live here, asserting that a token plus an executable was
# enough. That is precisely the condition the 401 exposed: in GitHub Actions both were present and
# every call still failed, so the probe would have shown GREEN over a worker that had never run.
# What turns the light green is now a recorded passing proof, tested under "a green light has to
# be earned" below - `test_a_token_and_a_binary_are_not_evidence_the_worker_works` and
# `test_a_passing_proof_is_what_turns_the_light_green`.


def test_the_selected_worker_is_one_that_can_actually_execute(monkeypatch) -> None:
    """`select_worker` must not hand back a worker that will immediately hand off again."""
    from aicc.fulfillment.worker import ClaudeWorker, RuleBasedWorker, select_worker

    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "not-a-real-token")
    monkeypatch.setattr(ClaudeWorker, "_executor", classmethod(lambda cls: None))
    chosen, why = select_worker()
    assert chosen is RuleBasedWorker
    assert "not on PATH" in why


def test_a_paid_key_alone_never_makes_the_ai_worker_available(monkeypatch) -> None:
    from aicc.fulfillment.worker import ClaudeWorker

    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-not-real")
    ok, why = ClaudeWorker.available()
    assert not ok
    assert "zero-cost" in why


def test_the_ai_worker_never_reports_success_having_produced_nothing(monkeypatch, tmp_path) -> None:
    """Reporting success with no files is the fake autonomy this system exists to avoid."""
    import subprocess

    from aicc.fulfillment.worker import ClaudeWorker
    from aicc.models import Job

    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "not-a-real-token")
    monkeypatch.setattr(ClaudeWorker, "_executor", classmethod(lambda cls: "/usr/bin/claude"))
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, "did nothing", ""))
    job = Job(title="x", requirements=["y"], acceptance_criteria=["z"])
    try:
        ClaudeWorker.execute(job)
    except RuntimeError as exc:
        assert "wrote no files" in str(exc)
    else:
        raise AssertionError("Producing nothing was reported as success.")


def test_the_brief_is_written_where_it_can_be_reviewed_next_to_the_output(monkeypatch) -> None:
    from aicc.fulfillment.worker import ClaudeWorker, workspace_for
    from aicc.models import Job

    job = Job(title="Consolidate CSVs", requirements=["Preserve every row"], acceptance_criteria=["Row counts match"])
    ws = workspace_for(job)
    brief = ClaudeWorker.brief(job, ws, 1)
    text = brief.read_text(encoding="utf-8")
    assert brief.parent == ws
    assert "Preserve every row" in text
    assert "Row counts match" in text
    assert "Do not invent facts" in text
    assert "NEEDS_ANDRES.md" in text


def test_a_worker_failure_never_leaks_a_credential_into_the_logs() -> None:
    """The worker's error text lands in CI logs, a JSON artifact and a step summary - three
    public places on a public repository. The excerpt is worth having; the token in it is not."""
    from aicc.fulfillment.worker import redact_secrets

    samples = [
        "Invalid API key provided: sk-ant-api03-abcdefghijklmnopqrstuvwxyz012345",
        "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.abcdefghijklmnop",
        "oauth token oat_01ABCDEFGHIJKLMNOPQRSTUVWXYZ rejected",
        "credential AAAAAAAAAABBBBBBBBBBCCCCCCCCCCDDDDDDDDDDEEEE was refused",
    ]
    for s in samples:
        out = redact_secrets(s)
        assert "[REDACTED]" in out, s
        for token_ish in ("sk-ant-api03-abcdefghijklmnopqrstuvwxyz012345", "oat_01ABCDEFGHIJKLMNOPQRSTUVWXYZ"):
            assert token_ish not in out


def test_redaction_keeps_the_error_readable() -> None:
    """A redactor that eats the whole message defeats the point of including it."""
    from aicc.fulfillment.worker import redact_secrets

    msg = "Error: the credential was rejected (401). Run `claude setup-token` again."
    assert redact_secrets(msg) == msg


# ------------------------------------------------- audit attribution across all four actors


@pytest.mark.parametrize(
    "env,expected",
    [
        # No declaration and no terminal: some automation, and the log should say exactly that.
        ({}, "SYSTEM"),
        ({"GITHUB_ACTIONS": "true"}, "GITHUB_ACTIONS"),
        ({"AICC_ACTOR": "CLAUDE"}, "CLAUDE"),
        ({"AICC_ACTOR": "claude"}, "CLAUDE"),
        ({"AICC_ACTOR": "SYSTEM"}, "SYSTEM"),
        ({"AICC_ACTOR": "ANDRES"}, "ANDRES"),
        # A declared actor wins over the environment guess: a scheduled run that knows it is the
        # system should not be recorded as CI merely because it happens to be running in CI.
        ({"GITHUB_ACTIONS": "true", "AICC_ACTOR": "CLAUDE"}, "CLAUDE"),
        # Unknown automation becomes SYSTEM. Never, under any circumstance, ANDRES.
        ({"AICC_ACTOR": "some-new-runner"}, "SYSTEM"),
        ({"AICC_ACTOR": "   "}, "SYSTEM"),  # blank is not a declaration, and blank is not a person
    ],
)
def test_every_actor_is_attributed_correctly(monkeypatch, env: dict, expected: str) -> None:
    from aicc.cli import _actor

    monkeypatch.delenv("AICC_ACTOR", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    _pretend_a_person_is_typing(monkeypatch, yes=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    assert _actor() == expected, f"{env} should attribute to {expected}"


def test_unknown_automation_is_never_silently_attributed_to_andres(monkeypatch) -> None:
    """The rule with teeth. A misattributed automated action is a false statement about who did
    what, in the one record that exists to answer that question."""
    from aicc.cli import _actor

    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    _pretend_a_person_is_typing(monkeypatch, yes=False)
    for unknown in ("robot", "cron", "scheduler-v2", "AGENT", "bot[]", "123"):
        monkeypatch.setenv("AICC_ACTOR", unknown)
        assert _actor() == "SYSTEM", f"{unknown!r} became {_actor()}"


def test_an_agent_driving_the_cli_is_not_recorded_as_andres(monkeypatch) -> None:
    """The regression that made this rule concrete.

    Every `selftest` run from the agent's container landed in the audit log as ANDRES, because a
    container is not CI and the fallback said "therefore a person". This is that exact situation:
    no declaration, not CI, no terminal.
    """
    from aicc import audit
    from aicc.cli import _actor

    monkeypatch.delenv("AICC_ACTOR", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    _pretend_a_person_is_typing(monkeypatch, yes=False)

    audit.record("safety_selftest", actor=_actor(), object_type="system", object_id="")
    entry = next(e for e in audit.read_all(10) if e.action == "safety_selftest")
    assert entry.actor == "SYSTEM"
    assert entry.actor != "ANDRES", "An automated selftest was signed with a person's name."


def test_an_actor_reaches_the_audit_log_unchanged(monkeypatch) -> None:
    """End to end: the value _actor() returns is the value written to the log."""
    from aicc import audit
    from aicc.cli import _actor

    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    for declared in ("CLAUDE", "SYSTEM", "ANDRES"):
        monkeypatch.setenv("AICC_ACTOR", declared)
        audit.record("attribution_probe", actor=_actor(), object_type="system", object_id=declared)
    events = [e for e in audit.read_all(50) if e.action == "attribution_probe"]
    assert {e.object_id: e.actor for e in events} == {"CLAUDE": "CLAUDE", "SYSTEM": "SYSTEM", "ANDRES": "ANDRES"}


def _runner_attestation(**over):
    """An attestation as `claude-worker.yml` would upload it, so these tests exercise the real
    transport rather than a shape that only exists in a test."""
    from datetime import UTC, datetime

    from aicc import proof_transport as pt

    payload = {
        "schema_version": pt.SCHEMA_VERSION,
        "workflow_run_id": "34672397024",
        "workflow_run_url": "https://github.com/amercado19/ai-income-control-center/actions/runs/34672397024",
        "workflow_name": pt.EXPECTED_WORKFLOW,
        "repository": pt.EXPECTED_REPOSITORY,
        "commit_sha": "e3ad577c932a1f5d8a08326afa7f554bc26e88ef",
        "branch": "main",
        "runner_environment": "GitHub Actions runner (Linux, x86_64)",
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "execution_attempted": True,
        "execution_succeeded": True,
        "subscription_auth_path": "CLAUDE_CODE_OAUTH_TOKEN",
        "anthropic_api_key_absent": True,
        "paid_fallback_disabled": True,
        "production_path": pt.PRODUCTION_PATH,
        "result_state": "VERIFIED",
        "failures": [],
    }
    payload.update(over)
    return payload


def test_a_token_and_a_binary_are_not_evidence_the_worker_works(monkeypatch) -> None:
    """The gap a real CI run found: credential present, CLI present, and the API answered
    `401 OAuth access token is invalid`. Presence is a config flag; only a model call is a probe."""
    from aicc import health, worker_proof
    from aicc.fulfillment import worker as worker_mod
    from aicc.state import Health

    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "not-a-real-token")
    monkeypatch.setattr(worker_mod.ClaudeWorker, "_executor", classmethod(lambda cls: "/usr/bin/claude"))

    # Never proved.
    assert not worker_proof.PROOF_FILE.exists()
    cap = health.probe_ai_worker()
    assert cap.health != Health.HEALTHY.value, "Green with no proof is a config flag, not a probe."
    assert "NOT YET VERIFIED" in cap.detail
    assert "not evidence" in cap.blocking_reason


def test_a_401_proof_reads_auth_failed_and_says_only_a_person_can_fix_it(monkeypatch) -> None:
    """RED means broken, WHITE means unconfigured, and AUTH FAILED says which kind of broken.

    The three failures need three different responses: a rejected credential needs a person at a
    browser, a spent window needs nobody at all, and anything else is a real defect. Collapsing
    them into one red throws away the only part of the signal that says what to do.
    """
    from aicc import health, worker_proof
    from aicc.proof_transport import WorkerState
    from aicc.state import Health

    worker_proof.ingest_attestation(
        _runner_attestation(
            execution_succeeded=False,
            result_state="FAILED",
            failures=["Failed to authenticate. API Error: 401 OAuth access token is invalid."],
        )
    )
    cap = health.probe_ai_worker()
    assert cap.health == Health.DOWN.value
    assert str(WorkerState.AUTH_FAILED) in cap.detail
    assert "setup-token" in cap.blocking_reason


def test_a_spent_window_reads_capacity_limited_and_asks_nobody_for_anything(monkeypatch) -> None:
    """The consequence of the usage limit is waiting, never a bill - so this must not look like
    the same emergency as a rejected credential."""
    from aicc import health, worker_proof
    from aicc.proof_transport import WorkerState
    from aicc.state import Health

    worker_proof.ingest_attestation(
        _runner_attestation(
            execution_succeeded=False,
            result_state="FAILED",
            failures=["429 rate_limit_error: usage limit reached"],
        )
    )
    cap = health.probe_ai_worker()
    assert cap.health == Health.DEGRADED.value
    assert str(WorkerState.CAPACITY_LIMITED) in cap.detail
    assert "never a bill" in cap.blocking_reason


def test_a_validated_runner_proof_is_what_turns_the_light_green() -> None:
    from aicc import health, worker_proof
    from aicc.state import Health

    accepted, state, _ = worker_proof.ingest_attestation(_runner_attestation())
    assert accepted and state == "HEALTHY"

    cap = health.probe_ai_worker()
    assert cap.health == Health.HEALTHY.value
    assert cap.last_success


def test_a_stale_proof_is_not_a_pass() -> None:
    """A credential that worked two days ago is not evidence that it works now. Tokens expire,
    get revoked and get rotated."""
    from datetime import UTC, datetime, timedelta

    from aicc import health, worker_proof
    from aicc.proof_transport import PROOF_TTL_HOURS, WorkerState
    from aicc.state import Health

    old = datetime.now(UTC) - timedelta(hours=PROOF_TTL_HOURS + 5)
    worker_proof.ingest_attestation(_runner_attestation(generated_at=old.isoformat(timespec="seconds")))

    cap = health.probe_ai_worker()
    assert cap.health == Health.DEGRADED.value
    assert str(WorkerState.STALE_PROOF) in cap.detail


def test_the_dashboard_reports_the_worker_without_holding_a_credential(monkeypatch) -> None:
    """The point of the whole transport.

    `health.yml` is deliberately credential-free, so the machine that renders the dashboard has
    no token and no CLI. It must still report the worker correctly, because the proof came from
    the runner that did have both. An earlier version asked `ClaudeWorker.available()` first and
    so answered a question about the wrong machine.
    """
    from aicc import health, worker_proof
    from aicc.fulfillment import worker as worker_mod
    from aicc.state import Health

    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(worker_mod.ClaudeWorker, "_executor", classmethod(lambda cls: None))

    worker_proof.ingest_attestation(_runner_attestation())
    cap = health.probe_ai_worker()
    assert cap.health == Health.HEALTHY.value, "A credential-free validator must still report a proven worker."

    worker_proof.ingest_attestation(
        _runner_attestation(
            execution_succeeded=False,
            failures=["Failed to authenticate. API Error: 401 OAuth access token is invalid."],
        )
    )
    assert health.probe_ai_worker().health == Health.DOWN.value


def test_a_pass_on_the_wrong_machine_does_not_turn_the_light_green(monkeypatch) -> None:
    """A proof is evidence about the environment it ran in, and only that one.

    Client jobs execute on a GitHub Actions runner. A pass recorded on a laptop says the
    credential on THAT machine works - a different claim, and a misleading one: Andres could run
    `claude setup-token`, prove it locally, and turn the light green while the repository secret
    Actions uses is still the expired one returning 401.
    """
    from aicc import health, worker_proof
    from aicc.state import Health

    accepted, _, _ = worker_proof.ingest_attestation(_runner_attestation(runner_environment="local (Darwin, arm64)"))
    assert not accepted
    assert health.probe_ai_worker().health != Health.HEALTHY.value, "Green over a machine that never runs a client job."


def test_a_workflow_dispatch_by_the_repository_owner_is_andres_and_says_how(monkeypatch) -> None:
    """The remaining half of the attribution problem, found by pressing the button.

    PAUSE was dispatched through the Control workflow and the audit log recorded ANDRES - not
    because anything checked, but because `state.pause()` defaulted to it and the CLI passed no
    actor at all. The same assumption as before, in the one path that changes system state.

    GitHub authenticating the repository owner IS the strongest evidence of Andres available
    remotely, so ANDRES is the right answer here. What was missing is that the log read as though
    a person had been at a terminal. It now records both the who and the evidence.
    """
    from aicc import audit
    from aicc.cli import _actor, _actor_source
    from aicc.models import Actor

    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_REPOSITORY_OWNER", "amercado19")
    monkeypatch.setenv("GITHUB_RUN_ID", "424242")
    monkeypatch.setenv("AICC_ACTOR", "amercado19")

    assert _actor() == Actor.ANDRES.value
    assert "github dispatch by amercado19" in _actor_source()
    assert "424242" in _actor_source()

    audit.record("system_paused", actor=_actor(), source=_actor_source(), object_type="system")
    entry = next(e for e in audit.read_all(10) if e.action == "system_paused")
    assert entry.actor == "ANDRES"
    assert "github dispatch" in entry.source, "ANDRES with no provenance reads as a person at a keyboard."


def test_a_dispatch_by_anyone_other_than_the_owner_is_not_andres(monkeypatch) -> None:
    """A GitHub handle is not a name. Only the owner's own account is evidence of Andres."""
    from aicc.cli import _actor, _actor_source

    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_REPOSITORY_OWNER", "amercado19")
    monkeypatch.setenv("AICC_ACTOR", "some-contributor")

    assert _actor() == "SYSTEM"
    assert "some-contributor" in _actor_source(), "The real handle must still be recorded."


def test_the_control_commands_never_fall_back_to_the_andres_default(monkeypatch) -> None:
    """Read the source. Every state transition must pass an actor explicitly, because the
    parameter's default is ANDRES and a caller that omits it is asserting a person acted."""
    import inspect

    from aicc import cli

    for name in ("cmd_start", "cmd_stop", "cmd_pause", "cmd_resume", "cmd_emergency_stop"):
        src = inspect.getsource(getattr(cli, name))
        assert "_actor()" in src, f"{name} relies on the ANDRES default instead of reading the actor."
        assert "_actor_source()" in src, f"{name} records no provenance for its actor."


# ------------------------------- a resume that resumes nothing, found by pressing the button


def test_resume_restores_what_the_emergency_stop_switched_off(active_system) -> None:
    """Found on the live system, by pressing the button and then reading the state file.

    It reported ACTIVE with all eight automations disabled, and had done for some time. An earlier
    emergency stop had switched them off; `resume` set run_state back to ACTIVE and never touched
    them. Nothing was scheduled to run, and the dashboard said ACTIVE - LIVE.

    A resume that resumes nothing is worse than one that fails, because a failure is visible.
    """
    st = state.SystemState.load()
    for key in st.automations:
        st.automations[key]["enabled"] = True
    st.save()

    state.emergency_stop("test")
    st = state.SystemState.load()
    assert not any(a["enabled"] for a in st.automations.values()), "The stop must switch everything off."

    ok, msg = state.resume()
    assert ok
    st = state.SystemState.load()
    assert st.run_state == "ACTIVE"
    assert all(a["enabled"] for a in st.automations.values()), "ACTIVE with nothing enabled is not active."
    assert "re-enabled" in msg


def test_resume_does_not_switch_on_something_andres_turned_off_himself(active_system) -> None:
    """The stop restores exactly what it disabled, not everything. An automation deliberately
    switched off months ago must survive a stop and a resume still switched off."""
    st = state.SystemState.load()
    keys = list(st.automations)
    for key in keys:
        st.automations[key]["enabled"] = True
    deliberately_off = keys[0]
    st.automations[deliberately_off]["enabled"] = False
    st.save()

    state.emergency_stop("test")
    state.resume()

    st = state.SystemState.load()
    assert not st.automations[deliberately_off]["enabled"], f"{deliberately_off} was switched on by a resume."
    assert all(st.automations[k]["enabled"] for k in keys[1:])


def test_active_with_nothing_enabled_never_reports_running(active_system) -> None:
    """The structural guarantee under the fix above. However the automations came to be off - a
    stop, a manual toggle, a future bug - the status must not claim the system is running."""
    from aicc import health

    st = state.SystemState.load()
    for key in st.automations:
        st.automations[key]["enabled"] = True
    st.save()
    assert health.overall_status()[1] != "YELLOW" or True  # baseline: may be YELLOW for other reasons

    st = state.SystemState.load()
    for key in st.automations:
        st.automations[key]["enabled"] = False
    st.save()

    status, light = health.overall_status()
    assert light != "GREEN", "Green over a system where nothing can run."
    assert "NOTHING ENABLED" in status


# ---------------------------------- no proposal is ever drafted, or approvable, for blocked work


def test_no_proposal_is_drafted_for_work_the_rules_forbid() -> None:
    """The consequence that made the disconnected policy layer concrete.

    A proposal for "Contract to permanent | $120-160/hr" was found sitting in NEEDS ME, awaiting
    approval, one tap from being sent. Approving it would have opened a conversation about a job
    that costs seven years of PSLF-qualifying payments.

    "Approval is a separate human step" is only a safeguard when what reaches the human is worth
    approving. A drafted proposal is a trap with Andres's own approval button on it.
    """
    from aicc.connectors.base import make_opportunity
    from aicc.proposals import PolicyBlockedError, generate

    opp = make_opportunity(
        source="hackernews",
        title="Noricum - Senior Backend Engineer",
        description=NORICUM,
        skills=["python"],
    )
    with pytest.raises(PolicyBlockedError) as exc:
        generate(opp)
    assert "PSLF" in str(exc.value)


def test_an_already_drafted_proposal_for_blocked_work_cannot_be_approved(active_system, monkeypatch, capsys) -> None:
    """A gate on the front door leaves whatever is already inside.

    Drafting now refuses blocked work, but proposals drafted before that gate existed are already
    in the approval queue - one of them for a contract-to-permanent role. The rules have to hold
    for those too, so approval re-checks rather than trusting that drafting did.
    """
    import argparse

    from aicc.cli import cmd_approve
    from aicc.connectors.base import make_opportunity
    from aicc.models import Proposal

    opp = make_opportunity(source="hackernews", title="Noricum - Senior Backend Engineer", description=NORICUM, skills=["python"])
    storage.opportunities.put(opp)
    prop = Proposal(opportunity_id=opp.id, source=opp.source, status="AWAITING_APPROVAL", problem_statement="x", body="y")
    storage.proposals.put(prop)

    code = cmd_approve(argparse.Namespace(proposal_id=prop.id, connects=0))
    out = capsys.readouterr().out

    assert code != 0, "A blocked proposal was approved."
    assert "REFUSED" in out
    assert "PSLF" in out
    assert storage.proposals.get(prop.id).status == "AWAITING_APPROVAL", "Status must not advance."


def test_a_blocked_proposal_card_offers_no_approve_button() -> None:
    """The card is the single screen that answers 'what do I have to do?'. A button wired to fail
    is still a button someone presses, and the card said nothing about why they should not."""
    from aicc.connectors.base import make_opportunity
    from aicc.dashboard.build import _attention
    from aicc.models import Proposal

    opp = make_opportunity(source="hackernews", title="Noricum - Senior Backend Engineer", description=NORICUM, skills=["python"])
    storage.opportunities.put(opp)
    prop = Proposal(opportunity_id=opp.id, source=opp.source, status="AWAITING_APPROVAL", problem_statement="x", body="y")

    card = next(c for c in _attention([], [prop]) if c["title"] == opp.title)
    assert card["policy_gate"], "The card does not say the listing is blocked."
    assert card["severity"] == "stop"
    assert not any(a["label"] == "APPROVE" for a in card["actions"])


# --------------------------------------------------------------- one list of checks, not two


def _ci_test_job_steps() -> list[str]:
    """The named steps of ci.yml's `test` job, in order.

    Parsed rather than hardcoded, so this cannot pass by agreeing with a stale copy of CI.
    """
    import re
    from pathlib import Path

    ci = (Path(__file__).resolve().parents[1] / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    return re.findall(r"^      - name: (.+)$", ci, flags=re.MULTILINE)


def test_ci_and_verify_run_the_same_checks() -> None:
    """Nine consecutive red CI runs, and not one of them was a bug in the code.

    The local checks were assembled by hand - pytest, ruff, mypy, the self-test - and the secret
    scan was never in the hand-assembled list. So the suite was green locally and red remotely
    for nine commits, over a test fixture shaped like a credential. The lesson is not "remember
    the secret scan": it is that two lists of checks drift, silently, and the drift shows up as
    a red badge nobody trusts.

    `scripts/verify.sh` is the one local entrypoint. This test is what keeps it honest: add a
    step to CI without adding it there and the suite fails here, naming the step.
    """
    from pathlib import Path

    verify = (Path(__file__).resolve().parents[1] / "scripts/verify.sh").read_text(encoding="utf-8")

    # Installing dependencies is CI provisioning a fresh runner; a local shell already has them.
    provisioning = {"Install dev tooling"}

    missing = [s for s in _ci_test_job_steps() if s not in provisioning and s not in verify]
    assert not missing, (
        "ci.yml runs checks that scripts/verify.sh does not, so a local run can be green while "
        f"CI is red: {missing}. Add them to scripts/verify.sh."
    )


def test_verify_is_runnable_and_fails_loudly() -> None:
    """A verify script that exits 0 on a failed check is worse than no verify script: it converts
    "I did not check" into "I checked and it was fine".

    Runnability is asserted as "has a shebang", not as "has the executable bit". This repository
    is pushed through GitHub's web upload form, which does not carry file modes, so the bit
    cannot survive the transport - and a test asserting a property the transport cannot deliver
    is a permanently red check, which is the exact failure this section of the file exists to
    stop. `bash scripts/verify.sh` works either way.
    """
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "scripts/verify.sh"
    body = path.read_text(encoding="utf-8")
    assert body.startswith("#!"), "scripts/verify.sh has no shebang"
    assert "exit 1" in body, "the script never fails"
    # `set -e` would abandon the run at the first failure and report only that one. Collecting
    # failures is deliberate: the point of a pre-push check is to learn everything that is wrong.
    assert "set -e\n" not in body and "set -eu" not in body, "aborting early hides later failures"
    assert "failed+=" in body, "failures are not collected"


def test_verify_does_not_mutate_the_repositorys_data() -> None:
    """The script's own comment claims this, so the claim is checked.

    `selftest` and `demo` both write operational state, and CI commits that state deliberately.
    A local pre-push run must not: it would leave the working tree dirty with events that did
    not happen in production, and the next commit would carry them. Asserted structurally
    because the alternative - restoring data/ afterwards - would destroy real local changes.
    """
    from pathlib import Path

    body = (Path(__file__).resolve().parents[1] / "scripts/verify.sh").read_text(encoding="utf-8")

    assert "AICC_DATA_DIR" in body, "the script does not redirect the data directory"
    assert "AICC_WORKSPACE_ROOT" in body, "the script does not redirect client workspaces"
    assert "mktemp -d" in body, "the redirect does not point somewhere disposable"
    assert "trap " in body and "rm -rf" in body, "the temp directory is never cleaned up"
    # The steps must see real data, not an empty store: a dashboard built from nothing verifies
    # nothing, and that failure mode is invisible because the build still succeeds.
    assert "cp -R data" in body, "the temp store is not a copy of the real one"
    for destructive in ("git checkout data", "git restore", "git reset"):
        assert destructive not in body, f"verify.sh must never run `{destructive}` on a person's work"
