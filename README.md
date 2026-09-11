# AI Income Control Center

A compliant, zero-cash-cost operations system for an AI-assisted freelance business: it watches
sources that permit it, scores what it finds transparently, drafts specific proposals, runs won
work through a worker/reviewer pipeline with real QA, and tells you exactly what needs your
attention.

**Additional monthly cost: $0.00.**

### What it is honestly for

Two halves, and they are not equally productive:

* **Inbound — the Fiverr storefront.** Fiverr has no discovery surface at all, so the work is
  front-loaded into four validated gigs and buyers come to you. This is the half that makes money.
  See `FIVERR LAUNCH CENTER` in the dashboard.
* **Outbound — a contract-role watcher.** The free, permitted sources are *job boards*, not
  project marketplaces, and what they contain is ongoing roles rather than discrete gigs.

That second sentence is measured, not assumed. `scripts/market_reality_check.py` sampled 417 live
Freelancer.com projects in these categories: 78 bidders on the median $250 job, and exactly one
listing survived the full funnel - a crypto fraud product the risk scoring rejects. See **D13** in
`DECISIONS.md`. Re-run the script before believing it; that is what it is for.

So a scan returning zero STRONG matches is usually the market, not a broken threshold.

---

## The one principle

**Never fake a capability.**

A channel that cannot be automated says so. A number that is an estimate is labelled an estimate.
A status light is green only when a probe proved it green. A rate quoted in PLN is not reported as
dollars. A metric with too few observations reads *Insufficient Data*, not `0`.

A system that lies about what it can do is worse than no system, because you act on it.

---

## What is actually automated

| | Discovery | Proposal | Submit | Deliver |
|---|---|---|---|---|
| **Hacker News** | Automatic | Drafted | You send | You send |
| **Himalayas / RemoteOK / WWR** | Automatic | Drafted | You send | You send |
| **Contra** | Via Contra's MCP, interactive | Drafted | You confirm | You send |
| **Upwork** | Via Upwork's MCP, interactive | Drafted | **You confirm — costs Connects** | You send |
| **Fiverr** | **Nothing to scan** | n/a | n/a | You send |
| **Freelancer.com** | Automatic, filtered hard | Drafted | You send | You send |
| **Python.org Jobs** | Automatic | Drafted | You send | You send |

Upwork, Contra and Fiverr are not automated because **they do not permit it**, not because the
code is unfinished. `docs/MARKETPLACE_RULES.md` quotes the governing rule for each.

Submission and delivery are human-gated everywhere, by design.

---

## Quick start

```bash
pip install -r requirements-dev.txt
export PYTHONPATH=src

python -m aicc demo          # full 25-step lifecycle, no network, no marketplace
python -m aicc status        # one-screen summary
python -m aicc health        # probe every capability, honestly
python -m aicc connectors    # what each source actually permits
python -m aicc build --out site && open site/index.html
```

`python -m aicc demo` is the acceptance test. It runs in CI on every push.

## Running it for real

```bash
python -m aicc start                     # START BUSINESS
python -m aicc discover --limit 50       # scan permitted sources
python -m aicc fiverr check               # validate the gig kit against Fiverr's limits
python3 scripts/market_reality_check.py   # re-measure whether the open market is winnable
python3 scripts/browser_test.py           # drive the built dashboard in a real browser
python -m aicc top --limit 10            # ranked, with full score reasoning
python -m aicc draft --min-score 65      # draft proposals (nothing is sent)
python -m aicc approve <proposal_id>     # approve one
python -m aicc emergency-stop            # kill all external actions now
```

---

## Layout

```
src/aicc/
  models.py          normalized schema: opportunity, proposal, job, QA, revenue, audit
  config.py          the cost gate, paths, and the operator profile
  state.py           START/PAUSE/EMERGENCY STOP; honest capability reporting
  scoring.py         transparent 0-100 scoring with per-factor evidence
  money.py           revenue vs profit; AI cash cost vs AI usage draw
  proposals.py       specific proposals; claims verified against real artifacts
  analytics.py       metrics that say Insufficient Data rather than zero
  health.py          live probes and the LIVE-mode checklist
  storage.py         JSON persistence with a secret-leak guard on every write
  audit.py           append-only log of every consequential action
  connectors/        one module per source, each declaring what it may do
  fulfillment/       worker, independent reviewer, and the job pipeline
  dashboard/         static site generator + publish gate
```

## Documentation

| Document | Answers |
|---|---|
| [`ARCHITECTURE.md`](docs/ARCHITECTURE.md) | How it fits together and why |
| [`MARKETPLACE_RULES.md`](docs/MARKETPLACE_RULES.md) | What each platform permits, quoted, with URLs |
| [`ZERO_COST_LIMITS.md`](docs/ZERO_COST_LIMITS.md) | What $0.00 buys and what it costs you |
| [`SECURITY.md`](docs/SECURITY.md) | Where client data lives and why it is never committed |
| [`DEPLOYMENT.md`](docs/DEPLOYMENT.md) | Getting it running on GitHub |
| [`OPERATIONS.md`](docs/OPERATIONS.md) | Daily use |
| [`TROUBLESHOOTING.md`](docs/TROUBLESHOOTING.md) | When something is wrong |
| [`TOOLS_AND_SKILLS.md`](docs/TOOLS_AND_SKILLS.md) | What is installed, and what it cost |
| [`../DECISIONS.md`](../DECISIONS.md) | Architectural decisions and their reasoning |
| [`../PROJECT_STATUS.md`](../PROJECT_STATUS.md) | Current state — start here in a new session |

---

## Dependencies

**Zero runtime dependencies.** The package uses only the Python standard library. No supply chain,
no `pip-audit` surface, instant CI. Dev tooling (ruff, mypy, pytest, pip-audit) is in
`requirements-dev.txt`.
