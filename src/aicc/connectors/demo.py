"""Demo mode data (spec section 42).

Realistic synthetic opportunities so every dashboard workflow can be exercised without touching
a real marketplace. Three guarantees:

* Every record carries ``is_demo=True``, and REAL REVENUE figures structurally exclude them.
* Titles are prefixed ``[DEMO]`` so a screenshot can never be mistaken for live data.
* The set deliberately spans the score bands and includes opportunities that must be REJECTED
  (AI-prohibited, academic dishonesty, scam) so the rejection paths are exercised, not just the
  happy path.

The data is synthetic but the *shape* is drawn from listings observed on real sources in
September 2026, so scoring behaves the way it will in production.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from ..models import AutomationPolicy, BudgetType, OpportunityStatus
from .base import Capabilities, Connector, extract_skills, make_opportunity, register


def _ago(hours: int) -> str:
    return (datetime.now(UTC) - timedelta(hours=hours)).isoformat(timespec="seconds")


DEMO_LISTINGS: list[dict[str, Any]] = [
    {
        "title": "Clean and consolidate 14 monthly sales spreadsheets into one reporting workbook",
        "client": "Brightline Retail Group",
        "budget_min": 400.0,
        "budget_max": 600.0,
        "budget_type": BudgetType.FIXED.value,
        "hours_ago": 3,
        "description": (
            "We have 14 monthly Excel exports from our POS system, one per month, each with slightly "
            "different column names and some merged header rows. We need them consolidated into a single "
            "clean workbook with a normalized schema, plus a summary tab with month-over-month revenue by "
            "store and by category. Deliverables: one .xlsx with a Data tab, a Summary tab with pivot-ready "
            "structure, and a short README describing the column mapping you used. Requirements: preserve "
            "every original row (we will check row counts), flag any rows that failed to map rather than "
            "dropping them, and keep currency formatting consistent. We would also like the mapping logic "
            "as a Python script so we can re-run it next quarter ourselves. Scope is fixed; timeline is "
            "one week. Please tell us how you would handle the duplicate order IDs we know exist in Q3."
        ),
    },
    {
        "title": "Build a scheduled Python pipeline to pull our API data into a daily report",
        "client": "Northwind Logistics",
        "budget_min": 1200.0,
        "budget_max": 2000.0,
        "budget_type": BudgetType.FIXED.value,
        "hours_ago": 9,
        "description": (
            "We need a scheduled job that calls three REST APIs (our TMS, a weather API, and a fuel price "
            "feed), normalizes the responses, stores them, and emails a daily operations summary as a PDF. "
            "Must run unattended on a schedule - we do not want to babysit it. Requirements: retry on "
            "transient failures, alert us when a source is unavailable rather than silently producing an "
            "empty report, and keep credentials out of the codebase. Deliverables: the pipeline code with "
            "tests, a deployment guide, and the scheduled job configured and demonstrably running. We use "
            "GitHub already so GitHub Actions is fine. Acceptance criteria: seven consecutive successful "
            "scheduled runs and a correct report on each. Milestone-based payment is fine."
        ),
    },
    {
        "title": "Extract line items from 300 scanned supplier invoices into CSV",
        "client": "Kestrel Manufacturing",
        "budget_min": 250.0,
        "budget_max": 250.0,
        "budget_type": BudgetType.FIXED.value,
        "hours_ago": 20,
        "description": (
            "We have roughly 300 scanned PDF invoices from suppliers. We need supplier name, invoice number, "
            "invoice date, each line item with quantity and unit price, and the invoice total, extracted into "
            "a CSV. Scans are decent quality but not perfect. Deliverable is the CSV plus a list of any "
            "invoices the extraction could not read confidently, so we can key those manually. Accuracy on "
            "totals matters more than speed - we will spot check 30 of them against the originals."
        ),
    },
    {
        "title": "Weekly competitor pricing research compiled into a tracker",
        "client": "Lumen Home Goods",
        "budget_min": 55.0,
        "budget_max": 75.0,
        "budget_type": BudgetType.HOURLY.value,
        "hours_ago": 30,
        "description": (
            "Ongoing part-time contract. Each week, check publicly listed prices for about 40 SKUs across six "
            "competitor websites and record them in a shared tracker with the date and a source link for each "
            "figure. We need the sources cited so we can verify. Roughly 4-6 hours a week. Please only use "
            "publicly available information; do not create accounts or bypass anything to get pricing."
        ),
    },
    {
        "title": "Small Python script to rename and sort files",
        "client": "Anonymous",
        "budget_min": 15.0,
        "budget_max": 15.0,
        "budget_type": BudgetType.FIXED.value,
        "hours_ago": 5,
        "description": "Need a script to rename files. Simple job. Quick turnaround.",
    },
    {
        "title": "Write my graduate finance coursework - 4000 words",
        "client": "Private Client",
        "budget_min": 300.0,
        "budget_max": 300.0,
        "budget_type": BudgetType.FIXED.value,
        "hours_ago": 2,
        "description": (
            "I need someone to write my assignment for my graduate finance module. It is 4000 words on "
            "capital structure and is due Friday. Must be original and pass the plagiarism checker. "
            "This is for my coursework so it needs to be in my voice."
        ),
    },
    {
        "title": "Blog content writing - human written only, no AI",
        "client": "Vertex Media",
        "budget_min": 400.0,
        "budget_max": 400.0,
        "budget_type": BudgetType.FIXED.value,
        "hours_ago": 12,
        "description": (
            "Looking for a writer for 10 blog posts about supply chain software. Must be 100% human written - "
            "we run every submission through AI detection and will reject anything flagged. No ChatGPT, no AI "
            "assistance of any kind. Please confirm you understand before applying."
        ),
    },
    {
        "title": "Urgent - payment processing assistant needed, start today",
        "client": "Global Ventures LLC",
        "budget_min": 2500.0,
        "budget_max": 4000.0,
        "budget_type": BudgetType.FIXED.value,
        "hours_ago": 1,
        "description": (
            "We need someone to help process payments for us. You will receive funds into your account and "
            "forward them to our suppliers, keeping 10%. Must be available today. We communicate on Telegram "
            "only. Please send a small deposit to verify your account is active and we will begin immediately."
        ),
    },
    {
        "title": "Build an internal dashboard for our grant spending",
        "client": "Meridian Research Institute",
        "budget_min": 2000.0,
        "budget_max": 3500.0,
        "budget_type": BudgetType.FIXED.value,
        "hours_ago": 40,
        "description": (
            "Non-profit research institute. We track sponsored program spending across about 60 active awards "
            "in spreadsheets and it has stopped scaling. We want a dashboard showing burn rate per award, "
            "projected end-of-award balance, and a flag for awards trending to underspend or overspend. Data "
            "comes from a monthly export from our finance system as CSV. Deliverables: the dashboard, the "
            "ingestion script for the monthly export, documentation, and a handover session. Requirements: it "
            "must run somewhere we control, must not require a paid subscription, and the calculations must be "
            "auditable - our finance director needs to see how each number was derived. Timeline is flexible; "
            "we care more about getting the burn-rate logic right than about speed."
        ),
    },
]


@register
class DemoConnector(Connector):
    CAPS = Capabilities(
        name="demo",
        label="Demo Mode",
        discovery=True,
        api=False,
        allowed_automated_fetch=True,
        auto_proposal=True,
        manual_approval_required=True,
        automation_policy=AutomationPolicy.DEMO.value,
        cost_to_apply="Free - synthetic",
        rules_evidence="Synthetic data generated locally. No external system is contacted.",
        notes="Every record is labelled [DEMO] and excluded from real revenue figures.",
    )

    @classmethod
    def discover(cls, limit: int = 50) -> list[Any]:
        out = []
        for idx, spec in enumerate(DEMO_LISTINGS[:limit]):
            text = f"{spec['title']} {spec['description']}"
            out.append(
                make_opportunity(
                    source="demo",
                    external_id=f"demo_{idx:03d}",
                    title=f"[DEMO] {spec['title']}",
                    description=spec["description"],
                    client=spec["client"],
                    url=f"https://example.invalid/demo/{idx:03d}",
                    budget_min=spec.get("budget_min"),
                    budget_max=spec.get("budget_max"),
                    budget_type=spec.get("budget_type", BudgetType.UNKNOWN.value),
                    posted_time=_ago(spec.get("hours_ago", 1)),
                    skills=extract_skills(text),
                    automation_policy=AutomationPolicy.DEMO.value,
                    status=OpportunityStatus.NEW.value,
                    is_demo=True,
                )
            )
        return out
