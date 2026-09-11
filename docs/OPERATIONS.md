# Operations

## The daily loop

Open the dashboard. Read **NEEDS ME**. Do those things. Close it.

If NEEDS ME is empty, everything permitted is running and there is nothing for you to do. That is
the intended state.

---

## Commands

```bash
export PYTHONPATH=src

# control
python -m aicc start                        # START BUSINESS
python -m aicc pause                        # pause all automation
python -m aicc resume
python -m aicc emergency-stop --reason "…"  # disable every external action, now
python -m aicc stop

# see
python -m aicc status                       # one screen
python -m aicc health                       # probe every capability
python -m aicc connectors                   # what each source actually permits
python -m aicc top --limit 10               # ranked opportunities with full reasoning
python -m aicc audit --limit 40
python -m aicc costs                        # every cost request and its decision

# act
python -m aicc discover --limit 50
python -m aicc draft --min-score 65 --limit 5
python -m aicc approve <proposal_id>
python -m aicc mark-submitted <proposal_id> --connects 8

# build
python -m aicc build --out site
python -m aicc verify-site --site site
python -m aicc demo                         # full acceptance lifecycle
python -m aicc clear-demo                   # remove synthetic records
```

---

## Approving a proposal

```bash
python -m aicc top --limit 5          # read the reasoning, not just the score
python -m aicc approve prop_abc123
```

For a non-Upwork source this marks it APPROVED and tells you to send it yourself. **The system
never sends anything.** You paste it into your own mail client or the platform's own interface,
then record it:

```bash
python -m aicc mark-submitted prop_abc123
```

For Upwork, `approve` first prices the Connects through the cost gate:

```json
{
  "connects_required": 12,
  "free_connects_remaining": 10,
  "billable_connects": 2,
  "cash_cost": 0.30,
  "approved": false,
  "reason": "DECLINED. Requested $0.30/mo exceeds the ceiling of $0.00/mo."
}
```

Within the free allowance it passes at $0.00. Beyond it, the gate declines and you decide.

Note: the free-Connect counter is **local**, not read from Upwork — they expose no balance API at
this tier. If you submit proposals directly on upwork.com it will drift. It is a spending guard,
not an authoritative balance.

---

## AI worker handoff

The worker has two implementations and the system always tells you which is running.

**Rule-based** (no credential): deterministic, always available. Genuinely does spreadsheet
consolidation and data cleaning — the highest-volume category this business targets.

**Claude** (with `CLAUDE_CODE_OAUTH_TOKEN`): the agent runs inside a Claude Code GitHub Action and
works directly in the job workspace. `ClaudeWorker.execute` is the handoff point, not an inference
call from the CLI process. Running it locally raises `NotImplementedError` and the pipeline falls
back to rule-based rather than fabricating output.

Check which you have:

```bash
python -m aicc health | grep -A1 "AI Worker"
```

---

## Handling a job

Jobs move themselves from RECEIVED to READY_TO_DELIVER or PROBLEM. You only act at the ends.

- **READY_TO_DELIVER** — QA passed. Review the deliverable, send it to the client, then mark it
  delivered.
- **PROBLEM** — QA failed twice, or the brief was incomplete. `human_action_required` says why.

The pipeline never reaches DELIVERED on its own. `pipeline.deliver()` refuses any non-human actor.

---

## Reading a score

```bash
python -m aicc top --limit 3
```

Every factor shows points, available points, and the evidence from the listing. If a score looks
wrong, the evidence tells you which factor is misfiring — that is the point of printing it.

Bands: **90+ EXCELLENT · 80–89 STRONG · 65–79 REVIEW · below 65 SKIP.**

Rejections are separate from low scores. A rejected opportunity scores 0 with a reason, and the
reason is non-negotiable: academic dishonesty, scam patterns, credential sharing, off-platform
payment, physical presence, equity-only, or client-prohibited AI.

The AI-prohibited rejection can be overridden **only** by choosing to do the work by hand:

```python
scoring.score_opportunity(opp, allow_manual_ai_prohibited=True)
```

That books zero AI hours and applies a 25-point penalty, because you have given up the entire
automation advantage. Accepting such a job and quietly using AI anyway is dishonest, and the system
will not help you do it.

---

## Enabling LIVE mode

```bash
python -m aicc status        # shows the checklist
```

Every item must pass. The checklist is enforced in code, not advisory — `acknowledge_live_mode()`
refuses if anything fails, including "demo data cleared" and "emergency stop tested".

Test your emergency stop before you rely on it. That is why it is on the checklist.

---

## Monthly

1. `python -m aicc costs` — confirm the additional cost is still $0.00.
2. Check the scoring calibration on the Analytics page. Once there are 5+ decided outcomes it
   compares the scores of won vs lost work. If separation is negative, the score is **inverted**
   and should not be trusted until investigated.
3. Weight changes are never automatic. Edit `scoring.WEIGHTS` in a commit, so the change is visible
   and reversible.
