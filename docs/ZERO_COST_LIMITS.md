# Zero-cost limits

**Current additional monthly cost: $0.00.**

This document is the honest accounting of what that buys and what it costs you. The limits below
are real, and pretending otherwise would defeat the point of the system.

---

## The cost gate

`aicc.config.MAX_NEW_MONTHLY_CASH_SPEND = 0.00`

Every component that would incur a charge must call `cost_gate.request()` and treat a refusal as
final. There is **no override flag, no environment variable, and no force argument** — a test
asserts the signature takes no such parameter. Raising the ceiling means editing that constant,
which is a reviewable commit rather than a runtime accident. CI asserts it is still zero on every
push.

Every refusal is logged to `data/cost_requests.jsonl` and surfaced on the dashboard, so you can see
what the $0 rule has actually cost you in opportunities.

---

## What runs for free, and its real ceiling

| Resource | Free allowance | What we use | Risk |
|---|---|---|---|
| **Claude Max subscription** | Your existing ~$200/mo | AI worker + reviewer via `CLAUDE_CODE_OAUTH_TOKEN` | Draws against your Max usage limits. **$0 cash, not $0 resource.** |
| **GitHub Actions** | Unlimited on public repos; 2,000 min/mo private | ~4.5 hrs/month | See below — this is the binding constraint if the repo is private |
| **GitHub Pages** | 1 GB storage, 100 GB/mo bandwidth | A single ~90 KB page | Not a realistic constraint |
| **Git as a database** | Repo soft limit ~1 GB | JSON files, a few MB/year | Not a realistic constraint at this volume |
| **HN / Himalayas / RemoteOK / WWR APIs** | Free, keyless | 4 scans/day | Rate limits are generous; we are far under |
| **Render** | Free web service | **Not used** | Free instances sleep and have an ephemeral filesystem — unusable for persistence. GitHub Pages is strictly better here and costs nothing. |

### GitHub Actions minutes — read this before making the repo private

Your account already runs two pipelines. From the NFL repo's own documentation:

> in-season ≈ 730 min/month (peak game-day-heavy month ≈ 1,200) · offseason ≈ 370 min/month

Plus MLB. The free **private** repo allowance is 2,000 minutes/month total across the account, so
in an NFL-season month you may have only a few hundred minutes of headroom.

This project's schedule is deliberately modest — discovery every 6 hours (~2 min/run) plus a daily
health check — which is roughly **270 minutes/month**. That fits, but it is not free headroom you
have much of.

**Recommendation: keep this repository public.** Public repos get unlimited Actions minutes, so
this project competes with MLB and NFL for nothing. The data it stores is public job listings and
system state; there is no client-confidential material in the repository by design (see
`SECURITY.md`). If you make it private, raise the discovery cron to every 12 hours.

### The Claude subscription is the real scarce resource

Running the worker and reviewer on `CLAUDE_CODE_OAUTH_TOKEN` costs **$0.00 cash** — the official
docs state that *"if you authenticate with an OAuth token, runs use your Claude subscription
instead of API billing."* But it consumes your Max allowance, which you also use for everything
else.

This is why the money engine tracks `ai_cash_cost` and `ai_usage_units` as **separate numbers**.
Collapsing them into one would make every job look infinitely profitable and hide the constraint
that actually binds.

---

## What $0 genuinely costs you

Be clear-eyed about these. They are not bugs.

### 1. Upwork is capped at roughly one serious proposal per month

10 free Connects/month; 4–16 per proposal. Under a strict $0 rule that is **one to two
applications a month**. No amount of software changes this. If you want volume on Upwork, Connects
are the first thing worth buying — roughly $15 for 100, which funds about 8–12 proposals.

### 2. Fiverr cannot be scanned at all

Not a cost issue — there is simply no discovery surface (see `MARKETPLACE_RULES.md`). Fiverr only
works as a storefront you publish to and wait on.

### 3. No persistent secure storage for client work

Client files must not go in the repository. There is no zero-cost datastore that is both durable
and appropriately secured for client-confidential material, so the interface exists and **the
feature is disabled**. Client work stays in the gitignored local `workspaces/` directory.

This is the one place where the $0 rule genuinely limits what the business can take on: jobs
involving confidential client data should be handled manually until there is revenue to pay for
real infrastructure. **Security beats automation.**

### 4. No email sending

No transactional email provider. Notifications are GitHub's own (workflow failure emails) plus the
dashboard's own alert surface. Proposals are sent by you, by hand, from your own mail client.

### 5. No custom domain

The dashboard lives at `*.github.io`. Fine for your own use; slightly less credible if you send the
link to a client. Worth ~$12/year later, not now.

---

## The upgrade ladder, in the order it becomes worth paying for

Nothing here executes automatically. The dashboard surfaces a recommendation only once **real**
(non-demo) revenue exists, and you decide.

| Trigger | Upgrade | Cost | Why |
|---|---|---|---|
| First real revenue | **Upwork Connects** | ~$15 | Cheapest way to test whether proposals convert. Buy only if the free proposals get responses. |
| ~$350 real revenue | **Persistent secure datastore** | ~$7/mo | Unlocks jobs involving confidential client data — currently declined. |
| ~$500 real revenue | **Custom domain** | ~$12/yr | Credibility on a client-facing link. |
| Consistent inbound | **Business payment processing** | ~2.9% + $0.30 | Direct clients without a marketplace. **Do not use personal PayPal for this.** |
| Volume beyond Max limits | **Claude API credits** | usage-based | Only when subscription allowance is actually the bottleneck, which it is not yet. |

### The question that decides all of this

> **Can this system win profitable work?**

Not "can we build expensive software". Until at least **50 qualified opportunities** have been
reviewed and there is real proposal-response data, no paid infrastructure is justified — you would
be buying capacity for a business that has not proven it can sell.
