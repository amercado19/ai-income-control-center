"""The standing rules, tested in both directions.

A safety rule that only ever passes is indistinguishable from a rule that does nothing. So each
gate is tested twice: once that it fires on the thing it exists to catch, and once that it stays
quiet on the thing it would be embarrassing to block. The second half matters more than it
looks - an over-firing PSLF check would reject every freelance contract in the feed, and the
system would look safe while quietly finding no work at all.
"""

from __future__ import annotations

import pytest

from aicc import policy
from aicc.models import Opportunity
from aicc.policy import AIUse, Capability, Gate


def _opp(title: str = "", description: str = "", **kw) -> Opportunity:
    return Opportunity(title=title, description=description, **kw)


# ---------------------------------------------------------------- PSLF / full-time employment


@pytest.mark.parametrize(
    "text",
    [
        "Full-time Senior Data Engineer, salaried, with health insurance and 401(k).",
        "Permanent position. W-2 employee. Paid time off and employee benefits.",
        "We are hiring a full time Python developer to join our team.",
    ],
)
def test_for_profit_full_time_employment_is_a_hard_reject(text: str) -> None:
    result = policy.full_time_employment_check(_opp("Engineer", text))
    assert result.triggered, text
    assert result.gate == Gate.PSLF.value
    assert "PSLF" in result.detail


@pytest.mark.parametrize(
    "text",
    [
        "Freelance contract to build a CSV pipeline. Fixed price, one-time project.",
        "Independent contractor, project-based work, roughly 20 hours total.",
        "Short-term consulting engagement to clean up a spreadsheet. Part-time.",
        "1099 contractor for a discrete data migration. Per project, paid on delivery.",
    ],
)
def test_the_word_contract_alone_does_not_reject_legitimate_freelance_work(text: str) -> None:
    """The amendment is explicit: do not reject side projects merely for using the word 'contract'.

    A contract is the normal legal form of freelance work. An earlier version of this system
    both over-rejected on that word and, separately, read '1099 contractor' as proof a forty-hour
    salaried role was not full-time. Tax status and hours are different axes and both directions
    of that confusion are expensive.
    """
    result = policy.full_time_employment_check(_opp("Project", text))
    assert not result.triggered, f"{text!r} was rejected: {result.detail}"


def test_full_time_at_a_qualifying_employer_is_not_a_pslf_conflict() -> None:
    """PSLF is about the employer, not the hours. A 501(c)(3) full-time role still qualifies."""
    result = policy.full_time_employment_check(_opp("Analyst", "Full-time analyst at a 501(c)(3) nonprofit. Salaried with benefits."))
    assert not result.triggered
    assert "PSLF" in result.detail


def test_engagement_type_full_time_is_enough_on_its_own() -> None:
    result = policy.full_time_employment_check(_opp("Role", "Join us.", engagement_type="FULL_TIME"))
    assert result.triggered


# ---------------------------------------------------------------- personal information


@pytest.mark.parametrize(
    "text",
    [
        "Please provide your Social Security number before we can onboard you.",
        "Upload a photo of your driver's license and a selfie for identity verification.",
        "We'll need your home address and banking information for payment setup.",
        "Background check required; provide date of birth.",
    ],
)
def test_personal_information_requests_stop_the_system(text: str) -> None:
    result = policy.personal_information_check(text)
    assert result.triggered, text
    assert result.gate == Gate.PERSONAL_INFORMATION.value
    assert result.matches


def test_ordinary_project_language_is_not_a_personal_information_request() -> None:
    clean = "Clean up a customer address column in a spreadsheet and normalise the date of the transaction field."
    # 'address' and 'date' appear, but neither 'home address' nor 'date of birth' does.
    assert not policy.personal_information_check(clean).triggered


def test_an_unknown_fact_about_andres_is_needs_andres_not_a_guess() -> None:
    result = policy.needs_andres_for_unknown_fact("years of Airflow experience")
    assert result.triggered
    assert result.gate == Gate.NEEDS_ANDRES.value
    assert "Guessing" in result.detail


# ---------------------------------------------------------------- security controls


@pytest.mark.parametrize(
    "text",
    [
        "Complete the CAPTCHA to continue.",
        "Enter the 2FA verification code.",
        "Sign with DocuSign.",
        "Liveness check required.",
        "Approve with your passkey.",
    ],
)
def test_security_controls_are_brought_to_andres_never_solved(text: str) -> None:
    result = policy.security_control_check(text)
    assert result.triggered, text
    assert result.gate == Gate.SECURITY.value
    assert "never bypassed" in result.detail


def test_a_normal_login_mention_is_not_a_security_challenge() -> None:
    assert not policy.security_control_check("Log in to our Jira to see the ticket backlog.").triggered


# ---------------------------------------------------------------- commitments


@pytest.mark.parametrize("action", policy.COMMITMENT_ACTIONS)
def test_every_commitment_action_requires_andres(action: str) -> None:
    result = policy.commitment_check(action)
    assert result.triggered
    assert result.gate == Gate.COMMITMENT.value


@pytest.mark.parametrize("action", policy.AUTONOMOUS_ACTIONS)
def test_back_office_work_runs_without_interrupting_him(action: str) -> None:
    assert not policy.commitment_check(action).triggered


def test_an_unclassified_action_fails_closed() -> None:
    """The failure directions are not symmetric: a false stop costs a message, a false start
    costs a signed agreement."""
    result = policy.commitment_check("wire_money_to_vendor")
    assert result.triggered
    assert "not on the autonomous list" in result.detail


# ---------------------------------------------------------------- marketplace matrix


def test_every_marketplace_entry_cites_its_basis() -> None:
    """A capability without a citation is an assumption about terms of service."""
    for name, entry in policy.MARKETPLACES.items():
        assert entry.basis, name
        assert len(entry.basis) > 30, f"{name}: {entry.basis!r}"


def test_no_source_permits_automated_submission_today() -> None:
    """True as of the last review of each platform's rules. If this ever fails, it is because
    someone changed a capability - which must be a deliberate, cited decision, not a default."""
    assert not any(policy.submission_permitted(s) for s in policy.MARKETPLACES)


def test_upwork_discovery_is_assisted_because_scraping_is_prohibited() -> None:
    assert policy.capability_for("upwork", "discovery") == Capability.ASSISTED.value
    assert "prohibit" in policy.MARKETPLACES["upwork"].basis.lower()


def test_reddit_is_prohibited_rather_than_merely_unused() -> None:
    assert policy.capability_for("reddit", "discovery") == Capability.PROHIBITED.value


def test_an_unknown_source_is_human_required_not_automatic() -> None:
    assert policy.capability_for("some-new-board", "discovery") == Capability.HUMAN_REQUIRED.value


# ---------------------------------------------------------------- AI use


def test_an_explicit_ai_prohibition_blocks_the_work() -> None:
    cls, why = policy.classify_ai_use(_opp("Writing", "No AI. Human written only, must pass AI detection."))
    assert cls == AIUse.PROHIBITED.value
    assert policy.ai_use_blocks_work(cls)
    assert why


def test_a_client_describing_their_own_process_is_not_setting_our_policy() -> None:
    """The bug this test exists for: 'I do not use AI to screen your applications' once rejected
    a perfectly good listing, because the matcher could not tell whose process was being described."""
    cls, why = policy.classify_ai_use(_opp("Analyst", "We do not use AI to screen your applications - a person reads every one."))
    assert cls != AIUse.PROHIBITED.value, why
    assert cls == AIUse.UNCLEAR.value


def test_a_disclosure_requirement_is_allowed_with_disclosure() -> None:
    cls, _ = policy.classify_ai_use(_opp("Report", "Please disclose AI use in your proposal."))
    assert cls == AIUse.ALLOWED_WITH_DISCLOSURE.value
    assert not policy.ai_use_blocks_work(cls)


def test_silence_is_unclear_not_permission() -> None:
    cls, why = policy.classify_ai_use(_opp("Script", "Write a Python script to merge CSVs."))
    assert cls == AIUse.UNCLEAR.value
    assert "Silence is not permission" in why


def test_a_posting_that_names_ai_tooling_is_ai_allowed() -> None:
    cls, _ = policy.classify_ai_use(_opp("Build", "We use Claude and Copilot internally; AI-assisted work is fine."))
    assert cls == AIUse.ALLOWED.value


# ---------------------------------------------------------------- Claude as scarce resource


@pytest.mark.parametrize("task", sorted(policy.DETERMINISTIC_ONLY))
def test_free_deterministic_work_never_reaches_a_model(task: str) -> None:
    ok, why = policy.may_use_claude(task)
    assert not ok
    assert "capacity" in why


def test_semantic_work_may_use_claude() -> None:
    ok, _ = policy.may_use_claude("read_a_client_brief")
    assert ok


def test_the_screening_funnel_puts_free_filters_before_the_model() -> None:
    order = policy.SCREENING_ORDER
    assert order.index("deterministic_filter") < order.index("claude_semantic")
    assert order.index("compliance_filter") < order.index("claude_semantic")
    assert order.index("capability_filter") < order.index("claude_semantic")
    assert order.index("claude_semantic") < order.index("proposal")


# ---------------------------------------------------------------- identity


def test_unbacked_claims_about_andres_are_reported() -> None:
    problems = policy.identity_claim_problems(
        "With 10 years of experience and as a certified AWS expert, I have worked at Fortune 500 clients.",
        verified={},
    )
    assert "years of experience" in problems
    assert "certified" in problems
    assert "worked at" in problems


def test_language_that_appears_in_verified_material_is_not_flagged() -> None:
    problems = policy.identity_claim_problems(
        "Expert in Python data pipelines.",
        verified={"pipelines": "Expert in Python data pipelines, demonstrated in the NFL repository."},
    )
    assert problems == []


# ---------------------------------------------------------------- the twelve


def test_there_are_exactly_twelve_named_invariants() -> None:
    assert len(policy.INVARIANTS) == 12
    assert len({i.key for i in policy.INVARIANTS}) == 12


def test_every_invariant_says_what_breaking_it_would_cost() -> None:
    """The consequence, not the mechanism. Someone reads this deciding whether to leave it running."""
    for inv in policy.INVARIANTS:
        assert inv.statement and inv.consequence
        assert len(inv.consequence) > 35, f"{inv.key}: {inv.consequence!r} is too thin to act on"


# ---------------------------------------------------------------- the combined verdict


def test_a_full_time_role_is_disallowed_by_the_combined_evaluation() -> None:
    v = policy.evaluate(_opp("Engineer", "Full-time salaried role with 401(k) and health insurance."))
    assert not v.allowed
    assert v.blocking_gate == Gate.PSLF.value


def test_a_personal_information_request_gates_a_step_without_killing_the_job() -> None:
    """The work can still be worth doing; one step inside it belongs to Andres."""
    v = policy.evaluate(_opp("CSV cleanup", "Clean 20 CSVs. We will need a W-9 before payment."))
    assert v.allowed, v.gates
    assert any(g["gate"] == Gate.PERSONAL_INFORMATION.value for g in v.gates)


def test_the_verdict_carries_the_capability_matrix_for_that_source() -> None:
    v = policy.evaluate(_opp("Role", "Build a dashboard.", source="upwork"))
    assert v.discovery_capability == Capability.ASSISTED.value
    assert v.submission_capability == Capability.HUMAN_REQUIRED.value


# ------------------------------------- the shape that slipped through both filters at once


def test_a_contract_that_converts_to_permanent_is_a_pslf_conflict() -> None:
    """The listing that was live on the dashboard when this was written.

    "Senior Backend Engineer, Payments | REMOTE | Contract to permanent | $120-160/hr" passed the
    screen with no gate at all and was ranked NOW - first out of eighty-five listings, the single
    most attractive thing on the board. It matched no employment word, because "contract to
    permanent" contains neither "permanent position" nor "permanent role", so the check returned
    clear on its first line and nothing downstream had another chance to catch it.

    A contract-to-permanent posting is an employment offer with a probation period on the front.
    Seven years of qualifying payments do not survive taking one by default.
    """
    from aicc.models import Opportunity

    opp = Opportunity(
        title="Senior Backend Engineer, Payments, Ledger & Provable Fairness",
        description=(
            "Noricum | Senior Backend Engineer, Payments, Ledger & Provable Fairness | "
            "REMOTE (2h overlap with US Pacific) | Contract to permanent | $120-160/hr | "
            "Start by 14 Sep. I'm the founder of Noricum where we build the money infrastructure, "
            "ledger, engine and payment rails behind a product."
        ),
    )
    verdict = policy.evaluate(opp)
    assert not verdict.allowed
    assert verdict.gates[0]["gate"] == policy.Gate.PSLF.value
    assert "contract to permanent" in verdict.gates[0]["matches"]


@pytest.mark.parametrize(
    "description",
    [
        "Backend engineer. Contract-to-hire, 6 month contract then permanent.",
        "Data analyst, temp to perm after 90 days.",
        "Start as a contractor with a clear path to full-time for the right person.",
        "Senior engineer, C2H, remote.",
        "6 month engagement with a view to permanent.",
        "Contractor role that converts to full-time after the trial period.",
    ],
)
def test_every_route_into_employment_is_caught_however_it_is_worded(description: str) -> None:
    from aicc.models import Opportunity

    verdict = policy.evaluate(Opportunity(title="Engineer", description=description))
    assert not verdict.allowed, f"{description!r} passed the PSLF screen"
    assert verdict.gates[0]["gate"] == policy.Gate.PSLF.value


@pytest.mark.parametrize(
    "description",
    [
        "Contract Python developer needed for a fixed-price data cleanup contract. 20 hours.",
        "Seeking an independent contractor on a project basis. Contract signed per project.",
        "Hourly contract, freelance, roughly 10 hours of work total.",
        "Short-term contract to build one dashboard. One-time project.",
        "Consulting engagement, contract attached, two weeks of work.",
    ],
)
def test_ordinary_contract_work_is_not_rejected_for_using_the_word(description: str) -> None:
    """The amendment is explicit: 'Do not reject legitimate side projects merely because the
    listing uses the word contract.' A filter that fired on 'contract' would reject most of the
    freelance market, which is the entire business."""
    from aicc.models import Opportunity

    verdict = policy.evaluate(Opportunity(title="Developer", description=description))
    assert verdict.allowed, f"{description!r} was rejected for saying 'contract'"


def test_a_conversion_role_at_a_qualifying_employer_is_not_a_conflict() -> None:
    """PSLF is about who the employer is, not about the shape of the contract. Converting to
    permanent at a 501(c)(3) keeps the clock running."""
    from aicc.models import Opportunity

    verdict = policy.evaluate(
        Opportunity(title="Engineer", description="Contract to permanent role at our nonprofit, a 501c3 organization.")
    )
    assert verdict.allowed
