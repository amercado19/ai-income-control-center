"""Contra connector - back office only. It cannot find you work.

Verified September 2026 against https://contra.com/features/mcp and https://contra.com/policies/terms
(updated 9 April 2026), plus a live protocol probe of the MCP endpoint.

**The correction that matters.** An earlier reading of this project assumed Contra's official MCP
server could discover opportunities. It cannot. Contra's own capability description splits by role,
and the split is decisive:

    "If you're **hiring**: search talent by role, skills, expertise, and location, post jobs,
    review candidates, send inquiries or paid project proposals, and pay invoices.
    If you're **growing your independent business**: bring your own clients onto Contra, send
    proposals, productize your services, manage your portfolio and products, send invoices, and
    create payment links to get paid commission-free."

The only *search* verb on that list is **search talent** - discovery flows toward clients looking
for freelancers, not freelancers looking for work. Every independent-side example Contra gives is
bring-your-own-client: send *my client at Acme* a proposal, invoice *Acme*, add *my Acme case
study* to my portfolio.

Contra's web product does have a "Find jobs" page, but it is **not exposed through the MCP**.

So for this system's purpose - finding work - Contra is not a channel. It is a back office: a
commission-free way to send proposals, invoice, and get paid **once Andres already has the client**.
That is genuinely useful, and it is the best net economics of any platform here (the freelancer
keeps 100%), but it belongs at the *end* of the pipeline, not the start.

**Endpoint**, confirmed by protocol probe: ``https://contra.com/mcp`` answers 401 with
``WWW-Authenticate: Bearer realm="contra-mcp", scope="mcp:tools"``. OAuth 2.1 with dynamic client
registration and PKCE; authorization server ``https://contra.com/api``.

**Why our own crawler is not an option.** The Terms prohibit accessing the platform with

    "any engine, software, tool, agent, device or mechanism (including spiders, robots, crawlers,
    data mining tools or the like) **other than the software and/or search agents provided by
    Contra**"

That carve-out is what makes Contra's own MCP legitimate and a self-built scraper not. Note the
live Terms are unnumbered - cite the clause by its heading, "General prohibitions and Contra's
enforcement rights", rather than by a section number.

**Writes are interactive by contract**: *"Every action that changes something on your account goes
through a two-step prepare-and-confirm flow... If you don't confirm within 15 minutes, the draft
expires automatically."* Unattended submission is impossible by design, which happens to match the
Level 2 approval model exactly.

An outreach-volume cap also applies regardless of tooling ("Rate Limits and Automated Outreach"),
so this system never batch-sends.
"""

from __future__ import annotations

from typing import Any

from ..models import AutomationPolicy
from .base import Capabilities, Connector, NotPermittedError, register

MCP_ENDPOINT = "https://contra.com/mcp"
MCP_PAGE = "https://contra.com/features/mcp"
TERMS_URL = "https://contra.com/policies/terms"
OPPORTUNITIES_URL = "https://contra.com/independent/opportunities"

# What the MCP genuinely offers an independent. Discovery is conspicuously absent.
INDEPENDENT_CAPABILITIES = [
    "Bring an existing client onto Contra",
    "Send a proposal to a client you already have",
    "Productize a service as a fixed-price offering",
    "Manage portfolio and products",
    "Send invoices",
    "Create payment links (commission-free)",
    "Read and send messages",
]


@register
class ContraConnector(Connector):
    CAPS = Capabilities(
        name="contra",
        label="Contra (back office)",
        discovery=False,
        api=True,
        allowed_automated_fetch=False,
        auto_proposal=False,
        manual_approval_required=True,
        auto_delivery=False,
        automation_policy=AutomationPolicy.ASSISTED.value,
        requires_credential=True,
        credential_present=False,
        cost_to_apply="n/a - no opportunity discovery. Commission-free on work you bring.",
        rules_evidence=(
            "Contra's official MCP exposes 'search talent' to CLIENTS only; the independent-side "
            "capabilities are all bring-your-own-client (proposals, invoices, portfolio, payment "
            "links). The web 'Find jobs' page is not exposed through the MCP. The Terms permit "
            "automated access only via 'software and/or search agents provided by Contra', so a "
            "self-built crawler is prohibited. Writes use a prepare-and-confirm flow whose drafts "
            "expire in 15 minutes, so unattended submission is impossible."
        ),
        rules_url=TERMS_URL,
        notes=(
            "Commission-free, so the best net economics of any platform here - but it is a back "
            "office, not a lead source. Use it to invoice and get paid on work won elsewhere."
        ),
    )

    @classmethod
    def discover(cls, limit: int = 50) -> list[Any]:
        raise NotPermittedError(
            "Contra has no opportunity-discovery surface available to this system. Its official MCP "
            "server exposes talent search to CLIENTS; for an independent it offers proposals, "
            "invoices, portfolio and payment links on clients you already have. The web job board "
            f"at {OPPORTUNITIES_URL} is not exposed through the MCP, and the Terms permit automated "
            "access only through software Contra provides, so a crawler is not an option. "
            "Contra is a back office here, not a channel."
        )

    @classmethod
    def independent_capabilities(cls) -> list[str]:
        """What Contra can actually do for Andres, so the dashboard states it accurately."""
        return list(INDEPENDENT_CAPABILITIES)
