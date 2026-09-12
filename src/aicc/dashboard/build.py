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

from .. import analytics, audit, capacity, compliance, fiverr_kit, fiverr_wizard, health, money, portfolio, profit, scheduler, storage
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

    from .. import policy as _policy

    opp_rows = []
    for o in opps:
        econ = money.compute(o)
        bd = o.score_breakdown or {}
        # The gate is shown on the listing itself, not only in the queue it was removed from.
        # Without this the two pages contradicted each other: the Profit Queue declined the
        # contract-to-permanent role, while the Opportunities page went on presenting it as a
        # REVIEW candidate at score 69 with an Open button.
        _verdict = _policy.evaluate(o)
        _gate = _verdict.gates[0] if _verdict.gates else None
        opp_rows.append(
            {
                "policy_allowed": _verdict.allowed,
                "policy_gate": _gate["gate"] if _gate else "",
                "policy_reason": _gate["detail"] if _gate else "",
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
                "opportunity_class": o.opportunity_class,
                "win_estimate": o.win_estimate or {},
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
        "fiverr_wizard": fiverr_wizard.plan(),
        "portfolio": portfolio.summary(),
        "repo_url": _repo_url(),
        "compliance": compliance.panel(),
        "capacity": capacity.snapshot(),
        "emergency_scope": st.emergency_stop_scope(),
        **_profit_section(opps, jobs),
    }


def _repo_url() -> str:
    """The repository this dashboard was built from, for deep-linking the control workflow.

    Read from the Actions environment when running there, and from the git remote otherwise.
    Returns "" rather than a guess if neither is available - a control button that links to the
    wrong repository is worse than one that explains it cannot link anywhere.
    """
    import os
    import subprocess

    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if repo:
        return f"{server}/{repo}"
    try:
        remote = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""
    if remote.startswith("git@github.com:"):
        remote = "https://github.com/" + remote.split(":", 1)[1]
    return remote.removesuffix(".git")


def _profit_section(opps: list[Any], jobs: list[Any]) -> dict[str, Any]:
    """The Profit Queue and Profit panels.

    Scored, live opportunities only. A rejected listing has no place in a plan, and a demo row
    in a profit projection is how a synthetic number ends up being believed.

    Accepted jobs enter as committed candidates so their capacity is subtracted before anything
    speculative is considered - which is the mechanism, not a nicety: it is what makes "a paid
    deadline is not endangered by twenty small opportunities" true rather than intended.
    """
    from ..models import JobStatus as _JobStatus

    candidates: list[scheduler.Candidate] = []

    # Every pipeline state before delivery. A job in any of these is an obligation whose
    # capacity is already spoken for; DELIVERED and PROBLEM are not drawing on the window.
    open_jobs = {
        _JobStatus.RECEIVED.value,
        _JobStatus.VALIDATE.value,
        _JobStatus.PLAN.value,
        _JobStatus.WORK.value,
        _JobStatus.VERIFY.value,
        _JobStatus.QA.value,
        _JobStatus.FIX.value,
        _JobStatus.FINAL_QA.value,
        _JobStatus.READY_TO_DELIVER.value,
    }
    for j in jobs:
        if getattr(j, "is_demo", False) or j.status not in open_jobs:
            continue
        ai_minutes = max(30.0, float(getattr(j, "estimated_hours", 0.0) or 1.0) * 60.0 * 0.8)
        prof = profit.ProfitProfile(
            opportunity_id=j.id,
            title=j.title,
            category=getattr(j, "job_type", "") or "generic",
            expected_gross_revenue=float(getattr(j, "agreed_price", 0.0) or 0.0),
            expected_net_revenue=float(getattr(j, "agreed_price", 0.0) or 0.0),
            expected_net_profit=float(getattr(j, "agreed_price", 0.0) or 0.0),
            win_probability=1.0,
            win_probability_basis="Accepted. This is an obligation, not a bid.",
            expected_value=float(getattr(j, "agreed_price", 0.0) or 0.0),
            estimated_claude_minutes=ai_minutes,
            total_claude_minutes=round(ai_minutes * 1.85, 1),
            andres_active_minutes=20.0,
            estimated_completion_hours=float(getattr(j, "estimated_hours", 0.0) or 1.0),
            delivery_confidence=0.9,
        )
        candidates.append(scheduler.Candidate(prof, committed=True))

    profiles: list[profit.ProfitProfile] = []
    for o in opps:
        if o.is_demo or (o.score_breakdown or {}).get("rejected"):
            continue
        if o.status in {"REJECTED", "ARCHIVED", "LOST"}:
            continue
        prof = profit.build(o)
        profiles.append(prof)
        if prof.is_profitable:
            candidates.append(scheduler.Candidate(prof))

    est = capacity.estimate()
    p = scheduler.plan(candidates, est=est)
    return {
        "profit": {
            **profit.totals(profiles),
            "expected_captured_profit": p.expected_captured_profit,
            "expected_missed_profit": p.expected_missed_profit,
            "capacity_utilization_pct": p.to_dict()["capacity_utilization_pct"],
            "lanes": p.lane_summary(),
            "committed_minutes": p.committed_minutes,
            "available_minutes": p.available_minutes,
        },
        "profit_queue": scheduler.profit_queue(p, limit=15),
        "plan_notes": p.notes,
        # Blocked work is shown, not silently dropped. A listing that vanishes teaches nothing;
        # "$1,200 declined because it converts to permanent employment" is a statement Andres can
        # check, and can overrule with his eyes open if the screen ever gets one wrong.
        "policy_blocked": [
            {
                "title": pr.title,
                "source": pr.source,
                "gross": pr.expected_gross_revenue,
                "gate": pr.policy_gate,
                "reason": pr.policy_reason,
            }
            for pr in sorted(profiles, key=lambda x: -x.expected_gross_revenue)
            if not pr.policy_allowed
        ],
    }


def _attention(jobs: list[Any], props: list[Any]) -> list[dict[str, Any]]:
    """Spec section 29: the single screen that answers 'what do I have to do?'"""
    items: list[dict[str, Any]] = []

    # An approval card must carry enough to make the decision it is asking for. Every card
    # previously read "Proposal awaiting your approval" with the raw problem statement under
    # it - no indication of WHICH opportunity, no score, and no way to read what would actually
    # be sent. On a phone that is a one-click approval for something unread, which is the same
    # fake autonomy this system refuses everywhere else, just pointed the other way.
    from . import build as _self  # noqa: F401  (kept for clarity; storage is imported at module scope)

    opps_by_id = {o.id: o for o in storage.opportunities.all()}
    awaiting = [p for p in props if p.status == "AWAITING_APPROVAL"]
    for p in awaiting:
        opp = opps_by_id.get(p.opportunity_id)
        title = opp.title if opp else (p.problem_statement[:70] or "Proposal")
        meta: list[str] = []
        if opp:
            meta.append(f"{opp.score:.0f} {opp.score_band}")
            meta.append(opp.source)
            if opp.budget_display():
                meta.append(opp.budget_display())
        gaps = [
            pen["evidence"]
            for pen in (opp.score_breakdown.get("penalties", []) if opp else [])
            if pen.get("name") == "Unmet stated requirements"
        ]

        # A blocked proposal must not present an APPROVE button. This card is the single screen
        # that answers "what do I have to do?", and one of these was a contract-to-permanent role
        # sitting one tap from approval with nothing on the card to say so. Drafting now refuses
        # such work and `approve` refuses it again, but a card that still offers the button is a
        # trap even when the button is wired to fail.
        from .. import policy as _policy

        _blocked = None
        if opp is not None:
            _v = _policy.evaluate(opp)
            if not _v.allowed and _v.gates:
                _blocked = _v.gates[0]

        items.append(
            {
                "severity": "stop" if _blocked else "normal",
                "title": title,
                "detail": p.problem_statement[:200],
                "meta": " · ".join(meta),
                "caveat": (f"{_blocked['gate']} - {_blocked['detail']}" if _blocked else (gaps[0] if gaps else "")),
                "body": p.body,
                "url": opp.url if opp else "",
                "value": f"${p.quoted_price:,.0f}" if p.quoted_price else "",
                "policy_gate": _blocked["gate"] if _blocked else "",
                "actions": (
                    [{"label": "Discard", "cmd": f"reject:{p.id}"}]
                    if _blocked
                    else [
                        {"label": "APPROVE", "cmd": f"approve:{p.id}", "primary": True},
                        {"label": "Skip", "cmd": f"reject:{p.id}"},
                    ]
                ),
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
