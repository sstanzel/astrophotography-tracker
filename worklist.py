#!/usr/bin/env python3
"""
worklist.py - print the tracker's Work Queue in the terminal.

The same "what's next" lists the dashboard shows, for when you're sitting down
to work: what to cull, integrate, edit, restack, capture more of, or turn into
calibration masters. Read-only against tracker.db.

    python3 worklist.py                # summary counts for every action
    python3 worklist.py cull           # the sessions awaiting a blink/cull pass
    python3 worklist.py masters        # calibration sets that need a master
    python3 worklist.py all            # every list in full
"""
import argparse
import os
import sqlite3
import sys

ACTIONS = ("capture", "coverage", "masters", "cull", "integrate",
           "restack", "edit")

QUERIES = {
    "cull": ("Sessions to cull (captured, not yet reviewed)", """
        SELECT s.target_id AS target, s.session_date AS date, s.scope, s.sensor,
               s.lights_kept AS lights, s.lights_rejected AS rejected,
               s.integration_s/3600.0 AS hours
        FROM sessions s
        JOIN v_session_pipeline vp ON vp.session_id=s.session_id
        WHERE NOT s.is_other_capture AND vp.furthest_stage='1 Captured'
        ORDER BY s.session_date DESC"""),
    "integrate": ("Sessions to integrate (culled, not yet stacked)", """
        SELECT s.target_id AS target, s.session_date AS date, s.scope, s.sensor,
               s.lights_kept AS lights, s.integration_s/3600.0 AS hours
        FROM sessions s
        JOIN v_session_pipeline vp ON vp.session_id=s.session_id
        WHERE NOT s.is_other_capture AND vp.furthest_stage='2 Culled'
        ORDER BY s.session_date DESC"""),
    "edit": ("Finished images to edit (integrated, not edited)", """
        SELECT s.target_id AS target, s.session_date||' '||s.scope||' '||s.sensor AS image,
               'session' AS type, s.integration_s/3600.0 AS hours
        FROM sessions s
        WHERE NOT s.is_other_capture AND s.stage_integrate=2 AND s.stage_edit<2
        UNION ALL
        SELECT i.target_id, i.folder_name, 'integration',
               (SELECT SUM(ss.integration_s)/3600.0 FROM integration_members im
                JOIN sessions ss ON ss.session_id=im.session_id
                WHERE im.integration_id=i.integration_id)
        FROM integrations i WHERE i.stage_edit<2
        ORDER BY hours DESC"""),
    "restack": ("Integrations to restack (new data since last stack)", """
        SELECT target_id AS target, folder_name AS integration,
               built_hours AS built, available_hours AS available,
               available_hours-built_hours AS behind
        FROM v_integration_overview WHERE is_stale=1 ORDER BY behind DESC"""),
    "capture": ("Targets to capture more of (under goal, or planned)", """
        SELECT t.target_id AS target,
               COALESCE(SUM(s.integration_s),0)/3600.0 AS hours,
               g.goal_hours AS goal,
               g.goal_hours-COALESCE(SUM(s.integration_s),0)/3600.0 AS gap,
               g.priority
        FROM targets t
        LEFT JOIN sessions s ON s.target_id=t.target_id AND NOT s.is_other_capture
        LEFT JOIN target_goals g ON g.target_id=t.target_id
        WHERE NOT t.is_other_capture
        GROUP BY t.target_id
        HAVING (g.goal_hours IS NOT NULL AND hours < g.goal_hours) OR COUNT(s.session_id)=0
        ORDER BY g.priority, gap DESC"""),
    "masters": ("Calibration sets to turn into masters (bias/dark, no master)", """
        SELECT class, camera, temperature_c AS temperature, gain,
               exp_s AS exposure, frame_count AS frames
        FROM calibration_masters
        WHERE class IN ('bias','dark') AND is_generated_master=0
        ORDER BY class, camera, temperature_c, gain, exp_s"""),
    "coverage": ("Light combos missing dark/bias coverage (shoot or build)", """
        SELECT camera, gain, exp_s AS exposure, light_subs AS subs, hours,
               subs_dark_none AS "missing dark", dark_status AS dark,
               bias_status AS bias
        FROM v_light_calibration_coverage
        WHERE dark_status != 'ok' OR bias_status != 'ok'
        ORDER BY camera, gain, exp_s"""),
}


# --- display formatting (see STYLE.md) --------------------------------------
def fmt_hm(hours):
    """Hours as 'Xh Ym' (per-item breakdowns); whole hours show as 'Xh'."""
    if hours is None:
        return ""
    total_min = round(hours * 60)
    h, m = divmod(total_min, 60)
    if not h:
        return f"{m}m"
    return f"{h}h {m:02d}m" if m else f"{h}h"


def fmt_h(hours):
    """Hours rounded to the nearest whole hour (summaries and totals)."""
    return "" if hours is None else str(round(hours))


def fmt_exp(exp):
    """Exposure seconds without a trailing .0; decimals only when meaningful."""
    if exp is None:
        return ""
    return str(int(exp)) if float(exp) == int(exp) else str(exp)


# per-action column formatters, keyed by the SQL column alias
FORMATTERS = {
    "cull":      {"hours": fmt_hm},
    "integrate": {"hours": fmt_hm},
    "edit":      {"hours": fmt_hm},
    "restack":   {"built": fmt_hm, "available": fmt_hm, "behind": fmt_hm},
    "capture":   {"hours": fmt_h, "goal": fmt_h, "gap": fmt_h},
    "masters":   {"exposure": fmt_exp},
    "coverage":  {"exposure": fmt_exp, "hours": fmt_h},
}


def fetch(cur, key):
    """Return (title, column names, display-formatted rows) for one worklist key."""
    title, sql = QUERIES[key]
    cur.execute(sql)
    headers = [d[0] for d in cur.description]
    fmts = FORMATTERS.get(key, {})
    data = [[fmts[h](v) if h in fmts else v for h, v in zip(headers, row)]
            for row in cur.fetchall()]
    return title, headers, data


def print_table(headers, data):
    """Print rows as an aligned text table."""
    if not data:
        print("  (nothing — all clear)")
        return
    widths = [max(len(str(h)), max(len(str(r[i]) if r[i] is not None else "")
                                    for r in data)) for i, h in enumerate(headers)]
    line = "  ".join(h.ljust(widths[i]) for i, h in enumerate(headers))
    print("  " + line)
    print("  " + "  ".join("-" * w for w in widths))
    for r in data:
        print("  " + "  ".join((str(v) if v is not None else "").ljust(widths[i])
                               for i, v in enumerate(r)))


def main():
    """Print a summary, one worklist, or all of them."""
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(description="Print the tracker Work Queue.")
    ap.add_argument("action", nargs="?", default="summary",
                    choices=("summary", "all") + ACTIONS)
    ap.add_argument("--db", default=os.path.join(here, "tracker.db"))
    args = ap.parse_args()
    if not os.path.exists(args.db):
        sys.exit(f"Database not found: {args.db}\nRun ingest.py first.")

    con = sqlite3.connect(args.db)
    cur = con.cursor()

    def print_summary():
        print("Work Queue")
        for key in ACTIONS:
            title, _h, data = fetch(cur, key)
            print(f"  {len(data):4}  {title}")

    if args.action == "summary":
        print_summary()
        print("\nRun 'worklist.py <action>' for a list, or 'all' for everything.")
    else:
        if args.action == "all":
            print_summary()
        keys = ACTIONS if args.action == "all" else (args.action,)
        for key in keys:
            title, headers, data = fetch(cur, key)
            print(f"\n=== {title} — {len(data)} ===")
            print_table(headers, data)
    con.close()


if __name__ == "__main__":
    main()
