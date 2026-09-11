"""Contra connector - ASSISTED via Contra's own MCP server.

Verified September 2026.

Contra's Terms of Service section 13.3(v) prohibits accessing the platform with
"any engine, software, tool, agent, device or mechanism (including spiders, robots, crawlers,
data mining tools or the like) **other than the software and/or search agents provided by
Contra**".

That carve-out is the whole story: Contra's official MCP server is "software provided by
Contra", so using it is permitted; writing our own scraper is not. Contra ships an MCP server
with 60+ tools covering proposals, invoices, portfolio and payment links for independents, and
it uses a two-step prepare-and-confirm flow that requires user approval for account changes.

Contra is also commission-free, which makes its net economics the best of any marketplace here.

Section 14.3 additionally caps outreach volume regardless of the tool used, so this connector
never batch-sends and never queues more than one proposal at a time for approval.
"""

from __future__ import annotations

from typing import Any

from ..models import AutomationPolicy
from .base import Capabilities, Connector, NotPermittedError, register

MCP_MARKETING_PAGE = "https://contra.com/features/mcp"
OPPORTUNITIES_URL = "https://contra.com/independent/opportunities"


@register
class ContraConnector(Connector):
    CAPS = Capabilities(
        name="contra",
        label="Contra",
        discovery=False,  # not by our own crawler; via Contra's MCP, interactively
        api=True,
        allowed_automated_fetch=False,
        auto_proposal=False,
        manual_approval_required=True,
        auto_delivery=False,
        automation_policy=AutomationPolicy.ASSISTED.value,
        requires_credential=True,
        credential_present=False,
        cost_to_apply="Free - commission-free platform",
        rules_evidence=(
            "ToS 13.3(v) permits access only via 'software and/or search agents provided by Contra'. "
            "The official MCP server qualifies; a self-built crawler does not. ToS 14.3 caps "
            "outreach volume regardless of tooling."
        ),
        rules_url="https://contra.com/policies/terms",
        notes=(
            "Commission-free, so net revenue is the highest of any marketplace channel here. "
            "Opportunity board is at contra.com/independent/opportunities and requires a login to apply."
        ),
    )

    @classmethod
    def discover(cls, limit: int = 50) -> list[Any]:
        raise NotPermittedError(
            "Contra discovery runs through Contra's own MCP server, interactively, not through a "
            f"crawler written by this system (ToS 13.3(v)). See {MCP_MARKETING_PAGE}. "
            f"Browse manually at {OPPORTUNITIES_URL} and use manual import."
        )
