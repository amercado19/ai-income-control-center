# Acquisition channels — what was measured, so it is not re-measured

**Updated:** 2026-09-14. Standing rule: *do not repeatedly analyze the same rejected opportunity.*
Each entry below is a channel that was tested against live data and the number that closed it.
Re-open one only if the underlying market or platform changes, not because it sounds promising.

## Freelancer.com — REJECTED, market not defect

`aicc discover` reports `freelancer_com  0 found`. That looked like a broken connector. It is not.

Pulled the raw feed the connector uses (HTTP 200, 80 live projects across our seven job ids):

| | count |
|---|---|
| projects returned | 80 |
| USD | 45 |
| USD **and** ≤25 bids | 3 |
| …and at or above the $50 floor | **0** |

**42 of 45 USD projects already carry more than 25 bids.** The three that do not are under $50.
Tried `sort_field=time_submitted` and `reverse_sort` in both directions — the API returns the same
window, so there is no "fresher" slice to reach. The filters are working correctly on a market that
does not contain winnable work at our floor.

Bidding also consumes bid credits beyond a small free monthly allowance, which collides with the
$0 rule even if a candidate appeared. **Leave the connector as it is.** A zero from it is a correct
answer, not a bug to fix.

## Job-board sources (hackernews, himalayas, remoteok, weworkremotely, python_jobs) — LOW VALUE

103 listings in the 2026-09-14 cycle. The top three scored results were *Lead Healthcare
Integration Engineer*, *Controller* and *ML Engineer* — full-time roles, not discrete gigs. They
score well on profitability precisely because the scorer reads an annualized or hourly rate, and
badly where it matters: win probability 9.9/18, "applicant volume unknown and often high".

Full-time for-profit employment is a hard reject on PSLF grounds anyway. These sources are worth
running because they cost nothing and occasionally surface a contract, but **they are not the path
to the first order** and should not absorb capacity.

## Fiverr Briefs — PUSH ONLY, cannot be pursued

The old Buyer Requests board was retired in 2022-23. Its replacement, Briefs, has no browsable
listing: a buyer submits a brief and Fiverr's matching routes it to a small number of sellers,
weighted toward seller level and Success Score. There is nothing to search and nothing to answer
until one is routed to us, and a zero-review seller is at the bottom of that ranking.

Briefs arrive in the Fiverr inbox, which is off-limits, so the only compliant way we learn about
one is a notification email — which is exactly why `WATCH_QUERY` is sender-scoped rather than
subject-scoped. See `src/aicc/gmail_intake.py`.

## Fiverr message notifications — UNVERIFIED, worth confirming

A message from a buyer arrived in the Fiverr seller UI on 2026-09-14 within minutes of the first
gig going live. **No corresponding email reached Gmail.** Either Fiverr does not email on a first
message, or the notification is switched off in account settings.

This matters more than it sounds: the Gmail watch is the only automated way this system learns a
buyer exists, and Fiverr ranks sellers on response time. If those emails are off, every buyer
inquiry is invisible until Andres happens to open the app. Confirming the setting is a one-minute
human task with a large payoff — NEEDS ANDRES.

## The channel that is fenced off — NEEDS ANDRES

Everything currently listed is commodity work: Python pipelines, spreadsheet cleanup, report
automation. Thousands of sellers offer it and a zero-review seller ranks below all of them.

The genuinely scarce thing on the verified profile is research-grants and sponsored-programs
finance with advanced Excel — effort reporting, award budget reconciliation, F&A calculations,
burn-rate trackers. That is a narrow market with few credible sellers and buyers who can pay.

It is also the exact market the **outside-activity review** covers, which is why Gig 3 is on HOLD.
So the review is not blocking one gig. It is blocking the only differentiated position available,
while the storefront competes on price in the most crowded category on the platform. Reading the
employer's outside-activity, consulting, conflict-of-interest and IP policy is the highest-leverage
unblocked action in this entire system, and it costs nothing but Andres's time.

Nothing here is a recommendation to proceed without that answer. PSLF-qualifying employment depends
on the standing, and that outranks any gig.
