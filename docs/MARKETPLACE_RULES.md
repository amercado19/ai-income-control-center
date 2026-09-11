# Marketplace rules

Verified September 2026. **Re-verify before changing any connector's capability flags** — these
rules change, and a stale assumption here is how an account gets banned.

The governing principle: this system never argues with a platform's published rules. Where a
platform forbids automated access, the connector raises `NotPermittedError` rather than returning
an empty list, so the limitation is loud rather than silent.

---

## Summary

| Platform | Automated discovery | Cost to apply | What this system does |
|---|---|---|---|
| **Hacker News** | Permitted (public API) | Free | Full automated scan |
| **Himalayas** | Permitted (public API) | Free | Full automated scan |
| **RemoteOK** | Permitted (attribution required) | Free | Full automated scan + attribution |
| **We Work Remotely** | Permitted (RSS) | Free | Full automated scan |
| **Contra** | **Only via Contra's own MCP** | Free (commission-free) | Assisted, interactive |
| **Upwork** | **Only via Upwork's own MCP** | **4–16 Connects** | Assisted, cost-gated |
| **Fiverr** | **None exists** | n/a (inbound) | Storefront only |

---

## Upwork

### Scraping is explicitly and specifically prohibited

`https://www.upwork.com/robots.txt` contains `Disallow: /jobs/` — the job listings themselves.

Upwork publishes a dedicated bots policy that names exactly what a job-scanner would do:

> "Job alert or watcher tools that scrape or run searches. Auto-refresh or tab reload tools that
> refresh pages on a timer. Page monitors or change detectors that poll pages for updates. Macro
> or RPA recorders that replay clicks and searches. User-script managers that run custom scripts."

— <https://support.upwork.com/hc/en-us/articles/43342677368467>

Penalties run to a permanent ban. **This connector performs no HTTP request to Upwork at all.**

### RSS is dead

> "Upwork officially discontinued support for RSS feeds after August 20, 2024."

— <https://support.upwork.com/hc/en-us/articles/52052528243731-RSS-deprecation>

### The GraphQL API exists but this account does not qualify

Requires **$25,000 in lifetime earnings or spend** and a **90% Job Success Score**, plus completed
identity verification. It is also documented as *"available for personal and internal use only.
Commercial use isn't supported."*

— <https://support.upwork.com/hc/en-us/articles/115015857647-How-to-request-an-API-key-from-Upwork>

### The sanctioned path: Upwork's own MCP server

- Endpoint: `https://mcp.upwork.com/mcp`
- **Free with any Upwork account.** OAuth 2.1 with dynamic client registration — no API key, and
  **no $25k gate**.
- Supports job search by skill/category/budget/type, and proposal drafting grounded in your profile.
- Write actions are draft-then-confirm by design: *"Every write action is a draft you confirm
  separately, never a one-shot commit."*
- *"Connects only apply when you confirm."*

— <https://www.upwork.com/ai/mcp>

MCP is an interactive agent surface tied to a human's session. There is no supported way to run it
unattended from CI, so the Upwork connector is **ASSISTED**, not automated. That is a fact about
Upwork, not a gap in this system.

### Connects are real money

| | |
|---|---|
| Free per month | 10 (Basic plan; eligibility varies) |
| Cost per proposal | 4–16, typically 8–12 |
| Cash price | $0.15 each |
| Refunded when you lose? | **No.** Also not refunded on expiry or withdrawal. |

Every Upwork submission routes through `UpworkConnector.connect_spend_request`, which prices the
spend and puts it through the cost gate. Within the free allowance it is approved at $0.00; beyond
it, the gate declines and Andres must approve explicitly.

### AI is allowed; automated submission is not

> "Always disclose to clients whether you use AI-generated content or tools during a project."

— <https://support.upwork.com/hc/en-us/articles/35120504756499-Ethics-of-AI-on-Upwork>

AI-drafted proposals are permitted. Bot-submitted proposals are not. This system drafts with AI,
discloses that to the client, and requires a human to send.

---

## Contra

### The carve-out that makes MCP compliant

Terms of Service §13.3(v) prohibits accessing the platform with:

> "any engine, software, tool, agent, device or mechanism (including spiders, robots, crawlers,
> data mining tools or the like) **other than the software and/or search agents provided by Contra**"

— <https://contra.com/policies/terms> (last updated 9 April 2026)

Contra's own MCP server is "software provided by Contra", so using it is permitted. A crawler we
write is not. §14.3 additionally caps outreach volume *regardless of tooling*, so this system never
batch-sends.

### Capabilities

Official MCP server with 60+ tools, both stdio and hosted HTTP transports, covering proposals,
invoices, portfolio and payment links for independents. Two-step prepare-and-confirm flow requiring
user approval for account changes.

— <https://contra.com/features/mcp>

### Economics

**Commission-free for independents** — the best net economics of any marketplace here. Free plan
charges the *client* $15–29 per payment over $500; the freelancer keeps 100%.

— <https://contra.com/pricing>

robots.txt permits crawling `/independent/opportunities`, but §13.3(v) is the binding constraint,
not robots.txt. Applying requires a login.

---

## Fiverr

**There is no discovery surface. Not a restricted one — none.**

### Buyer Requests was removed and replaced with a push-only mechanism

Sellers now receive **Briefs**, selected by Fiverr's matching engine and delivered to the "Your
matches" tab, by email, and by in-app notification. Requires a service success score ≥ 7. Briefs
expire after 72 hours. **There is no browsable board of buyer-posted jobs**, so there is nothing to
poll even manually.

— <https://help.fiverr.com/hc/en-us/articles/4415608857745-Personalized-offers-Briefs-for-freelancers>

### No seller API

The only Fiverr API on their partnerships page is for companies embedding Fiverr talent into their
own products, and is marked **COMING SOON**. Every "Fiverr API" on npm or GitHub is an unofficial
scraper.

### Scraping is prohibited

> "Any attempts to access the platform through unauthorized methods … **scrape data from the
> platform** … are strictly prohibited."

— <https://help.fiverr.com/hc/en-us/articles/32242973123985-Our-Community-Standards>

`robots.txt` disallows `/search/`, `/gigs/search`, `/users/`, `/inbox/`, `/conversations/`,
`/orders/timeline/*`, `/recommendations/` — every path automation would want.

### The direction of travel is against automation

Fiverr is **shutting down its own Fiverr Go Personal Assistant on 1 October 2026**, citing a
preference for *"direct, personal communication between freelancers and clients."*

— <https://help.fiverr.com/hc/en-us/articles/32545737221649-Personal-Assistant-for-freelancers>

### The only compliant workflow

1. Publish gigs (manually, with approval).
2. Buyers order. Fiverr sends **email and push notifications** — the only channels it offers.
3. Parse those notifications **from your own mailbox** into the dashboard. That touches no Fiverr
   system and violates nothing, because it is your own mail.

`FiverrConnector.import_order` is that path. Seller keeps **80%** of each order.

---

## Permitted free sources

### Hacker News — the primary channel

Public Algolia API, no key, no signup, no robots restriction: `https://hn.algolia.com/api/v1/`

Two threads are read, and they are **not** equally useful:

- **"Ask HN: Who is hiring?"** — several hundred comments monthly. Mostly full-time, but a
  consistent minority are contract with rates stated. September 2026 sample: 376 comments, 26
  contract listings, published rates **$45–70/hr, $80–90/hr, $120–160/hr**. **This is where the
  money is.**
- **"Ask HN: Freelancer? Seeking freelancer?"** — nearly dead as a demand source. September 2026:
  22 comments, almost all `SEEKING WORK` (other freelancers advertising). Only the
  `SEEKING FREELANCER` side is demand, and there is very little of it.

Thread discovery must **not** assume the poster: the freelancer thread is no longer posted by the
`whoishiring` bot, so it is found by title match. Watch for duplicate same-month posts.

### Himalayas, RemoteOK, We Work Remotely

All free, keyless, permitted. Caveats encoded in the parsers:

- **Himalayas**: the `employmentType` query parameter is **silently ignored** by the API, so
  contract filtering happens client-side.
- **RemoteOK**: the first array element is a **legal/ToS object, not a job** — a naive loop produces
  a garbage record every run. `robots.txt` is `Allow: /` with `Crawl-delay: 1`. Attribution is a
  contractual condition stated in the API response itself, and the dashboard renders it.
- **We Work Remotely**: RSS only, no salary element — rates come from description text.

**Structured salary fields on all three are ANNUAL figures for full-time roles, not freelance
rates.** Passing them through as project budgets would inflate every profitability estimate by
roughly two orders of magnitude. `_annual_to_hourly` converts them and marks the budget type.

---

## Deliberately excluded

| Source | Why |
|---|---|
| **Remotive** | `robots.txt` contains `Disallow: /api/*`. The API is otherwise open and its own terms invite use — that contradiction is theirs, but a robots directive is a robots directive. |
| **Authentic Jobs** | RSS feed is robots-disallowed. |
| **Reddit r/forhire** | `robots.txt` is a blanket `Disallow: /`, and unauthenticated `.json` endpoints returned 403 from May 2026. The **OAuth Data API** (free tier, 100 QPM, no card required) is the only legal route — but its terms require approval for *commercial* use. **This needs a human decision before it ships.** Content-wise it is the best freelance demand source on the list, so it is worth resolving. |
| **Wellfound / AngelList** | No public API; every "API" is a paid third-party scraper. |
| **Freelancer.com** | Docs unreachable; free-tier status unverified. Do not plan around it without manual verification. |
| **GitHub Jobs, Stack Overflow Jobs** | Dead. Confirmed. |
| **SAM.gov** | Free API (key from your own SAM.gov account, no card). Real contracts, but gated on CAGE registration with long lead times and heavy compliance. A long-tail lottery ticket, not a same-week gig channel. |

---

## What this system will never do

Regardless of instruction:

- Bypass CAPTCHAs or bot detection
- Circumvent rate limits or robots directives
- Evade AI detection or identity verification
- Create or operate multiple accounts
- Impersonate Andres, or fabricate experience, portfolio work, or reviews
- Evade marketplace fees or move payment off-platform
- Send mass generic proposals
- Accept a job that prohibits AI and then secretly use AI
- Take academic cheating work

The first three are refused in code. The rest are enforced by the scoring rejections in
`aicc.scoring.HARD_REJECT` and the claim verification in `aicc.proposals._verify_claims`.

---

## Sources evaluated and rejected, with the measurement

Recording rejections matters as much as recording adoptions: without the number, "we looked at
that" degrades into "we should look at that again" every few months.

### jobr.pro — rejected, 1 useful listing in 100

The one genuinely new free feed found in the September 2026 sweep. Technically it is the cleanest
option available: `https://jobr.pro/feed.xml` returns 100 jobs over HTTP 200 with **no API key, no
authentication, no approval process**, and `robots.txt` permits it (`Allow: /`, with only `/api/`,
`/jobs?` and admin paths disallowed).

It was still rejected, because the content does not survive contact with the filters:

| Measure | Result |
|---|---|
| Listings in feed | 100 |
| Remote | 11 |
| **Salary stated** | **0** |
| **Company stated** | **0** |
| Contract-style language | 20 |
| On-stack technical | 10 |
| Remote **and** contract **and** on-stack | **1** |

And that one was *"Lead Systems Specialist (Controls & Automation), Travel up to 75%"* — a hard
reject under `PHYSICAL_PRESENCE_REQUIRED`. So the real yield is **zero**.

With no salary and no company on any listing, every entry would also score near the floor on
Clarity and could not be costed at all. Adding this connector would import noise and no signal.
Re-measure with `scripts/market_reality_check.py`-style sampling before reconsidering.

### USAJobs API — out of scope, but worth knowing it exists

Free, email registration, API key issued immediately, automated access permitted. Federal
positions only.

Not integrated, because federal jobs are full-time employment rather than freelance work, which
this system rejects on PSLF grounds anyway. Noted here for a different reason: federal and
501(c)(3) employers are the **only** full-time roles that do not break Andres's PSLF eligibility.
If the question ever changes from "find freelance work alongside the job" to "find a different
qualifying job", this is the correct source and it is free. That is a different project.

### Contra — re-verified September 2026, unchanged

Checked again against `https://contra.com/features/mcp` because it would be valuable if it had
changed. It has not: the MCP covers proposals to clients you already have, offerings, invoices,
payment links, portfolio and chat history. **There is no job discovery surface for freelancers.**
Contra remains a back office, not a source.
