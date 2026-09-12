# Decisions

Architectural decisions and why they were made, so a future session does not silently undo them.

---

## D1 — Invert the channel priority: job boards first, marketplaces second

**Context.** The brief assumed Upwork, Fiverr and Contra were the acquisition channels. Research
into their current rules said otherwise.

**Decision.** Hacker News and the aggregator feeds are the primary automated channel. Upwork and
Contra are assisted-only via their own MCP servers. Fiverr is a storefront.

**Why.** Fiverr has no discovery surface at all — Buyer Requests was removed and replaced by
push-only Briefs, so there is nothing to poll. Upwork prohibits job watchers by name and charges
4–16 Connects per proposal, which under a $0 rule caps applications at one or two a month. Hacker
News "Who is hiring?" is free, permitted, needs no reputation, and carried 26 contract listings at
$45–160/hr in the month sampled.

**Consequence.** The system produces real deal flow at $0 instead of an empty dashboard pointed at
platforms that will not feed it.

---

## D2 — Zero runtime dependencies

**Decision.** Standard library only. Dev tooling is separate.

**Why.** No supply-chain surface, nothing for `pip-audit` to find in production code, CI installs in
seconds, and the system runs anywhere Python 3.11 does. At this scale, hand-written parsers cost
less than the dependency risk.

**Revisit if.** Real Excel (`.xlsx`) work becomes a significant job category — `openpyxl` would then
earn its place.

---

## D3 — Public repository

**Decision.** Ship this public.

**Why.** Public repos get **unlimited** GitHub Actions minutes. The private free allowance is 2,000
minutes/month across the whole account, and NFL alone uses 730–1,200 in season, plus MLB. A private
repo would put this project in direct competition with the pipelines that already work. Nothing
confidential is in the repository by design (D4).

**Consequence.** If this is ever made private, raise the discovery cron from 6 hours to 12.

---

## D4 — Git as the database, with a hard data boundary

**Decision.** Operational data as JSON committed to the repository. Client material in a gitignored
`workspaces/` directory, never committed. Confidential-client-data jobs stay **disabled**.

**Why.** Git is free, durable, diffable, and its history is the backup. Render's free filesystem is
ephemeral; a free-tier hosted database expires silently. But no zero-cost store is appropriate for
client-confidential material, so rather than ship it on "good enough" storage, the feature is off
until revenue funds real infrastructure. Security beats automation.

---

## D5 — The reviewer cannot receive the worker's opinion

**Decision.** `reviewer.review()` takes requirements, criteria and artifacts. It does not take the
`Job` object.

**Why.** A reviewer told "the worker thinks this is done" grades the claim rather than the artifact.
That is how AI review theatre happens: two models agreeing while neither checks. Making it
structural rather than a convention means it cannot regress by accident.

**Enforcement.** A runtime guard, plus two tests — one asserts the signature has no `job`
parameter, the other reads the pipeline source and asserts the call site does not forward worker
notes.

---

## D6 — Decide on effective hours, not human hours

**Decision.** `effective_hours = human_hours + 0.25 × ai_hours`, and profitability blends rate
(60%) with absolute net profit on a saturating curve (40%).

**Why.** Both halves fixed a real defect found in testing. Dividing net profit by human hours alone
reported "$833 per hour" on a 90%-automatable $500 job — true, and useless, because it made every
automatable job look identically perfect. Scoring on rate alone then ranked a $450 job above a
$2,475 one, which is backwards for a business.

AI hours are cheaper than human hours, not costless: they draw subscription allowance, occupy
wall-clock time, and carry revision risk.

---

## D7 — A static dashboard with no write endpoints

**Decision.** The dashboard reports state. The CLI changes it. Clicking an action shows the command.

**Why.** A dashboard with live controls needs authentication, and there is no free authentication
approach worth trusting with control of a business. Inventing weak password security was explicitly
out of scope. No API means no injection surface, no session to steal, and the page can be published
without exposing control.

**Consequence.** Approving from a phone means running a command, not tapping a button. Accepted for
Phase 1.

---

## D8 — Capability probes, never config flags

**Decision.** Every status light derives from a live probe. A probe that raises is DOWN with the
exception text.

**Why.** A config flag records an intention; a probe records reality. The publish gate additionally
refuses any build containing a GREEN light with no supporting detail, so "fake green" cannot ship.

---

## D9 — Insufficient Data is not zero

**Decision.** A metric without enough observations is `None` and renders "Insufficient Data". Rates
require at least 5 decided outcomes.

**Why.** A zero win rate and an unknown win rate are different facts. One win from one proposal is
not a 100% win rate. Every metric also carries formula, source, observation count and timestamp, so
no figure on screen is unfalsifiable.

---

## D10 — Estimates are labelled as estimates

**Decision.** Effort and profitability figures carry `method` and `inputs`. Performance metrics are
computed only from actual records.

**Why.** Forward-looking figures are necessarily estimates; presenting them with the same authority
as banked revenue is how a dashboard starts lying. The category priors are explicitly *priors*, and
`calibrate_from_ledger` replaces them with observed medians once real jobs exist.

---

## D11 — Learning never modifies production scoring automatically

**Decision.** `scoring_calibration()` returns a recommendation. A human edits `scoring.WEIGHTS`.

**Why.** Opaque self-modifying production code is unreviewable and unrevertable. It also refuses to
recommend anything below 5 decided outcomes, because tuning on noise makes scoring worse. If
separation between won and lost scores is negative, it says the score is **inverted** rather than
quietly adjusting.

---

## D12 — Cannot-automate raises rather than returning empty

**Decision.** Upwork, Contra and Fiverr connectors raise `NotPermittedError` with the governing rule
quoted.

**Why.** An empty list reads as "no jobs today". An exception reads as "this cannot be automated".
Those are completely different facts and the operator must not confuse them.

---

## D13 — Outbound discovery of discrete freelance projects is a dead end on free sources

**Decision.** Treat the Fiverr storefront (inbound) and contract-role listings as the real
channels. Do not build more outbound project-discovery connectors, and do not relax the scoring
thresholds to make the existing ones produce results.

**Why.** Measured, not assumed. `scripts/market_reality_check.py` samples the live Freelancer.com
public API across the operator's job categories and applies the funnel in order. On 11 Sep 2026,
417 active projects:

| Step | Surviving | Share |
|---|---:|---:|
| Active projects in-category | 417 | 100% |
| Priced in USD | 191 | 46% |
| ...and at most 25 bids | 12 | 2.9% |
| ...and max budget over $50 | 2 | 0.5% |
| ...and min budget over $50 | 1 | 0.2% |

Median bids per project: **78**. p90: 238. Maximum: 466. Median USD maximum budget: **$250**.

So the typical listing is 78 people bidding on a $250 job. Winning there costs more in unpaid
proposal writing than the job pays, and the single survivor of the whole funnel was
*"Flash TRC20 Token"* - a well-known crypto fraud product, which the risk scoring would reject
anyway. One measurement on one day, so it is worth re-running before treating it as permanent -
that is what the script is for - but a market this lopsided does not turn around in a week.

The other sources do not fill the gap, and the reason is categorical rather than incidental:

* **Hacker News "Who is hiring?", Himalayas, RemoteOK, We Work Remotely, Python.org Jobs** are
  **job boards**. Every one of the top-scoring live results is an ongoing role - Senior Backend
  Engineer, Financial Systems Expert, Lead Python Backend Engineer - not a discrete project. They
  are worth watching, but for contract roles, which is a different business from gig work.
* **Upwork** prohibits automated discovery outright (D4), and caps free applications at 10
  Connects a month.
* **Fiverr** has no discovery surface in any form. It is a storefront: buyers find you (D12).

**What this means.** The scanning half of this system is a contract-role watcher, and it should be
described that way rather than as a freelance-gig finder. The money-making half is the Fiverr
storefront, which is why the launch kit is the priority and why it is priced to buy the first
reviews. Zero STRONG matches from 75 live listings is the honest output of a market that does not
currently contain what is being searched for - it is not a threshold that needs loosening.

---

## D14 — Full-time employment is a reject, on PSLF grounds

**Decision.** `RiskFlag.FULL_TIME_EMPLOYMENT` is a hard reject, not a penalty. Contract,
part-time and project work are unaffected.

**Why.** Andres needs roughly seven more years at a 501(c)(3) or government employer for Public
Service Loan Forgiveness, and has stated employer type is a non-negotiable filter rather than a
factor to weigh. Every full-time role these sources carry is a for-profit company. Taking one
does not merely compete for his hours — it ends qualifying employment and forfeits seven years
of progress. No hourly figure on a job board compensates for that.

This was previously a 30-point penalty, left visible so he could judge case by case. That was
sound reasoning from an incomplete premise: it is not a career decision with a rate attached,
and leaving it ranked invited exactly the mistake it could not afford. 13 of 75 live listings
are rejected on these grounds.

The one full-time employment that would NOT break PSLF is federal or 501(c)(3) — which is why
`docs/MARKETPLACE_RULES.md` records the free USAJobs API without integrating it. That is a
different project.

---

## D15 — Five Fiverr gigs, four slots, one on the bench

**Decision.** Prepare five complete candidates; publish four; hold `pdf_extraction` in reserve.

**Why.** The brief asked for five candidates and Fiverr gives a new seller four slots, and an
earlier version of this project treated that as a contradiction and built four. It is not one.
A gig that draws no impressions in six weeks should be replaced, and the difference between
swapping next Tuesday and swapping in two months is whether the replacement is already
researched, priced, validated and imaged. A bench candidate is held to the same bar as a live
one; one that still needs work is not a bench candidate, it is a TODO.

---

## D16 — A number that has never been tested says so, next to itself

**Decision.** The win estimate is always rendered with `INITIAL HEURISTIC - not calibrated
against outcomes` attached, plus the step-by-step trail that produced it. A test asserts the
dashboard payload cannot carry the figure without the caveat.

**Why.** A probability displayed on its own acquires authority it has not earned. This one is a
heuristic over source priors, listing age, opportunity class and competition, and has never
been checked against a single real outcome, because there are none yet. It is useful for
comparing listings to each other and useless as a probability to act on, and the display has to
make that difference impossible to miss. Showing the four adjustments that assembled it does
more for that than any wording could.

---

## D17 — Scheduling is a knapsack, not a ranking

**Decision.** `scheduler.py` solves an exact 0/1 knapsack over Claude capacity, maximising total
expected profit, rather than sorting candidates by any single metric.

**Why.** Every single-metric ranking has a failure it cannot see from inside itself. Sort by
total price and four five-minute jobs never get done. Sort by profit-per-minute and a $500
project loses to $60 of small work that scored a fraction higher. Both look reasonable while
they are happening. The brief's own worked example — one $500 job at ~60 min of AI work plus four
$15 jobs at 5 min each — has one right answer, $560, and it is a test.

The properties that follow from optimising the right thing rather than adding rules:

* Protecting the large job needed **no special rule**. It falls out of maximising total profit.
* Committed work is **subtracted before optimisation**, not entered into it, which is what makes
  "a paid deadline is not endangered by twenty small opportunities" structurally true instead of
  a policy someone has to remember.
* Opportunity cost is **computed** — the planner re-solves without each selected item — so
  "displaces $202 of other work" is a number, not a claim.

The problem is tiny (dozens of items against a few dozen five-minute buckets), so there is no
reason to accept a greedy approximation that fails in precisely the way the brief says not to.

---

## D18 — Claude capacity is a rate, not a budget

**Decision.** A job's feasibility is judged against every subscription window arriving before its
deadline, discounted to 50% utilisation — not against the window open right now.

**Why.** This one was a bug first and a decision second, which is why it is worth recording. The
first version of `capacity.pre_job_check` compared a job's whole demand against one five-hour
window. An ordinary freelance contract — roughly fourteen hours of AI work, due in a week — came
out as "exceeds available capacity", and **every real listing in the store was deferred**. The
queue was a wall of WAIT FOR RESET and nothing was ever schedulable.

That reads as caution and is actually a broken model. Capacity refills; more windows keep
arriving. The 50% discount exists because Andres sleeps and has a day job, so not every window
can be spent — planning against capacity that never materialises is how a deadline gets missed,
and the failure directions are not symmetric.

The distinction the module now draws: **this window** decides whether work can begin now; **the
horizon** decides whether the job is feasible at all. A job that clears the horizon but not this
window is WAIT FOR RESET, which is a schedule. A job that cannot clear the horizon genuinely
cannot be delivered on time, and saying so before accepting the work is the whole point.

---

## D19 — Every capacity figure carries its confidence

**Decision.** No number in `capacity.py` is rendered without ESTIMATED or MEASURED attached, and
`snapshot()` states plainly that Anthropic exposes no exact remaining-subscription telemetry to a
GitHub Actions runner.

**Why.** "62.4% capacity remaining" would be more comfortable to read and completely invented.
A made-up number is worse than an honest range precisely because people act on numbers that look
precise. MEASURED appears only after five recorded runs — the same threshold, and the same
reasoning, as the win-rate gate.

---

## D20 — The twelve invariants are joined to their checks by key

**Decision.** `policy.INVARIANTS` and the live checks in `selftest.py` are joined by key at
import, and the import **raises** if a named invariant has no check.

**Why.** An invariant nothing attempts to violate is a comment, not a guarantee. The failure
mode this prevents is quiet: someone adds a thirteenth rule to the list, the documentation now
says thirteen things are enforced, and twelve are.

Several of those checks test **both directions**, which turned out to matter more than the
forward case. An over-firing PSLF gate that rejected every freelance contract would leave the
system looking perfectly safe while finding no work at all — and a safety report that cannot
tell those two states apart is not reporting on safety.

---

## D21 — Emergency stop is scoped in both directions

**Decision.** `HALTED_BY_EMERGENCY_STOP` names every kind of new work including scheduled scans;
`NEVER_HALTED` names the audit log, the self-test, redaction, the cost gate and the dashboard
build. An unclassified activity is treated as work, so it stops.

**Why.** A stop that also silenced the audit log would destroy the record of why it was pressed,
at exactly the moment that record matters most. A control that can switch off its own oversight
is not a safety control. And nothing is deleted: records, drafts and queued jobs survive
untouched, because pressing the button should be cheap enough to press when unsure.

Scheduled scans are in the halt list deliberately. A stop that leaves acquisition running is the
system continuing to take on obligations while its owner believes it has stopped.

---

## D22 — An unverifiable condition is not a failing one, and the consumer has to agree

**Decision.** `acknowledge_live_mode` filters on `passing is False`, not on `not passing`.

**Why.** `live_mode_checklist` returns three states: True, False, and None for something that
genuinely cannot be probed from inside the system — today, who last edited a cron schedule on
GitHub. A test already guarded the checklist against rendering that as a failure. Nothing guarded
the **consumer**, which used `if not c["passing"]` and therefore treated None as False, so live
mode was permanently blocked on a question the system can never answer. A gate that can never
open is not a safety feature. The unverifiable items are surfaced in the return message instead,
where a person can act on them.

---

## D23 — An AI action is never recorded under Andres's name

**Decision.** `acknowledge_live_mode` and `fiverr_kit.mark_ready` take a required or explicit
`actor`, and the system passes `Actor.CLAUDE` when it is the one acting.

**Why.** The audit log exists to answer "who did what". An AI action written down as ANDRES is a
false answer in the one place where a false answer is least recoverable — and it is the exact
failure the identity rule is about, pointed inward at the record rather than outward at a client.

---

## Open — needs a human decision

**Reddit r/forhire.** Content-wise the best freelance demand source available. `robots.txt` is a
blanket `Disallow: /` and the unauthenticated `.json` endpoints closed in May 2026, so the OAuth
Data API is the only legal route. Its free tier is genuinely free (100 QPM, no card), **but the
terms require approval for commercial use**. A personal job-finding pipeline is arguably
non-commercial — but it is finding paid work, so this needs Andres to read the terms and decide
rather than an inference from me.
