# Security

**Security beats automation.** Where the two conflict in this codebase, automation loses.

---

## The data boundary

There are exactly two places data lives, and the distinction is enforced by code, by `.gitignore`,
and by CI.

### Committed to the repository — `data/`

Public job listings, scores, proposals, system state, the audit log, and revenue records. All of it
is either already public or is our own operational bookkeeping. It is committed deliberately: git
history is the backup, it is diffable, and it costs nothing.

### Never committed — `workspaces/`

Client files, client-confidential content, work product in progress. Gitignored, and
`scripts/secret_scan.py` fails CI if anything under `workspaces/`, `jobs_private/` or
`client_files/` ever becomes tracked.

**Nothing else is acceptable.** Credentials, session cookies, OAuth tokens and payment data go in
neither — they live in GitHub Actions secrets or your local environment.

---

## Three independent layers against a leak

A credential has to get past all three:

1. **Write-time** — `aicc.storage.assert_safe_to_commit` runs on **every** write and raises
   `UnsafeToCommitError` on anything matching a credential or personal-identifier pattern. This
   catches a leak *before* it reaches disk.
2. **Commit-time** — `scripts/secret_scan.py` scans every tracked file in CI. This catches a leak
   that reached disk but has not yet reached a remote.
3. **Structural** — `.gitignore` blocks the directories where sensitive material lives, and the
   audit log summarizer truncates payloads so a client file body can never end up in a log line
   (the audit log *is* committed).

Patterns screened: Anthropic API keys and OAuth tokens, GitHub tokens and fine-grained PATs, AWS
access keys, private key blocks, Slack tokens, Google API keys, Stripe live keys, JWTs, payment
card numbers, US SSNs, and session cookie headers.

---

## Confidential client work is deliberately disabled

There is no zero-cost datastore that is both durable and appropriately secured for
client-confidential material. So the interface exists, and the feature is off.

Jobs involving confidential client data should be handled manually until there is revenue to fund
proper infrastructure (`ZERO_COST_LIMITS.md` has the upgrade ladder). Shipping it anyway on
"good enough" storage would be the single worst decision available here.

---

## Path containment

`aicc.fulfillment.worker._safe_path` resolves every deliverable filename inside the job workspace
and raises `WorkspaceEscapeError` on anything that escapes. Filenames from a client brief are
sanitized to `[A-Za-z0-9._-]` before use, so `../../../etc/passwd` in a requirements list cannot
write outside the workspace. Tested.

---

## Credentials

| Secret | Purpose | Where |
|---|---|---|
| `CLAUDE_CODE_OAUTH_TOKEN` | AI worker and reviewer, $0.00 cash | GitHub Actions secret |
| `GITHUB_TOKEN` | Provided automatically by Actions | Never stored |

Generate the Claude token with `claude setup-token` locally. It authenticates against your
subscription — it is **not** an API key and does not enable pay-as-you-go billing.

**No credential is ever printed.** The workflow reports credential *state* (`HAS_CLAUDE=true/false`)
and never the value. A missing credential degrades the run and reports `BLOCKED BY CREDENTIAL`
rather than failing silently or pretending to work.

---

## Dashboard exposure

The dashboard is a **static page with no write endpoints**. There is no API to attack, no session
to steal, no injection surface — every action runs through the CLI, which requires shell access to
the repository.

This is a deliberate trade. A dashboard with live controls would need authentication, and there is
no free authentication approach here that is worth trusting with control over a business. Rather
than invent weak password security (which spec §37 explicitly forbids), the controls simply are not
exposed to the web. The page shows a CLI command instead of performing the action.

The page carries `noindex, nofollow` and inlines all CSS and JS — no CDN, no external script, no
`localStorage` beyond a theme preference (wrapped in try/catch, since storage throws in private
windows).

---

## What this system refuses to do

Enforced in code, not policy:

- **Prohibited actions** — CAPTCHA solving, bot-detection evasion, rate-limit circumvention,
  AI-detection evasion, multi-accounting, credential sharing, off-platform payment. Connectors for
  platforms that forbid automation raise `NotPermittedError`; they do not quietly return nothing.
- **Human-only actions** — delivery, submission, payment, price changes, and enabling LIVE mode.
  `pipeline.deliver` refuses any non-human actor. Tested for every actor type.
- **Unverifiable claims** — `proposals._verify_claims` raises rather than generating a proposal
  claiming experience not backed by a real artifact in the operator profile.
- **Cash spend** — the cost gate fails closed with no override path.

---

## Reporting

This is a single-operator system. If you find a way past any of the above, the fix is a commit and
a test that would have caught it.

---

## Pre-live security review — 11 Sep 2026

Run before enabling live mode, as required. Eight checks, two findings, both fixed.

| Check | Result |
|---|---|
| Public repo contains no secrets | **PASS** — 91 tracked files, `scripts/secret_scan.py` clean. The only credential-shaped strings are the scanner's own detection regexes. |
| No contact information improperly retained | **PASS** — zero files in `data/` carry surviving contact details. |
| External job text sanitised | **PASS** — all 75 stored listings excerpted to ≤1500 chars; `full_description` is an attribute, not a field, and is never persisted. |
| Prompt-injection defences | **PASS** — 51-case regression suite, zero attacks missed, zero false positives on real listings. |
| Workflow permissions least privilege | **PASS** — CI holds `contents: read` only. Scheduled workflows hold `contents/pages/id-token/issues: write`, each used. |
| Unsafe shell interpolation | **TWO FINDINGS, FIXED** — see below. |
| No customer-sensitive files public | **PASS** — `workspaces/`, `jobs_private/`, `client_files/` gitignored and untracked. |
| Emergency stop | **PASS** — blocks outbound actions and states a reason. |

### Finding 1 — caller-supplied values interpolated into shell scripts

```yaml
printf '%s\n' "${{ inputs.steps }}" > steps.txt     # then eval'd line by line
git commit -m "${{ inputs.commit_message }} ..."
```

A `${{ }}` expansion is substituted as **text** before bash parses the line, so a quote or a
`$(...)` inside the value escapes the string and executes.

**Exploitable today?** No. The only callers are checked-in workflow files in this repository, so
nothing untrusted reaches those inputs. But that is a property of the callers rather than of the
file being called, and `discover.yml` already accepted a `workflow_dispatch` input that the
obvious next edit would have piped into that `eval`.

**Fixed** by passing both through `env:` and referencing them as `"$VAR"`, where the value is
data rather than syntax. The dispatch input is now wired up the same way — it had been accepted
on the form and silently ignored, which is a control that lies about what it does.

### Finding 2 — the check that found it was itself wrong

The detector was added to `scripts/validate_workflows.py` so this cannot recur. Its first
version matched only `        run: |` and missed `      - run: |` and single-line `run:`
entirely — it reported **clean** on a file containing the exact problem it was written to find.

That is worse than having no check, because it converts an unknown into a false assurance. It
now handles all three spellings and is tested in both directions: three unsafe forms must be
flagged, three safe ones (`env:`-passed, `with:`-block, no interpolation) must not.
