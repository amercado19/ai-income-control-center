# Project status

**Last updated:** 2026-09-12
**Additional monthly cost:** $0.00
**Mode:** LIVE (demo data cleared)
**Real revenue:** $0.00

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

* **Inbound — the Fiverr storefront — is the half that makes money.** Four gigs, validated and
  marked READY TO PUBLISH, with a step-by-step launch wizard. This is where effort should go.
* **Outbound — the scanner — is worth running, but for contract roles.** A scan returning zero
  STRONG matches is usually the market, not a threshold that wants loosening.

Full reasoning and the numbers: **D13** in `DECISIONS.md`. Re-run the script before believing it.

---

## The one thing that needs Andres

**Publish the four Fiverr gigs.** `python -m aicc fiverr wizard` prints the exact sequence: six
steps per gig, every field's value ready to paste, and the **20 fields that lock permanently on
save** flagged before he types anything. Roughly 12 minutes per gig.

Everything else on this list — the research, the copy, the pricing, the validation, the images,
the publishing order — is done. Fiverr has no seller API and driving its seller UI with
automation is not something the platform sanctions, so the twelve minutes of typing are his. The
account staying in good standing outranks the convenience of automating a form.

---

## What is actually working

| Capability | Status |
|---|---|
| Opportunity discovery (6 live sources) | **Working against live data.** 97 listings retrieved in the last scan. |
| Scoring, with per-factor evidence | Fully working |
| Portfolio scheduler (knapsack over Claude capacity) | Fully working |
| Claude capacity estimation and reservation | Working, labelled ESTIMATED throughout |
| Safety & compliance panel (9 live probes) | Fully working |
| Proposal drafting | Fully working; every claim verified against real artifacts |
| Rule-based worker / reviewer / QA / revision | **Fully working end to end** — see the demo lifecycle |
| AI worker in GitHub Actions | Workflow written; see "Open" below |
| AI degradation on rate limits | Fully working, tested |
| Fiverr gig kit | 4 gigs READY TO PUBLISH + 1 on the bench; **publishing is manual** |
| Fiverr launch wizard | Complete — 6 steps per gig, 20 locked fields flagged |
| Portfolio case studies | Written, with public/private evidence separated |
| Prompt-injection defence | 51-case regression suite; 0 missed, 0 false positives |
| Safety self-test | **21 invariants** attempted against the live system, all refused correctly |
| Emergency stop | Scoped in both directions; halts all new work, never its own oversight |
| Free notifications | One rolling GitHub issue, quiet when nothing is waiting |
| Marketplace submission | Approval required — by design, everywhere |
| Delivery | Approval required — by design |
| Payments | Not configured, deliberately |
| Upwork discovery | Assisted only. Upwork prohibits automated discovery. |
| Fiverr discovery | **Impossible.** No discovery surface exists. |
| Finding discrete freelance projects outbound | **Not viable on free sources.** See D13. |

Nothing above is green that is not actually working.

---

## The economic model

The objective is **maximise total legitimate net profit**, which is a portfolio problem rather
than a ranking. `scheduler.py` solves an exact knapsack over Claude capacity: the amendment's own
worked example — one $500 job at ~60 min of AI work plus four $15 jobs at 5 min each — returns
**$560**, not "$500" and not "$60", and that example is a test. Protecting the large job needed
no special rule; it falls out of optimising the right thing.

Four things the scheduler does that a sorted list cannot:

* **Committed work is subtracted before optimisation**, not entered into it. That is what makes
  "a paid deadline is not endangered by twenty small opportunities" structurally true.
* **Opportunity cost is computed** by re-solving the knapsack without each item, so *"displaces
  $202 of other work"* is a number rather than a claim.
* **Capacity is a rate, not a budget.** A 14-hour contract due in a week is judged against every
  window arriving before the deadline, discounted to half. Judging it against one five-hour
  window made every real listing look infeasible — that bug is recorded in `DECISIONS.md`.
* **Low capacity defers, it never rejects.** A profitable job that cannot start now and is not due
  yet is WAIT FOR RESET.

Every figure carries its confidence. Anthropic exposes no exact remaining-subscription telemetry
to a runner, so capacity numbers say ESTIMATED and `capacity.snapshot()` says why.

---

## Open

### 1. The Claude worker — BLOCKED on a credential only Andres can create

**Root cause found and confirmed.** The stored `CLAUDE_CODE_OAUTH_TOKEN` repository secret is
rejected:

```
Failed to authenticate. API Error: 401 OAuth access token is invalid.
```

**Andres must run `claude setup-token` and update the secret.** Nothing else unblocks this.

This is not mis-wiring, and that was checked rather than assumed: with a deliberately bogus token
the CLI hangs on authentication, and with no token at all it works. So the CLI does consume that
variable and the stored value is genuinely invalid or expired. Tokens last about a year and do
not auto-refresh.

The workflow no longer uses `anthropics/claude-code-action@v1`. A green action step proves an
action ran; it does not prove `ClaudeWorker.execute` — the path a paid client job actually takes —
can invoke a model and get an answer back. It now installs the CLI and runs
`python -m aicc worker-proof`, which puts a real `Job` through `ClaudeWorker.execute` and requires
a runtime-minted nonce back, character for character. A missing file is a failure, never a pass.

| Run | Commit | Result |
|---|---|---|
| #1 | — | Failed: missing OIDC token. Fixed by handing the job its own read-only token rather than granting `id-token: write` — the error's own suggestion would have let the job mint identity tokens it does not need. |
| #2 | `8181cf6` | Failed. |
| #3, #4 | — | Failed: `401 OAuth access token is invalid`. Run #3 reported only "the credential was rejected"; a redacted excerpt of the underlying error was added, and run #4 then named the 401. |
| #5 | `e3ad577` | Dispatched to record a runner-sourced proof on current HEAD. |

Verified in every run: **`ANTHROPIC_API_KEY` absent, paid fallback DISABLED.** The workflow fails
the run outright if that key ever exists, so the paid path cannot be switched on by adding a
secret and forgetting.

**The AI Worker light is honest about all of this.** It reads the recorded proof, not the presence
of a token and a binary — PASSED under 72h is HEALTHY, FAILED is DOWN, STALE is DEGRADED, never
run is NOT_CONFIGURED, and a pass recorded off-runner is `PASSED_ELSEWHERE`/DEGRADED, because a
credential proved on a laptop says nothing about the repository secret Actions uses.

#### A second blocker sits behind the token, and it is circular

`claude-worker.yml` holds `permissions: contents: read`. It therefore **uploads the proof as an
artifact and cannot commit it** to `data/worker_proof.json`, which is the file the dashboard
reads. So the only proofs that reach the dashboard are written outside a runner — exactly the
proofs the code now correctly refuses to go green on. **As wired today, the light can never
legitimately reach HEALTHY, even with a valid token.**

The read-only permission is deliberate and should not simply be widened. That workflow is the one
holding the OAuth token, and it runs `claude -p` against a job brief — and job briefs originate in
marketplace listings, which the project treats as untrusted external input throughout. A workflow
that can both be steered by untrusted text and write to the repository is a different risk class
from one that can only read.

Two clean options, both needing a human decision rather than a quiet edit:

1. **Have `health.yml` consume the artifact.** It already has `contents: write`, already commits,
   and is not the workflow holding the credential. The proof crosses as data, not as a permission.
2. **A separate minimal job** in `claude-worker.yml` that needs `contents: write` and runs *after*
   the credential step, with no access to the brief.

Option 1 is the better separation and does not touch the credential-handling job at all.

### 2. GitHub Pages — LIVE

**https://amercado19.github.io/ai-income-control-center/**

Deployed from `_reusable-run.yml` (`publish: true`) and browser-tested at 1440px and 400px across
Overview, Profit Queue, Opportunities, Needs Me, System Health and Safety & Compliance. Five
contradictions were found and fixed in the process; see `DECISIONS.md`.

### 3. Reddit r/forhire terms (a judgment call, not a task)

Free OAuth API, no card, and content-wise the best freelance demand source on the list. But the
Data API terms require approval for *commercial* use and this pipeline finds paid work. The
capability matrix records it as **PROHIBITED** rather than merely unused, which is the honest
state until a human reads the terms.

---

## Live market test — it has earned its keep four times

**First round** (nine HN listings) found four parser bugs synthetic tests would never catch:
`270-300 zł/hr` read as dollars (~4× overvaluation); `30-40 hours/week` parsed as a wage;
*"I do not use AI to screen your applications"* rejecting a legitimate job because the client was
describing their **own** process; and `ocr` matching inside "S**ocr**acy".

**Second round** (105 listings, six sources) found three more, one serious: the **top-ranked
opportunity was a full-time job** — a 1099 Senior Data Engineer role at rank 1, because the parser
read "1099 contractor" as evidence it was not full-time. Tax status and hours are different axes.
Now a hard reject.

**Third round**, from reading a drafted proposal rather than running a test, found the worst
defect the system has had: it rendered the client's *hiring requirements* as things on offer —
*"What you would get: Have shipped a double-entry ledger in production"*. Read plainly, a claim to
have shipped a production ledger. Fabricated experience, under his name, past a claim verifier
that only guards the experience section. Fixed, plus a body guard that raises rather than
silently stripping.

**Fourth round**, building the scheduler, found four more — all recorded in `DECISIONS.md`,
including a label (`HIGH VALUE / LOW EFFORT`) that was unreachable in practice because an
unscored listing arrived with an empty category and silently degraded to "generic". The code was
correct and nothing could ever earn the label, which is the worst kind of dead branch because it
looks like a working feature.

Standing result: **85 live listings, 0 STRONG.** That is the honest output of the market
described at the top of this file, scored honestly.

---

## Next actions, in order

1. **Publish the four Fiverr gigs.** `python -m aicc fiverr wizard`. This is the revenue path.
2. Read the Claude worker run result and record it in "Open" above.
3. Confirm the Pages deploy and record the live URL in `docs/DEPLOYMENT.md`.
4. Review the contract roles: `python -m aicc top --limit 10`, and the plan: `python -m aicc queue`.
5. Record outcomes as they land — win rate stays *Insufficient Data* until 5 decided outcomes,
   and that is deliberate.
