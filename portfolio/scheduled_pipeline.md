# A multi-source data pipeline that runs itself on a schedule

Two production pipelines ingest third-party feeds on a cron schedule, rebuild a committed data store, and publish a dashboard - unattended, for $0 a month in infrastructure.

## The problem

Data that has to be refreshed several times a day from providers who do not coordinate with each other. Done by hand it is a chore nobody keeps up; done badly by a machine it is worse, because a missed refresh produces a page that looks current and is not.

## Approach

- Every source is fetched by an idempotent refresh step, so a re-run repairs rather than duplicates.
- The data store is plain text committed to the repository, which makes every change diffable and every bad value traceable to the run that introduced it.
- Scheduling is tiered rather than uniform: frequent during the hours that matter, skipped overnight when nothing changes, because Actions minutes are a real budget.
- The published page carries its own freshness state, so a stale build announces itself instead of pretending.

## Outcome

- Runs unattended on GitHub Actions' free tier; no server, no subscription, no cloud bill.
- The public dashboard shows its own last-refresh time and a data-status indicator on every load.

**Stack:** python, github actions, cron, rest api, etl, github pages

## Evidence you can check yourself

- A live dashboard, published automatically by a scheduled pipeline, showing its own refresh age and data status.  
  nfl-dashboard, GitHub Pages - <https://amercado19.github.io/nfl-dashboard/>
- The generated dashboard repository, updated by the pipeline rather than by hand.  
  nfl-dashboard repository - <https://github.com/amercado19/nfl-dashboard>

## Available on request

These live in private repositories, so they are offered for a walkthrough rather than
asserted to someone who cannot open them:

- 28 GitHub Actions workflow definitions covering refresh, retrain, backtest, health check and deploy.  
  nfl-pipeline (private)
- A cron schedule whose comments record why each slot exists, including the extra runs added after a real missed deadline.  
  mlb-pipeline (private)

---

*Relevant to: data_pipeline, automation, api_integration, data_engineering, scheduled_automation.*
