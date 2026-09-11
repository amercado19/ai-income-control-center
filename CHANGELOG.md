# Changelog

## 0.1.0 — 2026-09-11

First build. A compliant, zero-cash-cost freelance operations system.

### Added

**Core**
- Normalized opportunity/proposal/job/QA/revenue/audit schema with `ai_cash_cost` and
  `ai_usage_units` as deliberately separate fields
- System state machine: START / PAUSE / RESUME / EMERGENCY STOP, persisted, failing closed
- JSON storage committed to git, with a credential-pattern guard on every write
- Append-only audit log with payload truncation (the log is committed, so it must not carry
  client content)
- Cost gate at `MAX_NEW_MONTHLY_CASH_SPEND = 0.00`, failing closed with no override path

**Scoring and money**
- Six-factor 0–100 scoring, each factor recording awarded points, available points, and the
  evidence from the listing that drove it
- Hard rejects: academic dishonesty, scam patterns, credential sharing, off-platform payment,
  physical presence, equity-only, client-prohibited AI
- Profitability engine separating revenue from net profit and cash cost from usage draw

**Connectors**
- Automated: Hacker News (Algolia), Himalayas, RemoteOK, We Work Remotely
- Assisted via the vendor's own MCP: Upwork, Contra
- Inbound only: Fiverr
- Demo generator spanning every score band including the rejection paths
- Rate extraction that handles real listing formats and refuses non-USD currencies

**Fulfillment**
- Worker/reviewer separation enforced structurally, not by convention
- Real QA: spreadsheet (row/column/null/duplicate/ragged counts vs known input), code (AST parse,
  ruff, pytest, dangerous-pattern scan), research (placeholder-URL and overclaiming detection)
- Two automatic revision loops, then escalation to a human
- Delivery refuses any non-human actor

**Interface**
- Static dashboard, 13 sections, responsive, dark mode, IBM Plex, CVD-validated palette
- Every metric inspectable: formula, source, observation count, timestamp
- Publish gate that refuses a broken build and refuses any green light without supporting evidence

**Engineering**
- 120 tests
- CI: format, lint, types, tests, workflow validation, secret scan, dependency audit,
  cost-ceiling assertion, full demo lifecycle, dashboard verification
- Scheduled discovery every 6 hours and a daily health check, both deliberately conservative
- Nine documentation files

### Fixed during the build

- **Security detector false positive.** Matching the bare word "credentials" fired on
  *"keep credentials out of the codebase"* — a client asking for good security practice — and cost
  a legitimate $1,440 job 15 points. Patterns are now specific phrases; a regression test covers it.
- **Profit-per-hour absurdity.** Dividing net profit by human hours alone reported "$833/hour" on a
  90%-automatable $500 job. Decisions now use effective hours (`human + 0.25 × ai`).
- **Ranking inversion.** Scoring on rate alone ranked a $450 job above a $2,475 one. Profitability
  now blends rate with absolute net profit on a saturating curve.
- **Rate extraction missing real formats.** `$45-70 USD per hour` — verbatim from a live HN listing
  — did not parse, and `180-280 PLN` would have been read as dollars. Now handles intervening
  currency codes and rejects non-USD.

### Known limitations

- Fiverr cannot be scanned. No discovery surface exists.
- Upwork is capped at roughly 1–2 free proposals per month by the Connects system.
- Confidential-client-data jobs are deliberately disabled pending real infrastructure.
- Live network verification of the discovery connectors is pending the first GitHub Actions run;
  the build sandbox's egress policy blocks those hosts.
