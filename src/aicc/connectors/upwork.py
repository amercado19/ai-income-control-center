"""Upwork connector - ASSISTED, not automated. And rationed, because applying costs money.

Verified September 2026. Two facts govern this module.

**1. Scraping Upwork is explicitly and specifically banned.** Not ambiguous, not a grey area.
``upwork.com/robots.txt`` contains ``Disallow: /jobs/``, and Upwork publishes a dedicated bots
policy naming exactly the things a job-scanner would do:

    "Job alert or watcher tools that scrape or run searches. Auto-refresh or tab reload tools
    that refresh pages on a timer. Page monitors or change detectors that poll pages for
    updates. Macro or RPA recorders that replay clicks and searches."
    - https://support.upwork.com/hc/en-us/articles/43342677368467

The penalty ladder ends at a permanent ban. This connector therefore performs **no HTTP request
to Upwork at all**. ``discover`` raises rather than returning an empty list, so the failure is
loud.

**2. The sanctioned path is Upwork's own MCP server**, ``https://mcp.upwork.com/mcp`` - free
with any account, OAuth, no API key, and no $25,000-lifetime-earnings gate (their GraphQL API
does have that gate, and this account does not clear it). It supports job search and grounded
proposal drafting, with every write action draft-then-confirm by design.

MCP is an interactive agent surface: it authenticates a human's session, and there is no
supported way to run it unattended from CI. So the flow is human-in-the-loop by construction,
which happens to match the Level 2 approval model exactly.

**Connects are real money.** 10 free per month, 4-16 per proposal, $0.15 each beyond that, and
no refund when you lose. ``connect_spend_request`` routes every submission through the cost gate
so that a Connect is never spent without Andres seeing the price first.
"""

from __future__ import annotations

from datetime import UTC
from typing import Any

from ..config import CostRequest, cost_gate
from ..models import AutomationPolicy
from .base import Capabilities, Connector, NotPermittedError, register

MCP_ENDPOINT = "https://mcp.upwork.com/mcp"
CONNECT_CASH_PRICE = 0.15
FREE_CONNECTS_PER_MONTH = 10


@register
class UpworkConnector(Connector):
    CAPS = Capabilities(
        name="upwork",
        label="Upwork",
        discovery=False,  # not from this process - see module docstring
        api=True,  # an API exists, gated behind $25k lifetime earnings
        allowed_automated_fetch=False,
        auto_proposal=False,
        manual_approval_required=True,
        auto_delivery=False,
        automation_policy=AutomationPolicy.ASSISTED.value,
        requires_credential=True,
        credential_present=False,
        cost_to_apply="4-16 Connects per proposal (10 free/month, then $0.15 each, non-refundable)",
        rules_evidence=(
            "robots.txt disallows /jobs/. The bots policy explicitly prohibits job watchers, page "
            "monitors, auto-refresh tools and RPA replay. The GraphQL API requires $25,000 lifetime "
            "earnings and a 90% Job Success Score. The official MCP server is the only open "
            "programmatic surface and it is interactive, not unattended."
        ),
        rules_url="https://support.upwork.com/hc/en-us/articles/43342677368467",
        notes=(
            "AI-drafted proposals are permitted and AI use must be disclosed to clients. Automated "
            "SUBMISSION is what is prohibited, not AI authorship."
        ),
    )

    @classmethod
    def discover(cls, limit: int = 50) -> list[Any]:
        raise NotPermittedError(
            "Upwork discovery is not performed programmatically by this system. "
            "robots.txt disallows /jobs/ and the published bots policy prohibits job watchers and "
            "page monitors. Use the official Upwork MCP server interactively "
            f"({MCP_ENDPOINT}), or import listings manually. See docs/MARKETPLACE_RULES.md."
        )

    # -- cost gating ---------------------------------------------------------

    @classmethod
    def connect_spend_request(cls, connects: int, expected_net: float, job_title: str) -> dict[str, Any]:
        """Price a proposal submission and route it through the cost gate.

        Within the free monthly allowance this returns a $0.00 request that the gate approves.
        Beyond it, the gate declines and Andres must approve the cash explicitly (spec section 16).
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
                alternative="Apply to a direct-contact opportunity (Hacker News, aggregator feeds) at no cost.",
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

        Upwork exposes no balance API at this tier, so this is a local counter, not an
        observation of the real balance. It is labelled as such on the dashboard: it can drift
        if Andres submits proposals directly on upwork.com, and it is a spending guard rather
        than an authoritative figure.
        """
        from datetime import datetime

        from .. import storage

        month = datetime.now(UTC).strftime("%Y-%m")
        spent = 0
        for prop in storage.proposals.all():
            if prop.source == "upwork" and prop.submitted_at.startswith(month):
                spent += prop.submit_cost_units
        return max(0, FREE_CONNECTS_PER_MONTH - spent)
