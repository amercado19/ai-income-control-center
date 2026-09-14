# Fiverr storefront — build status

**Updated:** 2026-09-13 · **Additional monthly cost: $0.00** · Seller: `amercado19`

## Where it stands

Three gigs are built through all six Fiverr wizard steps and sit in **DRAFT** on the live
platform. The ACTIVE tab is empty — nothing is published, and no draft becomes live on its own.
Fiverr states the blocker on every one of them, verbatim:

> You aren't visible to clients yet, verify your identity and submit your Form W-9 on your
> dashboard to publish your service.

**Both steps are done.** ID verification cleared, and Andres submitted the W-9 on 2026-09-13 at
02:04 UTC. Fiverr's own confirmation email says the form "is being reviewed. This might take a few
days." So the checklist item is not showing "not done" — it is showing "not yet approved", and the
remaining gate is Fiverr's review queue, not Andres.

A scheduled task checks the gate every six hours. When Fiverr moves the W-9 from pending to
approved it notifies Andres and stops - it does not publish. Publication waits on his
`APPROVE LAUNCH`, which he can send from a phone. Only then does it publish Gig 1, Gig 2 and
Gig 4 in that order and run the verification chain below.

| # | Gig | Category → Service type | Prices | Delivery | State |
|---|-----|------------------------|--------|----------|-------|
| 1 | I will build a python data pipeline with tests and scheduling | Data → Data Engineering → Data ETLs | $125 / $375 / $875 | 5 / 10 / 21d | DRAFT, ready |
| 2 | I will automate your recurring report to run on a schedule | Data → Data Processing → Automations | $75 / $190 / $440 | 4 / 7 / 14d | DRAFT, ready |
| 4 | I will clean and consolidate your messy excel or csv data | Data → Data Cleaning | $30 / $75 / $150 | 2 / 3 / 5d | DRAFT, ready |
| 3 | I will build an auditable financial model in excel | — | — | — | **HOLD — outside-activity review** |

Each of the three carries: 5 search tags, a full description, 5–6 FAQs, 4–5 buyer requirements
(one of them an attachment request), and 3 original 1280×769 gallery images generated for this
purpose. No gig extras are enabled on any of them.

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

**Confirm the Basic scope you will actually honour.** The platform field understates it, and a
buyer reading "100 items" for $30 may simply not order.

## Order intake — Gmail

Fiverr has no seller API and prohibits scraping `/inbox/` and `/orders/`, so the system cannot ask
Fiverr whether an order exists. It reads Andres's own mailbox instead, which touches no Fiverr
system — the path `docs/MARKETPLACE_RULES.md` already identified as the only compliant one.

`src/aicc/gmail_intake.py` is that parser and only that parser. Sender validation is proven
against real mail (`noreply@e.fiverr.com` trusted, `announce.fiverr.com` marketing refused,
lookalike domains refused). Bodies are scanned for injection before any field is read, and a body
carrying a high-severity finding is never auto-imported even when its fields parse cleanly. It
fails closed on an unrecognised gig title, an unparseable price, or a missing order id.
Deduplication keys on the Fiverr order id, so a resend is still a duplicate. Only order id, gig
title, price, deadline and buyer handle are stored.

**Body extraction is unproven.** Nothing is published, so no real order notification exists yet.
The patterns are marked PROVISIONAL; the first genuine order email is the specification.

## Fulfilment readiness — checked, not assumed

- `aicc order` reachable; `order show` reports no real jobs, which is correct.
- Delivery is human-gated in code: `pipeline.deliver` refuses any actor other than ANDRES and
  writes a `delivery_refused` audit event.
- Storefront ledger operational: 4 listings, 0 live, $0.00 gross/net, conversion INSUFFICIENT DATA.
- All nine compliance indicators green, including **PAID API FALLBACK: DISABLED** — no
  `ANTHROPIC_API_KEY` anywhere in the environment. An exhausted subscription window yields
  RETRY_LATER, never a bill.
- Health: GREEN RUNNING. AI Worker verified against a real `claude -p` call. Additional monthly
  cost $0.00.
- Capacity: 162 min safe to start (ESTIMATED), 0 reserved, window resets 2026-09-13T05:00 UTC.
- Full test suite passes.

## Corrections made this session

- **Gig 1 Premium delivery was 18 days in the kit and the ledger; Fiverr has no 18-day option.**
  The live listing is 21 days. Both records were corrected to 21 — the system must not track a
  deadline the buyer was never shown.
- American spelling in three buyer-visible strings (`normalises`, `standardise`, `standardised`),
  matching what was actually typed into the live listings.
- Removed the unverified `airflow` tag from Gig 1 before saving.

## What happens the moment verification clears

1. Publish Gigs 1, 2 and 4.
2. Capture each real public URL and the exact launch timestamp.
3. Verify each loads logged out.
4. `aicc storefront mark-live <key> --url <url>` for each.
5. Verify pricing, package display, title/category rendering, buyer requirements and that the
   order path is reachable.

Gig 3 stays HOLD until the employer's outside-activity policy is actually read. PSLF-qualifying
employment depends on that standing.
