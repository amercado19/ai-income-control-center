# Project status

**Last updated:** 2026-09-11
**Additional monthly cost:** $0.00
**Phase:** 1 — zero-cost MVP, DEMO mode

> A new session should read this file first, then `DECISIONS.md`, then
> `docs/MARKETPLACE_RULES.md`. Those three carry everything needed to continue.

---

## Completed

- **Environment + account audit.** Cloud container tooling, the NFL pipeline's CI/workflow
  patterns (reused throughout), and Claude authentication for unattended runs.
- **Marketplace compliance research**, verified against primary sources. Findings in
  `docs/MARKETPLACE_RULES.md`.
- **Core engine** — schema, system state machine with emergency stop, JSON storage with a
  secret-leak guard, append-only audit log, cost gate that fails closed at $0.00.
- **Scoring** — transparent 0–100 across six factors, each with the evidence that drove it; hard
  rejects; penalties.
- **Money engine** — revenue separated from profit; AI cash cost separated from AI usage draw;
  every figure carries its derivation.
- **Connectors** — Hacker News, Himalayas, RemoteOK, We Work Remotely (automated); Upwork, Contra
  (assisted, via their own MCP); Fiverr (inbound only); demo.
- **Proposal generator** — specific, grounded, with claim verification that raises rather than
  shipping an unverifiable claim.
- **Fulfillment** — worker/reviewer separation enforced structurally; real QA for spreadsheet,
  code and research work; two automatic revision loops then escalation.
- **Dashboard** — static, responsive, dark mode, 13 sections, honest status lights, publish gate.
- **Tests** — 120 passing. Safety properties, scoring regressions, QA detection, analytics honesty.
- **CI + workflows** — reusing the NFL pattern: format, lint, types, tests, workflow validation,
  secret scan, dependency audit, cost-ceiling assertion, demo lifecycle, dashboard verify.
- **Documentation** — all nine required documents.
- **Acceptance test (spec §52)** — 25 steps, all passing, including QA genuinely catching a real
  seeded defect (7 dropped rows) and the revision fixing it.

---

## Blocked — needs Andres

### 1. GitHub access (blocks the repository going live)

This session's cloud container has no GitHub credential — the API returns
*"No linked GitHub account."* The linked MacBook's shell is also behind an egress proxy that
returns 403 for github.com, so it cannot push either.

**What unblocks it:** link the GitHub account in Claude, or run these locally:

```bash
cd ai-income-control-center
gh repo create amercado19/ai-income-control-center --public --source=. --push
```

The repository is fully built and committed locally. Nothing else is waiting on this.

### 2. Claude token (blocks the AI worker)

```bash
claude setup-token
gh secret set CLAUDE_CODE_OAUTH_TOKEN --repo amercado19/ai-income-control-center
```

$0.00 cash — it authenticates against the Max subscription. Until it is set, the rule-based worker
carries the pipeline and the dashboard honestly reports the AI worker as NOT CONFIGURED.

### 3. Reddit r/forhire terms (a judgment call, not a task)

The best freelance demand source on the list. Free OAuth API, no card. But the Data API terms
require approval for *commercial* use, and this pipeline finds paid work. Needs a human reading of
the terms. See `DECISIONS.md` → Open.

---

## Live market test — done, and it earned its keep

Nine real contract listings from the September 2026 Hacker News "Who is hiring?" thread were run
through the live scoring engine. **It found four parser bugs that unit tests on synthetic data
would never have caught**, all now fixed with regression tests drawn from the real text:

| Bug | Consequence |
|---|---|
| `270-300 zł/hr` read as dollars | ~4x overvaluation. The currency guard only understood three-letter codes, not symbols. |
| `30-40 hours/week` parsed as a rate | A time commitment priced as a wage. |
| *"I do not use AI to screen your applications"* → AI_PROHIBITED | The client was describing **their own** process. A legitimate job was rejected outright. |
| `ocr` matched inside "S**ocr**acy", `cli` inside "**cli**ent" | A GPU-container-orchestration role got the PDF-invoice proposal template. |

Two new risk flags came out of it: `GEO_EXCLUDED` (a Polish firm's "Poland or Romanian residents
only" now rejects rather than scoring 62.9) and `AI_PROPOSAL_DISCOURAGED` (a client asking for
human-written *applications* now costs 6 points and a note to write it by hand, instead of
rejecting the job).

Current ranking of the real listings, and 3 proposals awaiting approval, are loaded in the
dashboard.

### Still unverified from this session

The connectors' live HTTP paths. This sandbox's egress policy returns 403 for hn.algolia.com,
himalayas.app, remoteok.com and weworkremotely.com, so listings were retrieved through a sanctioned
fetch tool and fed through the real parsing and scoring code. **This is a sandbox restriction, not
a code defect** — GitHub Actions runners have open egress. The first scheduled run is the real
verification of the HTTP layer specifically.

---

## Next actions, in order

1. Push the repository (blocked item 1).
2. Enable GitHub Pages: Settings → Pages → Source: GitHub Actions.
3. Run `gh workflow run discover.yml` and confirm the sources return data.
4. Add the Claude token (blocked item 2).
5. Review the top 10 real opportunities: `python -m aicc top --limit 10`.
6. Approve and send **one** proposal by hand. Record the outcome.
7. Repeat until 50 qualified opportunities have been reviewed — then, and only then, decide about
   paid infrastructure.

---

## The honest state of automation

| Capability | Status |
|---|---|
| Opportunity discovery (HN, feeds) | Built and tested. Live network unverified from this sandbox. |
| Scoring | Fully working |
| Proposal drafting | Fully working |
| AI worker / reviewer | Architecture complete; needs the token |
| Rule-based worker / reviewer | Fully working |
| Marketplace submission | Approval required — by design, everywhere |
| Delivery | Approval required — by design |
| Payments | Not configured, deliberately |
| Upwork discovery | Assisted only. Upwork prohibits automated discovery. |
| Fiverr discovery | **Impossible.** No discovery surface exists. |

Nothing above is green that is not actually working.
