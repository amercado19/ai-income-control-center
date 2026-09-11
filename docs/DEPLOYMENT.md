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
