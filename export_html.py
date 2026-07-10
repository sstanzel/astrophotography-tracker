#!/usr/bin/env python3
"""
export_html.py — generate a self-contained HTML dashboard from tracker.db.

Reads the SQLite database produced by ingest.py and writes a single
tracker_dashboard.html file: KPI cards, an integration-by-year chart, a
top-targets chart, pipeline status, target-goal progress bars, calibration
status, a quality-control list, and a filterable sessions table.

The file is self-contained except for Chart.js, which loads from a CDN.
It is a snapshot — re-run after ingest.py to refresh.

Usage:
    python3 export_html.py [--db PATH] [--out PATH]
"""
import os, sys, sqlite3, argparse, json, datetime


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(description="Generate the HTML tracker dashboard.")
    ap.add_argument("--db", default=os.path.join(here, "tracker.db"))
    ap.add_argument("--out", default=os.path.join(here, "tracker_dashboard.html"))
    args = ap.parse_args()
    if not os.path.exists(args.db):
        sys.exit(f"Database not found: {args.db}\nRun ingest.py first.")

    con = sqlite3.connect(args.db)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    def rows(sql):
        return [dict(r) for r in cur.execute(sql)]

    def scalar(sql):
        v = cur.execute(sql).fetchone()[0]
        return v if v is not None else 0

    has_validation = cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' "
        "AND name='validation_findings'").fetchone() is not None
    has_integrations = cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' "
        "AND name='integrations'").fetchone() is not None

    data = {
        "generated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "kpis": {
            "hours": scalar("SELECT ROUND(SUM(integration_s)/3600.0,1) FROM sessions WHERE NOT is_other_capture"),
            "deepSkySessions": scalar("SELECT COUNT(*) FROM sessions WHERE NOT is_other_capture"),
            "otherSessions": scalar("SELECT COUNT(*) FROM sessions WHERE is_other_capture"),
            "targetsImaged": scalar("SELECT COUNT(DISTINCT target_id) FROM sessions WHERE lights_kept>0 AND NOT is_other_capture"),
            "keptLights": scalar("SELECT COUNT(*) FROM frames WHERE frame_type='light' AND NOT is_rejected"),
            "calSets": scalar("SELECT COUNT(*) FROM calibration_masters"),
            "calNeeds": scalar("SELECT COUNT(*) FROM v_calibration_needs WHERE status IN ('NO MASTER','STALE (new raw)','STALE (age)')"),
            "integrations": (scalar("SELECT COUNT(*) FROM integrations")
                             if has_integrations else 0),
            "unpublishedTargets": (scalar("SELECT COUNT(*) FROM v_targets_unpublished")
                                   if has_integrations else 0),
            "validationIssues": (scalar("SELECT COUNT(*) FROM validation_findings "
                                        "WHERE severity IN ('error','warning')")
                                 if has_validation else 0),
        },
        "byYear": rows("""SELECT substr(session_date,1,4) AS year, COUNT(*) AS sessions,
                                 ROUND(SUM(integration_s)/3600.0,1) AS hours
                          FROM sessions WHERE NOT is_other_capture
                          GROUP BY year ORDER BY year"""),
        "topTargets": rows("""SELECT catalog_id AS name, hours_lifetime AS hours, sessions
                              FROM v_target_lifetime
                              WHERE hours_lifetime > 0
                              ORDER BY hours_lifetime DESC LIMIT 15"""),
        # A target's furthest stage = the best any of its sessions (single-
        # session integrations) or multi-session integrations reached.
        "targetPipeline": rows("""
            WITH stages AS (
              SELECT target_id, CAST(substr(furthest_stage,1,1) AS INTEGER) AS n
              FROM v_session_pipeline WHERE NOT is_other_capture
              UNION ALL
              SELECT target_id, CAST(substr(furthest_stage,1,1) AS INTEGER) AS n
              FROM v_integration_overview
              UNION ALL
              SELECT t.target_id, 0 FROM targets t
              WHERE NOT t.is_other_capture
                AND NOT EXISTS (SELECT 1 FROM sessions s WHERE s.target_id=t.target_id)
            ),
            best AS (SELECT target_id, MAX(n) AS n FROM stages GROUP BY target_id)
            SELECT CASE n
                WHEN 6 THEN '6 Printed'   WHEN 5 THEN '5 Published'
                WHEN 4 THEN '4 Edited'    WHEN 3 THEN '3 Integrated'
                WHEN 2 THEN '2 Culled'    WHEN 1 THEN '1 Captured'
                ELSE '0 Planned' END AS stage,
              COUNT(*) AS count
            FROM best GROUP BY n ORDER BY n DESC""") if has_integrations else [],
        "sessionPipeline": rows("""SELECT furthest_stage AS stage, COUNT(*) AS count
                                   FROM v_session_pipeline WHERE NOT is_other_capture
                                   GROUP BY furthest_stage ORDER BY furthest_stage"""),
        "progress": rows("""SELECT t.catalog||' '||COALESCE(t.number,'') AS name,
                                   ROUND(COALESCE(v.hours_lifetime,0),1) AS hours,
                                   g.goal_hours AS goal
                            FROM target_goals g
                            JOIN targets t USING(target_id)
                            LEFT JOIN v_target_lifetime v USING(target_id)
                            ORDER BY g.priority, name"""),
        "calibration": rows("""SELECT class, COALESCE(camera,scope,'') AS rig,
                                      temperature_c AS temp, gain, exp_s AS exp,
                                      raw_sets, raw_frames, COALESCE(master_date,'') AS master_date,
                                      status, below_threshold
                               FROM v_calibration_needs
                               ORDER BY class, rig, temperature_c, gain"""),
        "qc": rows("""SELECT target_id, session_date, captured_at_utc, hfr, rms_arcsec
                      FROM v_qc_candidates LIMIT 100"""),
        "sessions": rows("""SELECT s.library_id AS library, s.target_id, t.common_name,
                                   s.scope, s.sensor, s.session_date,
                                   s.lights_kept AS lights, s.lights_rejected AS rejected,
                                   ROUND(s.integration_s/3600.0,2) AS hours,
                                   COALESCE(s.integration_method,'') AS method,
                                   vp.furthest_stage AS stage,
                                   s.is_other_capture AS other
                            FROM sessions s JOIN targets t USING(target_id)
                            JOIN v_session_pipeline vp ON vp.session_id = s.session_id
                            ORDER BY s.session_date DESC, s.target_id"""),
        # One row per deep-sky target: totals for the Targets table (session
        # detail comes from D.sessions, grouped in the browser).
        "targetRollup": rows("""
            SELECT t.target_id, t.common_name,
                   COUNT(s.session_id) AS sessions,
                   COALESCE(SUM(s.lights_kept),0) AS lights,
                   ROUND(COALESCE(SUM(s.integration_s),0)/3600.0,2) AS hours,
                   MIN(s.session_date) AS first_date,
                   MAX(s.session_date) AS last_date,
                   g.goal_hours,
                   CASE WHEN g.goal_hours>0 THEN MIN(100,
                        ROUND(100.0*COALESCE(SUM(s.integration_s),0)/3600.0/g.goal_hours))
                        END AS goal_pct
            FROM targets t
            LEFT JOIN sessions s ON s.target_id=t.target_id AND NOT s.is_other_capture
            LEFT JOIN target_goals g ON g.target_id=t.target_id
            WHERE NOT t.is_other_capture
            GROUP BY t.target_id
            ORDER BY hours DESC, t.target_id"""),
    }
    if has_integrations:
        data["integrations"] = rows("""
            SELECT common_name, target_id, kind, folder_name,
                   COALESCE(scope || ' ' || sensor, 'composite') AS rig,
                   span,
                   COALESCE(built_hours, 0)     AS built_hours,
                   COALESCE(available_hours, 0) AS available_hours,
                   goal_hours,
                   CASE WHEN goal_hours > 0
                        THEN MIN(100, ROUND(100.0 * available_hours / goal_hours))
                        END AS goal_pct,
                   sessions_built, sessions_available,
                   data_through,
                   COALESCE(integration_method, '') AS method,
                   is_stale,
                   furthest_stage
            FROM v_integration_overview
            ORDER BY target_id, folder_name""")
        data["prune"] = rows("""
            SELECT target_id, span, version_count, latest_version
            FROM v_integration_prune ORDER BY target_id""")
    else:
        data["integrations"] = []
        data["prune"] = []
    if has_validation:
        data["validation"] = {
            "summary": rows("SELECT severity, code, n FROM v_validation_summary"),
            "findings": rows("""
                SELECT vf.severity, vf.code, vf.message,
                       COALESCE(s.folder_path, vf.ref_path, '') AS location
                FROM validation_findings vf
                LEFT JOIN sessions s ON s.session_id = vf.session_id
                ORDER BY CASE vf.severity WHEN 'error' THEN 0
                         WHEN 'warning' THEN 1 ELSE 2 END, vf.code, location"""),
        }
    else:
        data["validation"] = {"summary": [], "findings": []}
    con.close()

    html = HTML_TEMPLATE.replace("/*DATA*/", json.dumps(data))
    with open(args.out, "w") as f:
        f.write(html)
    print(f"Wrote {args.out}")
    print(f"  {data['kpis']['deepSkySessions']} sessions · "
          f"{data['kpis']['hours']} hours · {len(data['sessions'])} session rows")


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Astrophotography Tracker</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<style>
  :root {
    --bg:#0f1822; --panel:#18242f; --panel2:#1f2e3c; --ink:#e8eef4;
    --muted:#8aa0b4; --line:#2c3e4f; --accent:#5b9bd5; --accent2:#7ec8a0;
    --warn:#e8a55c; --bad:#e07a6b; --good:#7ec8a0;
  }
  * { box-sizing:border-box; margin:0; padding:0; }
  body { background:var(--bg); color:var(--ink); font:14px/1.5 -apple-system,
         BlinkMacSystemFont,"Segoe UI",Arial,sans-serif; padding:28px; }
  h1 { font-size:24px; font-weight:700; }
  h2 { font-size:15px; font-weight:700; color:var(--accent);
       margin:0 0 12px; letter-spacing:.02em; text-transform:uppercase; }
  .sub { color:var(--muted); font-size:12px; margin-top:4px; }
  .grid { display:grid; gap:16px; }
  .kpis { grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); margin:22px 0; }
  .kpi { background:var(--panel); border:1px solid var(--line); border-radius:10px;
         padding:16px 18px; }
  .kpi .v { font-size:28px; font-weight:700; }
  .kpi .l { color:var(--muted); font-size:11px; text-transform:uppercase;
            letter-spacing:.04em; margin-top:2px; }
  .kpi.warn .v { color:var(--warn); }
  .panel { background:var(--panel); border:1px solid var(--line);
           border-radius:10px; padding:18px 20px; margin-bottom:16px; }
  .two { grid-template-columns:1fr 1fr; }
  @media (max-width:860px){ .two{ grid-template-columns:1fr; } }
  table { width:100%; border-collapse:collapse; font-size:13px; }
  th,td { text-align:left; padding:6px 10px; border-bottom:1px solid var(--line); }
  th { color:var(--muted); font-size:11px; text-transform:uppercase;
       letter-spacing:.03em; cursor:pointer; user-select:none; }
  th:hover { color:var(--accent); }
  tr:hover td { background:var(--panel2); }
  td.num { text-align:right; font-variant-numeric:tabular-nums; }
  .bar { background:var(--panel2); border-radius:4px; height:16px;
         position:relative; overflow:hidden; min-width:120px; }
  .bar > span { position:absolute; left:0; top:0; bottom:0;
                background:var(--accent2); }
  .pill { display:inline-block; padding:2px 8px; border-radius:10px;
          font-size:11px; font-weight:600; }
  .pill.bad{ background:#3a2420; color:var(--bad); }
  .pill.warn{ background:#3a3020; color:var(--warn); }
  .pill.good{ background:#1f3329; color:var(--good); }
  .pill.na{ background:var(--panel2); color:var(--muted); }
  input[type=search]{ background:var(--panel2); border:1px solid var(--line);
    color:var(--ink); border-radius:6px; padding:6px 10px; font-size:13px;
    width:260px; }
  .chartbox{ position:relative; height:260px; }
  .foot{ color:var(--muted); font-size:11px; margin-top:8px; text-align:center; }
  .scroll{ max-height:420px; overflow:auto; }
</style>
</head>
<body>
<header>
  <h1>Astrophotography Tracker</h1>
  <div class="sub" id="gen"></div>
</header>

<div class="grid kpis" id="kpis"></div>

<div class="panel" id="healthPanel">
  <h2>Data health</h2>
  <div id="healthSummary" style="margin-bottom:12px"></div>
  <div class="scroll"><table id="healthTable"></table></div>
</div>

<div class="grid two">
  <div class="panel"><h2>Integration by year</h2><div class="chartbox"><canvas id="yearChart"></canvas></div></div>
  <div class="panel"><h2>Top targets by lifetime hours</h2><div class="chartbox"><canvas id="targetChart"></canvas></div></div>
</div>

<div class="grid two">
  <div class="panel"><h2>Target Image Processing</h2><div id="targetPipeline"></div></div>
  <div class="panel"><h2>Session Image Processing</h2><div id="sessionPipeline"></div></div>
</div>

<div class="panel">
  <h2>Multi-session integrations</h2>
  <div id="integrations"></div>
</div>

<div class="panel"><h2>Target goal progress</h2><div id="progress"></div></div>

<div class="panel">
  <h2>Targets</h2>
  <div class="sub" style="margin-bottom:8px">Totals per target; expand detail in the Sessions table below.</div>
  <div class="scroll"><table id="targetsTable"></table></div>
</div>

<div class="panel">
  <h2>Sessions</h2>
  <div style="margin-bottom:10px"><input type="search" id="sfilter" placeholder="filter by target, scope, sensor..."></div>
  <div class="scroll"><table id="sessionsTable"></table></div>
</div>

<div class="panel"><h2>Calibration status</h2><div id="calibration"></div></div>

<div class="panel"><h2>Quality-control candidates</h2><div id="qc"></div></div>

<div class="foot">Generated by export_html.py from tracker.db — re-run after ingest.py to refresh.</div>

<script>
const D = /*DATA*/;
document.getElementById("gen").textContent = "Generated " + D.generated + " from tracker.db";

// ---- KPI cards ----
const kpiDefs = [
  ["hours","Deep-sky hours",false],
  ["deepSkySessions","Deep-sky sessions",false],
  ["targetsImaged","Targets imaged",false],
  ["keptLights","Kept light frames",false],
  ["otherSessions","Other-capture sessions",false],
  ["calSets","Calibration sets",false],
  ["calNeeds","Calibration needs attention",true],
  ["integrations","Multi-session integrations",false],
  ["unpublishedTargets","Targets not published",true],
  ["validationIssues","Validation issues",true],
];
document.getElementById("kpis").innerHTML = kpiDefs.map(([k,l,warn])=>
  `<div class="kpi${warn && D.kpis[k]>0?' warn':''}">
     <div class="v">${typeof D.kpis[k]==='number'?D.kpis[k].toLocaleString():D.kpis[k]}</div>
     <div class="l">${l}</div></div>`).join("");

// ---- data health ----
(function(){
  const v = D.validation || {summary:[], findings:[]};
  const sevClass = s => s==='error'?'bad':s==='warning'?'warn':'na';
  const summaryEl = document.getElementById("healthSummary");
  const tableEl = document.getElementById("healthTable");
  if(!v.findings.length){
    summaryEl.innerHTML = '<div class="sub">No validation findings — every '+
      'session, frame and calibration set passed the checks.</div>';
    tableEl.remove();
    return;
  }
  summaryEl.innerHTML = v.summary.map(r =>
    `<span class="pill ${sevClass(r.severity)}" style="margin:0 6px 6px 0">`+
    `${r.code} · ${r.n}</span>`).join("");
  tableEl.innerHTML =
    "<thead><tr><th>Severity</th><th>Check</th><th>Location</th>"+
    "<th>Detail</th></tr></thead><tbody>" +
    v.findings.map(r =>
      `<tr><td><span class="pill ${sevClass(r.severity)}">${r.severity}</span></td>`+
      `<td>${r.code}</td><td>${r.location}</td><td>${r.message}</td></tr>`
    ).join("") + "</tbody>";
})();

// ---- charts ----
const gridColor = "#2c3e4f", tickColor = "#8aa0b4";
new Chart(document.getElementById("yearChart"), {
  type:"bar",
  data:{ labels:D.byYear.map(r=>r.year),
    datasets:[
      {label:"Hours", data:D.byYear.map(r=>r.hours), backgroundColor:"#5b9bd5"},
      {label:"Sessions", data:D.byYear.map(r=>r.sessions), backgroundColor:"#7ec8a0"},
    ]},
  options:{ responsive:true, maintainAspectRatio:false,
    scales:{ x:{grid:{color:gridColor},ticks:{color:tickColor}},
             y:{grid:{color:gridColor},ticks:{color:tickColor}} },
    plugins:{ legend:{labels:{color:tickColor}} } }
});
new Chart(document.getElementById("targetChart"), {
  type:"bar",
  data:{ labels:D.topTargets.map(r=>r.name),
    datasets:[{label:"Hours", data:D.topTargets.map(r=>r.hours),
               backgroundColor:"#5b9bd5"}]},
  options:{ indexAxis:"y", responsive:true, maintainAspectRatio:false,
    scales:{ x:{grid:{color:gridColor},ticks:{color:tickColor}},
             y:{grid:{color:gridColor},ticks:{color:tickColor}} },
    plugins:{ legend:{display:false} } }
});

// ---- pipeline count tables (every stage in order, zero-filled) ----
const PIPELINE_STAGES = ["0 Planned","1 Captured","2 Culled","3 Integrated",
                         "4 Edited","5 Published","6 Printed"];
function stageTable(elId, data){
  const by = {}; (data||[]).forEach(r=>by[r.stage]=r.count);
  document.getElementById(elId).innerHTML =
    "<table><thead><tr><th>Processing step</th><th>Count</th></tr></thead><tbody>" +
    PIPELINE_STAGES.map(s=>`<tr><td>${s}</td>`+
      `<td class="num">${by[s]||0}</td></tr>`).join("") +
    "</tbody></table>";
}
stageTable("targetPipeline", D.targetPipeline);
stageTable("sessionPipeline", D.sessionPipeline);

// ---- multi-session integrations ----
(function(){
  const el = document.getElementById("integrations");
  const rows = D.integrations || [];
  if(!rows.length){
    el.innerHTML = '<div class="sub">No multi-session integrations yet — '+
      'they appear here once you create folders under '+
      '{target}/integrations/ with an integration.toml manifest.</div>';
    return;
  }
  const prune = (D.prune || []).length;
  const head = prune
    ? `<div class="sub" style="margin-bottom:8px">${prune} integration `+
      `lineage(s) have multiple versions — prune candidates (keep the latest).</div>`
    : "";
  el.innerHTML = head +
    "<table><thead><tr><th>Target</th><th>Rig</th><th>Span</th>"+
    "<th>Built hrs</th><th>Available</th><th>Goal</th><th>Progress</th>"+
    "<th>Data through</th><th>Method</th><th>State</th><th>Stage</th>"+
    "</tr></thead><tbody>" +
    rows.map(r=>{
      const pct = r.goal_pct;
      const prog = (r.goal_hours>0)
        ? `<div class="bar"><span style="width:${pct}%"></span></div> ${pct}%`
        : "";
      const state = r.is_stale
        ? `<span class="pill warn">⚠ ${r.sessions_available-r.sessions_built} new `+
          `(${(r.available_hours-r.built_hours).toFixed(1)} h)</span>`
        : `<span class="pill good">✓ current</span>`;
      return `<tr><td>${r.common_name||r.target_id}</td>`+
      `<td>${r.rig}</td><td>${r.span||""}</td>`+
      `<td class="num">${r.built_hours.toFixed(1)}</td>`+
      `<td class="num">${r.available_hours.toFixed(1)}</td>`+
      `<td class="num">${r.goal_hours??""}</td>`+
      `<td>${prog}</td>`+
      `<td>${r.data_through||""}</td>`+
      `<td>${r.method}</td>`+
      `<td>${state}</td>`+
      `<td>${r.furthest_stage}</td></tr>`;
    }).join("") + "</tbody></table>";
})();

// ---- targets table (per-target totals) ----
(function(){
  const rows = D.targetRollup || [];
  const span = r => (r.first_date && r.last_date)
    ? (r.first_date===r.last_date ? r.first_date : r.first_date+" → "+r.last_date) : "";
  document.getElementById("targetsTable").innerHTML =
    "<thead><tr><th>Target</th><th>Sessions</th><th>Lights</th>"+
    "<th>Hrs</th><th>Dates</th><th>Goal</th><th>Progress</th></tr></thead><tbody>"+
    rows.map(r=>{
      const prog = (r.goal_hours>0)
        ? `<div class="bar"><span style="width:${r.goal_pct}%"></span></div> ${r.goal_pct}%` : "";
      return `<tr><td>${r.common_name||r.target_id}</td>`+
        `<td class="num">${r.sessions}</td>`+
        `<td class="num">${r.lights}</td>`+
        `<td class="num">${r.hours.toFixed(1)}</td>`+
        `<td>${span(r)}</td>`+
        `<td class="num">${r.goal_hours??""}</td>`+
        `<td>${prog}</td></tr>`;
    }).join("") + "</tbody>";
})();

// ---- progress bars ----
(function(){
  const el = document.getElementById("progress");
  if(!D.progress.length){ el.innerHTML =
    '<div class="sub">No target goals set yet. Add rows to the target_goals table to see progress bars here.</div>';
    return; }
  el.innerHTML = "<table><thead><tr><th>Target</th><th>Hours</th><th>Goal</th>"+
    "<th>Progress</th></tr></thead><tbody>" +
    D.progress.map(r=>{
      const pct = r.goal>0 ? Math.min(100, 100*r.hours/r.goal) : 0;
      return `<tr><td>${r.name}</td><td class="num">${r.hours}</td>`+
        `<td class="num">${r.goal??''}</td>`+
        `<td><div class="bar"><span style="width:${pct}%"></span></div> `+
        `${pct.toFixed(0)}%</td></tr>`;
    }).join("") + "</tbody></table>";
})();

// ---- calibration ----
(function(){
  function pill(s){
    if(s==='OK') return '<span class="pill good">OK</span>';
    if(s==='NO MASTER') return '<span class="pill bad">NO MASTER</span>';
    if(s && s.startsWith('STALE')) return `<span class="pill warn">${s}</span>`;
    if(s && s.startsWith('N/A')) return '<span class="pill na">N/A</span>';
    return s||'';
  }
  document.getElementById("calibration").innerHTML =
    "<table><thead><tr><th>Class</th><th>Rig</th><th>Temp</th><th>Gain</th>"+
    "<th>Exp</th><th>Raw sets</th><th>Raw frames</th><th>Master</th><th>Status</th>"+
    "</tr></thead><tbody>" +
    D.calibration.map(r=>`<tr><td>${r.class}</td><td>${r.rig}</td>`+
      `<td class="num">${r.temp??''}</td><td class="num">${r.gain??''}</td>`+
      `<td class="num">${r.exp??''}</td><td class="num">${r.raw_sets}</td>`+
      `<td class="num">${r.raw_frames}</td><td>${r.master_date}</td>`+
      `<td>${pill(r.status)}</td></tr>`).join("") + "</tbody></table>";
})();

// ---- QC ----
(function(){
  const el = document.getElementById("qc");
  if(!D.qc.length){ el.innerHTML =
    '<div class="sub">No frames exceed the HFR &gt; 3 / RMS &gt; 1.5&Prime; thresholds. '+
    'This populates as NINA captures with quality metrics accumulate.</div>';
    return; }
  el.innerHTML = "<table><thead><tr><th>Target</th><th>Session</th><th>Captured</th>"+
    "<th>HFR</th><th>RMS&Prime;</th></tr></thead><tbody>" +
    D.qc.map(r=>`<tr><td>${r.target_id}</td><td>${r.session_date}</td>`+
      `<td>${r.captured_at_utc}</td><td class="num">${r.hfr??''}</td>`+
      `<td class="num">${r.rms_arcsec??''}</td></tr>`).join("") + "</tbody></table>";
})();

// ---- sessions table (filter + sort) ----
(function(){
  const cols = [
    ["session_date","Date"],["target_id","Target"],["common_name","Name"],
    ["scope","Scope"],["sensor","Sensor"],["lights","Lights","num"],
    ["rejected","Rej.","num"],["hours","Hrs","num"],
    ["stage","Stage"],["method","Method"],["library","Lib"],
  ];
  let sortKey="session_date", sortDir=-1;
  const tbl = document.getElementById("sessionsTable");
  function render(filter){
    let rows = D.sessions.slice();
    if(filter){
      const f = filter.toLowerCase();
      rows = rows.filter(r => (r.target_id+" "+r.common_name+" "+r.scope+" "+
        r.sensor).toLowerCase().includes(f));
    }
    rows.sort((a,b)=>{
      const x=a[sortKey], y=b[sortKey];
      if(x<y) return -sortDir; if(x>y) return sortDir; return 0;
    });
    tbl.innerHTML =
      "<thead><tr>"+cols.map(c=>`<th data-k="${c[0]}">${c[1]}`+
        (sortKey===c[0]?(sortDir>0?" ▲":" ▼"):"")+"</th>").join("")+
      "</tr></thead><tbody>"+
      rows.map(r=>"<tr>"+cols.map(c=>
        `<td class="${c[2]==='num'?'num':''}">${r[c[0]]??''}</td>`).join("")+
        "</tr>").join("")+"</tbody>";
    tbl.querySelectorAll("th").forEach(th=>th.onclick=()=>{
      const k=th.dataset.k;
      if(k===sortKey) sortDir=-sortDir; else { sortKey=k; sortDir=1; }
      render(document.getElementById("sfilter").value);
    });
  }
  document.getElementById("sfilter").oninput = e => render(e.target.value);
  render("");
})();
</script>
</body>
</html>
"""

if __name__ == "__main__":
    main()
