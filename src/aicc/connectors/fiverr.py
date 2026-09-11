"""Fiverr connector - INBOUND ONLY. There is nothing to discover.

This is the most important honesty statement in the codebase, so it is stated plainly:
**Fiverr has no discovery surface of any kind.** Not a restricted one. None.

Verified September 2026:

* "Buyer Requests" no longer exists. It was replaced by **Briefs**, which Fiverr's matching
  engine *pushes* to sellers based on service success score. There is no browsable board of
  buyer-posted jobs, so there is no page to poll even by hand.
* No seller-side API exists. The only Fiverr API on their partnerships page is for companies
  embedding Fiverr talent in their own products, and it is marked COMING SOON.
* Fiverr's Community Standards prohibit "attempts to access the platform through unauthorized
  methods ... scrape data from the platform", and robots.txt disallows /search/, /users/,
  /inbox/, /orders/timeline/* and every other path automation would want.
* The direction of travel is against automation: Fiverr is shutting down its own Fiverr Go
  Personal Assistant for everyone on **1 October 2026**.

So Fiverr is a storefront, not a feed. The compliant workflow is:

1. Andres publishes gigs (the launch kit in ``aicc.fiverr_kit`` prepares them; nothing is
   published without his approval).
2. Buyers order. Fiverr sends an order notification by email and push - the only permitted
   notification channels it offers.
3. Those notification emails, in Andres's own mailbox, can be parsed into this dashboard. That
   touches no Fiverr system and violates nothing, because it is his own mail.

``import_order`` below is that path. It takes an already-received notification and creates a Job.
"""

from __future__ import annotations

from typing import Any

from ..models import AutomationPolicy, Job, JobStatus
from .base import Capabilities, Connector, NotPermittedError, register

SELLER_FEE_RATE = 0.20


@register
class FiverrConnector(Connector):
    CAPS = Capabilities(
        name="fiverr",
        label="Fiverr",
        discovery=False,
        api=False,
        allowed_automated_fetch=False,
        auto_proposal=False,
        manual_approval_required=True,
        auto_delivery=False,
        automation_policy=AutomationPolicy.INBOUND_ONLY.value,
        requires_credential=True,
        credential_present=False,
        cost_to_apply="n/a - inbound only. Seller keeps 80% of each order.",
        rules_evidence=(
            "Buyer Requests removed; replaced by push-only Briefs with no browsable board. No "
            "seller API. Community Standards prohibit scraping. robots.txt disallows /search/, "
            "/users/, /inbox/ and /orders/timeline/*. Fiverr Go Personal Assistant shuts down "
            "1 October 2026."
        ),
        rules_url="https://help.fiverr.com/hc/en-us/articles/32242973123985-Our-Community-Standards",
        notes=(
            "AI-assisted delivery is allowed and there is no blanket disclosure requirement, but a "
            "client's explicit request for non-AI work must be honoured."
        ),
    )

    @classmethod
    def discover(cls, limit: int = 50) -> list[Any]:
        raise NotPermittedError(
            "Fiverr has no discovery surface. Buyer Requests was removed and replaced by push-only "
            "Briefs; no seller API exists; scraping is prohibited by Community Standards and "
            "robots.txt. Fiverr is inbound only - orders arrive by email and push notification."
        )

    @classmethod
    def import_order(
        cls,
        *,
        order_id: str,
        buyer: str,
        gig_title: str,
        price: float,
        requirements: list[str],
        deadline: str = "",
        job_type: str = "generic",
    ) -> Job:
        """Create a Job from an order notification Andres already received.

        This is a manual/assisted import, not a fetch. Nothing here contacts Fiverr.
        """
        return Job(
            source="fiverr",
            client=buyer,
            title=gig_title,
            job_type=job_type,
            agreed_price=price,
            deadline=deadline,
            requirements=requirements,
            acceptance_criteria=requirements,
            status=JobStatus.RECEIVED.value,
            opportunity_id=f"fiverr_order_{order_id}",
        )
