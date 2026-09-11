# Troubleshooting

## "REFUSED: System is off"

```bash
python -m aicc start
```

If that also refuses, the emergency stop is engaged and must be cleared deliberately:

```bash
python -m aicc resume
```

A corrupt state file also fails closed to OFF rather than failing open to ACTIVE. That is intended.

---

## Opportunity Sources shows RED

The health probe could not reach any source. Check what it actually said:

```bash
python -m aicc health
```

The blocking reason names each source and its error.

| Error | Meaning |
|---|---|
| `Tunnel connection failed: 403` | An egress proxy or network policy is blocking the host. Not a code problem — the same code works on a GitHub Actions runner, which has open egress. |
| `HTTP 429` | Rate limited. The 6-hour schedule is far under every published limit, so this usually means something else is hammering the API from the same IP. |
| `Malformed JSON` | The source changed its response shape. Check the parser against the live response. |
| `Could not locate a current 'Who is hiring?' thread` | The monthly thread has not been posted yet, or the title format changed. |

A single source failing degrades to YELLOW; all of them failing is RED. Neither is silent.

---

## AI Worker shows WHITE / NOT CONFIGURED

Expected when `CLAUDE_CODE_OAUTH_TOKEN` is not set. The rule-based worker carries the pipeline.

To enable it: `claude setup-token`, then add the value as a repository secret. See
`DEPLOYMENT.md`.

**Do not** set `ANTHROPIC_API_KEY` instead — that bills per token and the probe will report
DEGRADED for exactly that reason.

---

## A cost request was declined

Working as designed. `MAX_NEW_MONTHLY_CASH_SPEND` is `0.00` and the gate fails closed.

```bash
python -m aicc costs      # every request and its decision
```

To actually spend money you edit the constant in `src/aicc/config.py` and commit the change. There
is no runtime override, and CI asserts the value is still zero — so raising it is a deliberate,
reviewed act rather than something that happens by accident at 2am.

---

## A job is stuck in PROBLEM

```bash
python -m aicc status
```

`human_action_required` on the job says why. The two common causes:

**Incomplete brief** — no requirements, no acceptance criteria, no price, or no client. Fill those
in and re-run; the pipeline refuses to work on a brief where "done" is undefined.

**QA could not pass in two revisions** — the last QA round's findings say what failed. Look at the
critical findings first. If the acceptance criteria are genuinely unsatisfiable (a required column
that cannot exist, for example), fix the criteria rather than the worker.

---

## QA is failing on something that looks fine

Read the finding. The common false-positive shape is the acceptance-criteria keyword check, which
is explicitly labelled as *"keyword check only, not a semantic check"* — it can flag a criterion
that is genuinely met but phrased differently in the deliverable.

If a *risk* detector is misfiring, that is a real bug worth fixing. One already happened: an early
version matched the bare word "credentials" and penalised a client for asking that credentials be
kept out of the codebase — i.e. for having good security practice. `tests/test_scoring_and_money.py`
now has a regression guard for it. Add one for whatever you find.

---

## The dashboard did not update

The publish gate refused the build. Check the workflow run summary — it will say
`Dashboard publish: REFUSED - verification failed`. The previous working dashboard stays live
deliberately.

Reproduce locally:

```bash
python -m aicc build --out site
python -m aicc verify-site --site site
```

The JSON output names the failing check. `no unexplained green lights` means a capability reported
GREEN without supporting detail, which the gate treats as a lie and blocks.

---

## CI fails on the cost ceiling assertion

Someone changed `MAX_NEW_MONTHLY_CASH_SPEND`. If that was deliberate and approved, update the
assertion in `ci.yml` in the same commit so the change is explicit in the diff. If it was not
deliberate, revert it.

---

## Secret scan failed

```bash
python scripts/secret_scan.py
```

It prints file and line. Remove the credential, then **rotate it** — assume anything that reached
disk is compromised. If the file is a legitimate exception (a test fixture, or documentation
describing what is blocked), add its path to `ALLOWLIST` in the scanner, not to `.gitignore`.

If it reports something under `workspaces/` as tracked, that is a client-data leak in progress:

```bash
git rm -r --cached workspaces/
```

---

## Everything looks broken and I want it to stop

```bash
python -m aicc emergency-stop --reason "investigating"
```

This always succeeds — a stop must never fail. It disables every automation, blocks the pipeline,
and persists across restarts. Then disable the scheduled workflows in the GitHub UI (Actions →
workflow → Disable) to stop the cloud side too.

Nothing is lost. All state is committed to git.
