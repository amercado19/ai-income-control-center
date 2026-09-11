"""Fiverr launch kit (spec section 17).

Fiverr cannot be scanned - it has no discovery surface (see ``connectors/fiverr.py``). So the
only way it earns anything is as a storefront: publish gigs, and let Fiverr's matching engine
bring buyers. This module prepares those gigs. **Nothing here is published without approval.**

Everything below is grounded in market research done September 2026 against Fiverr's own category
pages, cost guides and help centre. The reasoning that shaped the kit:

* **Five candidates, four slots.** A new seller gets exactly four gig slots, and the brief asked
  for five candidates. Those are not in conflict: four go live, the fifth sits on the bench,
  fully written and priced and imaged. A gig with no impressions after six weeks should be
  replaced, and having the replacement already researched is the difference between swapping
  next Tuesday and swapping in two months.
* **The four live gigs do two jobs at once**: earn the first reviews, and sell the work that is
  actually worth doing. Three are priced for the work; one is priced to harvest reviews.
* **Data Engineering is the thinnest technical category on the platform** - roughly 1,500 gigs,
  against 64,000+ in Data Entry - and it carries the highest observed price floors ($50-500, with
  Top Rated sellers at $250). It is also the closest match to two production pipelines with CI/CD.
  That combination is the single best structural opportunity here.
* **Financial Modeling has ~1,600 gigs** and a modal price of $100. An MBA in financial
  technologies plus a research-grants finance role is a real differentiator in a category where
  most competitors are generic modellers.
* **Data Processing > Automations has the highest demand density measured** - about 5.7 category
  reviews per gig, against 3.8 for Data Entry.
* **Data Entry itself is skipped entirely.** 64,000+ gigs against a $20 commodity floor is not a
  market, it is a race.

Pricing accounts for Fiverr's **20% commission**: list price = desired net / 0.80. Tier ratios are
roughly 1 : 2.5 : 6, which is wider than the usual 1:2:3.5 because the premium tiers here include
genuinely more work (tests, documentation, scheduling) rather than just more volume.

Hard platform limits encoded below, all verified: title <= 80 characters, **5 tags maximum**,
description <= 1,200 characters, 3 packages, **at least 1 revision per package**, <= 10 FAQs,
<= 3 images at 1280x769. And one that bites: **the category cannot be changed after publishing**,
and the gig URL is locked from the first title saved.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from .config import PROFILE

# Platform constraints, verified from Fiverr's help centre.
MAX_TITLE_CHARS = 80
MAX_TAGS = 5
MAX_DESCRIPTION_CHARS = 1200
MAX_FAQS = 10
NEW_SELLER_GIG_SLOTS = 4
COMMISSION = 0.20


def list_price_for_net(net: float) -> float:
    """Fiverr takes 20%. To net $100 you must list at $125."""
    return round(net / (1 - COMMISSION), 2)


@dataclass
class Package:
    name: str
    price: float
    delivery_days: int
    revisions: int  # never "unlimited" on Basic - see the note in Gig.rationale
    includes: list[str] = field(default_factory=list)
    est_human_hours: float = 0.0
    """Operator clock time WITH AI assistance: scoping, review, testing, client comms, delivery.

    Not the calendar delivery window, which is a promise to the buyer rather than a cost - a
    2-day delivery on a 30-minute job is slack, not labour. And not the unassisted estimate
    either: compressing those hours is the entire premise of this system, so pricing against
    the unassisted number would understate every gig's real return.
    """
    est_ai_hours: float = 0.0
    """AI working time. Tracked separately and never collapsed into the human figure."""

    @property
    def net(self) -> float:
        return round(self.price * (1 - COMMISSION), 2)

    @property
    def implied_hourly(self) -> float | None:
        """Net divided by operator hours. ``None`` when no estimate was declared."""
        if self.est_human_hours <= 0:
            return None
        return round(self.net / self.est_human_hours, 2)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["net_after_commission"] = self.net
        d["implied_hourly"] = self.implied_hourly
        return d


@dataclass
class Gig:
    key: str
    title: str  # must start "I will", <= 80 chars
    category: str
    subcategory: str
    tags: list[str]  # <= 5
    description: str  # <= 1200 chars
    packages: list[Package]
    faqs: list[dict[str, str]]  # <= 10
    requirements: list[str]
    image_concept: str
    rationale: str
    status: str = "DRAFT"  # DRAFT | READY_TO_PUBLISH | PUBLISHED
    bench: bool = False
    """Prepared but not in the opening four.

    The brief asked for five candidates; Fiverr gives a new seller four slots. Those are not in
    conflict, and treating them as one was the earlier mistake: five candidates, four published,
    one ready to swap in. A gig that gets no impressions in six weeks should be replaced, and
    the replacement being already written - researched, priced, validated, with its image drawn -
    is the difference between swapping next Tuesday and swapping in two months."""
    below_floor_reason: str | None = None
    """Why this gig is allowed to price under the operator's own floor.

    ``None`` means it is not allowed to. A gig priced below ``PROFILE.minimum_hourly`` or
    ``PROFILE.minimum_job_value`` without a declared reason is a validation failure, not a
    judgement call - the point is that underpricing has to be a decision someone made on
    purpose and can see on the dashboard, rather than an accident discovered in the revenue
    figures three months later.
    """

    def validate(self) -> list[str]:
        """Every Fiverr constraint that would cause a rejection or a locked-in mistake."""
        problems: list[str] = []
        if len(self.title) > MAX_TITLE_CHARS:
            problems.append(f"Title is {len(self.title)} chars; Fiverr's limit is {MAX_TITLE_CHARS}.")
        if not self.title.lower().startswith("i will"):
            problems.append("Fiverr titles must begin 'I will'.")
        for bad in ("&", "/", '"', "+"):
            if bad in self.title:
                problems.append(f"Title contains {bad!r}, which Fiverr rejects in titles.")
        if len(self.tags) > MAX_TAGS:
            problems.append(f"{len(self.tags)} tags; the limit is {MAX_TAGS}.")
        if len(self.description) > MAX_DESCRIPTION_CHARS:
            problems.append(f"Description is {len(self.description)} chars; the limit is {MAX_DESCRIPTION_CHARS}.")
        if len(self.faqs) > MAX_FAQS:
            problems.append(f"{len(self.faqs)} FAQs; the limit is {MAX_FAQS}.")
        if len(self.packages) != 3:
            problems.append("Fiverr expects three packages.")
        for pkg in self.packages:
            if pkg.revisions < 1:
                problems.append(f"{pkg.name} has no revisions; Fiverr requires at least 1.")
            if pkg.price < 5:
                problems.append(f"{pkg.name} is below Fiverr's $5 minimum.")
        if not self.requirements:
            problems.append("No requirements questionnaire; an incomplete one blocks the order clock.")
        problems.extend(self._floor_problems())
        return problems

    def _floor_problems(self) -> list[str]:
        """The operator's own economics, not Fiverr's rules.

        Checked against every tier rather than only Basic: a Premium package that pays worse per
        hour than Basic is the classic scope-creep trap, where the buyer pays more and the seller
        earns less per hour for the privilege.
        """
        if self.below_floor_reason:
            return []
        out: list[str] = []
        for pkg in self.packages:
            if pkg.net < PROFILE.minimum_job_value:
                out.append(
                    f"{pkg.name} nets ${pkg.net:.2f}, under the ${PROFILE.minimum_job_value:.0f} minimum job value, and declares no reason."
                )
            hourly = pkg.implied_hourly
            if hourly is None:
                out.append(f"{pkg.name} declares no effort estimate, so its hourly return cannot be checked.")
            elif hourly < PROFILE.minimum_hourly:
                out.append(f"{pkg.name} implies ${hourly:.2f}/h, under the ${PROFILE.minimum_hourly:.0f} floor, and declares no reason.")
        return out

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["packages"] = [p.to_dict() for p in self.packages]
        d["validation"] = self.validate()
        d["valid"] = not d["validation"]
        d["title_chars"] = len(self.title)
        d["description_chars"] = len(self.description)
        d["image_path"] = self.image_path()
        return d

    def image_path(self) -> str:
        """Where the rendered gig image lives, if it has been generated.

        Returns "" rather than a broken path when it has not, because a gig published without
        an image is a real gap the dashboard should show rather than paper over.
        """
        from pathlib import Path

        candidate = Path("portfolio/gig_images") / f"{self.key}.png"
        return str(candidate) if candidate.exists() else ""


# ---------------------------------------------------------------------------
# The four gigs
# ---------------------------------------------------------------------------

GIGS: list[Gig] = [
    Gig(
        key="data_engineering",
        title="I will build a python data pipeline with tests and scheduling",
        category="Data",
        subcategory="Data Engineering > Data ETLs",
        tags=["data pipeline", "python etl", "data engineering", "airflow", "automation"],
        description=(
            "Your data arrives in files, APIs and exports that do not agree with each other, and "
            "somebody is stitching it together by hand every month.\n\n"
            "I build that as a pipeline instead: a repeatable job that ingests your sources, "
            "normalises them to one schema, and produces the output you actually need - on a "
            "schedule, without anyone babysitting it.\n\n"
            "How I work:\n"
            "- Every source row is accounted for. Rows that fail to map are FLAGGED, never "
            "silently dropped. You get a reconciliation showing counts before and after.\n"
            "- Failure cases handled first: retries on transient errors, and an alert when a "
            "source is unavailable rather than a quietly empty report.\n"
            "- Credentials live in environment secrets, never in the code.\n"
            "- Tests included, so a provider changing their format is caught rather than "
            "discovered three weeks later.\n\n"
            "I run two production data pipelines of my own on this exact stack, with CI, type "
            "checking and automated deployment. I am happy to walk you through the code.\n\n"
            "Message me with your sources and what the output needs to look like, and I will tell "
            "you honestly whether this is a fit before you order."
        ),
        packages=[
            Package(
                "Basic",
                125.0,
                5,
                1,
                [
                    "One data source to one clean output",
                    "Normalisation and deduplication",
                    "Reconciliation report (row counts before and after)",
                    "Documented Python script you own",
                ],
                est_human_hours=1.25,
                est_ai_hours=2.5,
            ),
            Package(
                "Standard",
                375.0,
                10,
                2,
                [
                    "Up to three sources consolidated",
                    "Scheduled execution (GitHub Actions or cron)",
                    "Retry and failure alerting",
                    "Unit tests",
                    "Setup documentation",
                ],
                est_human_hours=3.5,
                est_ai_hours=7.0,
            ),
            Package(
                "Premium",
                875.0,
                18,
                3,
                [
                    "Up to six sources, including REST APIs",
                    "Full scheduling with monitoring and health checks",
                    "Data-quality checks with an exceptions report",
                    "Test suite plus CI configuration",
                    "Handover session and runbook",
                ],
                est_human_hours=8.0,
                est_ai_hours=16.0,
            ),
        ],
        faqs=[
            {
                "q": "What data sources can you work with?",
                "a": "CSV, Excel, JSON, XML, SQL databases (Postgres, MySQL, SQLite) and REST APIs. If "
                "yours is something else, message me first and I will tell you honestly.",
            },
            {
                "q": "Will I be able to run it myself afterwards?",
                "a": "Yes. You get the source code and setup documentation, and the Premium package "
                "includes a handover session. The point is that you are not dependent on me.",
            },
            {
                "q": "What happens to rows that do not fit the schema?",
                "a": "They are flagged with a reason, never dropped. Silent row loss is the most "
                "common and most damaging defect in this kind of work, so I reconcile counts "
                "before and after and show you the difference.",
            },
            {
                "q": "Do you need access to our production systems?",
                "a": "No, and I would rather not have it. Exports or a read-only credential are enough for almost everything.",
            },
            {
                "q": "Can it run on a schedule without me doing anything?",
                "a": "Yes, from the Standard package up. I typically use GitHub Actions, which is "
                "free for public repositories and cheap otherwise, so there is no new "
                "subscription to buy.",
            },
            {
                "q": "What if a source changes format after delivery?",
                "a": "The tests will catch it. Within the revision window I will fix it; beyond that "
                "I am happy to quote a small maintenance job.",
            },
        ],
        requirements=[
            "Sample files or API documentation for each source (anonymised is fine)",
            "What the output should look like - a sample or a description of the columns you need",
            "Where it should run: your machine, your cloud, or a scheduled job",
            "How often it needs to refresh",
            "Any rows or records that must never be dropped or merged",
        ],
        image_concept=(
            "Three mismatched spreadsheet fragments on the left with clashing column headers, an "
            "arrow through a labelled 'normalise + reconcile' block, and one clean table on the "
            "right with a small green 'row counts match' badge. Dark background, monospace "
            "column labels. The badge is the whole proposition: nothing was lost."
        ),
        rationale=(
            "Thinnest technical category on Fiverr (~1,500 gigs vs 64,000+ in Data Entry) and the "
            "highest observed price floors. Directly matched by two production pipelines with CI. "
            "Basic at $125 nets $100 after the 20% commission."
        ),
    ),
    Gig(
        key="financial_model",
        title="I will build an auditable financial model in excel",
        category="Finance",
        subcategory="Financial Planning and Analysis > Financial Modeling",
        tags=["financial model", "excel model", "forecasting", "budget model", "cash flow"],
        description=(
            "Most financial models fail the same way: six months later nobody can tell where a "
            "number came from, and nobody trusts it enough to act on it.\n\n"
            "I build models that stay auditable. Assumptions live on their own tab so you can "
            "change them without touching the logic. Every output traces back to its inputs. "
            "Reconciliation checks sit in the workbook, so an error shows up immediately instead "
            "of propagating quietly through the forecast.\n\n"
            "What you get:\n"
            "- A clearly separated Assumptions tab - change a driver, watch the model respond\n"
            "- Formula structure documented, not a wall of nested IFs\n"
            "- Built-in checks that flag when something does not reconcile\n"
            "- A short walkthrough of how it works, so it is yours to run\n\n"
            "Background: I manage research grant and sponsored-programme finance at a major "
            "academic medical centre, and hold an MBA in financial technologies. I spend my "
            "working life on budgets that have to survive an audit.\n\n"
            "Tell me what decision the model needs to support and I will tell you what it needs to "
            "contain."
        ),
        packages=[
            Package(
                "Basic",
                95.0,
                5,
                1,
                [
                    "Single-scenario model, up to 3 years",
                    "Separated assumptions tab",
                    "Built-in reconciliation checks",
                    "Clean formatting",
                ],
                est_human_hours=1.0,
                est_ai_hours=2.0,
            ),
            Package(
                "Standard",
                250.0,
                9,
                2,
                [
                    "Up to 5 years with multiple scenarios",
                    "Sensitivity analysis on key drivers",
                    "Summary dashboard tab",
                    "Formula documentation",
                ],
                est_human_hours=2.5,
                est_ai_hours=4.0,
            ),
            Package(
                "Premium",
                625.0,
                16,
                3,
                [
                    "Full three-statement model",
                    "Scenario and sensitivity analysis",
                    "Charts and an executive summary tab",
                    "Walkthrough session",
                    "30 days of follow-up questions",
                ],
                est_human_hours=6.0,
                est_ai_hours=8.0,
            ),
        ],
        faqs=[
            {
                "q": "What do you need from me to start?",
                "a": "Historical figures if you have them, your key assumptions, and - most "
                "importantly - what decision the model is meant to support. That last one "
                "shapes everything else.",
            },
            {"q": "Excel or Google Sheets?", "a": "Either. Excel by default; say the word and I will deliver in Sheets."},
            {
                "q": "Can I change the assumptions myself later?",
                "a": "That is the whole design. Assumptions sit on their own tab, separate from the "
                "logic, so you can change drivers without risk of breaking formulas.",
            },
            {
                "q": "Do you handle grant or non-profit budgets?",
                "a": "Yes - that is my day job. Sponsored programmes, award budgets, burn rate and "
                "projected end-of-award balances are familiar territory.",
            },
            {
                "q": "Will you tell me if my assumptions look wrong?",
                "a": "Yes. I am not a financial adviser and will not tell you what to decide, but if "
                "an assumption looks internally inconsistent I will flag it rather than model it "
                "silently.",
            },
        ],
        requirements=[
            "What decision this model needs to support",
            "Historical figures, if any (anonymised is fine)",
            "Your key assumptions - growth, costs, timing",
            "Time horizon and the level of detail you need (monthly, quarterly, annual)",
            "Excel or Google Sheets",
        ],
        image_concept=(
            "A workbook tab bar where 'Assumptions' is highlighted and separated from 'Model' and "
            "'Summary', with a thin arrow showing one assumption cell driving three downstream "
            "figures, and a small green check labelled 'reconciles'. The tab separation is the "
            "selling point - it says maintainable at a glance."
        ),
        rationale=(
            "~1,600 gigs, modal price $100. The MBA in financial technologies plus a research "
            "finance role is a genuine credential differentiator in a category of generalists. "
            "Basic at $95 nets $76."
        ),
    ),
    Gig(
        key="scheduled_automation",
        title="I will automate your recurring report to run on a schedule",
        category="Data",
        subcategory="Data Processing > Automations",
        tags=["automation", "python script", "scheduled report", "api integration", "workflow"],
        description=(
            "If someone on your team rebuilds the same report every week, that is a script that "
            "has not been written yet.\n\n"
            "I automate the whole loop: pull the data, apply your logic, produce the output, and "
            "run it on a schedule so it happens whether anyone remembers or not.\n\n"
            "Typical jobs:\n"
            "- Weekly or monthly reports assembled from several sources\n"
            "- Data pulled from an API and written to a sheet or database\n"
            "- Files fetched, processed and filed automatically\n"
            "- Alerts when a number crosses a threshold\n\n"
            "Built to fail loudly, not silently. If a source is unavailable you get told, rather "
            "than receiving an empty report that looks fine. Credentials stay in secrets.\n\n"
            "I run scheduled jobs of my own that have executed unattended for months, with health "
            "checks and alerting. Same approach here.\n\n"
            "Tell me what you rebuild by hand and how often, and I will tell you what it takes to "
            "stop doing that."
        ),
        packages=[
            Package(
                "Basic",
                75.0,
                4,
                1,
                [
                    "One automation script",
                    "Runs on your machine or a free scheduler",
                    "Documented and yours to keep",
                ],
                est_human_hours=0.75,
                est_ai_hours=1.5,
            ),
            Package(
                "Standard",
                190.0,
                7,
                2,
                [
                    "Multi-step automation with up to three sources",
                    "Scheduled cloud execution, no machine left on",
                    "Failure alerting",
                    "Setup documentation",
                ],
                est_human_hours=2.0,
                est_ai_hours=4.0,
            ),
            Package(
                "Premium",
                440.0,
                14,
                3,
                [
                    "Full workflow with API integrations",
                    "Scheduling, monitoring and health checks",
                    "Formatted output (Excel, PDF or dashboard)",
                    "Tests and a runbook",
                    "Handover session",
                ],
                est_human_hours=4.5,
                est_ai_hours=9.0,
            ),
        ],
        faqs=[
            {
                "q": "Where does the automation run?",
                "a": "From Standard up, in the cloud on a schedule - usually GitHub Actions, which "
                "costs nothing for this kind of job. Your computer does not need to be on.",
            },
            {
                "q": "What if it breaks while I am not looking?",
                "a": "You get an alert. The one thing I will not build is something that fails "
                "quietly and hands you an empty report that looks correct.",
            },
            {
                "q": "Do you need my passwords?",
                "a": "No. API keys or read-only credentials go into a secrets store that only the "
                "job can read, and I never need your account password.",
            },
            {
                "q": "Can you work with our internal tools?",
                "a": "If it has an API or can export files, almost certainly. Message me with the "
                "details and I will tell you honestly before you order.",
            },
            {
                "q": "What if my requirements change later?",
                "a": "The code is yours and documented, so you or anyone else can extend it. I am also happy to quote small changes.",
            },
        ],
        requirements=[
            "What you currently do by hand, step by step",
            "How often it needs to run, and by when",
            "Where the data comes from (sources, logins, file locations)",
            "What the finished output should look like",
            "Who should be alerted if it fails",
        ],
        image_concept=(
            "A simple loop: a clock icon, an arrow into three stacked source blocks, into a "
            "'process' block, out to a single report, and an arrow back to the clock. One small "
            "amber node off the loop labelled 'alerts you if a source is down'. The failure path "
            "being visible is the differentiator."
        ),
        rationale=(
            "Highest demand density measured on Fiverr - about 5.7 category reviews per gig, "
            "against 3.8 for Data Entry - at ~2,800 gigs. Basic at $75 nets $60."
        ),
    ),
    Gig(
        key="spreadsheet_cleanup",
        title="I will clean and consolidate your messy excel or csv data",
        category="Data",
        subcategory="Data Formatting",
        tags=["excel cleanup", "data cleaning", "csv", "spreadsheet", "data formatting"],
        description=(
            "Mismatched column names, duplicates, inconsistent dates, and a merged header row "
            "somebody added in 2023. I turn that into one clean dataset you can actually use.\n\n"
            "What I do:\n"
            "- Consolidate multiple files into one, mapping the column names that do not agree\n"
            "- Remove genuine duplicates, standardise dates, numbers and text\n"
            "- Flag rows that look wrong instead of deleting them\n\n"
            "The part that matters: I reconcile row counts before and after and show you the "
            "difference. If anything was removed, you see exactly what and why. Silent row loss is "
            "the most common way this work goes wrong and you should never have to take it on "
            "trust.\n\n"
            "From the Standard package I also give you the script, so you can re-run the same "
            "cleanup next quarter without hiring anyone.\n\n"
            "Send a sample and I will tell you what is actually wrong with it before you order."
        ),
        packages=[
            Package(
                "Basic",
                30.0,
                2,
                1,
                [
                    "Up to 1,000 rows, one file",
                    "Deduplication and standardised formatting",
                    "Before and after row-count reconciliation",
                ],
                est_human_hours=0.5,
                est_ai_hours=0.75,
            ),
            Package(
                "Standard",
                75.0,
                3,
                2,
                [
                    "Up to 10,000 rows across up to 5 files",
                    "Consolidated into one clean output",
                    "Column mapping documented",
                    "The reusable Python script",
                ],
                est_human_hours=1.25,
                est_ai_hours=2.0,
            ),
            Package(
                "Premium",
                150.0,
                5,
                3,
                [
                    "Unlimited rows, up to 15 files",
                    "Full data-quality report with an exceptions list",
                    "Reusable script plus documentation",
                    "Summary tab or pivot-ready structure",
                ],
                est_human_hours=2.5,
                est_ai_hours=4.0,
            ),
        ],
        faqs=[
            {"q": "What formats do you accept?", "a": "Excel (.xlsx, .xls), CSV, TSV and Google Sheets."},
            {
                "q": "Will any of my data be deleted?",
                "a": "Not without telling you. Rows that fail validation are flagged with a reason, "
                "and you get a count reconciliation showing exactly what changed.",
            },
            {
                "q": "Can I re-run this myself next time?",
                "a": "Yes, from the Standard package - you get the script and a short note on how to run it.",
            },
            {
                "q": "Is my data kept confidential?",
                "a": "Yes. I work only with what you send, I do not share it, and I delete it on request once the order is complete.",
            },
            {
                "q": "What if my file is bigger than the package allows?",
                "a": "Message me before ordering and I will quote it properly rather than have you buy the wrong package.",
            },
        ],
        requirements=[
            "Your file or files (a sample is enough to start)",
            "What the clean output should look like",
            "Which columns matter most and which can be dropped",
            "How to decide when two rows are the same record",
            "Anything that must never be removed",
        ],
        image_concept=(
            "Split frame. Left: a spreadsheet with visibly ragged rows, three different date "
            "formats and a merged header, tinted red. Right: the same data clean and aligned, "
            "tinted green, with a small badge reading '1,000 rows in - 1,000 rows out'. That badge "
            "is the promise the competition does not make."
        ),
        rationale=(
            "The review-harvesting gig. ~3,300 gigs with $15-25 floors, priced slightly above the "
            "floor at $30 because the reconciliation guarantee is a real differentiator. Low "
            "ticket, fast delivery, and the quickest route to the 5 orders and 3 unique clients "
            "that Level 1 requires. Basic at $30 nets $24."
        ),
        below_floor_reason=(
            "DELIBERATE. Every tier here returns about $48/h against a $50 floor, and Basic nets "
            "$24 against a $50 minimum job value. This is the only gig in the kit that does not "
            "clear the floor, and it is not a pricing mistake: Fiverr's ranking is review-gated, "
            "so a new seller with zero reviews is invisible in the categories that do pay. This "
            "gig buys the first reviews at a small, bounded, known loss against the floor - "
            "roughly $2/h on jobs of half an hour to two and a half hours. RETIRE IT once Level 1 "
            "is reached (5 orders, 3 unique clients) or raise Basic to $65, which nets $52 and "
            "clears both floors. Revisit at Level 1 - it should not outlive its purpose."
        ),
    ),
    Gig(
        key="pdf_extraction",
        bench=True,
        title="I will extract data from your pdf invoices or reports into clean csv",
        category="Data",
        subcategory="Data Processing > Data Extraction",
        tags=["pdf to excel", "data extraction", "invoice processing", "ocr", "document parsing"],
        description=(
            "Hundreds of PDFs that somebody is retyping into a spreadsheet, line by line, "
            "getting slower and less accurate as they go.\n\n"
            "I extract them properly: a repeatable process that reads your documents, pulls the "
            "fields you actually need, and hands you one clean CSV or workbook.\n\n"
            "The part that matters, and the part this category usually gets wrong:\n"
            "- Every document is accounted for. A page the extraction could not read confidently "
            "is FLAGGED for you to key by hand - never guessed at, never silently skipped.\n"
            "- You get a confidence report: how many documents parsed cleanly, how many need a "
            "human, and exactly which ones.\n"
            "- Totals are checked against the line items, so a misread decimal shows up as a "
            "reconciliation failure rather than a wrong number in your accounts.\n\n"
            "Scanned documents are handled too, with OCR - though scan quality drives accuracy, "
            "and I will tell you honestly what to expect from a sample before you order.\n\n"
            "Send me two or three representative PDFs and I will tell you what is realistically "
            "extractable before you spend anything."
        ),
        packages=[
            Package(
                "Basic",
                95.0,
                3,
                1,
                [
                    "Up to 25 documents, one consistent layout",
                    "Fields you specify, extracted to CSV or Excel",
                    "Low-confidence pages flagged, never guessed",
                    "Confidence report: what parsed, what needs you",
                ],
                est_human_hours=1.0,
                est_ai_hours=2.0,
            ),
            Package(
                "Standard",
                325.0,
                6,
                2,
                [
                    "Up to 150 documents, up to three layouts",
                    "Line-item extraction with totals reconciled against the document",
                    "OCR for scanned pages",
                    "The reusable script, so next quarter costs you nothing",
                    "Exceptions list with page references",
                ],
                est_human_hours=3.5,
                est_ai_hours=7.0,
            ),
            Package(
                "Premium",
                695.0,
                12,
                3,
                [
                    "Unlimited documents, mixed and irregular layouts",
                    "Full validation rules and a data-quality report",
                    "Scheduled or watched-folder processing",
                    "Reusable pipeline with tests and documentation",
                    "Handover session",
                ],
                est_human_hours=7.5,
                est_ai_hours=16.0,
            ),
        ],
        faqs=[
            {
                "q": "Can you handle scanned PDFs, not just digital ones?",
                "a": "Yes, with OCR from the Standard package. Scan quality drives accuracy, so send a sample first and I will tell you honestly what to expect rather than promising a number I cannot hit.",
            },
            {
                "q": "What happens to a document it cannot read?",
                "a": "It is flagged with the page reference so you can key that one by hand. It is never guessed at and never silently dropped - a plausible wrong number is far more expensive than a blank you know about.",
            },
            {
                "q": "Do my documents stay confidential?",
                "a": "Yes. I work only with what you send, I do not share it, and I delete it on request once the order is complete. If your documents are sensitive, send redacted samples for the quote.",
            },
            {
                "q": "Will the totals be right?",
                "a": "Line items are reconciled against the document total, so a misread decimal surfaces as a reconciliation failure rather than as a wrong figure in your accounts. That check is the main reason to use this gig rather than a generic converter.",
            },
            {
                "q": "Can I re-run it on next quarter's documents myself?",
                "a": "Yes, from the Standard package - you get the script and a short note on running it.",
            },
            {
                "q": "What if my layouts are all different?",
                "a": "Up to three layouts on Standard, irregular and mixed on Premium. Message me with samples and I will quote it properly rather than have you buy the wrong package.",
            },
        ],
        requirements=[
            "Two or three representative PDFs (redacted is fine)",
            "The exact fields you need pulled out, and what to call them",
            "CSV or Excel, and whether one row per document or one row per line item",
            "Roughly how many documents, and how often this repeats",
            "What should happen to a page that cannot be read confidently",
        ],
        image_concept=(
            "Left: a stack of three PDF pages, the top one an invoice with a few line items, one "
            "of them tinted amber. Arrow right into a clean CSV grid where those rows appear as "
            "data, with the amber row carried through as a highlighted 'needs review' entry. "
            "Below the grid, a small green badge: '148 of 150 parsed - 2 flagged for you'. The "
            "flagged pair is the whole point: this gig advertises the exceptions rather than "
            "hiding them, which is the opposite of every competitor in the category."
        ),
        rationale=(
            "THE BENCH CANDIDATE - prepared, validated and imaged, but not in the opening four, "
            "because Fiverr gives a new seller exactly four slots. Swap it in for whichever gig "
            "has no impressions after six weeks. It is fifth rather than absent because it is "
            "the strongest of the remaining priorities: it matches document-heavy finance work "
            "directly, it is the one category where the honest handling of failure (flagging "
            "unreadable pages instead of guessing) is a visible differentiator buyers have been "
            "burned on, and at $95 Basic it nets $76 for about an hour. Ahead of a dashboards or "
            "GitHub-automation gig, which are both thinner markets on Fiverr and harder to scope "
            "into fixed packages."
        ),
    ),
]


def all_gigs() -> list[Gig]:
    return list(GIGS)


def validate_all() -> dict[str, list[str]]:
    """Every constraint violation across the kit, keyed by gig."""
    return {g.key: g.validate() for g in GIGS}


def summary() -> dict[str, Any]:
    """What the dashboard's FIVERR LAUNCH CENTER renders."""
    gigs = [g.to_dict() for g in GIGS]
    return {
        "gigs": gigs,
        "slots_used": sum(1 for g in GIGS if not g.bench),
        "slots_available": NEW_SELLER_GIG_SLOTS,
        "bench": [{"key": g.key, "title": g.title, "rationale": g.rationale} for g in GIGS if g.bench],
        "all_valid": all(g["valid"] for g in gigs),
        "commission": COMMISSION,
        "total_basic_net": round(sum(g["packages"][0]["net_after_commission"] for g in gigs), 2),
        "images_ready": sum(1 for g in GIGS if g.image_path()),
        "floor_hourly": PROFILE.minimum_hourly,
        "target_hourly": PROFILE.target_hourly,
        "below_floor": [{"key": g.key, "reason": g.below_floor_reason} for g in GIGS if g.below_floor_reason],
        "publishing_note": (
            "Nothing here is published automatically. Fiverr has no seller API, so publishing is "
            "manual by necessity as well as by policy - and the category cannot be changed after "
            "publishing, so check it before you save."
        ),
        "ai_disclosure_note": (
            "Fiverr has no blanket AI-disclosure requirement. Disclosure becomes mandatory only "
            "when a buyer explicitly asks for non-AI work, and that request must be honoured."
        ),
    }
