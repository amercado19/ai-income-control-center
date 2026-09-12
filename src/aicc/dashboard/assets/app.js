/* AI Income Control Center - dashboard renderer.
 *
 * Reads the DATA object embedded by the build step. No network calls, no external
 * dependencies, no browser storage beyond a theme preference.
 *
 * The rule this file exists to enforce: never render a figure the data does not support.
 * A metric that is null renders "Insufficient Data", not 0. A capability without a
 * successful probe renders white or yellow, never green.
 */

const D = window.DATA || {};
const $ = (sel, root = document) => root.querySelector(sel);

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const money = (n, dp = 0) =>
  n === null || n === undefined ? "Insufficient Data" : "$" + Number(n).toLocaleString("en-US", { minimumFractionDigits: dp, maximumFractionDigits: dp });

const pct = (n) => (n === null || n === undefined ? "Insufficient Data" : Number(n).toFixed(1) + "%");
const num = (n) => (n === null || n === undefined ? "Insufficient Data" : Number(n).toLocaleString("en-US"));

const ago = (iso) => {
  if (!iso) return "-";
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return esc(iso);
  const m = Math.round((Date.now() - t) / 60000);
  if (m < 1) return "just now";
  if (m < 60) return m + "m ago";
  if (m < 1440) return Math.round(m / 60) + "h ago";
  return Math.round(m / 1440) + "d ago";
};

/* ------------------------------------------------------------------ atoms */

function stat(label, value, note, opts = {}) {
  const insufficient = value === "Insufficient Data";
  const cls = ["stat", opts.tone || ""].join(" ").trim();
  const prov = opts.provenance ? provenance(label, opts.provenance) : "";
  return `<div class="${cls}">
    <div class="stat-label provenance">${esc(label)}${prov}</div>
    <div class="stat-value${insufficient ? " insufficient" : ""}">${esc(value)}</div>
    ${note ? `<div class="stat-note">${esc(note)}</div>` : ""}
  </div>`;
}

let provSeq = 0;
function provenance(label, p) {
  const id = "prov" + (provSeq++);
  const rows = [
    ["Formula", p.formula],
    ["Source", p.source],
    ["Observations", p.observations === undefined ? null : String(p.observations)],
    ["Computed", p.computed_at],
    ["Note", p.note],
  ].filter(([, v]) => v);
  return `<button class="prov-btn" type="button" data-prov="${id}" aria-label="How ${esc(label)} is calculated">i</button>
    <div class="prov-pop" id="${id}" hidden><dl>
      ${rows.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join("")}
    </dl></div>`;
}

const badge = (text, kind) => `<span class="badge ${esc(kind || text)}"><span class="dot" style="background:currentColor"></span>${esc(text)}</span>`;

const empty = (title, body) => `<div class="card"><div class="empty"><div class="big">${esc(title)}</div>${esc(body || "")}</div></div>`;

/* ------------------------------------------------------------------ chart */

/* Horizontal bar chart. One categorical series, so no legend box - the title names it.
 * Values are direct-labelled, which also satisfies the relief rule for low-contrast slots. */
function barChart(rows, { valueKey = "value", labelKey = "label", format = num, series = 1 } = {}) {
  if (!rows.length) return "";
  const max = Math.max(...rows.map((r) => Number(r[valueKey]) || 0), 1);
  const rowH = 30, padL = 132, padR = 66, w = 640;
  const h = rows.length * rowH + 12;
  const bars = rows.map((r, i) => {
    const v = Number(r[valueKey]) || 0;
    const bw = Math.max(2, ((w - padL - padR) * v) / max);
    const y = i * rowH + 6;
    return `<text class="tick" x="${padL - 10}" y="${y + 13}" text-anchor="end">${esc(r[labelKey])}</text>
      <rect class="bar" x="${padL}" y="${y}" width="${bw}" height="17" fill="var(--s${series})"><title>${esc(r[labelKey])}: ${esc(format(v))}</title></rect>
      <text class="val" x="${padL + bw + 8}" y="${y + 13}">${esc(format(v))}</text>`;
  }).join("");
  return `<svg class="chart" viewBox="0 0 ${w} ${h}" role="img" preserveAspectRatio="xMinYMin meet">
    <line class="baseline" x1="${padL}" y1="2" x2="${padL}" y2="${h - 4}"/>${bars}</svg>`;
}

/* ------------------------------------------------------------------ pages */

const PAGES = {};

PAGES.overview = () => {
  const m = D.metrics || {};
  const p = D.provenance || {};
  const att = D.attention || [];
  const sys = D.system || {};

  const cta = `<div class="mobile-cta">
    <button class="btn primary wide" data-nav="approvals">NEEDS ME (${att.length})</button>
    <button class="btn" data-nav="jobs">ACTIVE JOBS</button>
    <button class="btn" data-nav="health">SYSTEM</button>
  </div>`;

  const rs = sys.run_state || "OFF";
  const stopped = rs === "EMERGENCY_STOP";
  const master = `<div class="master">
    <div class="state"><span class="lamp ${esc(sys.light || "WHITE")}"></span>${esc(sys.status_text || "UNKNOWN")}</div>
    <div style="flex:1;min-width:120px">
      <div style="font-size:12px;color:var(--ink-muted)">${esc(sys.mode || "DEMO")} MODE${sys.blocked_reason ? " - " + esc(sys.blocked_reason) : ""}</div>
    </div>
    <div class="btn-row">
      ${rs === "PAUSED"
        ? '<button class="btn primary lg" data-cmd="resume">RESUME</button>'
        : `<button class="btn primary lg" data-cmd="start" ${rs === "ACTIVE" ? "disabled" : ""}>START BUSINESS</button>`}
      <button class="btn" data-cmd="pause" ${rs === "ACTIVE" ? "" : "disabled"}>PAUSE ALL</button>
      <button class="btn danger" data-cmd="emergency-stop" ${stopped ? "disabled" : ""}>${stopped ? "STOPPED" : "EMERGENCY STOP"}</button>
    </div>
    ${stopped ? `<div class="note stop" style="margin-top:10px;flex-basis:100%">
      EMERGENCY STOP is engaged. New work of every kind is halted - AI calls, marketplace actions,
      proposals, client communication, scheduled scans, job execution. Nothing has been deleted,
      and the audit log, safety self-test, redaction and cost gate are all still running.
      Clearing it is an explicit RESUME.</div>` : ""}
  </div>`;

  const realRevenueNote = m.includes_demo
    ? "Includes DEMO rows"
    : (m.jobs_completed ? "Confirmed payments only" : "No real revenue yet");

  const kpis = [
    stat("Revenue Today", money(m.revenue_today, 2), realRevenueNote, { provenance: p.revenue_net_total }),
    stat("Revenue This Week", money(m.revenue_week, 2), realRevenueNote),
    stat("Revenue This Month", money(m.revenue_month, 2), realRevenueNote),
    stat("Est. Net Profit", money(m.revenue_net_total, 2), `${pct(m.profit_margin_pct)} margin`, { provenance: p.profit_margin_pct }),
    stat("Jobs Won", num(m.jobs_won)),
    stat("Active Jobs", num(m.active_jobs)),
    stat("Jobs Completed", num(m.jobs_completed)),
    stat("Pending Approvals", num(att.length), att.length ? "Needs you" : "Nothing waiting", { tone: att.length ? "warn" : "" }),
    stat("Opportunities seen", num(m.opportunities_discovered),
      `${num(m.opportunities_active)} still live, ${num(m.opportunities_archived)} archived, ${num(m.opportunities_qualified)} ever qualified`,
      { provenance: { formula: "active + archived, demo rows excluded", source: "data/opportunities.json + data/opportunities_archive.json", note: "Cumulative since the first scan, not a count of today's listings. 'Qualified' means it reached the STRONG or EXCELLENT band at some point." } }),
    stat("Proposal Win Rate", pct(m.win_rate), m.win_rate === null ? `Needs ${D.min_observations || 5} decided outcomes` : "", { provenance: p.win_rate }),
    stat("Average Job Value", money(m.avg_order_value, 2)),
    stat("AI Cash Cost", money(m.ai_cash_cost_total, 2), `${num(m.ai_usage_units_total)} usage units drawn`, { tone: "accent", provenance: p.ai_cash_cost_total }),
  ].join("");

  const bySource = Object.entries(D.metrics?.by_source || {})
    .map(([k, v]) => ({ label: k, value: v.opportunities, ...v }))
    .sort((a, b) => b.value - a.value);

  const platformRows = bySource.length ? `<div class="table-wrap"><table>
    <thead><tr><th>Platform</th><th class="num">Opportunities</th><th class="num">Strong</th><th class="num">Proposals</th><th class="num">Interviews</th><th class="num">Won</th><th class="num">Revenue</th></tr></thead>
    <tbody>${bySource.map((s) => `<tr>
      <td><span class="cell-title">${esc(s.label)}</span><span class="cell-sub">${esc((D.connectors || []).find((c) => c.name === s.label)?.automation_policy || "")}</span></td>
      <td class="num">${num(s.opportunities)}</td><td class="num">${num(s.strong_matches)}</td>
      <td class="num">${num(s.proposals)}</td><td class="num">${num(s.interviews)}</td>
      <td class="num">${num(s.won)}</td><td class="num">${money(s.revenue, 2)}</td></tr>`).join("")}
    </tbody></table></div>` : empty("No platform activity yet", "Run a discovery scan to populate this.");

  return `${cta}${master}
    <div class="section-title">Today</div><div class="grid kpi">${kpis}</div>
    <div class="section-title">Platform breakdown</div>${platformRows}
    <div class="section-title">Cost</div>
    <div class="card card-pad"><div class="grid kpi">
      ${stat("Additional Monthly Cost", money(D.cost?.ceiling ?? 0, 2), "Hard ceiling; all cash requests fail closed", { tone: "accent" })}
      ${stat("Cost Requests Declined", num(D.cost?.declined ?? 0), "Every refusal is logged")}
    </div></div>`;
};

PAGES.approvals = () => {
  const att = D.attention || [];
  if (!att.length) return `<div class="page-head"><h2>Needs my attention</h2><p>The one screen that answers: what do I have to do?</p></div>
    ${empty("Nothing needs you right now", "Everything permitted is running. Anything requiring your decision will appear here.")}`;

  const cards = att.map((a) => `<div class="card card-pad attention ${a.severity === "critical" ? "crit" : ""}" style="margin-bottom:10px">
    <div style="display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap;align-items:flex-start">
      <div style="flex:1;min-width:220px">
        <div style="font-weight:600;margin-bottom:3px">${esc(a.title)}</div>
        ${a.meta ? `<div class="cell-sub" style="margin-bottom:5px">${esc(a.meta)}${a.url ? ` &middot; <a href="${esc(a.url)}" target="_blank" rel="noopener">the listing</a>` : ""}</div>` : ""}
        <div style="font-size:12.5px;color:var(--ink-2);line-height:1.55">${esc(a.detail)}</div>
        ${a.caveat ? `<div class="note" style="margin-top:8px">${esc(a.caveat)}</div>` : ""}
        ${a.value ? `<div style="font-size:12px;color:var(--ink-muted);margin-top:6px">Value: ${esc(a.value)}</div>` : ""}
      </div>
      <div class="btn-row">${(a.actions || []).map((x) => `<button class="btn ${x.primary ? "primary" : ""}" data-action="${esc(x.cmd)}">${esc(x.label)}</button>`).join("")}</div>
    </div>
    ${a.body ? `<details class="disclosure"><summary>Read what would be sent, in full</summary><pre class="proposal">${esc(a.body)}</pre></details>` : ""}
    </div>`).join("");

  return `<div class="page-head"><h2>Needs my attention</h2><p>${att.length} item${att.length === 1 ? "" : "s"} waiting on a decision only you can make.</p></div>${cards}`;
};

PAGES.opportunities = () => {
  const opps = D.opportunities || [];
  const sources = [...new Set(opps.map((o) => o.source))].sort();

  const filters = `<div class="filters">
    <label for="f-src">Source</label>
    <select id="f-src"><option value="">All</option>${sources.map((s) => `<option>${esc(s)}</option>`).join("")}</select>
    <label for="f-score">Min score</label>
    <select id="f-score"><option value="0">Any</option><option value="65">65+ Review</option><option value="80">80+ Strong</option><option value="90">90+ Excellent</option></select>
    <label for="f-budget">Min budget</label>
    <select id="f-budget"><option value="0">Any</option><option value="50">$50+</option><option value="100">$100+</option><option value="250">$250+</option><option value="500">$500+</option><option value="1000">$1,000+</option></select>
    <label for="f-hours">Max human hours</label>
    <select id="f-hours"><option value="999">Any</option><option value="1">1h</option><option value="3">3h</option><option value="8">8h</option></select>
    <label style="display:flex;align-items:center;gap:5px;text-transform:none"><input type="checkbox" id="f-hide-skip" checked> Hide rejected</label>
  </div>`;

  if (!opps.length) return `<div class="page-head"><h2>Opportunities</h2></div>${filters}
    ${empty("No opportunities yet", "Run a discovery scan. In DEMO mode the demo connector seeds synthetic listings.")}`;

  // Three true numbers, one word. The sidebar badge counts what is still in play, the Overview
  // card counts everything ever seen, and the table lists everything live - so the screen showed
  // 78, 121 and 91 all labelled "opportunities" and left the reader to guess which was real.
  // They are all correct; what was missing was saying which is which, in the one place the
  // smallest of them appears without explanation.
  const live = opps.length;
  const blocked = opps.filter((o) => o.policy_allowed === false).length;
  const rejected = opps.filter((o) => o.rejected).length;
  const inPlay = opps.filter((o) => !o.rejected && o.policy_allowed !== false).length;

  return `<div class="page-head"><h2>Opportunities</h2>
      <p>Every score shows the factors behind it. Expand a row to see the reasoning.</p>
      <p class="note"><strong>${num(live)}</strong> live listings on this page &middot;
        <strong>${num(inPlay)}</strong> still in play, which is the number on the sidebar badge &middot;
        ${num(rejected)} rejected by scoring${blocked ? " &middot; " + num(blocked) + " blocked by the standing rules" : ""}.
        The Overview's "opportunities seen" is larger again because it counts archived listings too.</p></div>
    ${filters}<div id="opp-list"></div>`;
};

function renderOpportunities() {
  const host = $("#opp-list");
  if (!host) return;
  const src = $("#f-src")?.value || "";
  const minScore = Number($("#f-score")?.value || 0);
  const minBudget = Number($("#f-budget")?.value || 0);
  const maxHours = Number($("#f-hours")?.value || 999);
  const hideSkip = $("#f-hide-skip")?.checked;

  const rows = (D.opportunities || []).filter((o) => {
    if (src && o.source !== src) return false;
    if (o.score < minScore) return false;
    if (minBudget && (o.budget_max || o.budget_min || 0) < minBudget) return false;
    if (o.human_hours > maxHours) return false;
    if (hideSkip && o.rejected) return false;
    return true;
  });

  if (!rows.length) { host.innerHTML = empty("Nothing matches these filters", "Widen the filters above."); return; }

  host.innerHTML = `<div class="table-wrap"><table>
    <thead><tr><th class="num">Score</th><th>Source</th><th>Job</th><th class="num">Budget</th><th class="num">Est. net</th><th class="num">Human</th><th class="num">Auto</th><th>Posted</th><th>Status</th></tr></thead>
    <tbody>${rows.map((o, i) => `
      <tr>
        <td class="num">${badge(o.score.toFixed(0), o.band)}</td>
        <td><span class="cell-sub">${esc(o.source)}</span>${o.is_demo ? " " + badge("DEMO", "demo") : ""}</td>
        <td style="min-width:260px">
          <span class="cell-title">${esc(o.title)}</span>
          <span class="cell-sub">${esc(o.client || "Client not stated")}${o.category ? " - " + esc(o.category) : ""}</span>
          ${o.policy_allowed === false ? `<div style="margin-top:6px">${badge(o.policy_gate, "RED")}
            <div class="cell-sub">${esc(o.policy_reason)}</div></div>` : ""}
          <details class="detail"><summary>Why this score</summary><div>${factorHtml(o)}</div></details>
        </td>
        <td class="num">${esc(o.budget_display)}</td>
        <td class="num">${money(o.net)}</td>
        <td class="num">${o.human_hours.toFixed(1)}h</td>
        <td class="num">${o.automation_pct}%</td>
        <td><span class="cell-sub">${ago(o.posted_time)}</span></td>
        <td><span class="cell-sub">${esc(o.status)}</span>
          <div class="btn-row" style="margin-top:6px">
            ${o.url ? `<a class="btn ghost" href="${esc(o.url)}" target="_blank" rel="noopener noreferrer">Open</a>` : ""}
          </div>
        </td>
      </tr>`).join("")}
    </tbody></table></div>
    ${(D.attribution || []).length ? `<div class="attrib">${D.attribution.map((a) => `Data from <a href="${esc(a.url)}" target="_blank" rel="noopener">${esc(a.name)}</a>`).join(" - ")}</div>` : ""}`;
}

/* The win estimate, with its calibration status attached to it rather than printed somewhere
 * nearby. A probability displayed on its own acquires authority it has not earned: this one is
 * a heuristic over source priors, listing age, class and competition, and has never been
 * checked against a single real outcome because there are no outcomes yet. The step-by-step
 * trail is shown for the same reason - a reader who can see it was assembled from four
 * adjustments treats it differently from a reader shown only "81%". */
function winHtml(o) {
  const w = o.win_estimate || {};
  if (!w.display) return "";
  const steps = (w.factors || []).map((f) => `
    <div class="factor"><div class="fname">${esc(f.factor)}</div>
    <div class="fpts">${esc(f.effect)}</div>
    <div class="fev">${esc(f.why)} <em>(${esc(f.from)} &rarr; ${esc(f.to)})</em></div></div>`).join("");
  return `<div class="section-title">Estimated chance of winning</div>
    <div class="note warn"><strong>${esc(w.display)}</strong> &mdash; ${esc(w.calibration || "uncalibrated")}.
      This is a heuristic, not a measurement. It has never been tested against a real outcome,
      because there are none yet. Treat it as a way of comparing listings to each other, not as
      a probability you could bet on.</div>
    ${steps}`;
}

function factorHtml(o) {
  if (o.rejected) return `<div class="note stop"><strong>Rejected.</strong> ${esc(o.rejection_reason)}</div>`;
  const factors = Object.entries(o.factors || {}).map(([name, f]) => `
    <div class="factor">
      <div class="fname">${esc(name)}</div>
      <div class="fpts">${f.awarded.toFixed(1)} / ${f.available}</div>
      <div class="meter"><i style="width:${Math.max(0, Math.min(100, (f.awarded / f.available) * 100))}%"></i></div>
      <div class="fev">${esc(f.evidence)}</div>
    </div>`).join("");
  const penalties = (o.penalties || []).map((p) => `
    <div class="factor penalty"><div class="fname">Penalty: ${esc(p.name)}</div>
    <div class="fpts">-${p.points}</div><div class="fev">${esc(p.evidence)}</div></div>`).join("");
  return factors + penalties + winHtml(o) +
    `<div class="note" style="margin-top:10px"><strong>Economics are an estimate, not an observation.</strong> ${esc(o.econ_method || "")}</div>`;
}

PAGES.proposals = () => {
  const props = D.proposals || [];
  if (!props.length) return `<div class="page-head"><h2>Proposals</h2></div>${empty("No proposals drafted", "Proposals are drafted for opportunities scoring 65 or above.")}`;
  return `<div class="page-head"><h2>Proposals</h2><p>Nothing here is sent without your explicit approval.</p></div>
    ${props.map((p) => `<div class="card card-pad" style="margin-bottom:12px">
      <div style="display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap;margin-bottom:10px">
        <div><div style="font-weight:600">${esc(p.opportunity_title)}</div>
          <div class="cell-sub">${esc(p.source)} - ${esc(p.status)}${p.is_demo ? " - DEMO" : ""}${p.quoted_price ? " - quoted " + money(p.quoted_price) : ""}</div></div>
        <div class="btn-row">
          <button class="btn primary" data-action="approve:${esc(p.id)}">APPROVE</button>
          <button class="btn" data-action="reject:${esc(p.id)}">Skip</button>
        </div>
      </div>
      ${p.submit_cost_note ? `<div class="note warn" style="margin-bottom:10px"><strong>Costs money to submit.</strong> ${esc(p.submit_cost_note)}</div>` : ""}
      <pre class="proposal">${esc(p.body)}</pre>
      <div class="note" style="margin-top:10px"><strong>Claims verified:</strong> ${esc((p.claims_made || []).join("; ") || "none")}. Each is backed by a real artifact in the NFL/MLB repositories.${p.ai_disclosure_included ? " AI use is disclosed to the client." : ""}</div>
    </div>`).join("")}`;
};

PAGES.jobs = () => {
  const jobs = D.jobs || [];
  const cols = ["RECEIVED", "PLAN", "WORK", "QA", "FIX", "READY_TO_DELIVER", "DELIVERED", "PROBLEM"];
  if (!jobs.length) return `<div class="page-head"><h2>Active jobs</h2></div>${empty("No jobs yet", "Jobs are created when an opportunity is won.")}`;
  return `<div class="page-head"><h2>Active jobs</h2></div><div class="kanban">
    ${cols.map((c) => {
      const inCol = jobs.filter((j) => (j.status === c) || (c === "WORK" && ["VALIDATE", "VERIFY"].includes(j.status)) || (c === "QA" && j.status === "FINAL_QA"));
      return `<div class="kcol"><h3><span>${esc(c.replace(/_/g, " "))}</span><span>${inCol.length}</span></h3>
        ${inCol.map((j) => `<div class="kcard">
          <div class="t">${esc(j.title)}</div>
          <div class="m"><span>${esc(j.client || "-")}</span><span>${money(j.agreed_price)}</span></div>
          <div class="m" style="margin-top:4px"><span>${esc(j.source)}</span><span>${j.qa_score !== null ? "QA " + j.qa_score : "no QA yet"}</span></div>
          <div class="progress"><i style="width:${j.progress}%"></i></div>
          ${j.human_action_required ? `<div class="m" style="margin-top:6px;color:var(--warning)">${esc(j.human_action_required)}</div>` : ""}
        </div>`).join("")}
      </div>`;
    }).join("")}</div>`;
};

PAGES.deliverables = () => {
  const jobs = (D.jobs || []).filter((j) => (j.deliverables || []).length);
  if (!jobs.length) return `<div class="page-head"><h2>Deliverables</h2></div>${empty(
    "No deliverables yet",
    "A deliverable appears here once a job has been worked and reviewed. Client files live in " +
    "workspaces/, which is gitignored - they are never committed to this public repository, and " +
    "only their filenames and QA scores are shown."
  )}`;
  return `<div class="page-head"><h2>Deliverables</h2><p>Client files live outside this repository and are never committed.</p></div>
    ${jobs.map((j) => `<div class="card card-pad" style="margin-bottom:10px">
      <div style="font-weight:600;margin-bottom:6px">${esc(j.title)}</div>
      <dl class="kv"><dt>QA score</dt><dd>${j.qa_score !== null ? j.qa_score + "/100" : "not yet reviewed"}</dd>
      <dt>Revisions</dt><dd>${j.revision_count}</dd>
      <dt>Files</dt><dd>${(j.deliverables || []).map((f) => esc(f.split("/").pop())).join(", ")}</dd></dl>
      ${(j.qa_findings || []).length ? `<details class="detail"><summary>QA findings</summary><div>${j.qa_findings.map((f) => `<div class="factor"><div class="fname">${esc(f.severity)} - ${esc(f.check)}</div><div class="fpts"></div><div class="fev">${esc(f.detail)}</div></div>`).join("")}</div></details>` : ""}
    </div>`).join("")}`;
};

PAGES.clients = () => {
  const clients = D.clients || [];
  if (!clients.length) return `<div class="page-head"><h2>Clients</h2></div>${empty(
    "No clients yet",
    "A client appears here after a won job. The route to the first one is the Fiverr storefront - " +
    "four gigs are written, priced and ready to publish - rather than the outbound scanner, which " +
    "watches for contract roles."
  )}`;
  return `<div class="page-head"><h2>Clients</h2></div><div class="table-wrap"><table>
    <thead><tr><th>Client</th><th>Source</th><th class="num">Jobs</th><th class="num">Revenue</th></tr></thead>
    <tbody>${clients.map((c) => `<tr><td class="cell-title">${esc(c.name)}</td><td>${esc(c.source)}</td>
      <td class="num">${num(c.jobs)}</td><td class="num">${money(c.revenue, 2)}</td></tr>`).join("")}</tbody></table></div>`;
};

PAGES.revenue = () => {
  const m = D.metrics || {};
  const p = D.provenance || {};
  const rows = D.revenue || [];
  const head = `<div class="page-head"><h2>Revenue</h2><p>Revenue is not profit. Every row separates gross, fees, and the cash cost of AI (which is $0.00 on a subscription token).</p></div>
    <div class="grid kpi">
      ${stat("Real Gross", money(m.revenue_gross_total, 2), "Demo excluded", { provenance: p.revenue_net_total })}
      ${stat("Real Net", money(m.revenue_net_total, 2), pct(m.profit_margin_pct) + " margin", { provenance: p.profit_margin_pct })}
      ${stat("AI Cash Cost", money(m.ai_cash_cost_total, 2), "Subscription token", { tone: "accent", provenance: p.ai_cash_cost_total })}
      ${stat("AI Usage Drawn", num(m.ai_usage_units_total), "Allowance, not dollars")}
    </div>`;
  if (!rows.length) return head + `<div class="section-title">Entries</div>${empty("No revenue recorded", "Only a confirmed payment creates a row here.")}`;
  return head + `<div class="section-title">Entries</div><div class="table-wrap"><table>
    <thead><tr><th>Job</th><th>Source</th><th class="num">Gross</th><th class="num">Platform fee</th><th class="num">AI cash</th><th class="num">Net</th><th>Received</th></tr></thead>
    <tbody>${rows.map((r) => `<tr><td><span class="cell-title">${esc(r.description)}</span>${r.is_demo ? badge("DEMO", "demo") : ""}</td>
      <td>${esc(r.source)}</td><td class="num">${money(r.gross, 2)}</td><td class="num">${money(r.platform_fee, 2)}</td>
      <td class="num">${money(r.ai_cash_cost, 2)}</td><td class="num"><strong>${money(r.net, 2)}</strong></td>
      <td><span class="cell-sub">${ago(r.received_at)}</span></td></tr>`).join("")}</tbody></table></div>`;
};

PAGES.analytics = () => {
  const m = D.metrics || {};
  const p = D.provenance || {};
  const funnel = [
    { label: "Discovered", value: m.opportunities_discovered || 0 },
    { label: "Qualified", value: m.opportunities_qualified || 0 },
    { label: "Proposals", value: m.proposals_drafted || 0 },
    { label: "Submitted", value: m.proposals_submitted || 0 },
    { label: "Interviews", value: m.interviews || 0 },
    { label: "Won", value: m.jobs_won || 0 },
  ];
  const cats = Object.entries(m.by_category || {})
    .map(([k, v]) => ({ label: k, value: v.opportunities, avg: v.avg_score }))
    .sort((a, b) => b.value - a.value).slice(0, 8);

  const cal = D.calibration || {};
  return `<div class="page-head"><h2>Analytics</h2><p>The question this page exists to answer: which work makes the most money for the least human effort?</p></div>
    <div class="grid kpi">
      ${stat("Win Rate", pct(m.win_rate), m.win_rate === null ? `Needs ${D.min_observations || 5} decided outcomes` : "", { provenance: p.win_rate })}
      ${stat("Avg QA Score", m.avg_qa_score === null ? "Insufficient Data" : m.avg_qa_score + "/100", "", { provenance: p.avg_qa_score })}
      ${stat("Revision Rate", pct(m.revision_rate), "", { provenance: p.revision_rate })}
      ${stat("Avg Human Min/Job", m.avg_human_minutes_per_job === null ? "Insufficient Data" : m.avg_human_minutes_per_job + " min", "Logged, not estimated")}
    </div>
    <div class="section-title">Acquisition funnel</div>
    <div class="card card-pad">${funnel.some((f) => f.value) ? barChart(funnel, { series: 1 }) : `<div class="empty">No funnel data yet.</div>`}</div>
    <div class="section-title">Opportunities by category</div>
    <div class="card card-pad">${cats.length ? barChart(cats, { series: 3 }) : `<div class="empty">No category data yet.</div>`}</div>
    <div class="section-title">Scoring calibration</div>
    <div class="card card-pad"><div class="note ${cal.status === "ok" ? "" : "warn"}">
      <strong>${esc(cal.status === "ok" ? "Calibrated against outcomes" : "Insufficient Data")}.</strong>
      ${esc(cal.recommendation || "")} Weight changes are never applied automatically.
    </div></div>`;
};

PAGES.automation = () => {
  const autos = D.automations || [];
  return `<div class="page-head"><h2>Automation</h2><p>What is scheduled, when it last ran, and whether it worked.</p></div>
    <div class="table-wrap"><table><thead><tr><th>Automation</th><th>State</th><th>Last run</th><th>Next run</th><th>Status</th><th></th></tr></thead>
    <tbody>${autos.map((a) => `<tr>
      <td class="cell-title">${esc(a.label)}</td>
      <td>${badge(a.enabled ? "ON" : "OFF", a.enabled ? "GREEN" : "WHITE")}</td>
      <td><span class="cell-sub">${a.last_run ? ago(a.last_run) : "never"}</span></td>
      <td><span class="cell-sub">${esc(a.next_run || "-")}</span></td>
      <td><span class="cell-sub">${esc(a.last_status || "-")}${a.last_error ? " - " + esc(a.last_error) : ""}</span></td>
      <td><button class="btn ghost" data-action="run:${esc(a.key)}">RUN NOW</button></td>
    </tr>`).join("")}</tbody></table></div>
    <div class="section-title">Connectors</div>
    <div class="table-wrap"><table><thead><tr><th>Source</th><th>Status</th><th>Discovery</th><th>Apply cost</th><th>Why</th></tr></thead>
    <tbody>${(D.connectors || []).map((c) => `<tr>
      <td><span class="cell-title">${esc(c.label)}</span><span class="cell-sub">${esc(c.automation_policy)}</span></td>
      <td>${badge(c.light === "GREEN" ? "AUTOMATED" : c.light === "YELLOW" ? "ASSISTED" : "MANUAL", c.light)}</td>
      <td><span class="cell-sub">${c.discovery ? "Permitted" : "None available"}</span></td>
      <td><span class="cell-sub">${esc(c.cost_to_apply)}</span></td>
      <td style="max-width:420px"><span class="cell-sub">${esc(c.rules_evidence)}</span>${c.rules_url ? ` <a href="${esc(c.rules_url)}" target="_blank" rel="noopener">source</a>` : ""}</td>
    </tr>`).join("")}</tbody></table></div>`;
};

PAGES.health = () => {
  const caps = D.capabilities || [];
  const sys = D.system || {};
  return `<div class="page-head"><h2>System health</h2><p>A light is green only when a probe confirmed it green. Designed-but-not-working reports white.</p></div>
    <div class="card card-pad" style="margin-bottom:12px">
      <div class="state" style="display:flex;align-items:center;gap:10px;font-size:17px;font-weight:650">
        <span class="lamp ${esc(sys.light || "WHITE")}"></span>${esc(sys.status_text || "UNKNOWN")}</div>
      ${sys.blocked_reason ? `<div class="note stop" style="margin-top:10px">${esc(sys.blocked_reason)}</div>` : ""}
    </div>
    <div class="table-wrap"><table><thead><tr><th>Capability</th><th>Status</th><th>Detail</th><th>Blocked by</th></tr></thead>
    <tbody>${caps.map((c) => `<tr>
      <td class="cell-title">${esc(c.label)}</td>
      <td>${badge(c.health.replace(/_/g, " "), c.light)}</td>
      <td><span class="cell-sub">${esc(c.detail)}</span></td>
      <td><span class="cell-sub">${esc(c.blocking_reason || "-")}</span></td>
    </tr>`).join("")}</tbody></table></div>
    <div class="section-title">Recent errors</div>
    ${(D.errors || []).length ? `<div class="card card-pad">${D.errors.map((e) => `<div class="factor"><div class="fname">${esc(e.action)}</div><div class="fpts">${ago(e.timestamp)}</div><div class="fev">${esc(e.error)}</div></div>`).join("")}</div>` : empty("No recent errors", "")}`;
};

PAGES.settings = () => {
  const chk = D.live_checklist || [];
  /* Three states, not two. An item this system cannot check from here - such as who last
   * edited a cron schedule on GitHub - is NOT a failure, and rendering it as one is the same
   * dishonesty as a green light with nothing behind it, only pointed the other way. It reads
   * YOU CHECK, it does not block the button, and its detail says what to go and look at. */
  const unverifiable = (c) => c.passing === null || c.passing === undefined;
  const blocking = chk.filter((c) => !unverifiable(c));
  const passing = blocking.every((c) => c.passing);
  return `<div class="page-head"><h2>Settings</h2></div>
    <div class="section-title">Cost gate</div>
    <div class="card card-pad"><dl class="kv">
      <dt>Max new monthly cash spend</dt><dd><strong>${money(D.cost?.ceiling ?? 0, 2)}</strong></dd>
      <dt>Behaviour</dt><dd>Fails closed. Any component that would incur a charge is declined and logged.</dd>
      <dt>Cost requests declined</dt><dd>${num(D.cost?.declined ?? 0)}</dd>
    </dl></div>
    <div class="section-title">Live mode</div>
    <div class="card card-pad">
      <div class="note ${passing ? "" : "warn"}" style="margin-bottom:12px">
        <strong>${D.system?.mode === "LIVE" ? "LIVE mode is enabled." : "Currently in DEMO mode."}</strong>
        Every checkable item below must pass before live mode can be enabled. Items marked
        YOU CHECK cannot be verified from here and do not block the button - they are things
        only you can confirm.
      </div>
      ${chk.map((c) => {
        const state = unverifiable(c) ? ["YOU CHECK", "CHECK YOURSELF", "YELLOW"] : c.passing ? ["PASS", "OK", "GREEN"] : ["FAIL", "BLOCKED", "RED"];
        return `<div class="factor"><div class="fname">${state[0]} - ${esc(c.name)}</div>
        <div class="fpts">${badge(state[1], state[2])}</div>
        <div class="fev">${esc(c.detail)}</div></div>`;
      }).join("")}
      <div class="btn-row" style="margin-top:12px">
        <button class="btn primary" data-action="enable-live" ${passing ? "" : "disabled"}>I UNDERSTAND - ENABLE LIVE MODE</button>
      </div>
    </div>
    <div class="section-title">Operator profile</div>
    <div class="card card-pad"><div class="note">Proposals may only claim capabilities listed in the operator profile, each backed by a real artifact. A claim outside that list raises an error rather than shipping.</div></div>`;
};

/* Fiverr is the one source with no discovery surface at all, so it is the one source where the
 * work is entirely front-loaded: the gigs are the product. Nothing here publishes anything -
 * Fiverr has no seller API, and the category locks permanently at publish time, so the review
 * step is the point rather than a formality. */
PAGES.fiverr = () => {
  const k = D.fiverr_kit;
  if (!k || !k.gigs) return `<div class="page-head"><h2>Fiverr launch center</h2></div>${empty("No gig kit built", "")}`;
  const slots = `${k.slots_used} of ${k.slots_available} new-seller slots used`;
  const floorNote = (k.below_floor || []).length
    ? `<div class="note" style="margin-top:10px"><strong>${k.below_floor.length} gig priced below your $${k.floor_hourly}/h floor, on purpose.</strong> ${esc(k.below_floor.map((b) => b.reason).join(" "))}</div>`
    : "";
  return `<div class="page-head"><h2>Fiverr launch center</h2>
      <p>Four gigs, drafted and validated against Fiverr's limits. ${esc(slots)}. Nothing is published without you.</p></div>
    <div class="card card-pad">
      <div class="note">${esc(k.publishing_note)}</div>
      <div class="note" style="margin-top:10px">${esc(k.ai_disclosure_note)}</div>
      ${floorNote}
      <div class="cell-sub" style="margin-top:10px">Implied /h is what you net divided by your own hours, with AI doing the rest.
        ${badge("$" + k.target_hourly + "/h+", "GREEN")} meets your target &middot;
        ${badge("$" + k.floor_hourly + "-" + k.target_hourly, "YELLOW")} clears your floor &middot;
        ${badge("under $" + k.floor_hourly, "RED")} below it.</div>
    </div>
    ${k.gigs.map((g) => `<div class="card card-pad" style="margin-top:14px">
      <div class="btn-row" style="justify-content:space-between;align-items:flex-start;gap:12px;flex-wrap:wrap">
        <div style="min-width:0">
          <div class="cell-title">${esc(g.title)}</div>
          <div class="cell-sub">${esc(g.category)} &rsaquo; ${esc(g.subcategory)} &middot; ${g.title_chars}/80 title &middot; ${g.description_chars}/1200 description</div>
        </div>
        <div class="btn-row" style="flex-wrap:wrap">
          ${badge(g.status.replace(/_/g, " "), g.status === "PUBLISHED" ? "GREEN" : "WHITE")}
          ${badge(g.valid ? "VALID" : "FIX REQUIRED", g.valid ? "GREEN" : "RED")}
        </div>
      </div>
      ${g.validation.length ? `<div class="note" style="margin-top:10px;color:var(--red)">${g.validation.map(esc).join("<br>")}</div>` : ""}
      ${g.image_path
        ? `<div class="cell-sub" style="margin-top:10px">Gig image ready: <code>${esc(g.image_path)}</code> (1280&times;769). Upload it when you publish.</div>`
        : `<div class="note" style="margin-top:10px">No gig image generated yet. Run <code>python3 scripts/gig_images.py</code> - a gig published without one performs badly.</div>`}
      <div class="table-wrap" style="margin-top:12px"><table class="fits-narrow"><thead><tr><th>Package</th><th>List</th><th>You net</th><th>Your hours</th><th>Implied /h</th><th class="hide-narrow">Delivery</th><th class="hide-narrow">Revisions</th></tr></thead>
      <tbody>${g.packages.map((p) => `<tr>
        <td><span class="cell-title">${esc(p.name)}</span><span class="cell-sub">${p.includes.length} item${p.includes.length === 1 ? "" : "s"}</span></td>
        <td>${money(p.price)}</td>
        <td>${money(p.net_after_commission)}</td>
        <td><span class="cell-sub">${p.est_human_hours}h you + ${p.est_ai_hours}h AI</span></td>
        <td>${badge(money(p.implied_hourly) + "/h", p.implied_hourly >= k.target_hourly ? "GREEN" : p.implied_hourly >= k.floor_hourly ? "YELLOW" : "RED")}</td>
        <td class="hide-narrow"><span class="cell-sub">${p.delivery_days}d</span></td>
        <td class="hide-narrow"><span class="cell-sub">${p.revisions}</span></td>
      </tr>`).join("")}</tbody></table></div>
      <details class="disclosure">
        <summary>Full listing copy - package contents, description, ${g.tags.length} tags, ${g.requirements.length} requirements, ${g.faqs.length} FAQs</summary>
        <div class="section-title">What each package includes</div>
        ${g.packages.map((p) => `<div style="margin-bottom:10px">
          <span class="cell-title">${esc(p.name)} - ${money(p.price)} &middot; ${p.delivery_days}-day delivery &middot; ${p.revisions} revision${p.revisions === 1 ? "" : "s"}</span>
          <ul class="cell-sub" style="margin:2px 0 0;padding-left:18px">${p.includes.map((i) => `<li>${esc(i)}</li>`).join("")}</ul>
        </div>`).join("")}
        <div class="section-title">Description</div>
        <pre class="proposal">${esc(g.description)}</pre>
        <div class="section-title">Search tags</div>
        <div class="btn-row" style="flex-wrap:wrap">${g.tags.map((t) => badge(t, "WHITE")).join(" ")}</div>
        <div class="section-title">Buyer requirements</div>
        <ul class="cell-sub" style="margin:0;padding-left:18px">${g.requirements.map((r) => `<li>${esc(r)}</li>`).join("")}</ul>
        <div class="section-title">FAQ</div>
        <dl style="margin:0">${g.faqs.map((f) => `<dt class="cell-title" style="margin-top:8px">${esc(f.q)}</dt><dd class="cell-sub" style="margin:2px 0 0">${esc(f.a)}</dd>`).join("")}</dl>
        <div class="section-title">Gig image concept</div>
        <div class="note">${esc(g.image_concept)}</div>
        <div class="section-title">Why this gig earned a slot</div>
        <div class="note">${esc(g.rationale)}</div>
      </details>
      <div class="btn-row" style="margin-top:14px">
        <button class="btn ghost" data-action="fiverr-edit:${esc(g.key)}">EDIT</button>
        <button class="btn" data-action="fiverr-ready:${esc(g.key)}" ${g.valid ? "" : "disabled"}>MARK READY TO PUBLISH</button>
      </div>
    </div>`).join("")}
    ${fiverrWizard()}`;
};

/* Proof, split by whether a stranger can check it. The distinction is the page: a claim backed
 * only by a private repository is something to offer on a call, not something to assert in a
 * cold proposal, and the proposal generator is held to the public half. */
PAGES.portfolio = () => {
  const pf = D.portfolio;
  if (!pf || !pf.studies) return `<div class="page-head"><h2>Portfolio</h2></div>${empty("No case studies", "")}`;
  return `<div class="page-head"><h2>Portfolio</h2>
      <p>${pf.count} case studies from real production work. ${pf.with_public_proof} have proof a client can open right now.</p></div>
    <div class="card card-pad">
      <div class="note">${esc(pf.visibility_note)}</div>
      <div class="note" style="margin-top:10px">${esc(pf.subject_matter_note)}</div>
    </div>
    ${pf.studies.map((s) => `<div class="card card-pad" style="margin-top:14px">
      <div class="cell-title">${esc(s.title)}</div>
      <div class="cell-sub" style="margin-top:4px">${esc(s.one_line)}</div>
      <div class="btn-row" style="flex-wrap:wrap;margin-top:10px">
        ${badge(s.has_public_proof ? "PUBLICLY VERIFIABLE" : "NO PUBLIC PROOF", s.has_public_proof ? "GREEN" : "YELLOW")}
        ${s.stack.map((t) => badge(t, "WHITE")).join(" ")}
      </div>
      <div class="section-title">Evidence a client can check</div>
      <ul class="cell-sub" style="margin:0;padding-left:18px">${s.public_evidence.map((e) => `<li>${esc(e.claim)}<br><a href="${esc(e.url)}" target="_blank" rel="noopener">${esc(e.where)}</a></li>`).join("")}</ul>
      ${s.private_evidence.length ? `<div class="section-title">Available on request (private repositories)</div>
        <ul class="cell-sub" style="margin:0;padding-left:18px">${s.private_evidence.map((e) => `<li>${esc(e.claim)} <em>- ${esc(e.where)}</em></li>`).join("")}</ul>` : ""}
      <details class="disclosure">
        <summary>The problem, the approach, and what came of it</summary>
        <div class="section-title">Problem</div>
        <div class="note">${esc(s.problem)}</div>
        <div class="section-title">Approach</div>
        <ul class="cell-sub" style="margin:0;padding-left:18px">${s.approach.map((a) => `<li>${esc(a)}</li>`).join("")}</ul>
        <div class="section-title">Outcome</div>
        <ul class="cell-sub" style="margin:0;padding-left:18px">${s.outcome.map((o) => `<li>${esc(o)}</li>`).join("")}</ul>
        <div class="section-title">Proof for</div>
        <div class="cell-sub">${s.sells.map(esc).join(" &middot; ")}</div>
      </details>
    </div>`).join("")}`;
};

PAGES.audit = () => {
  const ev = D.audit || [];
  if (!ev.length) return `<div class="page-head"><h2>Audit log</h2></div>${empty("No events recorded", "")}`;
  return `<div class="page-head"><h2>Audit log</h2><p>Append-only. Every consequential action, who did it, and what changed.</p></div>
    <div class="table-wrap"><table><thead><tr><th>When</th><th>Actor</th><th>Action</th><th>Object</th><th>Result</th></tr></thead>
    <tbody>${ev.map((e) => `<tr><td><span class="cell-sub">${ago(e.timestamp)}</span></td>
      <td><span class="cell-sub">${esc(e.actor)}</span></td><td class="cell-title">${esc(e.action)}</td>
      <td><span class="cell-sub">${esc(e.object_type)}${e.object_id ? " " + esc(e.object_id) : ""}</span></td>
      <td>${badge(e.result, e.result === "ok" ? "GREEN" : e.result === "refused" ? "YELLOW" : "RED")}</td></tr>`).join("")}
    </tbody></table></div>`;
};


/* ----------------------------------------------------- fiverr launch wizard */

function fiverrWizard() {
  const w = D.fiverr_wizard || {};
  const gigs = w.gigs || [];
  if (!gigs.length) return "";

  const gigBlock = (g) => `
    <details class="card card-pad" style="margin-bottom:10px" ${g.position === 1 ? "open" : ""}>
      <summary style="cursor:pointer;font-weight:650">
        ${g.position}. ${esc(g.title)} ${badge(g.ready ? "READY" : "BLOCKED", g.ready ? "GREEN" : "RED")}
      </summary>
      <div class="note" style="margin-top:10px">${esc(g.why_this_order)}</div>
      ${g.below_floor_reason ? `<div class="note stop" style="margin-top:8px"><strong>Priced below the floor, deliberately.</strong> ${esc(g.below_floor_reason)}</div>` : ""}
      ${(g.blocking || []).length ? `<div class="note stop" style="margin-top:8px">${g.blocking.map(esc).join("<br>")}</div>` : ""}
      ${!g.image_ready ? `<div class="note stop" style="margin-top:8px">Gig image not rendered. Run <code>python3 scripts/gig_images.py</code>.</div>` : ""}
      ${(g.steps || []).map((s) => `
        <div class="section-title" style="margin-top:14px">Step ${s.number}: ${esc(s.title)}${s.irreversible ? ' <span style="color:var(--red)">— IRREVERSIBLE</span>' : ""}</div>
        ${s.instruction ? `<div class="note">${esc(s.instruction)}</div>` : ""}
        ${(s.fields || []).map((f) => `
          <div class="factor">
            <div class="fname">${esc(f.label)}${f.locked_after_save ? ' <span style="color:var(--red)">LOCKS ON SAVE</span>' : ""}</div>
            <div class="fev"><pre class="proposal" style="white-space:pre-wrap;margin:4px 0">${esc(f.value)}</pre>${f.note ? `<span class="cell-sub">${esc(f.note)}</span>` : ""}</div>
          </div>`).join("")}
      `).join("")}
    </details>`;

  return `
    <div class="section-title">Launch wizard</div>
    <div class="card card-pad" style="margin-bottom:12px">
      <div class="note stop"><strong>${num(w.irreversible_count)} fields lock permanently on save.</strong> ${esc(w.warning || "")}</div>
      <div class="note" style="margin-top:8px">${esc(w.what_claude_did || "")}</div>
      <div class="note" style="margin-top:6px">${esc(w.human_required_reason || "")}</div>
      <div class="grid kpi" style="margin-top:12px">
        ${stat("Ready to publish", `${num(w.ready_count)} of ${num(w.total_slots)}`, "Validated against every platform limit")}
        ${stat("Your time", `~${num(w.estimated_total_minutes)} min`, "All four gigs, start to finish")}
        ${stat("Locked fields", num(w.irreversible_count), "Category and URL, per gig")}
      </div>
    </div>
    ${gigs.map(gigBlock).join("")}`;
}

/* ----------------------------------------------------- profit queue + capacity */

PAGES.queue = () => {
  const q = D.profit_queue || [];
  const pf = D.profit || {};
  const cap = D.capacity || {};
  const notes = D.plan_notes || [];

  const capTone = { "SAFE TO START": "GREEN", "TIGHT": "YELLOW", "RISKY": "RED", "WAIT FOR RESET": "YELLOW", "UNKNOWN — HUMAN REVIEW": "WHITE" };
  const posTone = (p) => (p === "NOW" || p === "HIGH PRIORITY" ? "GREEN" : p === "NEXT" ? "YELLOW" : p === "AFTER RESET" ? "YELLOW" : "WHITE");

  // `why` is the entire point of this page, so on a phone - where the last three columns are
  // dropped rather than pushed off-screen behind a horizontal scroll - it reappears under the
  // title. A queue that says what but not why is a queue nobody can correct, and that is just
  // as true at 390px as at 1440px.
  const rows = q.map((r) => `<tr>
      <td>${badge(r.position, posTone(r.position))}</td>
      <td class="cell-title">${esc(r.title)}<div class="cell-sub">${esc(r.lane)}</div>
        <div class="cell-sub show-narrow" style="margin-top:4px">${esc(r.why)}</div>
        <div class="cell-sub show-narrow">${num(Math.round(r.claude_minutes))} min AI · ${num(Math.round(r.andres_minutes))} min yours${r.opportunity_cost ? " · displaces " + money(r.opportunity_cost) : ""}</div>
        <div class="cell-sub show-narrow">${badge(r.capacity_status || "-", capTone[r.capacity_status] || "WHITE")}</div></td>
      <td>${money(r.gross)}<div class="cell-sub">EV ${money(r.expected_value)}</div></td>
      <td class="hide-narrow">${num(Math.round(r.claude_minutes))} min<div class="cell-sub">${num(Math.round(r.andres_minutes))} min yours</div></td>
      <td class="hide-narrow">${badge(r.capacity_status || "-", capTone[r.capacity_status] || "WHITE")}</td>
      <td class="hide-narrow">${r.deadline_slack_hours === null || r.deadline_slack_hours === undefined ? '<span class="cell-sub">none stated</span>' : num(Math.round(r.deadline_slack_hours)) + "h"}</td>
      <td class="hide-narrow">${r.opportunity_cost ? money(r.opportunity_cost) : '<span class="cell-sub">-</span>'}</td>
      <td class="hide-narrow"><span class="cell-sub">${esc(r.why)}</span></td>
    </tr>`).join("");

  return `<div class="page-head"><h2>Profit queue</h2>
      <p>What the system believes should happen next, and why. Ordering changes with deadlines,
      capacity, expected value and client commitments - it is a plan, not a sorted list.</p></div>

    <div class="grid kpi">
      ${stat("Expected captured profit", money(pf.expected_captured_profit), "Scheduled work, at expected value")}
      ${stat("Expected missed profit", money(pf.expected_missed_profit), "Deferred for capacity or deadline", { tone: pf.expected_missed_profit > 0 ? "warn" : "" })}
      ${stat("Total available profit", money(pf.total_available_profit), "Every profitable listing, if all were won")}
      ${stat("Capacity utilisation", pct(pf.capacity_utilization_pct), "Of the planning horizon")}
    </div>

    <div class="grid kpi">
      ${stat("Quick-win revenue", money(pf.quick_win_revenue), "Jobs under 15 min of AI time")}
      ${stat("High-value revenue", money(pf.high_value_revenue), "$200+ listings")}
      ${stat("Effective hourly", pf.total_andres_minutes ? money((pf.total_available_profit / (pf.total_andres_minutes / 60))) + "/h" : "Insufficient Data", "Per hour of Andres's own time")}
      ${stat("Median time to cash", pf.median_time_to_cash_days ? num(pf.median_time_to_cash_days) + " days" : "Insufficient Data", "Delivery plus platform clearing")}
    </div>

    ${notes.length ? `<div class="card card-pad" style="margin-bottom:12px">${notes.map((n) => `<div class="note">${esc(n)}</div>`).join("")}</div>` : ""}

    <div class="section-title">Claude capacity <span class="cell-sub">(${esc(cap.confidence || "ESTIMATED")})</span></div>
    <div class="card card-pad" style="margin-bottom:12px">
      <div class="state" style="display:flex;align-items:center;gap:10px;font-size:17px;font-weight:650">
        <span class="lamp ${capTone[cap.status] || "WHITE"}"></span>${esc(cap.status || "UNKNOWN")}</div>
      <div class="grid kpi" style="margin-top:12px">
        ${stat("Estimated remaining", esc(cap.remaining_display || "UNKNOWN"), "This five-hour window")}
        ${stat("Reserved for paid work", num(Math.round(cap.reserved_minutes || 0)) + " min", (cap.reserved_job_count || 0) + " accepted job(s)")}
        ${stat("Safe for new work", esc(cap.safe_new_work_display || "UNKNOWN"), "After the emergency margin")}
        ${stat("Next reset", cap.next_reset ? esc(String(cap.next_reset).slice(11, 16)) + " UTC" : "-", "Rolling window")}
      </div>
      <div class="note" style="margin-top:10px">${esc(cap.telemetry_note || "")}</div>
      <div class="note" style="margin-top:6px">${esc(cap.basis || "")}</div>
      ${(cap.shed || []).length ? `<div class="note stop" style="margin-top:10px"><strong>Paused to protect paid work:</strong><br>${cap.shed.map(esc).join("<br>")}</div>` : ""}
      <div class="note" style="margin-top:10px">Paid API fallback: <strong>${esc(cap.paid_api_fallback || "DISABLED")}</strong>. An exhausted window slows the system down; it never switches billing.</div>
    </div>

    <div class="section-title">The queue</div>
    ${q.length ? `<div class="table-wrap"><table class="fits-narrow"><thead><tr>
      <th>When</th><th>Work</th><th>Value</th><th class="hide-narrow">Effort</th>
      <th class="hide-narrow">Capacity</th><th class="hide-narrow">Deadline</th>
      <th class="hide-narrow">Displaces</th><th class="hide-narrow">Why</th>
    </tr></thead><tbody>${rows}</tbody></table></div>`
      : empty("Nothing queued", "No profitable, compliant work is currently available. That is usually the market, not a bug.")}

    ${(D.policy_blocked || []).length ? `
    <div class="section-title">Declined by the standing rules</div>
    <div class="card card-pad">
      <div class="note" style="margin-bottom:10px">These ${(D.policy_blocked || []).length} listing(s), worth
        ${money((D.policy_blocked || []).reduce((s, b) => s + (b.gross || 0), 0))} gross if every one were won, were
        removed from the queue before ranking. They are shown rather than dropped: work that quietly disappears
        teaches nothing, and a screen that gets one wrong should be visible enough to argue with.</div>
      <div class="table-wrap"><table class="fits-narrow"><thead><tr>
        <th>Listing</th><th>Value</th><th>Gate</th><th class="hide-narrow">Why</th>
      </tr></thead><tbody>${(D.policy_blocked || []).map((b) => `<tr>
        <td class="cell-title">${esc(b.title)}<div class="cell-sub">${esc(b.source)}</div>
          <div class="cell-sub show-narrow" style="margin-top:4px">${esc(b.reason)}</div></td>
        <td>${money(b.gross)}</td>
        <td>${badge(b.gate, "RED")}</td>
        <td class="hide-narrow"><span class="cell-sub">${esc(b.reason)}</span></td>
      </tr>`).join("")}</tbody></table></div>
    </div>` : ""}`;
};

/* ----------------------------------------------------- safety and compliance */

PAGES.compliance = () => {
  const panel = D.compliance || {};
  const items = panel.indicators || [];
  const scope = D.emergency_scope || {};

  const rows = items.map((i) => `<tr>
      <td class="cell-title">${esc(i.label)}</td>
      <td>${badge(i.value, i.ok ? "GREEN" : "RED")}</td>
      <td><span class="cell-sub">${i.desired ? esc(i.desired) : "reports what is true"}</span></td>
      <td><span class="cell-sub">${esc(i.detail)}</span></td>
    </tr>`).join("");

  return `<div class="page-head"><h2>Safety &amp; compliance</h2>
      <p>Every value below is probed live: each one is the result of attempting the thing it
      forbids and being refused. None of them is a constant in the source.</p></div>

    ${panel.all_ok
      ? `<div class="card card-pad" style="margin-bottom:12px"><div class="note">All nine indicators are in their desired state.</div></div>`
      : `<div class="card card-pad" style="margin-bottom:12px"><div class="note stop"><strong>Drifted:</strong> ${(panel.drifted || []).map(esc).join(", ") || "see below"}. Do not leave the system running unattended until this is resolved.</div></div>`}

    <div class="table-wrap"><table><thead><tr><th>Control</th><th>State</th><th>Desired</th><th>Evidence</th></tr></thead>
    <tbody>${rows}</tbody></table></div>

    <div class="section-title">Emergency stop</div>
    <div class="card card-pad">
      <div class="state" style="display:flex;align-items:center;gap:10px;font-weight:650">
        <span class="lamp ${scope.engaged ? "RED" : "GREEN"}"></span>${scope.engaged ? "ENGAGED" : "Not engaged"}</div>
      ${scope.reason ? `<div class="note stop" style="margin-top:8px">${esc(scope.reason)}</div>` : ""}
      <div class="note" style="margin-top:10px">${esc(scope.note || "")}</div>
      <div class="factor" style="margin-top:10px"><div class="fname">Halts</div><div class="fev">${(scope.halts || []).map((h) => esc(h.replace(/_/g, " "))).join(", ")}</div></div>
      <div class="factor"><div class="fname">Never halts</div><div class="fev">${(scope.never_halts || []).map((h) => esc(h.replace(/_/g, " "))).join(", ")}</div></div>
    </div>

    <div class="section-title">Safety self-test</div>
    <div class="card card-pad"><div class="note">Twenty-one invariants are attempted against the live system by
      <code>python -m aicc selftest</code>. Each passes only if the system refuses the violation.
      A check that cannot run reports SKIPPED - it is never counted as a pass.</div></div>`;
};

/* ------------------------------------------------------------------ shell */

const NAV = [
  ["overview", "Overview", "■"],
  ["approvals", "Needs Me", "●"],
  ["queue", "Profit Queue", "⇅"],
  ["opportunities", "Opportunities", "◆"],
  ["proposals", "Proposals", "✎"],
  ["jobs", "Active Jobs", "▶"],
  ["deliverables", "Deliverables", "✔"],
  ["clients", "Clients", "○"],
  ["revenue", "Revenue", "$"],
  ["analytics", "Analytics", "≈"],
  ["fiverr", "Fiverr Launch", "◇"],
  ["portfolio", "Portfolio", "⚑"],
  ["automation", "Automation", "↻"],
  ["health", "System Health", "♥"],
  ["compliance", "Safety & Compliance", "⛨"],
  ["settings", "Settings", "⚙"],
  ["audit", "Audit Log", "≡"],
];

function go(page) {
  const target = PAGES[page] ? page : "overview";
  document.querySelectorAll(".page").forEach((el) => el.classList.toggle("active", el.id === "page-" + target));
  document.querySelectorAll(".nav-item").forEach((el) => el.setAttribute("aria-current", el.dataset.nav === target ? "page" : "false"));
  $("#page-title").textContent = (NAV.find((n) => n[0] === target) || [, "Overview"])[1];
  if (target === "opportunities") renderOpportunities();
  $("#nav").classList.remove("open");
  $("#scrim").hidden = true;
  window.scrollTo(0, 0);
  try { history.replaceState(null, "", "#" + target); } catch (_) { /* file:// */ }
}

/* The four state transitions run as a GitHub Actions workflow rather than only as terminal
 * commands. This page is static and cannot change state - a page that could would need a server,
 * and a server costs money every month whether or not anyone presses anything. But the moment
 * EMERGENCY STOP is worth pressing is exactly the moment Andres is most likely to be away from
 * the one Mac that has a terminal, and a control that is unavailable when it matters is not a
 * control. GitHub's own Run-workflow button works from a phone. */
const CONTROL_ACTIONS = { start: 1, pause: 1, resume: 1, "emergency-stop": 1 };

function controlLink(cmd) {
  const repo = D.repo_url || "";
  return repo ? `${repo}/actions/workflows/control.yml` : "";
}

function commandHint(cmd) {
  if (CONTROL_ACTIONS[cmd]) {
    const url = controlLink(cmd);
    const label = cmd.replace("-", " ").toUpperCase();
    alertBox(
      url
        ? `# ${label} runs in the cloud - no laptop needed, works from a phone.\n` +
          `# Open, press "Run workflow", choose: ${cmd}\n` +
          `${url}\n\n` +
          `# Or, from a terminal on any machine with the repo:\n` +
          `python -m aicc ${cmd}${cmd === "emergency-stop" ? ' --reason "..."' : ""}`
        : `python -m aicc ${cmd}${cmd === "emergency-stop" ? ' --reason "..."' : ""}`
    );
    return;
  }
  const map = {
    "enable-live": "python -m aicc status  # then acknowledge live mode",
  };
  if (cmd.startsWith("fiverr-")) {
    const [verb, key] = [cmd.slice(7).split(":")[0], cmd.split(":")[1]];
    alertBox(verb === "edit"
      ? `# Gig copy lives in source, so edits are reviewable and revertible.\n$EDITOR src/aicc/fiverr_kit.py   # gig key: ${key}\npython -m aicc fiverr check`
      : `python -m aicc fiverr ready ${key}\n\n# Then publish by hand at fiverr.com/manage_gigs - there is no seller API,\n# and the category cannot be changed after you save it.`);
    return;
  }
  const base = map[cmd] || (cmd.startsWith("approve:") ? `python -m aicc approve ${cmd.split(":")[1]}` : `python -m aicc ${cmd}`);
  alertBox(base);
}

function alertBox(cmd) {
  const box = $("#cmdbox");
  $("#cmdbox-cmd").textContent = cmd;
  box.hidden = false;
}

function boot() {
  $("#nav-items").innerHTML = NAV.map(([id, label, glyph]) => {
    const count = id === "approvals" ? (D.attention || []).length
      // "Still in play": not rejected by scoring, and not blocked by the standing rules. The
      // Opportunities page states this breakdown in words so the badge is never a bare number
      // disagreeing with the two larger ones on the Overview.
      : id === "opportunities" ? (D.opportunities || []).filter((o) => !o.rejected && o.policy_allowed !== false).length
      : id === "proposals" ? (D.proposals || []).filter((p) => p.status === "AWAITING_APPROVAL").length
      : 0;
    return `<button class="nav-item" data-nav="${id}"><span class="glyph">${glyph}</span>${esc(label)}
      <span class="count${count ? "" : " zero"}">${count}</span></button>`;
  }).join("");

  $("#pages").innerHTML = NAV.map(([id]) => `<section class="page" id="page-${id}">${PAGES[id] ? PAGES[id]() : ""}</section>`).join("");

  $("#sys-lamp").className = "lamp " + (D.system?.light || "WHITE");
  $("#sys-text").textContent = (D.system?.status_text || "UNKNOWN") + " - " + (D.system?.mode || "DEMO");
  $("#built-at").textContent = "Built " + ago(D.built_at);
  $("#cost-line").innerHTML = `Added cost <span class="cost">${money(D.cost?.ceiling ?? 0, 2)}</span>/mo`;

  document.addEventListener("click", (e) => {
    const nav = e.target.closest("[data-nav]");
    if (nav) { go(nav.dataset.nav); return; }
    const prov = e.target.closest("[data-prov]");
    if (prov) {
      const pop = document.getElementById(prov.dataset.prov);
      document.querySelectorAll(".prov-pop").forEach((p) => { if (p !== pop) p.hidden = true; });
      pop.hidden = !pop.hidden;
      return;
    }
    if (!e.target.closest(".prov-pop")) document.querySelectorAll(".prov-pop").forEach((p) => (p.hidden = true));
    const act = e.target.closest("[data-action],[data-cmd]");
    if (act) commandHint(act.dataset.action || act.dataset.cmd);
  });

  $("#menu").addEventListener("click", () => { $("#nav").classList.toggle("open"); $("#scrim").hidden = !$("#nav").classList.contains("open"); });
  $("#scrim").addEventListener("click", () => { $("#nav").classList.remove("open"); $("#scrim").hidden = true; });
  $("#cmdbox-close").addEventListener("click", () => ($("#cmdbox").hidden = true));

  $("#theme").addEventListener("click", () => {
    const cur = document.documentElement.getAttribute("data-theme");
    const next = cur === "dark" ? "light" : cur === "light" ? "" : "dark";
    if (next) document.documentElement.setAttribute("data-theme", next);
    else document.documentElement.removeAttribute("data-theme");
    try { localStorage.setItem("aicc-theme", next); } catch (_) { /* private mode */ }
  });

  document.addEventListener("change", (e) => { if (e.target.closest(".filters")) renderOpportunities(); });

  go((location.hash || "#overview").slice(1));
}

try {
  const saved = localStorage.getItem("aicc-theme");
  if (saved) document.documentElement.setAttribute("data-theme", saved);
} catch (_) { /* storage can throw; the page must still render */ }

document.addEventListener("DOMContentLoaded", boot);
