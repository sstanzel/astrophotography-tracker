"""metrics.py — the single definition of the tracker's headline metrics.

Both display faces consume this list — the dashboard's KPI cards
(export_html.py) and the spreadsheet's Summary sheet (export_xlsx.py) — so
the two can never disagree on a label, a definition, or a number. The CLI
faces (worklist.py etc.) answer their own narrower questions; anything that
appears on BOTH a dashboard card and the Summary sheet must come from here.

Labels follow the working vocabulary of the scripts and docs (USAGE.md,
CHECKS.md, STYLE.md): "Data health findings" (the Data Health panel's noun),
"Targets imaged" (targets with kept lights — planned registry entries are a
different number, deliberately not a headline).
"""

import sqlite3


def _scalar(con: sqlite3.Connection, sql: str) -> int:
    v = con.execute(sql).fetchone()[0]
    return int(v) if v is not None else 0


def _table_exists(con: sqlite3.Connection, name: str) -> bool:
    return (
        con.execute(
            "SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name=?", (name,)
        ).fetchone()
        is not None
    )


def summary_metrics(con: sqlite3.Connection) -> list[dict]:
    """The headline metrics, in display order.

    Args:
        con: open tracker.db connection.

    Returns:
        List of dicts: label (exact display text for BOTH faces), value,
        warn (True = act-now metric, highlighted only when nonzero), and
        fmt (Excel number format for the spreadsheet face).
    """
    has_integrations = _table_exists(con, "integrations")
    has_validation = _table_exists(con, "validation_findings")

    def metric(label, value, warn=False, fmt="#,##0"):
        return {"label": label, "value": value, "warn": warn, "fmt": fmt}

    return [
        metric(
            "Deep-sky hours",
            _scalar(
                con,
                "SELECT ROUND(SUM(integration_s)/3600.0) FROM sessions WHERE NOT is_other_capture",
            ),
            fmt="0",
        ),
        metric(
            "Deep-sky sessions",
            _scalar(con, "SELECT COUNT(*) FROM sessions WHERE NOT is_other_capture"),
        ),
        metric(
            "Targets imaged",
            _scalar(
                con,
                "SELECT COUNT(DISTINCT target_id) FROM sessions"
                " WHERE lights_kept>0 AND NOT is_other_capture",
            ),
        ),
        metric(
            "Kept light frames",
            _scalar(
                con, "SELECT COUNT(*) FROM frames WHERE frame_type='light' AND NOT is_rejected"
            ),
        ),
        metric(
            "Other-capture sessions",
            _scalar(con, "SELECT COUNT(*) FROM sessions WHERE is_other_capture"),
        ),
        metric("Calibration sets", _scalar(con, "SELECT COUNT(*) FROM calibration_masters")),
        metric(
            "Calibration needs attention",
            _scalar(
                con,
                """SELECT COUNT(*) FROM v_calibration_needs
                   WHERE status IN ('no master','stale (new raw)','stale (age)')
                     AND (class='dark' OR (class='bias' AND (SELECT require_bias
                          FROM coverage_settings WHERE id=1)=1))""",
            ),
            warn=True,
        ),
        metric(
            "Multi-session integrations",
            _scalar(con, "SELECT COUNT(*) FROM integrations") if has_integrations else 0,
        ),
        metric(
            "Targets not published",
            _scalar(con, "SELECT COUNT(*) FROM v_targets_unpublished") if has_integrations else 0,
            warn=True,
        ),
        metric(
            "Data health findings",
            (
                _scalar(
                    con,
                    "SELECT COUNT(*) FROM validation_findings"
                    " WHERE severity IN ('error','warning')",
                )
                if has_validation
                else 0
            ),
            warn=True,
        ),
    ]
