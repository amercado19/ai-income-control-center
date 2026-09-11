# A monitor that makes silent failure impossible

An hourly cloud-side check that fails loudly when the pipeline stops producing fresh data - written specifically because a green run log is not proof the data moved.

## The problem

The dangerous failure in any scheduled job is not the one that crashes. It is the one where the run goes green, the push silently does not happen, and the output quietly ages while everyone assumes it is current.

## Approach

- A separate workflow checks the age of the last commit to the published data, independently of the job that is supposed to produce it.
- Over four hours stale during active hours and the check fails; GitHub's own failure notification does the alerting, so there is no paid alerting service.
- The error message names the exact place to look, including the specific failure mode where a run is green but the push did not land.
- It exits green during the off-season rather than crying wolf for months.

## Outcome

- Replaced a health check that depended on a particular laptop being awake.
- Costs nothing: one short job an hour on the free tier, and email notifications GitHub already sends.

**Stack:** github actions, monitoring, bash, rest api

## Evidence you can check yourself

- A staleness monitor running in a public repository, with its reasoning and its limitations written into the file.  
  mlb-dashboard/.github/workflows/freshness-alarm.yml - <https://github.com/amercado19/mlb-dashboard>

---

*Relevant to: automation, monitoring, scheduled_automation, data_pipeline.*
