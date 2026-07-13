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

    has_validation = (
        cur.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' " "AND name='validation_findings'"
        ).fetchone()
        is not None
    )
    has_integrations = (
        cur.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' " "AND name='integrations'"
        ).fetchone()
        is not None
    )

    data = {
        "generated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "kpis": {
            "hours": scalar(
                "SELECT ROUND(SUM(integration_s)/3600.0) FROM sessions WHERE NOT is_other_capture"
            ),
            "deepSkySessions": scalar("SELECT COUNT(*) FROM sessions WHERE NOT is_other_capture"),
            "otherSessions": scalar("SELECT COUNT(*) FROM sessions WHERE is_other_capture"),
            "targetsImaged": scalar(
                "SELECT COUNT(DISTINCT target_id) FROM sessions WHERE lights_kept>0 AND NOT is_other_capture"
            ),
            "keptLights": scalar(
                "SELECT COUNT(*) FROM frames WHERE frame_type='light' AND NOT is_rejected"
            ),
            "calSets": scalar("SELECT COUNT(*) FROM calibration_masters"),
            "calNeeds": scalar("""SELECT COUNT(*) FROM v_calibration_needs
                                  WHERE status IN ('no master','stale (new raw)','stale (age)')
                                    AND (class='dark' OR (class='bias' AND (SELECT require_bias
                                         FROM coverage_settings WHERE id=1)=1))"""),
            "integrations": (
                scalar("SELECT COUNT(*) FROM integrations") if has_integrations else 0
            ),
            "unpublishedTargets": (
                scalar("SELECT COUNT(*) FROM v_targets_unpublished") if has_integrations else 0
            ),
            "validationIssues": (
                scalar(
                    "SELECT COUNT(*) FROM validation_findings "
                    "WHERE severity IN ('error','warning')"
                )
                if has_validation
                else 0
            ),
        },
        "byYear": rows("""SELECT substr(session_date,1,4) AS year, COUNT(*) AS sessions,
                                 ROUND(SUM(integration_s)/3600.0) AS hours
                          FROM sessions WHERE NOT is_other_capture
                          GROUP BY year ORDER BY year"""),
        "topTargets": rows("""SELECT catalog_id AS name, ROUND(hours_lifetime) AS hours, sessions
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
        "calCoverage": rows("""SELECT camera, gain, exp_s AS exp,
                                      light_subs AS subs, hours,
                                      temp_min, temp_max,
                                      subs_dark_master, subs_dark_raw, subs_dark_none,
                                      dark_status, dark_low, bias_status, bias_low
                               FROM v_light_calibration_coverage
                               ORDER BY camera, gain, exp_s"""),
        "qc": rows("""SELECT target_id, session_date, captured_at_utc, hfr, rms_arcsec
                      FROM v_qc_candidates LIMIT 100"""),
        "sessions": rows("""SELECT s.library_id AS library, s.target_id, t.common_name,
                                   s.scope, s.sensor, s.session_date,
                                   s.lights_kept AS lights, s.lights_rejected AS rejected,
                                   ROUND(s.integration_s/3600.0,2) AS hours,
                                   COALESCE(s.integration_method,'') AS method,
                                   vp.furthest_stage AS stage,
                                   -- 'with sibling' shows the sibling's target id ('with M_44');
                                   -- 'nearest' adds the day gap ('nearest M_44 (5d prior)') —
                                   -- flats_ref is a session folder name ending in YYYY-MM-DD
                                   CASE s.flats_source
                                     WHEN 'with sibling' THEN 'with '||
                                       CASE WHEN instr(s.flats_ref,' ')>0
                                            THEN substr(s.flats_ref,1,instr(s.flats_ref,' ')-1)
                                            ELSE COALESCE(s.flats_ref,'?') END
                                     WHEN 'nearest' THEN 'nearest '||
                                       CASE WHEN instr(s.flats_ref,' ')>0
                                            THEN substr(s.flats_ref,1,instr(s.flats_ref,' ')-1)
                                            ELSE COALESCE(s.flats_ref,'?') END
                                       ||' ('||CAST(julianday(s.session_date)
                                               - julianday(substr(s.flats_ref,-10)) AS INTEGER)
                                       ||'d prior)'
                                     ELSE COALESCE(s.flats_source,'') END AS flats,
                                   -- 'master'/'raws' add the matched set's date
                                   -- ('master 2026-04-18') — bias_ref is a library
                                   -- set folder ending in YYYY-MM-DD
                                   CASE WHEN s.bias_source IN ('master','raws')
                                     THEN s.bias_source||' '||
                                       CASE WHEN s.bias_ref GLOB
                                                 '*[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'
                                            THEN substr(s.bias_ref,-10)
                                            ELSE COALESCE(s.bias_ref,'?') END
                                     ELSE COALESCE(s.bias_source,'') END AS bias,
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
        # Calibration findings (CAL_*) live in the Calibration status panel, not
        # Data Health.
        data["validation"] = {
            "summary": rows("""SELECT severity, code, COUNT(*) AS n
                               FROM validation_findings WHERE code NOT LIKE 'CAL%'
                               GROUP BY severity, code
                               ORDER BY CASE severity WHEN 'error' THEN 0
                                        WHEN 'warning' THEN 1 ELSE 2 END, code"""),
            "findings": rows("""
                SELECT vf.severity, vf.code, vf.message,
                       COALESCE(s.folder_path, vf.ref_path, '') AS location
                FROM validation_findings vf
                LEFT JOIN sessions s ON s.session_id = vf.session_id
                WHERE vf.code NOT LIKE 'CAL%'
                ORDER BY CASE vf.severity WHEN 'error' THEN 0
                         WHEN 'warning' THEN 1 ELSE 2 END, vf.code, location"""),
        }
        data["calFindings"] = rows("""
            SELECT severity, code, message, COALESCE(ref_path,'') AS location
            FROM validation_findings
            WHERE code LIKE 'CAL%'
            ORDER BY CASE severity WHEN 'error' THEN 0
                     WHEN 'warning' THEN 1 ELSE 2 END, code, location""")
    else:
        data["validation"] = {"summary": [], "findings": []}
        data["calFindings"] = []

    # ---- Work Queue: one worklist per next-action ----
    def sess_at(stage):
        # stage is a trusted literal ('1 Captured' etc.), not user input.
        return rows(f"""SELECT s.target_id, t.common_name, s.scope, s.sensor,
                              s.session_date, s.library_id AS library,
                              s.lights_kept AS lights,
                              s.lights_rejected AS rejected,
                              ROUND(s.integration_s/3600.0,2) AS hours,
                              (SELECT COUNT(*) FROM frames f
                               WHERE f.session_id=s.session_id AND f.frame_type='light'
                                 AND NOT f.is_rejected
                                 AND (f.hfr>3.0 OR f.rms_arcsec>1.5)) AS qc_flags,
                              (SELECT COUNT(*) FROM processing_todos pt
                               WHERE pt.session_id=s.session_id) AS todos
                       FROM sessions s JOIN targets t USING(target_id)
                       JOIN v_session_pipeline vp ON vp.session_id=s.session_id
                       WHERE NOT s.is_other_capture AND vp.furthest_stage='{stage}'
                       ORDER BY s.session_date DESC""")

    edit_rows = rows("""
        SELECT s.target_id, (s.session_date||'  '||s.scope||' '||s.sensor) AS image,
               'session' AS type, ROUND(s.integration_s/3600.0,2) AS hours,
               COALESCE(s.integration_method,'') AS method,
               s.library_id AS library,
               (SELECT COUNT(*) FROM processing_todos pt
                WHERE pt.session_id=s.session_id) AS todos
        FROM sessions s JOIN targets t USING(target_id)
        WHERE NOT s.is_other_capture AND s.stage_integrate=2 AND s.stage_edit<2
        UNION ALL
        SELECT i.target_id, i.folder_name AS image, 'integration' AS type,
               ROUND(SUM(mem.integration_s)/3600.0,2) AS hours,
               COALESCE(i.integration_method,'') AS method,
               i.library_id AS library,
               0 AS todos
        FROM integrations i JOIN targets t USING(target_id)
        LEFT JOIN (SELECT im.integration_id, s.integration_s FROM integration_members im
                   JOIN sessions s ON s.session_id=im.session_id) mem
             ON mem.integration_id=i.integration_id
        WHERE i.stage_edit<2 GROUP BY i.integration_id
        ORDER BY hours DESC""") if has_integrations else sess_at("__none__")
    data["worklists"] = {
        "cull": sess_at("1 Captured"),
        "integrate": sess_at("2 Culled"),
        "edit": edit_rows,
        "restack": rows("""SELECT target_id, folder_name,
                                  library_id AS library,
                                  COALESCE(built_hours,0) AS built_hours,
                                  COALESCE(available_hours,0) AS available_hours,
                                  ROUND(COALESCE(available_hours,0)-COALESCE(built_hours,0),2) AS behind
                           FROM v_integration_overview WHERE is_stale=1
                           ORDER BY behind DESC""") if has_integrations else [],
        "capture": rows("""
            SELECT t.target_id,
                   ROUND(COALESCE(SUM(s.integration_s),0)/3600.0,1) AS hours,
                   -- a target's sessions can span multiple libraries
                   GROUP_CONCAT(DISTINCT s.library_id) AS library,
                   g.goal_hours AS goal,
                   ROUND(g.goal_hours-COALESCE(SUM(s.integration_s),0)/3600.0,1) AS gap,
                   g.priority
            FROM targets t
            LEFT JOIN sessions s ON s.target_id=t.target_id AND NOT s.is_other_capture
            LEFT JOIN target_goals g ON g.target_id=t.target_id
            WHERE NOT t.is_other_capture
            GROUP BY t.target_id
            HAVING (g.goal_hours IS NOT NULL AND hours < g.goal_hours)
                OR COUNT(s.session_id)=0
            ORDER BY g.priority, gap DESC"""),
        "masters": rows("""SELECT class, camera, temperature_c AS temp, gain,
                                  exp_s AS exp, frame_count AS frames,
                                  library_id AS library
                           FROM calibration_masters
                           WHERE is_generated_master=0
                             AND (class='dark' OR (class='bias' AND (SELECT require_bias
                                  FROM coverage_settings WHERE id=1)=1))
                           ORDER BY class, camera, temperature_c, gain, exp_s"""),
        "coverage": rows("""SELECT camera, gain, exp_s AS exp, light_subs AS subs,
                                   hours, temp_min, temp_max,
                                   dark_status, bias_status
                            FROM v_light_calibration_coverage
                            WHERE dark_status NOT IN ('ok','n/a')
                               OR bias_status NOT IN ('ok','n/a')
                            ORDER BY camera, gain, exp_s"""),
    }

    # ---- Publish / Print ledgers + unpublished candidates ----
    data["published"] = rows("""
        SELECT p.target_id, t.common_name, p.kind, p.url, p.title, p.published_at,
               COALESCE(i.folder_name, s.folder_path, '') AS image
        FROM publications p JOIN targets t USING(target_id)
        LEFT JOIN integrations i ON i.integration_id=p.integration_id
        LEFT JOIN sessions s ON s.session_id=p.session_id
        WHERE p.kind IN ('astrobin','social','other')
        ORDER BY p.published_at DESC, t.common_name""")
    data["printed"] = rows("""
        SELECT p.target_id, t.common_name, p.title, p.published_at,
               COALESCE(i.folder_name, s.folder_path, '') AS image
        FROM publications p JOIN targets t USING(target_id)
        LEFT JOIN integrations i ON i.integration_id=p.integration_id
        LEFT JOIN sessions s ON s.session_id=p.session_id
        WHERE p.kind='print' ORDER BY p.published_at DESC, t.common_name""")
    data["todos"] = rows("""
        SELECT pt.todo, t.target_id AS target,
               s.scope||' '||s.sensor AS rig, s.session_date AS date
        FROM processing_todos pt
        JOIN sessions s ON s.session_id=pt.session_id
        JOIN targets t USING(target_id)
        ORDER BY s.session_date DESC, pt.seq""")
    con.close()

    html = HTML_TEMPLATE.replace("/*DATA*/", json.dumps(data))
    with open(args.out, "w") as f:
        f.write(html)
    print(f"Wrote {args.out}")
    print(
        f"  {data['kpis']['deepSkySessions']} sessions · "
        f"{data['kpis']['hours']} hours · {len(data['sessions'])} session rows"
    )


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
  th,td { text-align:center; padding:6px 10px; border-bottom:1px solid var(--line); }
  th { color:var(--muted); font-size:11px; text-transform:uppercase;
       letter-spacing:.03em; cursor:pointer; user-select:none; }
  th:hover { color:var(--accent); }
  tr:hover td { background:var(--panel2); }
  td.num { text-align:center; font-variant-numeric:tabular-nums; }
  tr.trow { cursor:pointer; }
  td.tog { width:1.4em; text-align:center; color:var(--muted); }
  tr.detail td { background:var(--panel2); padding:4px 8px; }
  table.mini { width:100%; font-size:.92em; margin:2px 0; }
  table.mini th { position:static; background:transparent; }
  .bar { background:var(--panel2); border-radius:4px; height:16px;
         position:relative; overflow:hidden; min-width:120px; }
  .bar > span { position:absolute; left:0; top:0; bottom:0;
                background:var(--accent2); }
  .pill { display:inline-block; padding:2px 8px; border-radius:10px;
          font-size:11px; font-weight:600; }
  .chip { display:inline-block; padding:5px 12px; margin:0 6px 6px 0; cursor:pointer;
          border-radius:14px; background:var(--panel2); color:var(--fg);
          font-size:13px; border:1px solid transparent; user-select:none; }
  .chip.on { background:var(--accent2); color:#0b1020; font-weight:700; }
  .chip .n { opacity:.7; margin-left:5px; }
  .pill.bad{ background:#3a2420; color:var(--bad); }
  .pill.warn{ background:#3a3020; color:var(--warn); }
  .pill.good{ background:#1f3329; color:var(--good); }
  .pill.na{ background:var(--panel2); color:var(--muted); }
  .pill.todo{ background:var(--panel2); color:var(--accent2);
              border:1px solid var(--accent2); }
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

<div class="grid two">
  <div class="panel"><h2>Integration by year</h2><div class="chartbox"><canvas id="yearChart"></canvas></div></div>
  <div class="panel"><h2>Top targets by lifetime hours</h2><div class="chartbox"><canvas id="targetChart"></canvas></div></div>
</div>

<div class="grid two">
  <div class="panel"><h2>Published</h2><div class="scroll"><table id="pubTable"></table></div></div>
  <div class="panel"><h2>Printed</h2><div class="scroll"><table id="printTable"></table></div></div>
</div>

<div class="grid two">
  <div class="panel"><h2>Target Image Processing</h2><div id="targetPipeline"></div></div>
  <div class="panel"><h2>Session Image Processing</h2><div id="sessionPipeline"></div></div>
</div>

<div class="panel">
  <h2>Image Processing Pipeline</h2>
  <div class="sub" style="margin-bottom:8px">What's next — pick a step, then filter/sort the list.</div>
  <div id="wqChips" style="margin-bottom:10px"></div>
  <div style="margin-bottom:10px"><input type="search" id="wqfilter" placeholder="filter this list..."></div>
  <div class="scroll"><table id="wqTable"></table></div>
</div>

<div class="panel"><h2>Target goal progress</h2><div id="progress"></div></div>

<div class="panel">
  <h2>Targets</h2>
  <div class="sub" style="margin-bottom:8px">Totals per target — click a row to expand its sessions.</div>
  <div style="margin-bottom:10px"><input type="search" id="tfilter" placeholder="filter by target or name..."></div>
  <div class="scroll"><table id="targetsTable"></table></div>
</div>

<div class="panel">
  <h2>Sessions</h2>
  <div style="margin-bottom:10px"><input type="search" id="sfilter" placeholder="filter by target, date, scope, sensor..."></div>
  <div class="scroll"><table id="sessionsTable"></table></div>
</div>

<div class="panel">
  <h2>Multi-session integrations</h2>
  <div class="scroll"><div id="integrations"></div></div>
</div>

<div class="panel">
  <h2>Processing to-dos</h2>
  <div class="sub" style="margin-bottom:8px">From the <code>[future_processing]</code> todo lists in session notes.toml.</div>
  <div style="margin-bottom:10px"><input type="search" id="todofilter" placeholder="filter to-dos..."></div>
  <div class="scroll"><table id="todoTable"></table></div>
</div>

<div class="panel"><h2>Light &harr; calibration coverage</h2>
  <div id="calCovSummary" class="sub" style="margin-bottom:8px"></div>
  <div id="calFindings"></div>
  <div class="sub" style="margin-bottom:8px">Every camera / gain / exposure combo your kept lights use, matched against the
  library's darks (same camera+gain+exposure, set temperature within &plusmn;5&nbsp;&deg;C; untracked-temperature sets match any)
  and bias (same camera+gain; sets with no readable gain/ISO match any). <b>ok</b> = a fresh master covers every sub &middot;
  <b>to build</b> = raws on hand, master not built &middot; <b>to shoot</b> = some subs have no matching calibration data &middot;
  <b>stale</b> = mastered, but newer raws exist or the master aged past its refresh window &middot; <b>n/a</b> = bias isn't part
  of the calibration recipe (<code>calibration_thresholds.toml</code> <code>[coverage] require_bias</code>) &middot;
  <i>low frames</i> = the matched raw set is under its min-frames threshold. Flats are per-session by design and not matched here.</div>
  <div style="margin-bottom:10px"><input type="search" id="covfilter" placeholder="filter by camera, status..."></div>
  <div class="scroll"><table id="covTable"></table></div>
</div>

<div class="panel">
  <h2>Quality-control candidates</h2>
  <div class="sub" style="margin-bottom:8px">Kept light subs whose NINA metrics exceed HFR&nbsp;&gt;&nbsp;3 or RMS&nbsp;&gt;&nbsp;1.5&Prime; — the first candidates to reject when culling that session. (Only NINA captures record these; ASIAir subs won't appear.)</div>
  <div class="scroll"><div id="qc"></div></div>
</div>

<div class="panel" id="healthPanel">
  <h2>Data health</h2>
  <div id="healthSummary" style="margin-bottom:12px"></div>
  <div class="scroll"><table id="healthTable"></table></div>
</div>

<div class="foot">Generated by export_html.py from tracker.db — re-run after ingest.py to refresh.</div>

<script>
const D = /*DATA*/;
document.getElementById("gen").textContent = "Generated " + D.generated + " from tracker.db";

// ---- display formatting (see STYLE.md) ----
// hours: summaries/totals round to the nearest hour; per-item breakdowns
// show hours + minutes. exposures: no trailing .0.
function fmtH(v){ return v==null ? "" : String(Math.round(v)); }
function fmtHM(v){ if(v==null) return "";
  const t=Math.round(v*60), h=Math.floor(t/60), m=t%60;
  return h ? (m ? h+"h "+String(m).padStart(2,"0")+"m" : h+"h") : m+"m"; }
function fmtExp(v){ return v==null ? "" : (v%1===0 ? String(Math.round(v)) : String(v)); }

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
    "<th>Built hours</th><th>Available</th><th>Goal</th><th>Progress</th>"+
    "<th>Data through</th><th>Method</th><th>State</th><th>Stage</th>"+
    "</tr></thead><tbody>" +
    rows.map(r=>{
      const pct = r.goal_pct;
      const prog = (r.goal_hours>0)
        ? `<div class="bar"><span style="width:${pct}%"></span></div> ${pct}%`
        : "";
      const state = r.is_stale
        ? `<span class="pill warn">⚠ ${r.sessions_available-r.sessions_built} new `+
          `(${fmtHM(r.available_hours-r.built_hours)})</span>`
        : `<span class="pill good">✓ current</span>`;
      return `<tr><td>${r.common_name||r.target_id}</td>`+
      `<td>${r.rig}</td><td>${r.span||""}</td>`+
      `<td class="num">${fmtHM(r.built_hours)}</td>`+
      `<td class="num">${fmtHM(r.available_hours)}</td>`+
      `<td class="num">${r.goal_hours??""}</td>`+
      `<td>${prog}</td>`+
      `<td>${r.data_through||""}</td>`+
      `<td>${r.method}</td>`+
      `<td>${state}</td>`+
      `<td>${r.furthest_stage}</td></tr>`;
    }).join("") + "</tbody></table>";
})();

// ---- targets table (totals; expandable sessions; filter + sort) ----
(function(){
  const cols = [
    ["target_id","Target ID"],["common_name","Name"],["sessions","Sessions","num"],
    ["lights","Lights","num"],["hours","Hours","num"],["dates","Dates"],
    ["goal_hours","Goal","num"],["goal_pct","Progress","num"],
  ];
  // child session detail columns (same labels as the Sessions table)
  const SCOLS = [["session_date","Date"],["scope","Scope"],["sensor","Sensor"],
    ["lights","Lights","num"],["rejected","Rejected","num"],["hours","Hours","num"],
    ["stage","Stage"],["method","Method"]];
  const byTarget = {};
  (D.sessions||[]).forEach(s=>{(byTarget[s.target_id]=byTarget[s.target_id]||[]).push(s);});
  const data = (D.targetRollup||[]).map(r=>({...r,
    dates:(r.first_date&&r.last_date)
      ? (r.first_date===r.last_date? r.first_date : r.first_date+" → "+r.last_date) : ""}));
  let sortKey="hours", sortDir=-1;
  const open = new Set();
  const tbl = document.getElementById("targetsTable");
  const detail = tid => {
    const ss=(byTarget[tid]||[]).slice().sort((a,b)=>a.session_date<b.session_date?1:-1);
    if(!ss.length) return `<tr class="detail"><td colspan="${cols.length+1}" class="sub">no sessions yet</td></tr>`;
    return `<tr class="detail"><td></td><td colspan="${cols.length}"><table class="mini"><thead><tr>`+
      SCOLS.map(c=>`<th>${c[1]}</th>`).join("")+"</tr></thead><tbody>"+
      ss.map(s=>"<tr>"+SCOLS.map(c=>`<td class="${c[2]==='num'?'num':''}">${c[0]==="hours"?fmtHM(s.hours):(s[c[0]]??'')}</td>`).join("")+"</tr>").join("")+
      "</tbody></table></td></tr>";
  };
  function render(filter){
    let rows=data.slice();
    if(filter){const f=filter.toLowerCase();
      rows=rows.filter(r=>((r.target_id||"")+" "+(r.common_name||"")).toLowerCase().includes(f));}
    rows.sort((a,b)=>{let x=a[sortKey],y=b[sortKey];
      if(x==null&&y==null)return 0; if(x==null)return 1; if(y==null)return -1;
      if(x<y)return -sortDir; if(x>y)return sortDir; return 0;});
    tbl.innerHTML="<thead><tr><th></th>"+cols.map(c=>`<th data-k="${c[0]}">${c[1]}`+
      (sortKey===c[0]?(sortDir>0?" ▲":" ▼"):"")+"</th>").join("")+"</tr></thead><tbody>"+
      rows.map(r=>{
        const isOpen=open.has(r.target_id), nSess=(byTarget[r.target_id]||[]).length;
        const prog=(r.goal_hours>0)?`<div class="bar"><span style="width:${r.goal_pct}%"></span></div> ${r.goal_pct}%`:"";
        const cell=c=> c[0]==="hours" ? fmtH(r.hours)
                     : c[0]==="goal_pct" ? prog
                     : (r[c[0]]??"");
        const main=`<tr class="trow" data-t="${r.target_id}"><td class="tog">${nSess?(isOpen?"▾":"▸"):""}</td>`+
          cols.map(c=>`<td class="${c[2]==='num'?'num':''}">${cell(c)}</td>`).join("")+"</tr>";
        return main+(isOpen?detail(r.target_id):"");
      }).join("")+"</tbody>";
    tbl.querySelectorAll("th[data-k]").forEach(th=>th.onclick=()=>{
      const k=th.dataset.k; if(k===sortKey)sortDir=-sortDir; else{sortKey=k;sortDir=(k==="target_id"||k==="common_name")?1:-1;}
      render(document.getElementById("tfilter").value);});
    tbl.querySelectorAll("tr.trow").forEach(tr=>tr.onclick=()=>{
      const t=tr.dataset.t; open.has(t)?open.delete(t):open.add(t);
      render(document.getElementById("tfilter").value);});
  }
  render("");
  document.getElementById("tfilter").oninput=e=>render(e.target.value);
})();

// ---- generic sortable + filterable table ----
function sortableTable(tblEl, cols, data, opts){
  opts = opts || {};
  let sortKey = opts.sortKey || cols[0][0], sortDir = opts.sortDir || 1;
  function draw(filter){
    let rows = (data||[]).slice();
    if(filter){ const f=filter.toLowerCase();
      rows = rows.filter(r=>cols.some(c=>String(r[c[0]]??"").toLowerCase().includes(f))); }
    rows.sort((a,b)=>{let x=a[sortKey],y=b[sortKey];
      if(x==null&&y==null)return 0; if(x==null)return 1; if(y==null)return -1;
      if(typeof x==="number"&&typeof y==="number"){} else {x=String(x);y=String(y);}
      if(x<y)return -sortDir; if(x>y)return sortDir; return 0;});
    tblEl.innerHTML = "<thead><tr>"+cols.map(c=>`<th data-k="${c[0]}">${c[1]}`+
        (sortKey===c[0]?(sortDir>0?" ▲":" ▼"):"")+"</th>").join("")+"</tr></thead><tbody>"+
      (rows.length? rows.map(r=>"<tr>"+cols.map(c=>{
        const v=r[c[0]]; const cell = (c[3]&&c[3](r)!=null)?c[3](r):(v??"");
        return `<td class="${c[2]==='num'?'num':''}">${cell}</td>`;}).join("")+"</tr>").join("")
        : `<tr><td colspan="${cols.length}" class="sub">nothing here — all clear</td></tr>`)+
      "</tbody>";
    tblEl.querySelectorAll("th[data-k]").forEach(th=>th.onclick=()=>{
      const k=th.dataset.k; if(k===sortKey)sortDir=-sortDir; else{sortKey=k;sortDir=1;}
      draw(opts.getFilter?opts.getFilter():"");});
  }
  return draw;
}

// ---- Work Queue ----
(function(){
  const WL = D.worklists || {};
  // session has open [future_processing] to-dos in its notes.toml — read them
  // before working this row (e.g. a stack that already failed once)
  const seeNotes = n => n ? '<span class="pill todo">see notes</span>' : "";
  const SPEC = {
    cull:      {label:"To cull",      cols:[["target_id","Target"],["session_date","Date"],["scope","Scope"],["sensor","Sensor"],["lights","Lights","num"],["rejected","Rejected","num"],["qc_flags","QC flags","num"],["hours","Hours","num",r=>fmtHM(r.hours)],["todos","Notes",null,r=>seeNotes(r.todos)],["library","Library"]]},
    integrate: {label:"To integrate", cols:[["target_id","Target"],["session_date","Date"],["scope","Scope"],["sensor","Sensor"],["lights","Lights","num"],["hours","Hours","num",r=>fmtHM(r.hours)],["todos","Notes",null,r=>seeNotes(r.todos)],["library","Library"]]},
    edit:      {label:"To edit",      cols:[["target_id","Target"],["image","Image"],["type","Type"],["hours","Hours","num",r=>fmtHM(r.hours)],["method","Method"],["todos","Notes",null,r=>seeNotes(r.todos)],["library","Library"]]},
    restack:   {label:"Restack",      cols:[["target_id","Target"],["folder_name","Integration"],["built_hours","Built","num",r=>fmtHM(r.built_hours)],["available_hours","Available","num",r=>fmtHM(r.available_hours)],["behind","Behind","num",r=>fmtHM(r.behind)],["library","Library"]]},
    capture:   {label:"Capture more", cols:[["target_id","Target"],["hours","Hours","num",r=>fmtH(r.hours)],["goal","Goal","num",r=>fmtH(r.goal)],["gap","Gap","num",r=>fmtH(r.gap)],["priority","Priority","num"],["library","Library"]]},
    masters:   {label:"Build masters",cols:[["class","Class"],["camera","Camera"],["temp","Temperature","num"],["gain","Gain","num"],["exp","Exposure (s)","num",r=>fmtExp(r.exp)],["frames","Frames","num"],["library","Library"]]},
    coverage:  {label:"Calibration to shoot",cols:[["camera","Camera"],["gain","Gain","num"],["exp","Exposure (s)","num",r=>fmtExp(r.exp)],["subs","Light subs","num"],["hours","Hours","num",r=>fmtH(r.hours)],["temp_min","Temperature min","num"],["temp_max","Temperature max","num"],["dark_status","Dark"],["bias_status","Bias"]]},
  };
  const order = ["capture","coverage","masters","cull","integrate","restack","edit"];
  let action = order.find(a=>(WL[a]||[]).length) || "cull";
  const chipsEl = document.getElementById("wqChips");
  const tbl = document.getElementById("wqTable");
  const fbox = document.getElementById("wqfilter");
  let draw = null;
  function build(){
    chipsEl.innerHTML = order.map(a=>`<span class="chip ${a===action?'on':''}" data-a="${a}">`+
      `${SPEC[a].label}<span class="n">${(WL[a]||[]).length}</span></span>`).join("");
    chipsEl.querySelectorAll(".chip").forEach(ch=>ch.onclick=()=>{action=ch.dataset.a; fbox.value=""; build();});
    draw = sortableTable(tbl, SPEC[action].cols, WL[action]||[], {getFilter:()=>fbox.value});
    draw("");
  }
  fbox.oninput = ()=>{ if(draw) draw(fbox.value); };
  build();
})();

// ---- processing to-dos ----
(function(){
  const fbox = document.getElementById("todofilter");
  const draw = sortableTable(document.getElementById("todoTable"),
    [["todo","To-do"],["target","Target"],["rig","Rig"],["date","Date"]],
    D.todos||[], {sortKey:"date",sortDir:-1,getFilter:()=>fbox.value});
  draw("");
  fbox.oninput = ()=>draw(fbox.value);
})();

// ---- publish / print ledgers ----
(function(){
  const link = r => r.url ? `<a href="${r.url}" target="_blank">${r.title||r.url}</a>` : (r.title||"");
  sortableTable(document.getElementById("pubTable"),
    [["target_id","Target"],["image","Image"],["kind","Where"],
     ["title","Link",null,link],["published_at","Date"]],
    D.published||[], {sortKey:"published_at",sortDir:-1})("");
  sortableTable(document.getElementById("printTable"),
    [["target_id","Target"],["image","Image"],["title","Print"],["published_at","Date"]],
    D.printed||[], {sortKey:"published_at",sortDir:-1})("");
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
      return `<tr><td>${r.name}</td><td class="num">${fmtH(r.hours)}</td>`+
        `<td class="num">${r.goal??''}</td>`+
        `<td><div class="bar"><span style="width:${pct}%"></span></div> `+
        `${pct.toFixed(0)}%</td></tr>`;
    }).join("") + "</tbody></table>";
})();

// ---- light <-> calibration coverage (absorbed the Calibration status panel:
// staleness, low-frames, and the CAL_* findings all surface here) ----
(function(){
  const cov = D.calCoverage||[];
  function pill(s){
    if(s==='ok') return '<span class="pill good">ok</span>';
    if(s==='to build') return '<span class="pill todo">to build</span>';
    if(s==='to shoot') return '<span class="pill warn">to shoot</span>';
    if(s && s.startsWith('stale')) return `<span class="pill warn">${s}</span>`;
    if(s==='n/a') return '<span class="pill na">n/a</span>';
    return s||'';
  }
  const statusCell = (s,low) => pill(s)+(low?' <span class="sub">low frames</span>':'');
  const n = (k,f) => cov.filter(f).length;
  function statusSummary(k){
    const shoot = n(k, r=>r[k]==='to shoot'),
          build = n(k, r=>r[k]==='to build'),
          stale = n(k, r=>(r[k]||'').startsWith('stale')),
          ok    = n(k, r=>r[k]==='ok'),
          na    = n(k, r=>r[k]==='n/a');
    if(na===cov.length && cov.length)
      return '<span class="pill na">n/a — not in the calibration recipe</span>';
    return (shoot?`<span class="pill warn">${shoot} to shoot</span> `:"")+
           (build?`<span class="pill todo">${build} to build</span> `:"")+
           (stale?`<span class="pill warn">${stale} stale</span> `:"")+
           (ok?`<span class="pill good">${ok} ok</span>`:"");
  }
  document.getElementById("calCovSummary").innerHTML =
    `${cov.length} light combos &middot; darks: `+statusSummary('dark_status')+
    ` &middot; bias: `+statusSummary('bias_status');
  // registry / structural findings (CAL_* — kept out of Data Health)
  const cf = D.calFindings||[];
  if(cf.length){
    const sevClass = s => s==='error'?'bad':s==='warning'?'warn':'na';
    document.getElementById("calFindings").innerHTML =
      '<div class="sub" style="margin-bottom:8px">'+
      cf.map(r=>`<span class="pill ${sevClass(r.severity)}" style="margin:0 6px 6px 0">`+
        `${r.code}</span> ${r.location} — ${r.message}`).join("<br>")+'</div>';
  }
  const fbox = document.getElementById("covfilter");
  const draw = sortableTable(document.getElementById("covTable"),
    [["camera","Camera"],["gain","Gain","num"],["exp","Exposure (s)","num",r=>fmtExp(r.exp)],
     ["subs","Light subs","num"],["hours","Hours","num",r=>fmtH(r.hours)],
     ["temp_min","Temperature min","num"],["temp_max","Temperature max","num"],
     ["subs_dark_none","Subs missing dark","num"],
     ["dark_status","Dark",null,r=>statusCell(r.dark_status,r.dark_low)],
     ["bias_status","Bias",null,r=>statusCell(r.bias_status,r.bias_low)]],
    cov, {sortKey:"camera",sortDir:1,getFilter:()=>fbox.value});
  draw("");
  fbox.oninput = ()=>draw(fbox.value);
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
    ["session_date","Date"],["target_id","Target ID"],["common_name","Name"],
    ["scope","Scope"],["sensor","Sensor"],["lights","Lights","num"],
    ["rejected","Rejected","num"],["hours","Hours","num"],["flats","Flats"],
    ["bias","Bias"],["stage","Stage"],["method","Method"],["library","Library"],
  ];
  let sortKey="session_date", sortDir=-1;
  const tbl = document.getElementById("sessionsTable");
  function render(filter){
    let rows = D.sessions.slice();
    if(filter){
      const f = filter.toLowerCase();
      rows = rows.filter(r => cols.some(c => String(r[c[0]]??"").toLowerCase().includes(f)));
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
        `<td class="${c[2]==='num'?'num':''}">${c[0]==="hours"?fmtHM(r.hours):(r[c[0]]??'')}</td>`).join("")+
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
