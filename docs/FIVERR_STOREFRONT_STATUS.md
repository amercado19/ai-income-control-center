# Fiverr storefront — live

**Updated:** 2026-09-14 · **Additional monthly cost: $0.00** · Seller: `amercado19`

## Where it stands

Three gigs are **LIVE**. Fiverr approved the W-9 at 14:35:27 UTC on 2026-09-14 — the email says the
form was "reviewed and approved by the US tax authorities. No further action is required on your
side." That was verified against the mail itself before anything was published, not taken on report.

Publication ran on Andres's `APPROVE LAUNCH`, in the order he gave:

| # | Gig | Category → Service type | Prices | Delivery | Live since (UTC) |
|---|-----|------------------------|--------|----------|------------------|
| 1 | [build a python data pipeline with tests and scheduling](https://www.fiverr.com/amercado19/build-a-python-data-pipeline-with-tests-and-scheduling) | Data → Data Engineering → Data ETLs | $125 / $375 / $875 | 5 / 10 / 21d | 16:10:24 |
| 2 | [automate your recurring report to run on a schedule](https://www.fiverr.com/amercado19/automate-your-recurring-report-to-run-on-a-schedule) | Data → Data Processing → Automations | $75 / $190 / $440 | 4 / 7 / 14d | 16:11:35 |
| 4 | [clean and consolidate your messy excel or csv data](https://www.fiverr.com/amercado19/clean-and-consolidate-your-messy-excel-or-csv-data) | Data → Data Cleaning | $30 / $75 / $150 | 2 / 3 / 5d | 16:12:18 |
| 3 | I will build an auditable financial model in excel | — | — | — | **HOLD — outside-activity review** |

Nothing was redesigned, rewritten or repriced at publish. Each listing went out as staged.

## Verified public, not merely saved

Fiverr answering "Your Gig is open for business!" is a save confirmation, not evidence a buyer can
see the page. Each URL was fetched **unauthenticated** afterwards and read back:

- The public profile renders all three, with the titles and Basic prices above.
- Gig 1: Data › Data Engineering › Data ETLs, Basic $125 / 5 days / 1 revision, 3 gallery images,
  order button reachable.
- Gig 2: Data › Data Processing › Automations, Basic $75 / 4 days.
- Gig 4: Data › Data Cleaning, Basic $30 / 2 days, "100 Items Cleaned", Continue and Contact me
  both reachable.
- Packages, delivery times, revisions, buyer requirements and gallery match the staged versions.
- The seller Gigs tab moved all three DRAFT → ACTIVE.

## Taxonomy decisions that are permanent

Fiverr locks the category and the gig URL from the first save. These were chosen against what the
repository and the resume actually support, not against what would rank best.

- **Gig 1 · Tools & Platforms.** The Data ETLs list is 21 commercial ETL SaaS products
  (Fivetran, Airbyte, Talend, Azure Data Factory, …) plus "Other". None describes the real stack,
  so: **Other → "Python GitHub Actions"**. Destination Platform likewise had no file/SQLite
  option: **Other → "CSV Excel SQLite"**. Free-text metadata is capped at 30 characters, 3 words,
  no punctuation — commas and slashes are rejected.
- **Gig 2 · Technology** offers Python and Excel as real options; both were selected. VBA was
  deliberately left off: it is Pro-level and resume-verified, but VBA macros are not scheduled
  cloud jobs, and listing it here would misdescribe the scope. Expertise: API integration, data
  acquisition, data extraction, data flow, data manipulation, data validation, ETL. SQL was left
  off — the resume says SQL (basic), and the field asks what you are expert in.
- **Gig 4 · Data Cleaning** has no service-type or metadata step at all.

## One platform constraint worth a decision — NEEDS ANDRES

Fiverr's Data Cleaning packages carry an **"Items Cleaned"** field. Basic is **locked to 100** and
cannot be changed; every tier caps at **10,000**. The kit's copy said 1,000 rows on Basic and
"unlimited" on Premium, so both were rewritten to avoid stating a number the platform contradicts:

- Basic now states no row count. The FAQ handles sizing ("Message me before ordering and I will
  quote it properly rather than have you buy the wrong package.")
- Premium now says 10,000 rows rather than "unlimited" — an unbounded promise at $150 is a bad
  order waiting to happen.

**Confirm the Basic scope you will actually honor.** The platform field understates it, and a
buyer reading "100 items" for $30 may simply not order.

## The portfolio is blocked, and the reason is structural — NEEDS ANDRES

Three case studies were written and three original diagrams drawn for
`fiverr.com/users/amercado19/portfolio/new`. They cannot be submitted as planned.

The plan was to leave **Project duration**, **Project cost** and **Project started on** blank,
because these are personal projects with no client and no fee. **All three fields are mandatory**,
and the cost field rejects `0` — it holds the error "Add a project cost." until a positive number
is entered. Verified directly against the live form on 2026-09-14; nothing was submitted.

That is not a form quirk. Fiverr's portfolio is built for *delivered client work*: the name
placeholder is a client campaign, the description prompt asks about "your client, their goals",
and step 2 is "Link to catalog". Entering a duration, a date and a dollar figure for a project
that had no client and no fee would state three things that are not true, on a public profile,
which is exactly what the standing no-fabrication rule forbids.

Three ways out, none of them chosen yet:

1. **Wait for the first delivered order** and build the portfolio entry from it. Real duration,
   real price, real date, and Fiverr offers this path from the order itself. Costs nothing and
   fabricates nothing; it just does not help before the first sale.
2. **Andres supplies the three facts himself** for the underlying personal projects. His call,
   his profile — but the cost field still has no true answer for unpaid work.
3. **Skip the portfolio.** Each gig already carries three original gallery images doing the same
   visual-proof job. The portfolio is additive, not required.

The written entries and the diagrams are committed and ready either way:
`portfolio/fiverr_portfolio_entries.md`, `portfolio/fiverr_case_images/`.

## Order intake — Gmail

Fiverr has no seller API and prohibits scraping `/inbox/` and `/orders/`, so the system cannot ask
Fiverr whether an order exists. It reads Andres's own mailbox instead, which touches no Fiverr
system — the path `docs/MARKETPLACE_RULES.md` already identified as the only compliant one.

`src/aicc/gmail_intake.py` is that parser and only that parser. Sender validation is proven
against real mail (`noreply@e.fiverr.com` trusted, `announce.fiverr.com` marketing refused,
lookalike domains refused). Bodies are scanned for injection before any field is read, and a body
carrying a high-severity finding is never auto-imported even when its fields parse cleanly. It
fails closed on an unrecognized gig title, an unparseable price, or a missing order id.
Deduplication keys on the Fiverr order id, so a resend is still a duplicate. Only order id, gig
title, price, deadline and buyer handle are stored.

**Body extraction is still unproven.** The gigs are live but no order has arrived, so no real
order notification exists yet. The patterns are marked PROVISIONAL; the first genuine order email
is the specification. It will either import cleanly or escalate — it will not guess.

## Fulfillment readiness — checked, not assumed

- `aicc order` reachable; `order show` reports no real jobs, which is correct.
- The full chain was exercised in an isolated data directory: import → ACCEPTED (capacity
  reserved) → run → READY_TO_DELIVER 100/100 → stop at the human gate.
- Delivery is human-gated in code: `pipeline.deliver` refuses any actor other than ANDRES and
  writes a `delivery_refused` audit event. SYSTEM, CLAUDE and GITHUB_ACTIONS all refused.
- Storefront ledger: 4 listings, 3 live, $0.00 gross/net, conversion INSUFFICIENT DATA.
- All nine compliance indicators green, including **PAID API FALLBACK: DISABLED** — no
  `ANTHROPIC_API_KEY` anywhere in the environment. An exhausted subscription window yields
  RETRY_LATER, never a bill.
- Health: GREEN RUNNING. Capacity 162 min safe to start (ESTIMATED), 0 reserved.
- Full test suite passes; CI green on `main` through the launch commits.

## An inbound message, unread on purpose — NEEDS ANDRES

A Fiverr message from **`@wolf_jackson359`** ("Hello") appeared in the seller UI within minutes of
Gig 1 going live. It has not been opened: `/inbox/` is off-limits under the marketplace rules, and
no notification for it reached Gmail. Andres reads it himself.

A "Hello" arriving before anyone could plausibly have read a listing is the shape new-seller spam
takes. If it moves toward WhatsApp, Telegram, payment outside Fiverr, or a "test" transaction,
that is a scam and belongs in a report, not a reply.

## What changed at launch

- `storefront mark-live` recorded each key with its real public URL and launch timestamp.
- The scheduled W-9 review check was deleted. Its gate is open; it had nothing left to watch.
- Gig 3 stays HOLD until the employer's outside-activity policy is actually read. PSLF-qualifying
  employment depends on that standing.
