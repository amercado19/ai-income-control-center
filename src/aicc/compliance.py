"""The nine indicators on the dashboard's Safety & Compliance panel.

Every value here is derived from a live probe of the running system. Not one is a constant, and
that is the whole design: a panel of nine reassuring green words that are typed into the source
would be worse than having no panel, because it would be consulted. `IDENTITY PROTECTION: ON`
has to mean that something just tried to fabricate a qualification and was refused - otherwise
it means nothing at all and looks exactly the same.

The desired state for eight of them is fixed, and the panel says so next to each one, so a
drifted value is visible as a difference rather than needing to be recognised:

    IDENTITY PROTECTION       ON
    PSLF PROTECTION           ON
    MARKETPLACE COMPLIANCE    ON
    AI POLICY                 ON
    PERSONAL DATA PROTECTION  ON
    SPENDING LOCK             $0
    PAID API FALLBACK         DISABLED
    CONTRACT APPROVAL         REQUIRED

The ninth, CLAUDE CAPACITY, has no desired state - it reports whatever is true, including
UNKNOWN, because the honest answer some of the time is that nobody can tell.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass
class Indicator:
    key: str
    label: str
    value: str
    desired: str
    ok: bool
    detail: str
    """Evidence for the value. What was attempted, and what the system did about it."""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["drifted"] = bool(self.desired) and self.value != self.desired
        return d


def _identity_protection() -> Indicator:
    """Attempt an unbacked claim about Andres and confirm it is refused."""
    from .proposals import PROFILE, UnverifiableClaimError, _verify_claims

    try:
        _verify_claims(["fifteen years of enterprise Kubernetes consulting"])
    except UnverifiableClaimError:
        return Indicator(
            "identity_protection",
            "IDENTITY PROTECTION",
            "ON",
            "ON",
            True,
            f"A claim absent from the verified profile was refused. {len(PROFILE.demonstrated)} "
            f"claims are backed by inspectable work and may be used; nothing else may.",
        )
    except Exception as exc:  # noqa: BLE001
        return Indicator("identity_protection", "IDENTITY PROTECTION", "ERROR", "ON", False, f"{type(exc).__name__}: {exc}")
    return Indicator(
        "identity_protection",
        "IDENTITY PROTECTION",
        "OFF",
        "ON",
        False,
        "An unverified claim about Andres was accepted into a proposal.",
    )


def _pslf_protection() -> Indicator:
    """Both directions: the job is rejected, the freelance contract is not."""
    from .models import Opportunity
    from .policy import Gate, evaluate

    job = evaluate(
        Opportunity(
            title="Senior Engineer",
            description="Full-time salaried role, W-2, with 401(k) and health insurance.",
            engagement_type="FULL_TIME",
        )
    )
    gig = evaluate(
        Opportunity(
            title="CSV consolidation",
            description="Freelance contract, project-based, fixed price, one-time deliverable.",
        )
    )
    if job.allowed:
        return Indicator("pslf", "PSLF PROTECTION", "OFF", "ON", False, "A for-profit full-time role passed the policy layer.")
    if not gig.allowed:
        return Indicator(
            "pslf",
            "PSLF PROTECTION",
            "OVER-FIRING",
            "ON",
            False,
            "The gate rejected an ordinary freelance contract. Over-blocking finds no work.",
        )
    if job.blocking_gate != Gate.PSLF.value:
        return Indicator(
            "pslf", "PSLF PROTECTION", "OFF", "ON", False, f"Full-time work was blocked for the wrong reason: {job.blocking_gate}"
        )
    return Indicator(
        "pslf",
        "PSLF PROTECTION",
        "ON",
        "ON",
        True,
        "For-profit full-time employment is a hard reject; freelance and contract projects pass "
        "normally. Roughly seven years of qualifying payments depend on this distinction.",
    )


def _marketplace_compliance() -> Indicator:
    from .policy import MARKETPLACES, Capability, capability_for, submission_permitted

    offenders = [s for s in MARKETPLACES if submission_permitted(s)]
    if offenders:
        return Indicator(
            "marketplace",
            "MARKETPLACE COMPLIANCE",
            "OFF",
            "ON",
            False,
            f"Automated submission is claimed for {offenders}, which no platform permits.",
        )
    if capability_for("an-unreviewed-source") != Capability.HUMAN_REQUIRED.value:
        return Indicator(
            "marketplace", "MARKETPLACE COMPLIANCE", "OFF", "ON", False, "An unreviewed source does not default to HUMAN_REQUIRED."
        )
    counts: dict[str, int] = {}
    for entry in MARKETPLACES.values():
        counts[str(entry.discovery)] = counts.get(str(entry.discovery), 0) + 1
    shape = ", ".join(f"{v} {k.lower()}" for k, v in sorted(counts.items()))
    return Indicator(
        "marketplace",
        "MARKETPLACE COMPLIANCE",
        "ON",
        "ON",
        True,
        f"{len(MARKETPLACES)} sources matrixed for discovery ({shape}). No source permits "
        f"automated submission; an unreviewed source fails closed to HUMAN_REQUIRED.",
    )


def _ai_policy() -> Indicator:
    from .models import Opportunity
    from .policy import AIUse, ai_use_blocks_work, classify_ai_use

    banned, _ = classify_ai_use(Opportunity(title="x", description="No AI. Human written only."))
    theirs, _ = classify_ai_use(Opportunity(title="x", description="We do not use AI to screen your applications."))
    silent, _ = classify_ai_use(Opportunity(title="x", description="Merge some CSVs."))
    problems = []
    if not ai_use_blocks_work(banned):
        problems.append("an explicit prohibition did not block")
    if ai_use_blocks_work(theirs):
        problems.append("a client's own process was read as a rule for the contractor")
    if silent != AIUse.UNCLEAR.value:
        problems.append(f"silence classified as {silent}")
    if problems:
        return Indicator("ai_policy", "AI POLICY", "OFF", "ON", False, "; ".join(problems))
    return Indicator(
        "ai_policy",
        "AI POLICY",
        "ON",
        "ON",
        True,
        "Four classes in use: allowed, allowed with disclosure, unclear, prohibited. Prohibited "
        "work is declined rather than disguised, and silence is treated as unclear - so the "
        "default is to disclose AI assistance and let the client decide.",
    )


def _personal_data_protection() -> Indicator:
    from pathlib import Path

    from .config import REPO_ROOT, WORKSPACE_ROOT
    from .policy import personal_information_check
    from .privacy import contains_contact_details, sanitize_for_storage

    fires = personal_information_check("Send your SSN and a photo of your passport.").triggered
    quiet = not personal_information_check("Normalise the customer address column.").triggered
    stored, redactions = sanitize_for_storage("Email hiring@example.com or telegram: @bob.")
    redacted = redactions > 0 and not contains_contact_details(stored)
    gitignore = REPO_ROOT / ".gitignore"
    ws_name = Path(WORKSPACE_ROOT).name
    excluded = gitignore.exists() and f"{ws_name}/" in gitignore.read_text(encoding="utf-8")

    problems = []
    if not fires:
        problems.append("an identity-document request did not raise the gate")
    if not quiet:
        problems.append("ordinary project language raised the gate")
    if not redacted:
        problems.append("third-party contact details survived redaction")
    if not excluded:
        problems.append(f"'{ws_name}/' is not gitignored")
    if problems:
        return Indicator("personal_data", "PERSONAL DATA PROTECTION", "OFF", "ON", False, "; ".join(problems))
    return Indicator(
        "personal_data",
        "PERSONAL DATA PROTECTION",
        "ON",
        "ON",
        True,
        f"Identity fields are never filled automatically, third-party contact details are "
        f"redacted before anything is written, and client material in '{ws_name}/' is excluded "
        f"from the public repository.",
    )


def _spending_lock() -> Indicator:
    from .config import MAX_NEW_MONTHLY_CASH_SPEND, CostGate, CostRequest

    decision = CostGate().request(
        CostRequest(
            service="compliance probe",
            reason="Attempt a charge to prove the ceiling holds.",
            monthly_estimate=9.99,
            benefit="None. This exists to be refused.",
            can_continue_without=True,
        )
    )
    value = f"${MAX_NEW_MONTHLY_CASH_SPEND:.0f}"
    if decision.approved or MAX_NEW_MONTHLY_CASH_SPEND != 0.00:
        return Indicator(
            "spending_lock", "SPENDING LOCK", value, "$0", False, "A charge was approved automatically, or the ceiling is no longer $0.00."
        )
    return Indicator(
        "spending_lock",
        "SPENDING LOCK",
        "$0",
        "$0",
        True,
        f"A $9.99/month request was refused: {decision.reason[:110]}",
    )


def _paid_api_fallback() -> Indicator:
    import inspect
    import os

    from . import capacity, degradation

    decision = degradation.classify("429 rate_limit_error: usage limit reached", status_code=429)
    safe = degradation.never_falls_back_to_paid(decision)
    key_present = bool(os.environ.get("ANTHROPIC_API_KEY"))
    source_clean = "anthropic_api_key" not in inspect.getsource(capacity).lower()

    if key_present:
        return Indicator(
            "paid_fallback",
            "PAID API FALLBACK",
            "REACHABLE",
            "DISABLED",
            False,
            "ANTHROPIC_API_KEY is present in the environment. Metered billing is reachable.",
        )
    if not safe or not source_clean:
        return Indicator(
            "paid_fallback",
            "PAID API FALLBACK",
            "REACHABLE",
            "DISABLED",
            False,
            "An exhausted window produced a decision that permits paid billing.",
        )
    return Indicator(
        "paid_fallback",
        "PAID API FALLBACK",
        "DISABLED",
        "DISABLED",
        True,
        f"No ANTHROPIC_API_KEY anywhere. An exhausted subscription window yields "
        f"{decision.action} - the consequence of the limit is a delay, never a bill.",
    )


def _contract_approval() -> Indicator:
    from .policy import COMMITMENT_ACTIONS, commitment_check

    ungated = [a for a in COMMITMENT_ACTIONS if not commitment_check(a).triggered]
    if ungated:
        return Indicator(
            "contract_approval", "CONTRACT APPROVAL", "NOT REQUIRED", "REQUIRED", False, f"These would proceed without Andres: {ungated}"
        )
    if not commitment_check("an_action_nobody_classified").triggered:
        return Indicator(
            "contract_approval",
            "CONTRACT APPROVAL",
            "NOT REQUIRED",
            "REQUIRED",
            False,
            "An unclassified action was allowed through. The gate must fail closed.",
        )
    return Indicator(
        "contract_approval",
        "CONTRACT APPROVAL",
        "REQUIRED",
        "REQUIRED",
        True,
        f"All {len(COMMITMENT_ACTIONS)} commitment actions - contracts, NDAs, terms, employment, "
        f"payments, submissions - require Andres. An unclassified action is treated as a "
        f"commitment rather than assumed safe.",
    )


def _claude_capacity() -> Indicator:
    """The one indicator with no desired state. It reports what is true, including UNKNOWN."""
    from . import capacity

    snap = capacity.snapshot()
    status = snap["status"]
    conf = snap["confidence"]
    caveat = " (ESTIMATED)" if conf != "MEASURED" else ""
    detail = (
        f"{snap['safe_new_work_display']} of capacity is safe to start new work against, after "
        f"{snap['reserved_minutes']:.0f} min reserved for accepted paid work. "
        f"Next window reset {snap['next_reset']}. {snap['basis']}"
    )
    if snap.get("exhausted_until"):
        detail = f"The window is spent until {snap['exhausted_until']}. AI work is queued, not billed. " + detail
    return Indicator("claude_capacity", "CLAUDE CAPACITY", f"{status}{caveat}", "", True, detail)


_PROBES = (
    _identity_protection,
    _pslf_protection,
    _marketplace_compliance,
    _ai_policy,
    _personal_data_protection,
    _spending_lock,
    _paid_api_fallback,
    _contract_approval,
    _claude_capacity,
)


def panel() -> dict[str, Any]:
    """The whole Safety & Compliance panel, probed fresh."""
    indicators: list[Indicator] = []
    for probe in _PROBES:
        try:
            indicators.append(probe())
        except Exception as exc:  # noqa: BLE001
            # A probe that dies must read as a failure, not vanish. A missing row on a safety
            # panel is indistinguishable from a row that is fine, which is the worst outcome.
            name = probe.__name__.strip("_")
            indicators.append(
                Indicator(
                    name, name.replace("_", " ").upper(), "ERROR", "ON", False, f"The probe itself raised {type(exc).__name__}: {exc}"
                )
            )
    return {
        "indicators": [i.to_dict() for i in indicators],
        "all_ok": all(i.ok for i in indicators),
        "drifted": [i.key for i in indicators if i.desired and i.value != i.desired],
        "note": (
            "Every value is probed live. Nothing on this panel is a constant - each one is the "
            "result of attempting the thing it forbids and being refused."
        ),
    }
