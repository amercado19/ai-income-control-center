# Project status

**Last updated:** 2026-09-11
**Additional monthly cost:** $0.00
**Phase:** 1 — zero-cost MVP, DEMO mode
**Local HEAD:** `88b3c31` (18 commits). **Pushed to GitHub:** through `38b4106` only — 7 commits are waiting, see Blocked #1.

> A new session should read this file first, then `DECISIONS.md`, then
> `docs/MARKETPLACE_RULES.md`. Those three carry everything needed to continue.

---

## Read this before anything else

**The outbound scanner is a contract-role watcher, not a freelance-gig finder.** That is a
measured conclusion, not a mood, and it is the single most important fact about this project.
`scripts/market_reality_check.py` sampled 417 live Freelancer.com projects in the operator's
categories: median **78 bidders** on a median **$250** job, and exactly **one** listing survived
the full funnel — *"Flash TRC20 Token"*, a crypto fraud product the risk scoring rejects anyway.
The remaining free sources (Hacker News, Himalayas, RemoteOK, We Work Remotely, Python.org Jobs)
are **job boards**: every top-scoring live result is an ongoing role, not a discrete project.
Upwork forbids automated discovery; Fiverr has no discovery surface at all.

So the two halves of this system are not equally productive:

* **Inbound — the Fiverr storefront — is the half that makes money.** Four validated gigs, ready
  to publish. This is where effort should go.
* **Outbound — the scanner — is worth running, but for contract roles.** A scan returning zero
  STRONG matches is usually the market, not a threshold that wants loosening.

Full reasoning and the numbers: **D13** in `DECISIONS.md`. Re-run the script before believing it.

---

## Completed

- **Core engine** — schema, system state machine with emergency stop, JSON storage with a
  secret-leak guard, append-only audit log, cost gate that fails closed at $0.00 with no override.
- **Scoring** — transparent 0–100 across six factors, each with the evidence that drove it; hard
  rejects; penalties; per-class weights renormalised to 100 so scores stay comparable.
- **Money engine** — revenue separated from profit; AI cash cost separated from AI usage draw.
- **Connectors** — Hacker News, Himalayas, RemoteOK, We Work Remotely, Python.org Jobs,
  Freelancer.com (automated); Upwork, Contra (assisted, via their own MCP); Fiverr (inbound only).
- **Prompt-injection boundary** (`untrusted.py`) — nonce-tagged envelope, standing policy,
  detection tripwire, capability denial. Documented as a tripwire, not a wall.
- **Privacy** (`privacy.py`) — third-party contact details redacted before anything is stored,
  because the operational store is committed to a public repository.
- **Fiverr launch kit** (`fiverr_kit.py`) — 4 gigs (Fiverr gives new sellers exactly 4 slots),
  each validated against every platform limit, each declaring its own effort estimate so the
  implied hourly is checkable. Three clear $76–88/h; one is deliberately below floor to buy the
  first reviews and says so in writing, with a retirement condition.
- **Portfolio** (`portfolio.py`) — 4 case studies from the real NFL/MLB work, with evidence split
  into what a client can open now versus what needs a call.
- **AI degradation** (`degradation.py`) — an exhausted subscription window pauses instead of
  failing the run; a revoked token still fails loudly; never falls back to paid billing.
- **Fulfillment** — worker/reviewer separation enforced structurally; real QA; two revision loops
  then escalation.
- **Dashboard** — static, responsive, 15 sections, honest status lights, publish gate.
- **Tests** — 350+ passing, plus a real-browser test across 15 pages × 2 viewports.
- **CI** — format, lint, types, tests, workflow validation, secret scan, dependency audit,
  cost-ceiling assertion, demo lifecycle, dashboard verify.

---

## Blocked — needs Andres

### 1. The push (blocks everything downstream)

```bash
cd ~/Documents/ai-income-control-center && git push
```

The repository is live and the first push has happened. **Seven commits since then are still
local**, including the fix for the CI failure that first push caused. Both copies are synced at
`88b3c31`.
The script `scripts/push_to_github.sh` tries existing credentials first and only asks for a token
if that fails — since three other repositories already push from this Mac over HTTPS, the keychain
almost certainly has one and **no token should be needed**.

**Why this needs a person and not the assistant:** the bridge into the Mac runs in a Linux VM that
reaches github.com but has no keychain; macOS grants assistants click-only access to Terminal, so
it cannot type the command; GitHub Desktop is not installed. Credential entry is on the operator's
own interrupt list, so this is the carve-out working as designed.

**GitHub Pages is already enabled** (Source: GitHub Actions). The deploy runs on the next push.

**Pre-publish security review: PASSED.** 89 tracked files, no emails, phone numbers, local paths,
credentials or client data. The only personal string is the public GitHub username. The workflow
checks the Claude token's *existence*, never its value.

### 2. Claude token (blocks the AI worker)

```bash
claude setup-token
gh secret set CLAUDE_CODE_OAUTH_TOKEN --repo amercado19/ai-income-control-center
```

$0.00 cash — it authenticates against the subscription. Until set, the rule-based worker carries
the pipeline and the dashboard reports the AI worker as NOT CONFIGURED. Note the token expires in
one year and does **not** auto-refresh.

### 3. Reddit r/forhire terms (a judgment call, not a task)

Free OAuth API, no card, and content-wise the best freelance demand source on the list. But the
Data API terms require approval for *commercial* use and this pipeline finds paid work. Needs a
human reading of the terms. See `DECISIONS.md` → Open.

### 4. Publishing the Fiverr gigs (the actual revenue step)

`python -m aicc fiverr check` validates all four. Publishing is manual by necessity — Fiverr has
no seller API — and **the category cannot be changed after saving**, so check it before you save.

---

## Live market test — done, and it earned its keep twice

**First round** (nine real HN listings) found four parser bugs that synthetic unit tests would
never have caught: `270-300 zł/hr` read as dollars (~4× overvaluation); `30-40 hours/week` parsed
as a wage; *"I do not use AI to screen your applications"* rejecting a legitimate job because the
client was describing their **own** process; and `ocr` matching inside "S**ocr**acy".

**Second round** (105 listings across six sources) found three more, one serious:

| Bug | Consequence |
|---|---|
| The **top-ranked opportunity was a full-time job** | A 1099 Senior Data Engineer role scored 83.8 STRONG at rank 1. The parser read "1099 contractor" as evidence it was not full-time — but tax status and hours are different axes, and it was openly both. Now rank 39, SKIP. |
| Every HN listing titled *"Company - contract role"* | The list was unreadable. The pipe-delimited header is now parsed for the real role. |
| Himalayas returns `pubDate` as an **epoch integer** | Crashed the entire scan in the win-probability model, a long way from the connector that caused it. Normalization moved to the single boundary all connectors funnel through. |

**A third round, from reading a drafted proposal rather than running a test**, found the worst
defect the system has had. For the payments-and-ledger role it rendered *"What you would get: —
Have shipped: a double-entry ledger or equivalent money system in production"* — the client's
hiring requirements, echoed back as things on offer. Read plainly, a claim to have shipped a
production ledger. Fabricated experience, under his name, past a claim verifier that only guards
the experience section. Fixed, plus a body guard that raises rather than silently stripping.

The same asymmetry one layer down: that role was **rank 1 of everything** at 78.8 STRONG on a
single keyword match, because skill fit rewarded what matched and never noticed what was required
and missing. Coverage and unmet-requirement detection now exist, and it sits at 68.8, rank 8, with
the reason on the card.

Standing result: **75 live listings, 4 REVIEW, 0 STRONG.** That is the honest output of the
market described at the top of this file, scored honestly.

---

## Next actions, in order

1. **Push the 7 waiting commits** (blocked #1). Everything below waits on it.
2. Watch CI go green, and the Pages deploy run. Pages is already configured.
3. Run `gh workflow run discover.yml`; confirm the sources return data from a runner with open
   egress. That run is the real verification of the live HTTP layer.
4. Add the Claude token (blocked #2).
5. **Review the four Fiverr gigs and publish them.** This is the revenue path; the scanner is not.
6. Review the contract roles: `python -m aicc top --limit 10`.
7. Record outcomes as they land — win rate stays *Insufficient Data* until 5 decided outcomes, and
   that is deliberate.

---

## The honest state of automation

| Capability | Status |
|---|---|
| Opportunity discovery (HN, feeds, Freelancer.com) | **Working against live data.** 105 listings retrieved this session. |
| Scoring | Fully working |
| Proposal drafting | Fully working |
| AI worker / reviewer | Architecture complete; needs the token |
| AI degradation on rate limits | Fully working, tested |
| Rule-based worker / reviewer | Fully working |
| Fiverr gig kit | 4 gigs drafted and validated; **publishing is manual** |
| Portfolio case studies | Written, with public/private evidence separated |
| Fiverr gig images | Rendered at 1280x769, `python3 scripts/gig_images.py` |
| AI rate-limit degradation | Working, tested. A usage window pauses rather than failing the run |
| Safety self-test | 9 invariants attempted against the live system, all refused correctly |
| Free notifications | One rolling GitHub issue, quiet when nothing is waiting |
| Role applications vs project proposals | Two registers; an ongoing role gets an application |
| Marketplace submission | Approval required — by design, everywhere |
| Delivery | Approval required — by design |
| Payments | Not configured, deliberately |
| Upwork discovery | Assisted only. Upwork prohibits automated discovery. |
| Fiverr discovery | **Impossible.** No discovery surface exists. |
| Finding discrete freelance projects outbound | **Not viable on free sources.** See D13. |

Nothing above is green that is not actually working.
