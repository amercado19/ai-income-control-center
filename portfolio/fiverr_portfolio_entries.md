# Fiverr portfolio entries — ready to paste

Three case studies for the Fiverr portfolio (`fiverr.com/users/amercado19/portfolio/new`).

**Rules these were written under** (Andres, 2026-09-13): no repo URLs, no project names, no betting
content or P&L, no employer names, no AICC internals, no pricing floors or capacity limits, no
order/revenue statistics. Nothing may imply paid client work. No invented clients, companies,
revenue, usage volume, years of operation, uptime, or performance results. The one permitted
quantitative claim is the ~50% reconciliation-time reduction from the resume, with the employer
unnamed.

**Form fields deliberately left blank:** Project duration, Project cost, Project started on.
These are personal projects with no client and no fee; entering a cost would fabricate a paid
engagement, and the dates are not known to me. Leave them empty.

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

The entries were built but could not be submitted. Chrome's window on the linked machine reports
a 0×0 viewport, so the page does not render. Plain text inputs can still be set programmatically
(the name and description went in), but the Industry selector is a React component that only
registers a real click in a rendered window, and Fiverr will not accept the form without it.

Nothing here is lost. With the window visible, each entry is: paste name, pick Industry
"Data Analytics", paste description, attach the image, Continue.
