# Fiverr portfolio entries — ready to paste

Three case studies for the Fiverr portfolio (`fiverr.com/users/amercado19/portfolio/new`).

**Rules these were written under** (Andres, 2026-09-13): no repo URLs, no project names, no private-project
content, no employer names, no AICC internals, no pricing floors or capacity limits, no
order/revenue statistics. Nothing may imply paid client work. No invented clients, companies,
revenue, usage volume, years of operation, uptime, or performance results. The one permitted
quantitative claim is the ~50% reconciliation-time reduction from the resume, with the employer
unnamed.

**Form fields that cannot be left blank — see the bottom of this file.** The plan was to leave
Project duration, Project cost and Project started on empty, because these are personal projects
with no client and no fee. Fiverr requires all three, and rejects `0` as a cost. That is an open
decision for Andres, not something to work around.

**Industry:** Data Analytics (all three).

**Attachment:** the matching diagram from `portfolio/fiverr_case_images/`. Fiverr recommends
1024×768 at 4:3, which is what these are.

---

## 1. Scheduled Python data pipeline

*Attachment: `case1_pipeline.png`*

```
Personal project. A technical demonstration of how I build these, not client work.

THE PROBLEM
Someone on the team rebuilds the same report every week by hand: pull from a few places,
reshape it, send it on. It gets skipped when they are busy, and mistakes surface later.

THE SOLUTION
One scheduled job that pulls from each source, maps the column names that do not agree,
validates what it produced, and writes the output - unattended, on a schedule.

ARCHITECTURE
Schedule triggers the run. Extract from each source (REST API, Excel or CSV, database).
Normalize to one schema. Validate, reconciling rows in against rows out. Write the output.
A source that fails raises an alert instead of publishing an empty result.

TECHNOLOGIES
Python, GitHub Actions, cron, REST APIs, unit tests, CSV / Excel / SQLite.

DELIVERABLES
Documented source code that is yours to keep, the working schedule, a short runbook, and the
reconciliation report each run produces.

WHY IT MATTERS
The failure that costs money is not the job crashing - it is the job succeeding with half the
data. Reconciling row counts on every run is what makes that visible.
```

Sells: Gig 1 (data pipeline), Gig 2 (scheduled automation).

---

## 2. Automated failure monitoring and alerting

*Attachment: `case2_monitoring.png`*

```
Personal project. A technical demonstration of how I build these, not client work.

THE PROBLEM
A scheduled job exits cleanly, the log is green, and the data never moved. The report still
renders, so nobody notices until a decision has already been made on stale numbers.

THE SOLUTION
A health check that runs separately from the pipeline and asks whether fresh data actually
landed - because the pipeline cannot be the thing that certifies itself.

ARCHITECTURE
The check runs on its own schedule and tests: is the newest record newer than the last run;
did the row count move in a direction that makes sense; did every source answer, or did one
quietly return nothing. Pass, and the output publishes. Fail, and you get an alert while the
output carries a visible staleness flag.

TECHNOLOGIES
Python, GitHub Actions, cron, email alerting, freshness and row-count checks.

DELIVERABLES
The monitoring job, the alert configuration, and a short runbook for what to do when it fires.

WHY IT MATTERS
You find out from an alert rather than from a client asking why the numbers look wrong.
```

Sells: Gig 2 (scheduled automation), and the reliability argument behind Gig 1.

---

## 3. Excel and CSV reconciliation automation

*Attachment: `case3_reconciliation.png`*

```
A technical demonstration of an approach I have used in a previous finance role. Not a client
project.

THE PROBLEM
Several files, several column layouts, dates in three formats, duplicate records, a merged
header row somebody added years ago. Consolidating it by hand is slow and quietly lossy.

THE SOLUTION
A repeatable cleanup: map the columns that do not agree, deduplicate by a rule you define,
standardize dates, numbers and text, then reconcile the counts before and after.

ARCHITECTURE
Messy inputs, then column mapping, deduplication, standardization, and reconciliation - out to
one clean dataset plus an exceptions list naming every row held back and why.

TECHNOLOGIES
Excel, Power Query, VBA, Python, CSV / XLSX, automated validation.

DELIVERABLES
The clean consolidated file, the exceptions report, and - where the scope includes it - the
script, so you can re-run the same cleanup yourself next quarter.

WHY IT MATTERS
1,000 rows in, 1,000 rows accounted for. Silent row loss is the most common way this work goes
wrong, and you should never have to take it on trust. In a previous finance role, automating
this kind of reconciliation reduced the time it took by roughly half.
```

Sells: Gig 4 (spreadsheet cleanup).

**One line to check before publishing:** the last sentence of #3 is the resume's ~50% figure.
It names no employer and says "previous finance role" rather than implying a client engagement.
If that still reads as ambiguous to you, delete the sentence — the rest of the entry stands
without it.

---

## Why these are not live yet

Not a rendering problem any more — the browser works, and entry 1 was filled in completely on
2026-09-14: name, Industry "Data Analytics", the full 1,138-character description, and
`case1_pipeline.png` attached and previewing correctly. Continue then failed validation on three
fields that were supposed to stay empty:

```
Project duration    Add a project duration.     (required; 1-7 days / 7-30 days / 1-3 months / 3-6 months / 6+ months)
Project cost        Add a project cost.         (required; $0 is rejected, a positive number clears it)
Project started on  You must select a month.    (required, MM + YY)
                    You must select a year.
```

Tested directly against the live form. Nothing was submitted, and the cost field was cleared again
afterwards, so no false figure was left sitting in a draft.

This is structural, not a quirk. Fiverr's portfolio is designed for delivered client work: the
name placeholder is a client campaign, the description prompt asks about "your client, their
goals, any challenges that came up", and step 2 is "Link to catalog". Filling in a duration, a
date and a dollar amount for a project with no client and no fee would publish three untrue claims
on a public profile.

### DEFERRED — waiting for the first eligible real delivered project

**Andres decided this on 2026-09-14.** Do not fabricate or assign hypothetical values for project
cost, duration or start date. These entries stay saved here for future use and are **not** published
through Fiverr while Fiverr requires factual fields that do not truthfully exist for unpaid personal
or demo work.

**Do not spend more time trying to work around the form.** After a real order is delivered,
evaluate whether that project suits the portfolio and whether Fiverr and client permissions allow
showcasing it — using only actual price, dates, duration, deliverables and permitted information.

Profile strength is not worth delaying revenue for. The three gig gallery images per listing are
the visual proof for launch. Everything below stays ready: the copy, and the diagrams in
`portfolio/fiverr_case_images/`.
