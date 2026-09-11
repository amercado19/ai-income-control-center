# Tools and skills

Everything used to build and run this, and what each cost.

**Total additional cost: $0.00.**

---

## Runtime — what the system itself needs

| Tool | Version | Cost | Why |
|---|---|---|---|
| Python | 3.11+ | Free | Already installed; matches the NFL/MLB pipelines |
| **Nothing else** | — | — | Zero runtime dependencies by design (see `DECISIONS.md` D2) |

The package imports only the standard library: `urllib.request` for HTTP, `re`/`csv`/`json`/
`xml.etree` for parsing, `dataclasses` for the schema, `subprocess` for code QA.

---

## Development tooling

| Tool | Version | Cost | Purpose |
|---|---|---|---|
| ruff | 0.15.11 | Free, OSS | Format + lint. Same version as the NFL pipeline. |
| mypy | 1.20.2 | Free, OSS | Type checking, `check_untyped_defs = true` |
| pytest | 9.0.3 | Free, OSS | 120 tests |
| pip-audit | 2.9.0 | Free, OSS | Dependency audit |

All reputable, all already in use in the NFL pipeline, none requiring elevated permissions.

---

## Infrastructure

| Service | Tier | Cost | Notes |
|---|---|---|---|
| GitHub | Existing account | $0.00 | Repository + secrets |
| GitHub Actions | Public repo | $0.00 | **Unlimited minutes on public repos.** ~270 min/month used. |
| GitHub Pages | Free | $0.00 | 1 GB storage, 100 GB/mo bandwidth. We ship a ~90 KB page. |
| Google Fonts | Free | $0.00 | IBM Plex Sans/Mono. Real fallback stack, so the page degrades gracefully if blocked. |
| **Render** | — | $0.00 | **Deliberately unused.** Free instances sleep and the filesystem is ephemeral, which is unusable for persistence. GitHub Pages is strictly better and equally free. |

### Verified free, no automatic conversion to paid

Each of these was checked against the three conditions in the brief — genuinely free, does not
auto-convert to billing, does not require enabling overages:

- **GitHub Actions on public repos** — unlimited, no billing relationship.
- **GitHub Pages** — free tier, no card, no overage mechanism.
- **Claude subscription OAuth token** — uses the existing Max subscription. Explicitly *not* an API
  key, so it cannot trigger pay-as-you-go billing.

---

## Data sources

| Source | API key? | Card? | Cost | Attribution required |
|---|---|---|---|---|
| Hacker News (Algolia) | No | No | $0.00 | No |
| Himalayas | No | No | $0.00 | No |
| RemoteOK | No | No | $0.00 | **Yes** — rendered on the dashboard |
| We Work Remotely | No (RSS) | No | $0.00 | No |
| Upwork MCP | No (OAuth) | No | $0.00 to use; Connects cost to apply | No |
| Contra MCP | No (OAuth) | No | $0.00, commission-free | No |

---

## Claude capabilities used

| Capability | Used for | Cost |
|---|---|---|
| Claude Code (this session) | Building the system | Existing Max subscription |
| Web search / fetch | Verifying marketplace rules against primary sources | Existing subscription |
| `dataviz` skill | The validated colour palette — CVD-checked, status colours kept separate from series colours | Free, bundled |
| `artifact-design` skill | Dashboard design calibration | Free, bundled |
| Subagents | Parallel marketplace research | Existing subscription |

### Available but deliberately not used

- **Browser automation (Claude in Chrome)** — available, and not needed. Every marketplace rule was
  verified from official documentation via web fetch. Using a browser to *read* job listings on a
  site that prohibits automated access would violate the same rule as scraping, so there was no
  legitimate use for it here.
- **Google Drive, Robinhood, Sentry, Credit Karma connectors** — present in the session, unrelated
  to this project, untouched.

---

## Not installed

Nothing was installed beyond the four dev tools above, all of which were already present in the
environment and are already used by the NFL pipeline. No browser extensions, no third-party
services, no packages requiring elevated permissions, no accounts created.

---

## The Claude authentication finding

Worth recording, because the brief was right to be sceptical and the answer turned out well.

`claude setup-token` produces `CLAUDE_CODE_OAUTH_TOKEN`, which authenticates GitHub Actions runs
against a Pro/Max/Team/Enterprise subscription. From Anthropic's current documentation:

> If you authenticate with an OAuth token, runs use your Claude subscription instead of API billing.

So unattended AI work is **supported, official, and $0.00 cash**. It draws against the Max
subscription allowance rather than dollars, which is why the money engine tracks `ai_cash_cost` and
`ai_usage_units` as separate figures.

`ANTHROPIC_API_KEY` is the thing to avoid — it enables pay-as-you-go billing. The health probe
reports its presence as DEGRADED rather than healthy, for that reason.
