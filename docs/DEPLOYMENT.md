# Deployment

## Prerequisites

- A GitHub account (you have one: `amercado19`)
- Python 3.11+ locally
- Claude Code installed locally, signed in with your Max subscription

---

## 1. Create the repository

```bash
cd ai-income-control-center
gh repo create amercado19/ai-income-control-center --public --source=. --push
```

**Public is the recommended choice**, and not only for cost. Public repositories get **unlimited
GitHub Actions minutes**, so this project competes with your MLB and NFL pipelines for nothing. The
private free allowance is 2,000 minutes/month across the whole account, and NFL alone uses
730–1,200 in season.

Nothing confidential is in the repository by design — see `SECURITY.md`. If you make it private
anyway, raise the discovery cron from every 6 hours to every 12.

---

## 2. Add the Claude credential

This is what makes the AI worker and reviewer run unattended at **$0.00 cash**.

```bash
claude setup-token          # prints a long-lived OAuth token
```

Then add it as a repository secret named `CLAUDE_CODE_OAUTH_TOKEN`:

```bash
gh secret set CLAUDE_CODE_OAUTH_TOKEN --repo amercado19/ai-income-control-center
```

From Anthropic's own documentation:

> `CLAUDE_CODE_OAUTH_TOKEN`: an OAuth token that authenticates with your Claude subscription,
> available on Pro, Max, Team, and Enterprise plans.

> If you authenticate with an OAuth token, runs use your Claude subscription instead of API billing.

**Do not set `ANTHROPIC_API_KEY`.** That enables pay-as-you-go billing and is outside the Phase 1
zero-cost rule. The health probe reports an API key as DEGRADED for exactly this reason.

If you skip this step entirely the system still runs — the rule-based worker and reviewer carry the
pipeline, and the dashboard honestly reports the AI worker as NOT CONFIGURED.

---

## 3. Enable GitHub Pages

Repository → Settings → Pages → **Source: GitHub Actions**.

The dashboard deploys from the `_reusable-run.yml` workflow after passing verification. It will
appear at:

```
https://amercado19.github.io/ai-income-control-center/
```

---

## 4. First run

```bash
gh workflow run discover.yml
gh run watch
```

Then check the run summary. It reports credential state, what each source returned, and the
health probe results.

---

## Workflows

| Workflow | Trigger | Approx. minutes/month |
|---|---|---|
| `ci.yml` | push, PR | ~3 per push |
| `discover.yml` | every 6 hours | ~240 |
| `health.yml` | daily 07:25 ET | ~30 |
| `_reusable-run.yml` | called by the above | — |

Total ≈ **270 minutes/month**, which is free and unmetered on a public repository.

Crons are deliberately conservative. `scripts/validate_workflows.py` fails CI on anything more
aggressive than hourly, and on a scheduled workflow with no timeout.

---

## What CI enforces

Every push must pass all of:

- `ruff format --check` and `ruff check`
- `mypy src`
- `pytest` (120 tests)
- `validate_workflows.py` — cron aggressiveness, timeouts, inlined secrets
- `secret_scan.py` — credentials and personal identifiers in tracked files
- `pip-audit`
- **The cost ceiling assertion** — CI fails if `MAX_NEW_MONTHLY_CASH_SPEND` is no longer `0.00`
- **The full demo lifecycle** — the 25-step acceptance test
- **Dashboard build + verify**

A failing verification refuses to publish rather than replacing a working dashboard with a broken
one.

---

## Local development

```bash
pip install -r requirements-dev.txt
export PYTHONPATH=src
pytest -q
python -m aicc demo
```

There are no runtime dependencies to install.

---

## Rollback

Operational data is committed, so any state is recoverable:

```bash
git log --oneline -- data/
git checkout <sha> -- data/
```

To stop everything immediately without touching git:

```bash
python -m aicc emergency-stop --reason "..."
```

Disable the schedules in the GitHub UI (Actions → workflow → Disable) if you want the cloud side
stopped too.

---

## Claude subscription authentication — verified against official docs, 11 Sep 2026

Andres asked for this to be settled from current official Anthropic documentation rather than
inference. It is settled, and the answer is yes.

### Officially supported, at $0 additional cost

> "`CLAUDE_CODE_OAUTH_TOKEN`: an OAuth token that authenticates with your Claude subscription,
> available on Pro, Max, Team, and Enterprise plans. Generate one by running `claude setup-token`
> locally."

And, unambiguously, on billing:

> "**If you authenticate with an OAuth token, runs use your Claude subscription instead of API
> billing.**"

— <https://code.claude.com/docs/en/github-actions>

So `claude setup-token` → `CLAUDE_CODE_OAUTH_TOKEN` → the `claude_code_oauth_token` workflow input
draws on the subscription, not on metered credits. `ANTHROPIC_API_KEY` is the alternative, is
**not** required, and is the thing to avoid: it is what enables pay-as-you-go.

### Unattended execution is supported

The docs describe an **automation mode** entered by supplying a `prompt` input, and document
running on a `cron` schedule directly. There is no requirement for a human to be present.

### Three operational gotchas, all from the same page

These are the details that turn a working setup into a silently broken one months later:

1. **A scheduled run is attributed to a human, and a bot is rejected.**

   > "This check also applies to scheduled runs, which GitHub attributes to a repository user,
   > usually **the one who last changed the workflow's `cron` schedule**."

   So **Andres must be the last person to edit a cron line**, or the run is refused as a bot
   actor. This is the same constraint his `mlb-dashboard` freshness alarm already documents in
   its own header comment, arrived at there by experience rather than from the docs.

2. **Scheduled workflows are disabled after 60 days of repository inactivity**, and run only from
   the default branch. Anthropic's page states this independently of GitHub's own. See
   `docs/OPERATIONS.md` for the mitigation.

3. **The token is tied to the person who generated it.**

   > "an OAuth token is tied to the subscription of the person who ran `claude setup-token`"

   Fine for a personal repository. It also means the token cannot be shared or inherited, and
   Anthropic recommends an API key instead for org-wide use — which this project will not do.

### What Andres has to do, and why it cannot be done for him

```bash
claude setup-token                                    # prints a token. Do not paste it into chat.
gh secret set CLAUDE_CODE_OAUTH_TOKEN --repo amercado19/ai-income-control-center
```

Two commands, and both must be his. `claude setup-token` authenticates interactively against his
subscription, and the second handles the resulting credential. An assistant holding a token that
can act as his Claude subscription is exactly what the credential boundary exists to prevent, and
that does not change because the token is short-lived or because permission was granted in
advance.

Until the secret exists, `HAS_CLAUDE` is `false`, the rule-based worker carries the pipeline, and
the dashboard reports the AI worker as **NOT CONFIGURED** rather than pretending. The workflow
checks only that the secret is non-empty — never its value.

### Token lifetime

The official page does not state an expiry for `CLAUDE_CODE_OAUTH_TOKEN`, and it is not inferred
here. Treat it as long-lived but finite: `src/aicc/degradation.py` classifies a rejected
credential as **NEEDS_HUMAN** and fails the run loudly, precisely so an expiry surfaces as a clear
instruction rather than as a pipeline that quietly stops producing.
