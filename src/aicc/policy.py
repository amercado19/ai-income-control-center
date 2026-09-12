"""The standing rules, as code rather than as prose in a document nobody re-reads.

Every constraint in here already existed somewhere - in `scoring`, in `proposals`, in
`SECURITY.md`, in a decision record. The reason to gather them into one module is that a rule
scattered across five files is a rule that quietly stops holding the first time one of those
files is refactored by someone who does not know it was load-bearing. Here each one is named,
has a single place to fail, and has a check in ``INVARIANTS`` that fails the build if it stops
being true.

Four ideas run through the whole file:

* **Claude is the back office, never the identity.** It may find, score, research, calculate,
  draft, build, QA and record. It may not *be* Andres. Anything that asserts a fact about him
  must trace to something already verified in this repository; anything else is ``NEEDS ANDRES``
  rather than a plausible guess. A plausible guess about a person's qualifications is a lie with
  good manners.
* **Some gates are not tradeable.** Full-time for-profit employment ends PSLF eligibility that
  is worth roughly seven more years of payments to him. Identity documents, security challenges
  and legal commitments are his to give, not the system's. No profit calculation, no START
  BUSINESS, no scheduler outranks these - which is why they are hard rejects and hard stops
  rather than heavy penalties.
* **The platform's rules beat our preferences.** Where a marketplace forbids automation, the
  system's capability is PROHIBITED regardless of how convenient automation would be.
* **A skipped check is not a passing check.** Every function here returns evidence, not just a
  verdict, so a `NEEDS ANDRES` can be read and argued with.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from .models import AutomationPolicy, Opportunity, RiskFlag

# ---------------------------------------------------------------------------
# Gate labels - the exact strings the dashboard and the CLI render
# ---------------------------------------------------------------------------


class Gate(StrEnum):
    """What the system says when it stops. These strings are load-bearing: the dashboard
    matches on them, so changing one changes what Andres sees."""

    NEEDS_ANDRES = "NEEDS ANDRES"
    PERSONAL_INFORMATION = "NEEDS ANDRES — PERSONAL INFORMATION"
    COMMITMENT = "NEEDS ANDRES — COMMITMENT"
    SECURITY = "NEEDS ANDRES — SECURITY CONTROL"
    PSLF = "HARD REJECT — PSLF CONFLICT"


@dataclass
class GateResult:
    """A stop, with the reason attached. ``triggered`` false means genuinely clear, not unchecked."""

    triggered: bool
    gate: str = ""
    matches: list[str] = field(default_factory=list)
    detail: str = ""

    def __bool__(self) -> bool:
        return self.triggered

    def to_dict(self) -> dict[str, Any]:
        return {"triggered": self.triggered, "gate": self.gate, "matches": self.matches, "detail": self.detail}


_CLEAR = GateResult(False)


def _phrases(*terms: str) -> re.Pattern[str]:
    """Word-boundary alternation. Built once per group so a match is cheap and predictable."""
    return re.compile(r"(?<![a-z0-9])(" + "|".join(re.escape(t) for t in terms) + r")(?![a-z0-9])", re.I)


# ---------------------------------------------------------------------------
# 1. Identity protection
# ---------------------------------------------------------------------------

#: Facts about Andres that only Andres can establish. The system may *repeat* one of these when
#: it is already verified in this repository; it may never *originate* one. The list is the
#: amendment's list, unedited, because narrowing it later should be a visible decision.
PROTECTED_ABOUT_ANDRES = (
    "experience",
    "employment history",
    "education",
    "degrees",
    "certifications",
    "skills",
    "portfolio",
    "clients",
    "references",
    "accomplishments",
    "location",
    "availability",
    "identity",
    "income",
    "qualifications",
)

#: Language that turns an estimate into an assertion about him. Proposal bodies are checked for
#: these against the verified profile; anything unbacked raises rather than being softened,
#: because a softened fabrication is still a fabrication and is harder to spot.
_EMBELLISHMENT = _phrases(
    "years of experience",
    "decade of experience",
    "certified",
    "licensed",
    "accredited",
    "degree in",
    "master's in",
    "phd in",
    "worked at",
    "worked with",
    "clients include",
    "trusted by",
    "award-winning",
    "industry-leading",
    "expert in",
    "specialist in",
    "fortune 500",
)


def identity_claim_problems(text: str, *, verified: dict[str, str] | None = None) -> list[str]:
    """Language in generated text that asserts something about Andres it cannot back.

    ``verified`` is the demonstrated-experience map from ``proposals.PROFILE``; a phrase that
    appears verbatim in a verified entry is his own material being quoted back and is fine.
    Everything else is reported so the caller can raise. The caller raises rather than strips:
    silently removing a fabricated sentence leaves a proposal that reads as if it were written
    that way, and nobody ever learns the generator tried.
    """
    backing = " ".join((verified or {}).values()).lower()
    found = []
    for m in _EMBELLISHMENT.finditer(text or ""):
        phrase = m.group(0)
        if phrase.lower() in backing:
            continue
        found.append(phrase)
    return sorted(set(found))


def needs_andres_for_unknown_fact(field_name: str) -> GateResult:
    """The answer to 'the proposal wants X about Andres and we do not have X'.

    There is exactly one correct behaviour and it is not to infer a reasonable value.
    """
    return GateResult(
        True,
        Gate.NEEDS_ANDRES.value,
        [field_name],
        f"{field_name} is not verified anywhere in this repository. Guessing it would put an "
        f"unverified statement about Andres in front of a client under his name.",
    )


# ---------------------------------------------------------------------------
# 2. PSLF - full-time for-profit employment
# ---------------------------------------------------------------------------

#: Roughly seven years of qualifying payments remain. A single for-profit full-time role resets
#: that, and no freelance engagement this system can find is worth the forgiveness balance. This
#: is the one rejection in the codebase that is not a score.
PSLF_REASON = (
    "Full-time employment at a for-profit employer ends PSLF-qualifying employment. Andres needs "
    "roughly seven more years of qualifying payments, and no contract this system can find is "
    "worth restarting that clock. Freelance projects, independent-contractor projects, side work "
    "and appropriate part-time consulting do not touch PSLF and are scored normally."
)

#: Words that indicate the listing is offering a job, not a project. Deliberately does NOT
#: include "contract": a contract is the normal legal form of freelance work, and an earlier
#: version of this system both over-rejected on that word and, worse, read "1099 contractor" as
#: proof a full-time role was not full-time. Tax status and hours are different axes.
_EMPLOYMENT = _phrases(
    "full-time",
    "full time",
    "fulltime",
    "permanent position",
    "permanent role",
    "salaried",
    "w-2",
    "w2 employee",
    "employee benefits",
    "401k",
    "401(k)",
    "health insurance",
    "pto",
    "paid time off",
)

#: Words that indicate discrete, bounded work. Their presence is what keeps "contract" safe.
_PROJECT_SHAPED = _phrases(
    "freelance",
    "project-based",
    "project based",
    "one-time",
    "one off",
    "fixed-price",
    "fixed price",
    "per project",
    "short-term",
    "short term",
    "side project",
    "part-time",
    "part time",
    "hourly contract",
    "consulting engagement",
)

#: Postings that are a route INTO employment, whatever they call the first few months.
#:
#: A separate axis from ``_EMPLOYMENT``, and separate on purpose. The employment words are
#: ambiguous enough that project language is allowed to clear them; these are not, and project
#: language must never clear them - a contract-to-hire posting is by definition full of contract
#: language, which is what makes it the one shape that would otherwise slip through both filters.
#:
#: The gap this closes was live on the dashboard: "Senior Backend Engineer, Payments | Contract to
#: permanent | $120-160/hr" passed the screen with no gate at all and was ranked NOW, first out of
#: eighty-five listings. It matched no employment word - "contract to permanent" contains neither
#: "permanent position" nor "permanent role" - so the check returned clear on its first line.
#:
#: Every phrase here names a conversion. Plain "contract", "contractor" and "contract work" are
#: deliberately absent: the amendment is explicit that a legitimate side project must not be
#: rejected merely for using the word.
_EMPLOYMENT_CONVERSION = _phrases(
    "contract to permanent",
    "contract-to-permanent",
    "contract to perm",
    "contract-to-perm",
    "contract to hire",
    "contract-to-hire",
    "temp to perm",
    "temp-to-perm",
    "temp to hire",
    "contract to full-time",
    "contract to full time",
    "c2h",
    "with a view to permanent",
    "view to permanent",
    "leading to a permanent role",
    "leading to a permanent position",
    "path to full-time",
    "path to full time",
    "convert to full-time",
    "convert to full time",
    "converts to full-time",
    "conversion to full-time",
    "with intent to hire",
    "intent to hire",
    "trial to hire",
    "right to hire",
)

#: Employers whose full-time roles would still qualify for PSLF, so the hard reject does not apply.
_QUALIFYING_EMPLOYER = _phrases(
    "501(c)(3)",
    "501c3",
    "nonprofit",
    "non-profit",
    "not-for-profit",
    "government",
    "federal agency",
    "state agency",
    "public school",
    "school district",
    "public university",
    "municipal",
    "county government",
    "city government",
)


def full_time_employment_check(opp: Opportunity) -> GateResult:
    """Is this a for-profit full-time job rather than freelance work?

    Three axes, read separately, because conflating any two of them is how the system once
    ranked a full-time Senior Data Engineer role first out of seventy-five listings:

    1. Does the posting describe employment hours or employment benefits?
    2. Does it describe discrete project work?
    3. Is the employer one whose full-time roles would still qualify for PSLF?

    Plus a fourth that overrides the second: does the posting describe a conversion INTO
    employment? "Contract to permanent" is an employment offer with a probation period on the
    front, and it is written almost entirely in contract vocabulary - so project language, which
    legitimately clears an ambiguous "full-time", must not be allowed to clear this.
    """
    text = f"{opp.title} {opp.description} {opp.engagement_type}"
    employment = sorted({m.group(0).lower() for m in _EMPLOYMENT.finditer(text)})
    project = sorted({m.group(0).lower() for m in _PROJECT_SHAPED.finditer(text)})
    qualifying = sorted({m.group(0).lower() for m in _QUALIFYING_EMPLOYER.finditer(text)})
    conversion = sorted({m.group(0).lower() for m in _EMPLOYMENT_CONVERSION.finditer(text)})

    declared_full_time = opp.engagement_type.upper().replace("-", "_") == "FULL_TIME"

    if conversion and not qualifying:
        return GateResult(
            True,
            Gate.PSLF.value,
            conversion,
            "The posting is a route into permanent employment, whatever the first few months are "
            "called. Accepting it would put the remaining PSLF years at a qualifying employer at "
            "risk, which is a decision only Andres can make - and not one to make by taking a "
            "contract that converts by default. " + PSLF_REASON,
        )

    if not (employment or declared_full_time):
        return _CLEAR
    if qualifying:
        return GateResult(
            False,
            "",
            qualifying,
            "Full-time, but at an employer whose service would itself qualify for PSLF. Not a "
            "conflict - though taking it is a career decision, not a scheduling one.",
        )
    # Project language alone does not clear explicit full-time hours: a posting can say
    # "contract" and still be a forty-hour salaried seat. It clears only the soft signals.
    if project and not declared_full_time and not _hard_employment_signal(employment):
        return GateResult(
            False,
            "",
            project,
            "Reads as project-shaped work. The employment words present are ambiguous and the listing describes discrete deliverables.",
        )
    return GateResult(True, Gate.PSLF.value, employment or ["engagement_type=FULL_TIME"], PSLF_REASON)


def _hard_employment_signal(matches: list[str]) -> bool:
    """Benefits and salaried hours are employment; 'full-time' alone can be loose usage."""
    hard = {
        "salaried",
        "w-2",
        "w2 employee",
        "employee benefits",
        "401k",
        "401(k)",
        "health insurance",
        "pto",
        "paid time off",
        "permanent position",
        "permanent role",
    }
    return any(m in hard for m in matches)


# ---------------------------------------------------------------------------
# 3. Personal information
# ---------------------------------------------------------------------------

PERSONAL_INFORMATION_FIELDS = (
    "social security number",
    "ssn",
    "government id",
    "government-issued id",
    "driver's license",
    "drivers license",
    "passport",
    "date of birth",
    "birthdate",
    "home address",
    "residential address",
    "bank account",
    "routing number",
    "banking information",
    "tax information",
    "w-9",
    "w9",
    "1099 form",
    "background check",
    "employment verification",
    "personal references",
    "identity document",
    "selfie",
    "video verification",
    "biometric",
    "fingerprint",
)

_PERSONAL = _phrases(*PERSONAL_INFORMATION_FIELDS)


def personal_information_check(text: str) -> GateResult:
    """Anything asking for a fact that identifies him rather than describes his work.

    The system never fills one of these fields, never infers a value, and never goes looking
    for one it was not given. The correct output is a labelled stop that tells him which field
    is waiting and where.
    """
    matches = sorted({m.group(0).lower() for m in _PERSONAL.finditer(text or "")})
    if not matches:
        return _CLEAR
    return GateResult(
        True,
        Gate.PERSONAL_INFORMATION.value,
        matches,
        "This asks for personal information that identifies Andres. The system does not invent, "
        "infer, retrieve or submit these. He fills the field himself, or decides not to.",
    )


# ---------------------------------------------------------------------------
# 4. Human security controls
# ---------------------------------------------------------------------------

HUMAN_SECURITY_CONTROLS = (
    "captcha",
    "recaptcha",
    "hcaptcha",
    "two-factor",
    "2fa",
    "two factor authentication",
    "one-time code",
    "verification code",
    "passkey",
    "identity verification",
    "id verification",
    "security challenge",
    "selfie verification",
    "video verification",
    "liveness check",
    "biometric verification",
    "account recovery",
    "electronic signature",
    "e-signature",
    "docusign",
    "legal attestation",
    "notarize",
)

_SECURITY = _phrases(*HUMAN_SECURITY_CONTROLS)


def security_control_check(text: str) -> GateResult:
    """A control that exists to prove a person is present.

    The system brings the real screen to Andres and waits. It does not solve it, does not route
    around it, and does not treat it as an obstacle - the control is doing exactly its job, and
    a system that defeats it is a system that has decided its convenience outranks the platform's
    consent.
    """
    matches = sorted({m.group(0).lower() for m in _SECURITY.finditer(text or "")})
    if not matches:
        return _CLEAR
    return GateResult(
        True,
        Gate.SECURITY.value,
        matches,
        "A human security control. The legitimate screen goes to Andres; the control is never bypassed, automated, or worked around.",
    )


# ---------------------------------------------------------------------------
# 5. Contract and legal commitment
# ---------------------------------------------------------------------------

COMMITMENT_ACTIONS = (
    "accept_employment",
    "accept_contract",
    "sign_agreement",
    "agree_to_terms",
    "accept_nda",
    "authorize_background_check",
    "change_tax_status",
    "create_financial_account",
    "authorize_payment",
    "issue_refund",
    "submit_proposal",
    "send_client_message",
    "publish_listing",
    "spend_connects",
)

#: Actions the system performs on its own. Everything not on this list and not a pure read is
#: treated as a commitment, which is the safe direction for a list to fail in.
AUTONOMOUS_ACTIONS = (
    "discover_opportunities",
    "normalize_listings",
    "score_opportunity",
    "reject_opportunity",
    "research_client",
    "calculate_profitability",
    "draft_proposal",
    "prepare_deliverable",
    "run_worker",
    "run_reviewer",
    "run_qa",
    "update_dashboard",
    "monitor_health",
    "prepare_fiverr_gig",
    "record_outcome",
    "compute_analytics",
    "schedule_work",
    "reserve_capacity",
)


def commitment_check(action: str) -> GateResult:
    """Does this action bind Andres to something?

    Fails closed: an action nobody has classified is treated as a commitment. The cost of a
    false stop is a message; the cost of a false start is a signed agreement.
    """
    key = (action or "").strip().lower().replace(" ", "_").replace("-", "_")
    if key in AUTONOMOUS_ACTIONS:
        return _CLEAR
    if key in COMMITMENT_ACTIONS:
        return GateResult(
            True,
            Gate.COMMITMENT.value,
            [key],
            "This binds Andres - to a client, an employer, a platform, or money. Affirmative "
            "personal consent is required and the system does not supply it on his behalf.",
        )
    return GateResult(
        True,
        Gate.COMMITMENT.value,
        [key],
        f"'{key}' is not on the autonomous list. An unclassified action is treated as a "
        f"commitment until someone classifies it, because the failure directions are not symmetric.",
    )


# ---------------------------------------------------------------------------
# 6. Marketplace capability matrix
# ---------------------------------------------------------------------------


class Capability(StrEnum):
    AUTOMATIC = "AUTOMATIC"
    ASSISTED = "ASSISTED"
    HUMAN_REQUIRED = "HUMAN_REQUIRED"
    PROHIBITED = "PROHIBITED"


@dataclass
class MarketplaceCapability:
    source: str
    discovery: str
    submission: str
    delivery: str
    basis: str
    """What in that platform's current published rules produced these values."""


#: The matrix. `discovery` is reading listings, `submission` is sending a proposal or publishing,
#: `delivery` is doing and handing over the work. The basis column matters more than the values:
#: a capability without a citation is an assumption, and assumptions about terms of service are
#: how accounts get banned.
MARKETPLACES: dict[str, MarketplaceCapability] = {
    "hackernews": MarketplaceCapability(
        "hackernews",
        Capability.AUTOMATIC,
        Capability.HUMAN_REQUIRED,
        Capability.HUMAN_REQUIRED,
        "Official public Firebase API, documented for programmatic use. Replies are posted by a person.",
    ),
    "himalayas": MarketplaceCapability(
        "himalayas",
        Capability.AUTOMATIC,
        Capability.HUMAN_REQUIRED,
        Capability.HUMAN_REQUIRED,
        "Published JSON/RSS feed intended for syndication. Applications go through the employer's own site.",
    ),
    "remoteok": MarketplaceCapability(
        "remoteok",
        Capability.AUTOMATIC,
        Capability.HUMAN_REQUIRED,
        Capability.HUMAN_REQUIRED,
        "Public API endpoint advertised for reuse with attribution.",
    ),
    "weworkremotely": MarketplaceCapability(
        "weworkremotely",
        Capability.AUTOMATIC,
        Capability.HUMAN_REQUIRED,
        Capability.HUMAN_REQUIRED,
        "Public RSS feed published for syndication; robots.txt permits the feed path. Applications "
        "go to each employer's own site, so nothing is submitted here at all.",
    ),
    "pythonjobs": MarketplaceCapability(
        "pythonjobs",
        Capability.AUTOMATIC,
        Capability.HUMAN_REQUIRED,
        Capability.HUMAN_REQUIRED,
        "Public RSS feed on python.org, a site that publishes the feed specifically for reuse. "
        "Applications are emailed by a person to the address in the posting.",
    ),
    "freelancer": MarketplaceCapability(
        "freelancer",
        Capability.AUTOMATIC,
        Capability.HUMAN_REQUIRED,
        Capability.HUMAN_REQUIRED,
        "Documented public project-search API. Bidding requires the account holder.",
    ),
    "upwork": MarketplaceCapability(
        "upwork",
        Capability.ASSISTED,
        Capability.HUMAN_REQUIRED,
        Capability.HUMAN_REQUIRED,
        "Upwork's terms prohibit automated scraping of the job feed. Their own MCP server is the "
        "sanctioned agent surface; Connects are spent only on an explicit human confirm.",
    ),
    "contra": MarketplaceCapability(
        "contra",
        Capability.ASSISTED,
        Capability.HUMAN_REQUIRED,
        Capability.HUMAN_REQUIRED,
        "Their MCP covers back-office objects, not discovery. No permitted programmatic job search.",
    ),
    "fiverr": MarketplaceCapability(
        "fiverr",
        Capability.PROHIBITED,
        Capability.HUMAN_REQUIRED,
        Capability.ASSISTED,
        "No discovery surface exists for sellers at all, and no seller API. Gigs are published by "
        "hand; work arrives inbound and is then delivered with AI assistance under the gig's own "
        "disclosure.",
    ),
    "reddit": MarketplaceCapability(
        "reddit",
        Capability.PROHIBITED,
        Capability.PROHIBITED,
        Capability.HUMAN_REQUIRED,
        "The Data API terms require approval for commercial use, and finding paid work is "
        "commercial use. Unapproved, so the capability is PROHIBITED rather than merely unused.",
    ),
}

#: How an AutomationPolicy on a listing maps into the matrix, so the two vocabularies cannot drift.
_POLICY_TO_CAPABILITY = {
    AutomationPolicy.FULL_AUTO.value: Capability.AUTOMATIC.value,
    AutomationPolicy.ASSISTED.value: Capability.ASSISTED.value,
    AutomationPolicy.MANUAL_IMPORT.value: Capability.HUMAN_REQUIRED.value,
    AutomationPolicy.INBOUND_ONLY.value: Capability.HUMAN_REQUIRED.value,
    AutomationPolicy.DEMO.value: Capability.HUMAN_REQUIRED.value,
}


def capability_for(source: str, action: str = "discovery") -> str:
    """What this system is permitted to do on `source`. Unknown sources are HUMAN_REQUIRED."""
    entry = MARKETPLACES.get((source or "").strip().lower())
    if entry is None:
        return Capability.HUMAN_REQUIRED.value
    return str(getattr(entry, action, Capability.HUMAN_REQUIRED.value))


def submission_permitted(source: str) -> bool:
    """True only where a platform's current rules actually allow an automated submission.

    Today this is false everywhere, and that is the honest answer rather than a placeholder.
    """
    return capability_for(source, "submission") == Capability.AUTOMATIC.value


# ---------------------------------------------------------------------------
# 7. AI-use policy
# ---------------------------------------------------------------------------


class AIUse(StrEnum):
    ALLOWED = "AI ALLOWED"
    ALLOWED_WITH_DISCLOSURE = "AI ALLOWED WITH DISCLOSURE"
    UNCLEAR = "AI POLICY UNCLEAR"
    PROHIBITED = "AI PROHIBITED"


_AI_BANNED = _phrases(
    "no ai",
    "no chatgpt",
    "no gpt",
    "human written only",
    "human-written only",
    "100% human",
    "must be human",
    "no ai-generated",
    "no ai generated",
    "ai-generated content will be rejected",
    "must pass ai detection",
    "zero ai",
    "without ai",
    "no llm",
)

_AI_DISCLOSE = _phrases(
    "disclose ai",
    "ai disclosure",
    "declare ai",
    "ai use must be disclosed",
    "please disclose",
    "state whether ai",
)

_AI_WELCOME = _phrases(
    "ai-assisted",
    "ai assisted",
    "ai welcome",
    "ai is fine",
    "ai tools encouraged",
    "use of ai is permitted",
    "llm",
    "claude",
    "chatgpt",
    "copilot",
)

#: A client describing *their own* process is not setting a policy for the contractor. An earlier
#: version rejected a perfectly good listing over "I do not use AI to screen your applications".
_ABOUT_THEIR_PROCESS = re.compile(r"\b(we|i|our team|our company)\s+(do not|don't|never|will not|won't)\s+use\s+ai\b", re.I)


def classify_ai_use(opp: Opportunity) -> tuple[str, str]:
    """Return (classification, the evidence that produced it).

    Ordered so the strictest reading wins on a tie, and so an explicit prohibition is never
    talked out of by an encouraging word elsewhere in the same posting.
    """
    text = f"{opp.title}\n{opp.description}"
    if opp.ai_allowed is False:
        return AIUse.PROHIBITED.value, "The connector recorded an explicit client prohibition."

    banned = [m.group(0) for m in _AI_BANNED.finditer(text)]
    if banned:
        if _ABOUT_THEIR_PROCESS.search(text) and len(banned) == 1:
            return (
                AIUse.UNCLEAR.value,
                f"'{banned[0]}' appears, but the sentence describes the client's own hiring "
                f"process rather than a rule for the contractor. Too ambiguous to act on.",
            )
        return AIUse.PROHIBITED.value, f"The posting says: {', '.join(sorted(set(banned)))}."

    disclose = [m.group(0) for m in _AI_DISCLOSE.finditer(text)]
    if disclose:
        return AIUse.ALLOWED_WITH_DISCLOSURE.value, f"The posting asks for disclosure: {disclose[0]}."

    welcome = [m.group(0) for m in _AI_WELCOME.finditer(text)]
    if welcome:
        return AIUse.ALLOWED.value, f"The posting names AI tooling itself: {', '.join(sorted(set(welcome))[:3])}."

    return (
        AIUse.UNCLEAR.value,
        "The posting says nothing about AI. Silence is not permission; this system's default is "
        "to disclose AI assistance in the proposal and let the client decide.",
    )


def ai_use_blocks_work(classification: str) -> bool:
    """Only an actual prohibition blocks. Unclear means disclose and ask, not assume."""
    return classification == AIUse.PROHIBITED.value


# ---------------------------------------------------------------------------
# 8. Screening order - Claude is the scarce resource
# ---------------------------------------------------------------------------

#: The funnel, in the order it must run. Every stage before CLAUDE_SEMANTIC is free
#: deterministic Python; the point of the ordering is that a listing which will be rejected
#: anyway is rejected by code that costs nothing rather than by a model call that costs capacity.
SCREENING_ORDER = (
    "deterministic_filter",
    "profitability_filter",
    "compliance_filter",
    "capability_filter",
    "claude_semantic",
    "profit_capacity_ranking",
    "proposal",
)

#: Work that must never reach a model, because deterministic code does it correctly and for free.
#: Spending subscription capacity on any of these is spending it twice: once on the call, and
#: once on the paid job that call displaced.
DETERMINISTIC_ONLY = frozenset(
    {
        "feed_retrieval",
        "normalization",
        "deduplication",
        "currency_parsing",
        "budget_parsing",
        "keyword_filtering",
        "employment_classification",
        "pslf_filtering",
        "compliance_filtering",
        "file_metadata",
        "arithmetic",
        "profitability_calculation",
        "scheduling",
        "status_tracking",
        "dashboard_generation",
        "health_checks",
    }
)


def may_use_claude(task: str) -> tuple[bool, str]:
    """Guard at the model boundary, not a guideline in a document."""
    key = (task or "").strip().lower().replace(" ", "_").replace("-", "_")
    if key in DETERMINISTIC_ONLY:
        return False, (
            f"'{key}' is handled by deterministic code, which is free, faster and reproducible. "
            f"Spending subscription capacity here takes it from paid client work."
        )
    return True, ""


# ---------------------------------------------------------------------------
# 9. The named invariants
# ---------------------------------------------------------------------------


@dataclass
class Invariant:
    key: str
    statement: str
    consequence: str
    """What would be true of the business if this stopped holding. Written for someone deciding
    whether to leave the system running unattended."""


INVARIANTS: tuple[Invariant, ...] = (
    Invariant(
        "no_full_time_applications",
        "No application is generated for for-profit full-time employment.",
        "Andres could be put in front of a job that ends roughly seven years of PSLF-qualifying payments.",
    ),
    Invariant(
        "no_fabricated_qualifications",
        "No claim about Andres is generated that is not already verified in this repository.",
        "A client would receive a false statement about his experience, under his name.",
    ),
    Invariant(
        "no_automatic_personal_information",
        "No personal-identifying field is ever filled or submitted automatically.",
        "Identity documents or banking details could be sent somewhere he never saw.",
    ),
    Invariant(
        "no_security_control_bypass",
        "No CAPTCHA, 2FA, identity check or signature control is solved, automated or routed around.",
        "Accounts would be banned, and the system would be defeating consent controls by design.",
    ),
    Invariant(
        "no_automatic_contract_acceptance",
        "No contract, NDA, terms agreement or employment offer is accepted without Andres.",
        "He would be legally bound to something he never read.",
    ),
    Invariant(
        "no_prohibited_marketplace_automation",
        "No source is automated beyond what that platform's current rules permit.",
        "Accounts would be suspended, and the business would be built on a terms violation.",
    ),
    Invariant(
        "no_ai_prohibited_work",
        "No work is accepted or performed where the client prohibits AI for that work.",
        "He would be delivering work under a false representation of how it was made.",
    ),
    Invariant(
        "no_paid_fallback_on_exhaustion",
        "An exhausted subscription window pauses AI work; it never falls back to metered billing.",
        "A usage spike would arrive as a bill instead of as a delay.",
    ),
    Invariant(
        "no_api_key_in_zero_cost_mode",
        "ANTHROPIC_API_KEY is not used, and its presence stops the run.",
        "Every model call would be billed to a card while the business is pre-revenue.",
    ),
    Invariant(
        "no_client_data_in_public_git",
        "Client material, credentials and personal information never reach the public repository.",
        "A client's confidential file would be world-readable and permanent in git history.",
    ),
    Invariant(
        "no_submission_without_approval",
        "No proposal, message or publication leaves the system without the approval gate.",
        "Clients would receive machine-sent proposals he never approved - the spam outcome he ruled out.",
    ),
    Invariant(
        "no_spending_without_approval",
        "No new cash spend occurs. The ceiling is $0.00 and has no override parameter.",
        "The business would start costing money before it earns any.",
    ),
)

assert len(INVARIANTS) == 12, "The amendment names twelve invariants; the list must not drift."


def invariant(key: str) -> Invariant:
    for inv in INVARIANTS:
        if inv.key == key:
            return inv
    raise KeyError(key)


# ---------------------------------------------------------------------------
# 10. One entry point for an opportunity
# ---------------------------------------------------------------------------


@dataclass
class PolicyVerdict:
    """Everything the standing rules have to say about one listing, in one object."""

    allowed: bool
    gates: list[dict[str, Any]] = field(default_factory=list)
    ai_use: str = AIUse.UNCLEAR.value
    ai_evidence: str = ""
    discovery_capability: str = Capability.HUMAN_REQUIRED.value
    submission_capability: str = Capability.HUMAN_REQUIRED.value
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "gates": self.gates,
            "ai_use": self.ai_use,
            "ai_evidence": self.ai_evidence,
            "discovery_capability": self.discovery_capability,
            "submission_capability": self.submission_capability,
            "notes": self.notes,
        }

    @property
    def blocking_gate(self) -> str:
        return self.gates[0]["gate"] if self.gates else ""


def evaluate(opp: Opportunity) -> PolicyVerdict:
    """Run every standing rule against one listing.

    Cheap and deterministic on purpose - this runs on every listing in a scan, before anything
    reaches a model, and its whole job is to make sure the expensive stages never see work that
    was never going to be allowed.
    """
    text = f"{opp.title}\n{opp.description}"
    gates: list[dict[str, Any]] = []

    pslf = full_time_employment_check(opp)
    if pslf:
        gates.append(pslf.to_dict())

    personal = personal_information_check(text)
    if personal:
        gates.append(personal.to_dict())

    security = security_control_check(text)
    if security:
        gates.append(security.to_dict())

    ai_use, ai_evidence = classify_ai_use(opp)
    if ai_use_blocks_work(ai_use):
        gates.append(GateResult(True, "AI PROHIBITED", [], f"{ai_evidence} Performing it with AI would misrepresent the work.").to_dict())

    notes = []
    if RiskFlag.PROMPT_INJECTION_ATTEMPT.value in opp.risk_flags:
        notes.append("Contains injection-shaped text. Analysed as content; never executed as instruction.")
    if ai_use == AIUse.ALLOWED_WITH_DISCLOSURE.value:
        notes.append("Disclosure required: the proposal must state AI assistance plainly.")
    if ai_use == AIUse.UNCLEAR.value:
        notes.append("AI policy unstated. Default is to disclose and let the client decide.")

    # A personal-information or security gate does not disqualify the *work* - it means a step
    # inside it belongs to Andres. Only PSLF and an AI prohibition make the listing itself wrong.
    disqualifying = {Gate.PSLF.value, "AI PROHIBITED"}
    allowed = not any(g["gate"] in disqualifying for g in gates)

    return PolicyVerdict(
        allowed=allowed,
        gates=gates,
        ai_use=ai_use,
        ai_evidence=ai_evidence,
        discovery_capability=capability_for(opp.source, "discovery"),
        submission_capability=capability_for(opp.source, "submission"),
        notes=notes,
    )
