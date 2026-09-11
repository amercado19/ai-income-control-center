"""Static dashboard builder.

Produces a single self-contained HTML file: CSS and JS are inlined, there are no external
requests, and no CDN is required. That makes it deployable to GitHub Pages for free, viewable
offline, and publishable as an Artifact without a content-security-policy fight.

``verify_site`` is the publish gate, modelled on the NFL pipeline's verify-before-publish step:
a build that fails verification is refused rather than deployed, so a broken dashboard never
replaces a working one.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .. import analytics, audit, fiverr_kit, health, money, portfolio, storage
from ..config import BRAND_NAME, BRAND_SHORT, COST_REQUESTS, MAX_NEW_MONTHLY_CASH_SPEND
from ..connectors import registry
from ..models import JobStatus
from ..state import SystemState

ASSETS = Path(__file__).parent / "assets"

# The one external request on the page. Google Fonts is the only font host permitted under an
# Artifact content-security-policy, and the CSS declares real fallbacks, so the page degrades to
# the system sans rather than breaking if the request is blocked or offline.
FONTS = (
    '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
    '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?'
    "family=IBM+Plex+Sans:wght@400;500;600;700&"
    'family=IBM+Plex+Mono:wght@400;500&display=swap">'
)

SHELL = """<div class="shell">
  <nav class="nav" id="nav" aria-label="Sections">
    <div class="brand">
      <div class="brand-name">{brand}</div>
      <div class="brand-sub">{short} &middot; <span id="built-at"></span></div>
    </div>
    <div class="nav-items" id="nav-items"></div>
    <div class="nav-foot"><div id="cost-line"></div></div>
  </nav>
  <div class="scrim" id="scrim" hidden></div>
  <div class="main">
    <header class="topbar">
      <button class="menu-btn" id="menu" aria-label="Open navigation">&#9776;</button>
      <h1 id="page-title">Overview</h1>
      <div class="spacer"></div>
      <div class="sysbar"><span class="lamp WHITE" id="sys-lamp"></span><span id="sys-text"></span></div>
      <button class="icon-btn" id="theme" aria-label="Toggle theme">Theme</button>
    </header>
    <main class="content" id="pages"></main>
  </div>
</div>
<div class="card card-pad" id="cmdbox" hidden
     style="position:fixed;left:50%;bottom:22px;transform:translateX(-50%);z-index:100;max-width:min(560px,92vw)">
  <div style="font-weight:600;margin-bottom:6px">Run this to perform the action</div>
  <p style="margin:0 0 10px;font-size:12.5px;color:var(--ink-2);line-height:1.5">
    This dashboard is a static page, so it reports state rather than changing it. Actions run through the CLI,
    which keeps every consequential change inside the audited, version-controlled path.</p>
  <pre class="proposal" id="cmdbox-cmd"></pre>
  <div class="btn-row" style="margin-top:10px"><button class="btn" id="cmdbox-close">Close</button></div>
</div>"""


def _collect() -> dict[str, Any]:
    st = SystemState.load()
    status_text, light = health.overall_status()
    metrics = analytics.compute_metrics(include_demo=(st.mode == "DEMO"))
    prov = analytics.describe_metrics(metrics)

    opps = sorted(storage.opportunities.all(), key=lambda o: o.score, reverse=True)
    props = storage.proposals.all()
    jobs = storage.jobs.all()
    rev = storage.revenue.all()

    opp_rows = []
    for o in opps:
        econ = money.compute(o)
        bd = o.score_breakdown or {}
        opp_rows.append(
            {
                "id": o.id,
                "source": o.source,
                "title": o.title,
                "client": o.client,
                "category": o.category,
                "url": o.url,
                "status": o.status,
                "score": o.score,
                "band": o.score_band or "SKIP",
                "budget_display": o.budget_display(),
                "budget_min": o.budget_min,
                "budget_max": o.budget_max,
                "net": econ.expected_net_profit,
                "human_hours": econ.estimated_human_hours,
                "automation_pct": money.automation_pct(econ),
                "posted_time": o.posted_time,
                "is_demo": o.is_demo,
                "rejected": bool(bd.get("rejected")),
                "rejection_reason": bd.get("rejection_reason", ""),
                "factors": bd.get("factors", {}),
                "penalties": bd.get("penalties", []),
                "econ_method": f"{econ.method} {econ.inputs.get('effort_basis', '')}",
            }
        )

    opp_by_id = {o.id: o for o in opps}
    prop_rows = []
    for p in props:
        opp = opp_by_id.get(p.opportunity_id)
        note = ""
        if p.source == "upwork":
            note = (
                "Upwork charges Connects to submit. 10 free per month, 4-16 per proposal, "
                "$0.15 each beyond that, non-refundable if you lose."
            )
        prop_rows.append(
            {
                "id": p.id,
                "source": p.source,
                "status": p.status,
                "body": p.body,
                "opportunity_title": opp.title if opp else p.problem_statement[:80],
                "quoted_price": p.quoted_price,
                "claims_made": p.claims_made,
                "ai_disclosure_included": p.ai_disclosure_included,
                "is_demo": p.is_demo,
                "submit_cost_note": note,
            }
        )

    job_rows = [
        {
            "id": j.id,
            "title": j.title,
            "client": j.client,
            "source": j.source,
            "status": j.status,
            "agreed_price": j.agreed_price,
            "progress": j.progress_pct(),
            "qa_score": j.latest_qa_score(),
            "revision_count": j.revision_count,
            "deliverables": j.deliverables,
            "human_action_required": j.human_action_required,
            "qa_findings": (j.qa_rounds[-1].get("findings", []) if j.qa_rounds else []),
            "is_demo": j.is_demo,
        }
        for j in jobs
    ]

    clients: dict[str, dict[str, Any]] = {}
    for j in jobs:
        if not j.client:
            continue
        c = clients.setdefault(j.client, {"name": j.client, "source": j.source, "jobs": 0, "revenue": 0.0})
        c["jobs"] += 1
    for r in rev:
        if r.client in clients:
            clients[r.client]["revenue"] += r.net

    # Persisted capabilities are already plain dicts. When none have been probed yet, probe now
    # rather than rendering an empty health page - but never synthesize a status.
    if st.capabilities:
        caps = list(st.capabilities.values())
    else:
        from ..state import probe_capabilities

        caps = [c.to_dict() for c in probe_capabilities()]

    connectors = []
    for _name, cls in registry().items():
        caps_obj = cls.CAPS
        entry = caps_obj.to_dict()
        entry["light"] = caps_obj.status_light()
        connectors.append(entry)

    declined = 0
    if COST_REQUESTS.exists():
        for line in COST_REQUESTS.read_text(encoding="utf-8").splitlines():
            try:
                if not json.loads(line).get("approved"):
                    declined += 1
            except json.JSONDecodeError:
                continue

    events = audit.read_all(limit=250)
    attribution = []
    if any(o.source == "remoteok" for o in opps):
        attribution.append({"name": "Remote OK", "url": "https://remoteok.com"})

    return {
        "brand": BRAND_NAME,
        "built_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "system": {
            "run_state": st.run_state,
            "mode": st.mode,
            "status_text": status_text,
            "light": light,
            "blocked_reason": st.why_blocked(),
        },
        "metrics": metrics,
        "provenance": prov,
        "min_observations": analytics.MIN_OBSERVATIONS_FOR_RATE,
        "opportunities": opp_rows,
        "proposals": prop_rows,
        "jobs": job_rows,
        "clients": sorted(clients.values(), key=lambda c: -c["revenue"]),
        "revenue": [r.to_dict() for r in sorted(rev, key=lambda r: r.received_at, reverse=True)],
        "capabilities": caps,
        "connectors": connectors,
        "automations": list(st.automations.values()),
        "attention": _attention(jobs, props),
        "audit": [e.to_dict() for e in events[:200]],
        "errors": [e.to_dict() for e in events if e.result in ("error", "refused")][:20],
        "live_checklist": health.live_mode_checklist(),
        "calibration": analytics.scoring_calibration(),
        "cost": {"ceiling": MAX_NEW_MONTHLY_CASH_SPEND, "declined": declined},
        "attribution": attribution,
        "fiverr_kit": fiverr_kit.summary(),
        "portfolio": portfolio.summary(),
    }


def _attention(jobs: list[Any], props: list[Any]) -> list[dict[str, Any]]:
    """Spec section 29: the single screen that answers 'what do I have to do?'"""
    items: list[dict[str, Any]] = []

    awaiting = [p for p in props if p.status == "AWAITING_APPROVAL"]
    for p in awaiting:
        items.append(
            {
                "severity": "normal",
                "title": "Proposal awaiting your approval",
                "detail": p.problem_statement[:200],
                "value": f"${p.quoted_price:,.0f}" if p.quoted_price else "",
                "actions": [
                    {"label": "APPROVE", "cmd": f"approve:{p.id}", "primary": True},
                    {"label": "Skip", "cmd": f"reject:{p.id}"},
                ],
            }
        )

    for j in jobs:
        if j.status == JobStatus.READY_TO_DELIVER.value:
            items.append(
                {
                    "severity": "normal",
                    "title": f"Job ready for delivery: {j.title}",
                    "detail": f"QA {j.latest_qa_score()}/100 after {j.revision_count} revision(s). Delivery is never automatic.",
                    "value": f"${j.agreed_price:,.0f}",
                    "actions": [{"label": "APPROVE DELIVERY", "cmd": f"deliver:{j.id}", "primary": True}],
                }
            )
        elif j.status == JobStatus.PROBLEM.value:
            items.append(
                {
                    "severity": "critical",
                    "title": f"Job needs you: {j.title}",
                    "detail": j.human_action_required or "The pipeline stopped and could not continue.",
                    "value": f"${j.agreed_price:,.0f}",
                    "actions": [{"label": "Open job", "cmd": f"job:{j.id}"}],
                }
            )
    return items


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------


def _page_parts() -> tuple[str, str, str, dict[str, Any]]:
    css = (ASSETS / "styles.css").read_text(encoding="utf-8")
    js = (ASSETS / "app.js").read_text(encoding="utf-8")
    data = _collect()
    body = SHELL.format(brand=BRAND_NAME, short=BRAND_SHORT)
    return css, js, body, data


def _data_script(data: dict[str, Any]) -> str:
    # </script> inside JSON would close the tag early; escaping the slash is the standard fix.
    payload = json.dumps(data, default=str).replace("</", "<\\/")
    return f"<script>window.DATA={payload};</script>"


def build_fragment() -> str:
    """Body-only output, for hosts that supply their own document skeleton."""
    css, js, body, data = _page_parts()
    return f"<title>{BRAND_NAME}</title>\n{FONTS}\n<style>{css}</style>\n{body}\n{_data_script(data)}\n<script>{js}</script>"


def build_site(out: Path) -> Path:
    """Full standalone document, for GitHub Pages."""
    css, js, body, data = _page_parts()
    out.mkdir(parents=True, exist_ok=True)
    html = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="color-scheme" content="light dark">
<meta name="robots" content="noindex, nofollow">
<title>{BRAND_NAME}</title>
{FONTS}
<style>{css}</style>
</head>
<body>
{body}
{_data_script(data)}
<script>{js}</script>
</body>
</html>"""
    (out / "index.html").write_text(html, encoding="utf-8")
    (out / "data.json").write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    (out / ".nojekyll").write_text("", encoding="utf-8")
    return out / "index.html"


# ---------------------------------------------------------------------------
# Publish gate
# ---------------------------------------------------------------------------

REQUIRED_MARKERS = ["window.DATA=", 'id="nav-items"', 'id="pages"', "AI Income Control Center"]


def verify_site(site: Path) -> tuple[bool, dict[str, Any]]:
    """Refuse to publish a broken build. Mirrors the NFL pipeline's verify_site step."""
    report: dict[str, Any] = {"checks": [], "ok": True}

    def check(name: str, passed: bool, detail: str = "") -> None:
        report["checks"].append({"name": name, "passed": passed, "detail": detail})
        if not passed:
            report["ok"] = False

    index = site / "index.html"
    check("index.html exists", index.exists(), str(index))
    if not index.exists():
        return False, report

    html = index.read_text(encoding="utf-8")
    check("index.html is substantial", len(html) > 20_000, f"{len(html):,} bytes")
    for marker in REQUIRED_MARKERS:
        check(f"contains {marker!r}", marker in html)

    check("no external script tags", 'src="http' not in html and "src='http" not in html, "The page must be self-contained.")

    data_file = site / "data.json"
    check("data.json exists", data_file.exists())
    if data_file.exists():
        try:
            data = json.loads(data_file.read_text(encoding="utf-8"))
            check("data.json parses", True)
            check("system block present", "system" in data and "light" in data["system"])
            check("metrics block present", "metrics" in data)
            # Honesty gate: a green light with no successful probe is a lie.
            caps = data.get("capabilities", [])
            green_without_detail = [c for c in caps if c.get("light") == "GREEN" and not c.get("detail")]
            check(
                "no unexplained green lights",
                not green_without_detail,
                f"{len(green_without_detail)} capability/capabilities green with no supporting detail",
            )
        except json.JSONDecodeError as exc:
            check("data.json parses", False, str(exc))

    return report["ok"], report
