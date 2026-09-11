# Portfolio

Case studies drawn from real production work. Every claim is checkable; the ones that
are not publicly checkable are marked as such rather than dressed up.

> nfl-pipeline and mlb-pipeline are private repositories; nfl-dashboard and mlb-dashboard are public and are what those pipelines produce. Proposals cite only the public evidence. The private material is for a call, where it can be shown.

> These pipelines model sports betting markets. What is being sold is the engineering - scheduling, reconciliation, calibration, failure handling - not the subject. Nothing in them is betting, investment or financial advice.

## [A multi-source data pipeline that runs itself on a schedule](scheduled_pipeline.md)

Two production pipelines ingest third-party feeds on a cron schedule, rebuild a committed data store, and publish a dashboard - unattended, for $0 a month in infrastructure.

- Verify: <https://amercado19.github.io/nfl-dashboard/>
- Verify: <https://github.com/amercado19/nfl-dashboard>

## [A monitor that makes silent failure impossible](failure_alerting.md)

An hourly cloud-side check that fails loudly when the pipeline stops producing fresh data - written specifically because a green run log is not proof the data moved.

- Verify: <https://github.com/amercado19/mlb-dashboard>

## [A model pipeline that publishes its own limitations](honest_reporting.md)

Calibration reports, a model card and a written record of known limitations, published alongside the projections rather than instead of them.

- Verify: <https://amercado19.github.io/nfl-dashboard/>

## [A test suite and CI gate that refuse to publish a broken build](tested_delivery.md)

Lint, type checking, tests and a verify-before-publish step, so a broken build never replaces a working page.

- Verify: <https://github.com/amercado19/ai-income-control-center>
