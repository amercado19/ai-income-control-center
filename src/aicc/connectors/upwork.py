"""Upwork connector - ASSISTED, rationed, and now bound by explicit contractual data limits.

Verified September 2026 against the **Upwork API & MCP Terms of Use v2.3, effective 13 August
2026** (https://www.upwork.com/legal#apimcpterms, PactSafe contract 652410).

Three facts govern this module. The third is new and it changed the design.

**1. Scraping Upwork is explicitly prohibited.** ``upwork.com/robots.txt`` contains
``Disallow: /jobs/``, and Upwork publishes a bots policy naming exactly what a job-scanner does:

    "Job alert or watcher tools that scrape or run searches. Auto-refresh or tab reload tools that
    refresh pages on a timer. Page monitors or change detectors that poll pages for updates."
    - https://support.upwork.com/hc/en-us/articles/43342677368467

This connector therefore makes **no HTTP request to Upwork at all**. ``discover`` raises.

**2. The sanctioned path is Upwork's own MCP server**, ``https://mcp.upwork.com/mcp`` - free with
any account, OAuth 2.1 with dynamic client registration, no API key, no earnings gate. It supports
job search and grounded proposal drafting, and *"Connects only apply when you confirm."*

It is interactive by contract, not merely by implementation: *"Can an AI agent hire on my behalf
without me? Not today. In the current release, a person confirms every binding action."*

**3. The v2.3 terms impose data limits that a git-backed store would violate.** These are the ones
that matter, quoted:

* **§8.3 Caching** - *"You may cache Upwork Content solely to improve the performance and user
  experience of your Developer Application, for no more than 24 hours, after which you shall
  permanently delete the cache. You shall not extend the cache by re-fetching solely to refresh
  the timer."*
* **§8.6 MCP outputs** - *"You shall not retain event payloads or MCP outputs beyond the period
  reasonably necessary to complete the Upwork User's immediate task, and in no event longer than
  30 days."*
* **§5.3** - no using retrieved content to *"train, fine-tune, retrieval-augment, evaluate,
  benchmark, or otherwise improve"* any model.
* **§4.1** - browsing permission covers *"only access reasonably necessary to perform a specific,
  documented, user-directed task"* and does **not** authorise *"activity designed to enumerate or
  continuously monitor Upwork's available content corpus."*
* **§5.9** - an Agent may not independently *"select, rank, score, or recommend among candidates,
  postings, proposals, or contracts using criteria the Agent itself determines."* Retrieving
  results on the Principal's own stated filter criteria is expressly permitted.
* **§5.12(e)** - no using the Tools to contact or transact with Upwork users off-platform.
* **Bulk Access** is excluded from the licence entirely.

What this means concretely, and it is a real constraint rather than a formality:

* Upwork listings **are never written to the committed store.** ``data/`` is a permanent public
  git history, which is flatly incompatible with a 24-hour cache limit. ``UPWORK_PERSISTENCE`` is
  ``False`` and ``assert_not_persisted`` enforces it.
* The autonomous scorer **must not** rank Upwork postings on criteria of its own. Under §5.9 the
  system may filter on *Andres's* stated criteria and show him the results; the ranking judgement
  is his. ``operator_filter_only`` builds that filter from his profile so the criteria are
  demonstrably his, not the system's.
* Nothing retrieved from Upwork feeds the learning system (§5.3).

**Connects are real money.** 10 free per month, 4-16 per proposal, $0.15 each beyond that, never
refunded on a loss. ``connect_spend_request`` prices every submission through the cost gate.
"""

from __future__ import annotations

from datetime import UTC
from typing import Any

from ..config import PROFILE, CostRequest, cost_gate
from ..models import AutomationPolicy
from .base import Capabilities, Connector, NotPermittedError, register

MCP_ENDPOINT = "https://mcp.upwork.com/mcp"
TERMS_URL = "https://www.upwork.com/legal#apimcpterms"
TERMS_VERSION = "v2.3, effective 2026-08-13"

CONNECT_CASH_PRICE = 0.15
FREE_CONNECTS_PER_MONTH = 10

# §8.3 / §8.6. Both are shorter than "forever in git", which is what the committed store offers.
CACHE_LIMIT_HOURS = 24
MCP_OUTPUT_RETENTION_DAYS = 30

UPWORK_PERSISTENCE = False
"""Upwork content is never written to the committed store.

Not a preference - §8.3 caps caching at 24 hours and requires permanent deletion after it. A
public git repository cannot honour that, because history is permanent by design. Upwork results
live in memory for the session that retrieved them and are then gone.
"""


class UpworkRetentionError(RuntimeError):
    """Something tried to persist Upwork content. Blocked by §8.3 / §8.6."""


def assert_not_persisted(record: Any) -> None:
    """Refuse to store anything sourced from Upwork.

    Called by the storage layer. Belt and braces against a future connector or a careless
    refactor quietly writing Upwork content into permanent git history.
    """
    source = getattr(record, "source", None) or (record.get("source") if isinstance(record, dict) else None)
    if source == "upwork":
        raise UpworkRetentionError(
            "Refusing to persist Upwork content. The API & MCP Terms "
            f"({TERMS_VERSION}) §8.3 cap caching at {CACHE_LIMIT_HOURS} hours and require "
            "permanent deletion after it; a committed git store keeps history forever. "
            "Upwork results are session-only. See docs/MARKETPLACE_RULES.md."
        )


@register
class UpworkConnector(Connector):
    CAPS = Capabilities(
        name="upwork",
        label="Upwork",
        discovery=False,
        api=True,
        allowed_automated_fetch=False,
        auto_proposal=False,
        manual_approval_required=True,
        auto_delivery=False,
        automation_policy=AutomationPolicy.ASSISTED.value,
        requires_credential=True,
        credential_present=False,
        cost_to_apply="4-16 Connects per proposal (10 free/month, then $0.15 each, non-refundable)",
        rules_evidence=(
            "robots.txt disallows /jobs/; the bots policy prohibits job watchers and page monitors. "
            f"API & MCP Terms {TERMS_VERSION}: §8.3 caps caching at 24h, §8.6 caps MCP-output "
            "retention at 30 days, §4.1 forbids enumerating or continuously monitoring the corpus, "
            "§5.9 forbids the agent ranking postings on criteria it chooses itself, and Bulk Access "
            "is excluded from the licence. The official MCP server is interactive, not unattended."
        ),
        rules_url=TERMS_URL,
        notes=(
            "AI-drafted proposals are permitted and AI use must be disclosed to clients. Automated "
            "SUBMISSION is what is prohibited. Upwork results are never persisted to the committed "
            "store, because a public git history cannot honour a 24-hour cache limit."
        ),
    )

    @classmethod
    def discover(cls, limit: int = 50) -> list[Any]:
        raise NotPermittedError(
            "Upwork discovery is not performed programmatically by this system. robots.txt "
            "disallows /jobs/, the bots policy prohibits job watchers, and API & MCP Terms "
            f"{TERMS_VERSION} §4.1 forbids enumerating or continuously monitoring the corpus. "
            f"Use the official MCP server interactively ({MCP_ENDPOINT}). "
            "See docs/MARKETPLACE_RULES.md."
        )

    # -- §5.9 compliance -----------------------------------------------------

    @classmethod
    def operator_filter_only(cls) -> dict[str, Any]:
        """The filter criteria to hand the MCP server - Andres's, not the system's.

        §5.9 permits retrieving results on *the Principal's* stated filter criteria and forbids the
        agent selecting or ranking on criteria it determines for itself. So this returns his
        standing preferences verbatim from the operator profile, to be passed through as a query.
        The system does not then re-rank what comes back; it presents it and he decides.
        """
        return {
            "skills": sorted(PROFILE.skill_set())[:20],
            "min_hourly_rate": PROFILE.minimum_hourly,
            "min_fixed_budget": PROFILE.minimum_job_value,
            "criteria_source": "OperatorProfile - set by Andres, not inferred by the system",
            "note": (
                "Under API & MCP Terms §5.9 the system may filter on these stated criteria but must "
                "not autonomously rank or recommend among the results. Ranking is the operator's."
            ),
        }

    @classmethod
    def may_autonomously_score(cls) -> bool:
        """False. §5.9 reserves ranking judgement to the operator for Upwork content."""
        return False

    # -- cost gating ---------------------------------------------------------

    @classmethod
    def connect_spend_request(cls, connects: int, expected_net: float, job_title: str) -> dict[str, Any]:
        """Price a proposal submission and route it through the cost gate.

        Within the free monthly allowance this is a $0.00 request the gate approves. Beyond it the
        gate declines and Andres must approve the cash explicitly.
        """
        remaining_free = cls.free_connects_remaining()
        billable = max(0, connects - remaining_free)
        cash = round(billable * CONNECT_CASH_PRICE, 2)

        decision = cost_gate.request(
            CostRequest(
                service="Upwork Connects",
                reason=f"Submit a proposal to: {job_title}",
                monthly_estimate=cash,
                benefit=f"Estimated net if won: ${expected_net:,.0f}",
                can_continue_without=True,
                alternative="Apply to a direct-contact opportunity (Hacker News, feeds) at no cost.",
            )
        )

        return {
            "connects_required": connects,
            "free_connects_remaining": remaining_free,
            "billable_connects": billable,
            "cash_cost": cash,
            "expected_net": expected_net,
            "approved": decision.approved,
            "reason": decision.reason,
            "requires_human": decision.requires_human,
        }

    @classmethod
    def free_connects_remaining(cls) -> int:
        """Connects spent this month, tracked locally.

        Upwork exposes no balance API at this tier, so this is a local counter rather than an
        observation. It drifts if Andres submits directly on upwork.com. It is a spending guard,
        not an authoritative balance, and the dashboard says so.
        """
        from datetime import datetime

        from .. import storage

        month = datetime.now(UTC).strftime("%Y-%m")
        spent = 0
        for prop in storage.proposals.all():
            if prop.source == "upwork" and prop.submitted_at.startswith(month):
                spent += prop.submit_cost_units
        return max(0, FREE_CONNECTS_PER_MONTH - spent)
