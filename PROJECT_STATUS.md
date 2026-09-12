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

## Ready for a first customer?

**The machine is ready to fulfil. The shop is not open.** Those are different claims and
collapsing them would be the fake-autonomy failure this project exists to avoid.

What is now proven by a real run rather than asserted:

* A real model call succeeds through `ClaudeWorker.execute -> claude -p`, the exact path a paid
  job takes, on cloud infrastructure with no Mac involved.
* Worker → independent reviewer → QA → revision → final QA works end to end. Two rounds, 93.9/100.
* Delivery still stops at READY_TO_DELIVER and waits for a human. By design, and tested.
* $0.00 cash. No `ANTHROPIC_API_KEY`; the workflow fails outright if one ever appears.

What stands between that and a paying customer:

| | |
|---|---|
| **No storefront is live** | Four gigs are READY TO PUBLISH, not published. No buyer can find or order anything. This is the binding constraint and it is twelve minutes per gig of Andres's typing. |
| **Zero real jobs, ever** | The pipeline has processed internal proof jobs only. Win rate reads *Insufficient Data* and will until five decided outcomes. |
| **The proof was a fixture** | 93.9/100 on "describe what a data pipeline does" is evidence the machinery runs. It is not evidence a buyer will accept a spreadsheet cleanup. |
| **Payments not configured** | Fine for Fiverr, which handles its own checkout. Direct-client checkout does not exist and stays COMING SOON. |
| **6 proposals awaiting approval** | One is a contract-to-permanent role the policy gate blocks. Nothing is sent without an explicit approval. |

So: **READY TO FULFIL, NOT OPEN FOR BUSINESS.** The next thing that moves revenue is publishing
the gigs, not building anything.

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
| AI worker in GitHub Actions | **Verified end to end** — run #10, real model call, reviewer 2 rounds, 93.9/100 |
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

### 1. The Claude worker — VERIFIED

**Worker run #10 (`7d0e012`) passed all three legs on a GitHub-hosted runner**, and `health.yml`
validated the artifact in a credential-free job and committed `HEALTHY`. The live dashboard reads
`AI Worker / HEALTHY`, and the system banner moved from `YELLOW LIMITED` to `GREEN RUNNING` on its
own — nobody set a flag; the probe started reporting a proof that exists.

```
PASS  No paid fallback           - no ANTHROPIC_API_KEY, ceiling $0.00, exhausted window = RETRY_LATER
PASS  Claude worker executes     - nonce minted 15:51:28Z returned exactly through ClaudeWorker.execute
PASS  Worker to reviewer, e2e    - reviewer ran 2 QA rounds, READY at 93.9/100, READY_TO_DELIVER
```

Two QA rounds is the part worth noticing: the first pass was rejected, the worker revised, the
second passed. The revision loop is not decoration.

#### Getting there took six failed runs, and the reasons are worth keeping

The 401 was real, and it was three different faults wearing one error message.

1. **The secret was never updated.** Runs #5–#7 used a value GitHub's own record showed was 15
   hours old. Checking "Last updated" on the secrets page took ten seconds and would have saved
   a run.
2. **Then the secret was truncated.** A diagnostic that reports facts *about* the token without
   printing it — length, a shape match, whitespace booleans, and a truncated SHA-256 so a person
   can compare from their own machine — showed length 80 against a real token's 109.
3. **Then the credential was fine and the proof's own fixture was broken.** `prove_worker_reviewer`
   built its job with `agreed_price=0.0`, and `pipeline.validate` refuses a job with no agreed
   price — correctly, since its whole purpose is to reject an unworkable brief before effort is
   spent. So `pipeline.run` went VALIDATE → PROBLEM and returned before any QA, and the proof
   reported "the pipeline produced no QA round at all, so the reviewer never ran." True, and it
   read like a reviewer defect.

That third one is the one to carry: **the leg had never executed in the project's history.**
`run_all` only reaches it when `prove_worker` passes, and `prove_worker` returned 401 on every run
from #3 to #8. A check gated behind another check is untested code wearing a test's clothes, and
the first time it ran it failed for a reason that had nothing to do with what it measures. It is
now validated by a unit test that needs no credential at all.

`.github/workflows/credential-check.yml` is the instrument from step 2, kept because the next
token expiry should cost 21 seconds rather than six runs. It holds the credential, has
`contents: read`, cannot commit, and never prints the token. One of its own labels asserted that
whitespace "alone causes a 401"; two runs later a whitespace-bearing token authenticated fine.
The label was corrected — a guess wearing a finding's clothes is the failure mode this whole
repository is about.

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
| #5 | `e3ad577` | Failed: same 401. Confirmed the cause is not anything this work changed. |
| #6 | `49a599d` | Failed: same 401, and produced the first full attestation artifact. |
| #7 | `d06063c` | Failed: same 401. The secret was 15 hours old and had not been replaced. |
| #8 | `5847f26` | Failed: same 401. Secret replaced but truncated — 80 bytes against 109. |
| #9 | `af39a08` | **Worker PASSED**, reviewer failed: the proof's own fixture could not pass `pipeline.validate`. |
| #10 | `7d0e012` | **VERIFIED.** All three legs. Reviewer 2 rounds, READY 93.9/100. |

Verified in every run: **`ANTHROPIC_API_KEY` absent, paid fallback DISABLED.** The workflow fails
the run outright if that key ever exists, so the paid path cannot be switched on by adding a
secret and forgetting.

**The AI Worker light is honest about all of this.** It reads a validated proof, not the presence
of a token and a binary. It read `DOWN / AUTH FAILED` through all six failures and reads `HEALTHY`
now, with a link to run #10 either way. It expires in 48 hours without a fresh proof. The seven
states and the transport that carries the proof are below.

#### The proof transport: credential execution separated from repository write

**Built and verified end to end.** The blocker was that `claude-worker.yml` is `contents: read`,
so it could not commit the proof the dashboard reads - and the read-only permission is correct,
because that workflow holds the credential and runs `claude -p` against a job brief, and briefs
originate in marketplace listings. Widening it would have put credential handling, untrusted-text
execution and repository write authority in one job.

So the halves stay apart and the proof crosses between them as data:

```
claude-worker.yml   contents: read    token, claude -p, uploads an artifact, commits nothing
        |
        |  artifact: a JSON attestation. No secrets, no model output.
        v
health.yml          contents: write   no token, no model. Validates, then commits the verdict.
                    actions: read
```

Neither half can be talked into doing the other's job, because it lacks the permission. Both
directions are asserted in tests, and `health.yml`'s ingest job asserts its own credential-free
state at runtime - a later edit adding `secrets: inherit` fails the run rather than quietly
becoming the thing this prevents.

`proof_transport.validate` distrusts its input. Every field in the artifact is a claim, so where
GitHub can be asked directly its answer wins and a disagreement is itself a rejection. Rejects:
malformed, wrong repository, unexpected workflow, unaccepted branch, not from a runner, stale,
execution failed, paid API configuration present, production path not exercised, never attempted.

**Seven states, not GREEN/RED**, because a lamp cannot say what to do:

| State | Lamp | What it asks of a person |
|---|---|---|
| HEALTHY | green | nothing - a real `claude -p` call succeeded on a runner, recently |
| AUTH FAILED | red | a person at a browser; does not recover on its own |
| CAPACITY LIMITED | yellow | nothing at all, just time; never a bill |
| STALE PROOF | yellow | check whether the daily worker run is still happening |
| DEGRADED | yellow | read the linked run |
| CONFIGURED BUT NOT OPERATIONAL | yellow | the pieces are here and it does not work |
| NOT YET VERIFIED | white | no valid proof has ever arrived |

Exactly one may be green, asserted at import.

**Proof TTL: 48 hours**, and the number is now answerable. The worker was dispatch-only, so a
freshness window measured nothing but whether someone remembered to press a button. It runs
daily at 11:00 UTC, 25 minutes ahead of `health.yml`, leaving exactly one missed run of margin.
Daily rather than weekly because a token expiring quietly is the likeliest way this breaks, and a
week would hold the light green over a dead worker. Free on a public repository.

Verified by running it: worker run #6 produced the artifact, `health.yml` validated it in a job
holding no credential, and committed `Worker proof: AUTH FAILED`. The live dashboard shows
`DOWN / AUTH FAILED` with a link to the run. The 401 is not masked anywhere.

#### Three defects found while verifying the above

None was in the design. All three were in the habits around it, and two are the same defect:
**a diagnostic quietly writing state that something else owns.**

**The diagnostic was overwriting the verdict.** `python -m aicc worker-proof` wrote
`data/worker_proof.json` — the file the border guard owns. Running it locally replaced a
committed `AUTH FAILED` verdict with a laptop's self-report, discarding the URL of the run that
produced it and the timestamp of the validation; anything that stages `data/` in CI would then
have committed that as the repository's state. No green light was ever reachable this way (the
guard re-validates a raw attestation on read and refuses a laptop's five different ways), which
is precisely why it went unnoticed — the clobber type-checked and the colour barely moved. The
two files now have two paths: `worker_proof.json` is what the guard accepted,
`worker_proof_local.json` is what happened on one machine, gitignored.

**CI had been red for nine commits, and not once because of a bug in the code.** The secret
scanner was flagging a test fixture shaped like a real token — `sk-ant-oat01-…` — written to
prove the OAuth token never reaches the attestation. It was right to flag it: a scanner that can
tell a decoy from a credential is a scanner a decoy can fool. The fixture is now a sentinel that
looks nothing like a credential, which proves the same property, because `attestation` never
reads the variable's value at all.

The nine runs are the part worth recording. Local checks were being assembled by hand each time
— tests, lint, types, self-test — and the one check never in the hand-assembled list was the one
that failed. `bash scripts/verify.sh` is now the single local entrypoint, running exactly what
`ci.yml` runs in its order, and a test parses `ci.yml` to assert the two lists agree, so a step
added to CI without being added there fails the suite and names it. **CI is green as of #57.**

A red badge that stays red stops being read. That is the same failure as a green light nobody
earned, pointing the other way.

**A probe result is a fact about the machine that ran it.** Found by noticing the working tree was
dirty after the other two fixes: `python -m aicc health` persisted its probe results into
`data/system_state.json`, and those results are machine-specific — free disk, whether a binary is
on PATH, how long ago the scheduler ran *here*. A local run wrote `Writable. 30,420 MB free.` over
the runner's 88,015 MB. Not cosmetic: the dashboard build reads the **persisted** capability set
rather than re-probing, and runs before the step that re-probes, so a local reading that reached a
commit would be published as the system's storage. On a runner the same result *is* the system's
state, so the rule is about where it ran rather than which command asked —
`state.probe_results_are_the_systems()`, with `persist=False` at the diagnostic call sites. Both
halves are asserted, because refusing to persist everywhere would freeze the dashboard on a stale
capability set: the same dishonesty in different clothes.

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
2. **Refresh the Claude token** — `claude setup-token`, then update the `CLAUDE_CODE_OAUTH_TOKEN`
   repository secret. The only step here that needs a person. Everything downstream of it is
   built, tested and waiting: the next worker run mints a proof, `health.yml` validates it, and
   the dashboard moves off `AUTH FAILED` on its own.
3. Review the contract roles: `python -m aicc top --limit 10`, and the plan: `python -m aicc queue`.
4. Record outcomes as they land — win rate stays *Insufficient Data* until 5 decided outcomes,
   and that is deliberate.

Before pushing anything: `bash scripts/verify.sh`. It runs what CI runs, so a clean run means a
green badge. It also does not write to `data/` — a local check that mutates shared state is the
defect recorded above.
