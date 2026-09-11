# Architecture

## Shape

```
     permitted sources                    NOT automatable
  HN · Himalayas · RemoteOK · WWR      Upwork · Contra · Fiverr
            │                                   │
            │ automated scan                    │ their own MCP, interactive
            ▼                                   ▼
      ┌─────────────────────────────────────────────┐
      │  normalize  →  dedupe  →  score  →  archive  │   Level 1: automatic
      └─────────────────────────────────────────────┘
                          │
                   draft proposal                        Level 1: automatic
                          │
                   ╔══════════════╗
                   ║  YOU APPROVE ║                      Level 2: one click
                   ╚══════════════╝
                          │
                   submit · win · job
                          │
      ┌─────────────────────────────────────────────┐
      │  WORKER → VERIFY → independent REVIEWER      │   Level 1: automatic
      │       ↑                    │                 │   max 2 revision loops
      │       └──── FIX ───────────┘                 │
      └─────────────────────────────────────────────┘
                          │
                   ╔══════════════╗
                   ║  YOU APPROVE ║                      Level 2: one click
                   ╚══════════════╝
                          │
                   DELIVERED → revenue → analytics
```

Identity, banking, contracts, purchases and withdrawals are **Level 3: never automated**.

---

## Decisions that shape everything else

### Zero runtime dependencies

The package imports only the standard library. HTTP is `urllib.request`, parsing is `re`, `csv`,
`json` and `xml.etree`. Consequences: no supply-chain surface, nothing for `pip-audit` to find in
production code, CI installs in seconds, and the whole thing runs anywhere Python 3.11 does.

The cost is hand-written parsers instead of `requests` + `pydantic` + `pandas`. At this scale that
is a good trade.

### Git as the database

JSON files committed to the repository. Free, durable, diffable, and the history is the backup.
Render's free filesystem is ephemeral and could not be trusted with this; a free-tier hosted
database would silently expire. See `SECURITY.md` for what is deliberately *not* stored here.

### A static dashboard with no write endpoints

The page reports state; the CLI changes it. No API, no auth, no injection surface. Clicking an
action shows the command to run. This is why the dashboard can be published publicly without
exposing control of the business.

### Capability probes, not config flags

`state.probe_capabilities()` runs a live check per capability. A config flag records an *intention*;
a probe records *reality*. A probe that raises is recorded as DOWN with the exception text — never
silently green. The publish gate additionally refuses any build containing a green light with no
supporting detail.

---

## Module map

| Module | Responsibility | Notable invariant |
|---|---|---|
| `models.py` | The normalized schema | `ai_cash_cost` and `ai_usage_units` are separate fields |
| `config.py` | Cost gate, paths, operator profile | `request()` takes no override parameter |
| `state.py` | Run state, capabilities | Only the transition functions may set `ACTIVE` |
| `storage.py` | Persistence | Every write passes the secret guard |
| `scoring.py` | 0–100 with evidence | Hard rejects short-circuit before scoring |
| `money.py` | Revenue → profit | Decisions use *effective* hours, not human hours |
| `proposals.py` | Drafting | A claim not in the profile raises |
| `analytics.py` | Metrics | `None` means Insufficient Data, never `0` |
| `connectors/` | One per source | Cannot-automate raises, never returns `[]` |
| `fulfillment/` | Worker, reviewer, pipeline | Reviewer cannot receive worker notes |
| `dashboard/` | Static build + verify | Refuses to publish a broken build |

---

## The three that carry the real judgment

### Scoring — `scoring.py`

Six factors totalling 100: Skill Fit 25, Automation Potential 20, Expected Profitability 20,
Likelihood of Winning 15, Clarity 10, Risk 10. Hard rejects run **before** scoring and
short-circuit it; penalties apply after.

Every factor records points awarded, points available, and the evidence in the listing that drove
it. The dashboard renders that verbatim. A single opaque number is not a score.

**`SOURCE_WIN_PRIOR` is deliberately unflattering about Upwork** (0.25 vs 0.85 for Hacker News),
because a new account with no Job Success Score genuinely does lose bids before the proposal is
read. Scoring that flattered us there would send you to spend Connects you cannot win back.

### Money — `money.py`

Revenue is not profit. Platform fee, payment fee, AI cash cost and infrastructure come out
separately, and each figure carries `method` and `inputs` so the dashboard can show the derivation.

The decision metric is **profit per effective hour**, where
`effective = human_hours + 0.25 × ai_hours`. Dividing by human hours alone reported "$833/hour" on
a 90%-automatable $500 job, which made every automatable job look identically perfect and destroyed
the ranking. AI hours are cheaper than human hours, not costless.

Profitability blends rate (60%) with absolute net profit on a saturating curve (40%), because rate
alone ranked a $450 job above a $2,475 one.

### Worker / Reviewer — `fulfillment/`

The reviewer's independence is **structural**. `reviewer.review()` takes requirements, acceptance
criteria and artifacts — it does not take the `Job` object, so there is no path by which
`Job.worker_notes` could reach it. `assert_reviewer_input_clean` enforces it at runtime, and two
tests enforce it statically: one asserts the signature has no `job` parameter, the other reads the
pipeline source and asserts the call site does not forward worker notes.

A reviewer told "the worker thinks this is done" grades the claim, not the artifact. That is how AI
review theatre happens: two models agreeing with each other while neither checks.

QA is real work, not a rubric. Spreadsheet QA counts rows, columns, nulls, duplicates and ragged
rows and compares against the known input count. Code QA parses the AST, runs ruff and pytest, and
scans for dangerous patterns — and explicitly refuses to call untested code working. Research QA
detects placeholder URLs and unhedged absolute claims.

Verdicts: ≥90 READY, 75–89 AUTO_REVISE, <75 HUMAN_REVIEW. **A security or file-integrity failure
escalates regardless of the average** — you do not average your way past a security finding. At
most two automatic revision loops, then it goes to you.

---

## Where the AI plugs in

`ClaudeWorker.available()` returns true only when `CLAUDE_CODE_OAUTH_TOKEN` is present. It does
**not** fabricate output when unavailable — it raises, and the pipeline falls back to
`RuleBasedWorker`, which does genuinely useful deterministic work (the spreadsheet consolidation
path is real). The dashboard reports the AI worker as NOT CONFIGURED rather than showing green over
a fallback.

When a token is present, the agent executes inside a Claude Code GitHub Action and works directly
in the job workspace; `ClaudeWorker.execute` is the handoff point rather than an inference call
from this process.

---

## Data flow in one run

```
discover → normalize → dedupe (source:external_id, or content hash)
  → score (writes factors + evidence onto the record)
  → archive rejects → draft proposals ≥65 → build dashboard → verify → publish
  → commit data/ back to the repo
```

Each step is a CLI subcommand, so the workflow YAML is a list of commands and every step is
reproducible locally.
