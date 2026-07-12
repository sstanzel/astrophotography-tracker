#!/usr/bin/env python3
"""
scrub.py — deep Data Health scrub for the occasional "spring cleaning" pass.

Where ingest.py's validate() checks *structure* every run (naming, dates,
registry membership, manifests), this scrub audits *consistency* — the
anomalies that hide inside otherwise well-formed sessions: mixed capture
settings, double-counted frames, cooler failures, cross-library duplicates.
Run it after a big filing pass, before a reorganization, or whenever the
library deserves a full physical. Every check is cataloged in CHECKS.md.

Read-only: reads tracker.db (plus one folder-name pass over the mounted
library roots recorded in the DB). Never writes the database, never touches
the libraries. Run ingest.py first so the DB reflects the current disk.

Usage:
    python3 scrub.py              # summary + every finding
    python3 scrub.py --summary    # check-by-check counts only
    python3 scrub.py --db PATH    # a tracker.db somewhere else
    python3 scrub.py --no-fs      # skip the filesystem pass (DB checks only)

Exit code: 1 if any error-severity finding, else 0.
"""

import argparse
import os
import re
import sqlite3
import sys

# A cooled camera holds a sub-zero set point all night; spread beyond this is
# a cooler failure or a mid-night set-point change, not normal regulation.
COOLED_TEMP_SPREAD_C = 5.0
# Camera rotation matters because flats stop matching; below this the spread
# is plate-solve jitter. Compared on a 180-degree circle (a meridian flip
# reads as +/-180 and is the same framing).
ROTATION_SPREAD_DEG = 2.0
# A frame under this fraction of its session's modal file size is truncated
# (interrupted transfer) rather than a legitimate variation.
UNDERSIZE_FRACTION = 0.9
# Modal sizes under 1 MB mean thumbnails/JPEG sidecars — not worth judging.
UNDERSIZE_MIN_MODAL_BYTES = 1_000_000
# Reject-rate worth a second look before planning a reshoot.
HIGH_REJECT_FRACTION = 0.5
HIGH_REJECT_MIN_LIGHTS = 10

# 4-token session grammar: Target_id Scope Sensor YYYY-MM-DD (matches ingest's
# folder naming; used to keep utility folders out of the cross-library check).
SESSION_NAME_RE = re.compile(r"^\S+ \S+ \S+ \d{4}-\d{2}-\d{2}$")

Finding = tuple[str, str, str, str]  # (severity, code, ref, message)


def _rows(cur: sqlite3.Cursor, sql: str, params: tuple = ()) -> list:
    return cur.execute(sql, params).fetchall()


# --------------------------------------------------------------------------
# Frame-consistency checks (within a session)
# --------------------------------------------------------------------------
def check_mixed_gain(cur: sqlite3.Cursor) -> list[Finding]:
    """Kept lights captured at more than one gain in a single session."""
    return [
        ("warning", "MIXED_GAIN", fp, f"Kept lights use {n} gains ({vals}).")
        for fp, n, vals in _rows(
            cur,
            """
            SELECT s.folder_path, COUNT(DISTINCT f.gain),
                   GROUP_CONCAT(DISTINCT f.gain)
            FROM frames f JOIN sessions s USING(session_id)
            WHERE f.frame_type='light' AND f.is_rejected=0
              AND NOT s.is_other_capture
            GROUP BY f.session_id HAVING COUNT(DISTINCT f.gain) > 1""",
        )
    ]


def check_mixed_exposure(cur: sqlite3.Cursor) -> list[Finding]:
    """Kept lights at more than one exposure length in a single session."""
    return [
        ("info", "MIXED_EXPOSURE", fp, f"Kept lights use {n} exposures ({fmt_exps(vals)} s).")
        for fp, n, vals in _rows(
            cur,
            """
            SELECT s.folder_path, COUNT(DISTINCT f.exp_s),
                   GROUP_CONCAT(DISTINCT f.exp_s)
            FROM frames f JOIN sessions s USING(session_id)
            WHERE f.frame_type='light' AND f.is_rejected=0 AND f.exp_unit='s'
              AND NOT s.is_other_capture
            GROUP BY f.session_id HAVING COUNT(DISTINCT f.exp_s) > 1""",
        )
    ]


def check_mixed_binning(cur: sqlite3.Cursor) -> list[Finding]:
    """Kept lights at more than one binning in a single session."""
    return [
        ("warning", "MIXED_BINNING", fp, f"Kept lights use binnings {vals}.")
        for fp, vals in _rows(
            cur,
            """
            SELECT s.folder_path, GROUP_CONCAT(DISTINCT f.binning)
            FROM frames f JOIN sessions s USING(session_id)
            WHERE f.frame_type='light' AND f.is_rejected=0
              AND NOT s.is_other_capture
            GROUP BY f.session_id HAVING COUNT(DISTINCT f.binning) > 1""",
        )
    ]


def check_mixed_camera(cur: sqlite3.Cursor) -> list[Finding]:
    """Light frames whose filename camera token differs within one session.

    A second camera token means frames copied in from another rig's session —
    they don't belong in this folder's totals.
    """
    return [
        ("error", "MIXED_CAMERA", fp, f"Light frames carry {n} camera tokens ({vals}).")
        for fp, n, vals in _rows(
            cur,
            """
            SELECT s.folder_path, COUNT(DISTINCT f.camera_short),
                   GROUP_CONCAT(DISTINCT f.camera_short)
            FROM frames f JOIN sessions s USING(session_id)
            WHERE f.frame_type='light'
            GROUP BY f.session_id HAVING COUNT(DISTINCT f.camera_short) > 1""",
        )
    ]


def check_temp_runaway(cur: sqlite3.Cursor) -> list[Finding]:
    """Cooled-camera sessions whose sensor temperature drifted off set point.

    Only sessions that reach a sub-zero temperature count as cooled — an
    uncooled DSLR legitimately tracks ambient across tens of degrees.
    """
    return [
        (
            "warning",
            "TEMP_RUNAWAY",
            fp,
            f"Cooled sensor spans {tmin:g} to {tmax:g} °C across kept lights "
            f"(spread {tmax - tmin:g} °C) — cooler failure or a mid-night set-point change.",
        )
        for fp, tmin, tmax in _rows(
            cur,
            """
            SELECT s.folder_path, MIN(f.temp_c), MAX(f.temp_c)
            FROM frames f JOIN sessions s USING(session_id)
            WHERE f.frame_type='light' AND f.is_rejected=0 AND f.temp_c IS NOT NULL
            GROUP BY f.session_id
            HAVING MIN(f.temp_c) <= 0 AND MAX(f.temp_c) - MIN(f.temp_c) > ?""",
            (COOLED_TEMP_SPREAD_C,),
        )
    ]


def check_rotation_drift(cur: sqlite3.Cursor) -> list[Finding]:
    """Kept lights whose camera rotation drifts enough to invalidate flats.

    Angles compare on a 180-degree circle: 0 == 360, and a meridian flip's
    +/-180 is the same framing, so neither trips the check.
    """
    by_session: dict[str, list[float]] = {}
    for fp, deg in _rows(
        cur,
        """
        SELECT s.folder_path, f.rotation_deg
        FROM frames f JOIN sessions s USING(session_id)
        WHERE f.frame_type='light' AND f.is_rejected=0
          AND f.rotation_deg IS NOT NULL""",
    ):
        by_session.setdefault(fp, []).append(deg % 180.0)

    findings = []
    for fp, angles in sorted(by_session.items()):
        spread = _circular_spread(angles, period=180.0)
        if spread > ROTATION_SPREAD_DEG:
            findings.append(
                (
                    "info",
                    "ROTATION_DRIFT",
                    fp,
                    f"Camera rotation varies {spread:.1f}° across kept lights — "
                    f"this session's flats may not match every sub.",
                )
            )
    return findings


def _circular_spread(angles: list[float], period: float) -> float:
    """Smallest arc (in degrees) containing every angle on a circle.

    Args:
        angles: Angle values already reduced modulo `period`.
        period: Circle size in degrees (180 for rotation-with-flip equivalence).

    Returns:
        The tightest angular spread — 0 when all angles coincide.
    """
    if len(angles) < 2:
        return 0.0
    pts = sorted(angles)
    # The spread is the full circle minus the largest gap between neighbors.
    gaps = [b - a for a, b in zip(pts, pts[1:])]
    gaps.append(pts[0] + period - pts[-1])
    return period - max(gaps)


# --------------------------------------------------------------------------
# Duplicate / phantom data checks
# --------------------------------------------------------------------------
def check_duplicate_frames(cur: sqlite3.Cursor) -> list[Finding]:
    """The same light (timestamp + counter) counted more than once.

    A copy left in a working folder or filed twice double-counts integration
    hours until one copy is removed.
    """
    return [
        (
            "error",
            "DUPLICATE_FRAME",
            fp,
            f"{n} light frame(s) appear more than once (same capture timestamp "
            f"and counter) — integration hours are inflated until the copies go.",
        )
        for fp, n in _rows(
            cur,
            """
            SELECT folder_path, COUNT(*) FROM (
                SELECT s.folder_path AS folder_path
                FROM frames f JOIN sessions s USING(session_id)
                WHERE f.frame_type='light'
                GROUP BY f.session_id, f.captured_at_utc, f.sequence_index
                HAVING COUNT(*) > 1)
            GROUP BY folder_path""",
        )
    ]


def check_scratch_frames(cur: sqlite3.Cursor) -> list[Finding]:
    """Frames inside PI Process/ or PI Magic/ scratch folders counted as data.

    Those folders hold recreatable intermediates (including Discarded/ copies
    of real lights); anything ingested from them double-counts the original.
    """
    return [
        (
            "error",
            "SCRATCH_FRAME",
            fp,
            f"{n} frame(s) counted from PI Process/ or PI Magic/ scratch folders.",
        )
        for fp, n in _rows(
            cur,
            """
            SELECT s.folder_path, COUNT(*)
            FROM frames f JOIN sessions s USING(session_id)
            WHERE f.file_path LIKE '%/PI Magic/%' OR f.file_path LIKE '%/PI Process/%'
            GROUP BY f.session_id""",
        )
    ]


def check_undersized_frames(cur: sqlite3.Cursor) -> list[Finding]:
    """Frames much smaller than their session's modal size — truncated files."""
    return [
        (
            "error",
            "UNDERSIZED_FRAME",
            fp,
            f"{path} is {sz:,} bytes vs the session's usual {modal:,} — "
            f"likely a truncated transfer.",
        )
        for fp, path, sz, modal in _rows(
            cur,
            """
            WITH modal AS (
                SELECT session_id, frame_type, file_size_bytes AS sz,
                       ROW_NUMBER() OVER (PARTITION BY session_id, frame_type
                                          ORDER BY COUNT(*) DESC) AS rn
                FROM frames WHERE file_size_bytes IS NOT NULL
                GROUP BY session_id, frame_type, file_size_bytes)
            SELECT s.folder_path, f.file_path, f.file_size_bytes, m.sz
            FROM frames f
            JOIN modal m ON m.session_id=f.session_id
                        AND m.frame_type=f.frame_type AND m.rn=1
            JOIN sessions s ON s.session_id=f.session_id
            WHERE f.file_size_bytes IS NOT NULL AND f.file_size_bytes > 0
              AND m.sz >= ? AND f.file_size_bytes < ? * m.sz""",
            (UNDERSIZE_MIN_MODAL_BYTES, UNDERSIZE_FRACTION),
        )
    ]


def check_zero_byte_frames(cur: sqlite3.Cursor) -> list[Finding]:
    """Zero-byte frame files — nothing was written."""
    return [
        ("error", "ZERO_BYTE_FRAME", fp, f"{path} is 0 bytes.")
        for fp, path in _rows(
            cur,
            """
            SELECT s.folder_path, f.file_path
            FROM frames f JOIN sessions s USING(session_id)
            WHERE f.file_size_bytes = 0""",
        )
    ]


# --------------------------------------------------------------------------
# Session-outcome checks
# --------------------------------------------------------------------------
def check_all_rejected(cur: sqlite3.Cursor) -> list[Finding]:
    """Sessions where every light was rejected — a total-loss night."""
    return [
        (
            "warning",
            "ALL_REJECTED",
            fp,
            f"All {rej} lights are in Rejected/ — total-loss night; "
            f"reshoot or retire the session.",
        )
        for fp, rej in _rows(
            cur,
            """
            SELECT folder_path, lights_rejected FROM sessions
            WHERE NOT is_other_capture AND lights_kept = 0 AND lights_rejected > 0""",
        )
    ]


def check_high_reject_rate(cur: sqlite3.Cursor) -> list[Finding]:
    """Sessions that lost at least half their lights to culling."""
    return [
        (
            "info",
            "HIGH_REJECT_RATE",
            fp,
            f"{rej} of {kept + rej} lights rejected ({100 * rej // (kept + rej)}%).",
        )
        for fp, kept, rej in _rows(
            cur,
            """
            SELECT folder_path, lights_kept, lights_rejected FROM sessions
            WHERE NOT is_other_capture AND lights_kept > 0
              AND lights_kept + lights_rejected >= ?
              AND lights_rejected >= ? * (lights_kept + lights_rejected)""",
            (HIGH_REJECT_MIN_LIGHTS, HIGH_REJECT_FRACTION),
        )
    ]


def check_ms_deepsky_lights(cur: sqlite3.Cursor) -> list[Finding]:
    """Millisecond-exposure lights in a deep-sky session.

    ms subs never count toward integration; in a deep-sky folder they are
    usually misfiled planetary/lunar frames.
    """
    return [
        (
            "info",
            "MS_DEEPSKY_LIGHTS",
            fp,
            f"{n} millisecond-exposure light(s) in a deep-sky session — "
            f"misfiled planetary frames?",
        )
        for fp, n in _rows(
            cur,
            """
            SELECT s.folder_path, COUNT(*)
            FROM frames f JOIN sessions s USING(session_id)
            WHERE f.frame_type='light' AND f.exp_unit='ms' AND NOT s.is_other_capture
            GROUP BY f.session_id""",
        )
    ]


def check_unknown_filter(cur: sqlite3.Cursor) -> list[Finding]:
    """Filter tokens in frame names that aren't in _organization/filter_values."""
    return [
        (
            "info",
            "UNKNOWN_FILTER",
            flt,
            f"Filter token '{flt}' ({n} frame(s)) is not in _organization/filter_values.",
        )
        for flt, n in _rows(
            cur,
            """
            SELECT f.filter, COUNT(*)
            FROM frames f LEFT JOIN filters ft ON ft.filter = f.filter
            WHERE f.filter IS NOT NULL AND ft.filter IS NULL
            GROUP BY f.filter""",
        )
    ]


# --------------------------------------------------------------------------
# Calibration + cross-library checks
# --------------------------------------------------------------------------
def check_nested_cal_sets(cur: sqlite3.Cursor) -> list[Finding]:
    """Calibration set folders nested inside another set — phantom sets.

    Happens when WBPP output (master/, logs/) is left as a subfolder instead
    of the master being filed next to the raws.
    """
    return [
        (
            "warning",
            "CAL_NESTED_SET",
            path,
            f"{cls} set is nested inside another set folder — file its contents "
            f"next to the raws and remove the subfolder.",
        )
        for cls, path in _rows(
            cur,
            """
            SELECT a.class, a.folder_path FROM calibration_masters a
            WHERE EXISTS (SELECT 1 FROM calibration_masters b
                          WHERE a.folder_path LIKE b.folder_path || '/%')""",
        )
    ]


def check_flats_host_empty(cur: sqlite3.Cursor) -> list[Finding]:
    """Sibling-flats pointers whose host session no longer holds flats."""
    return [
        (
            "warning",
            "FLATS_HOST_EMPTY",
            fp,
            f"Flats point at sibling '{ref}', but that session holds no flats.",
        )
        for fp, ref in _rows(
            cur,
            """
            SELECT s.folder_path, s.flats_ref
            FROM sessions s JOIN sessions h ON h.folder_path LIKE '%/' || s.flats_ref
            WHERE s.flats_source = 'with sibling' AND h.flats_count = 0""",
        )
    ]


def check_cross_library_duplicates(cur: sqlite3.Cursor) -> list[Finding]:
    """The same session folder present in more than one capture library.

    The sessions table's natural key keeps exactly one row per session, so a
    Stream+Peak duplicate is invisible in the DB — only the disk shows it.
    Walks folder names two levels deep in each mounted library root.
    """
    seen: dict[str, list[str]] = {}
    for lib, root in _rows(cur, "SELECT library_id, root_path FROM libraries"):
        if not os.path.isdir(root):
            continue  # unmounted volume — checked another day
        for target in os.listdir(root):
            tdir = os.path.join(root, target)
            if target.startswith(("_", ".")) or not os.path.isdir(tdir):
                continue
            for name in os.listdir(tdir):
                if SESSION_NAME_RE.match(name) and os.path.isdir(os.path.join(tdir, name)):
                    seen.setdefault(name, []).append(lib)
    return [
        (
            "error",
            "CROSS_LIBRARY_DUPLICATE",
            name,
            f"Session folder exists in {' and '.join(sorted(libs))} — the tracker "
            f"counts only one; delete or archive the other copy.",
        )
        for name, libs in sorted(seen.items())
        if len(libs) > 1
    ]


# --------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------
DB_CHECKS = [
    check_duplicate_frames,
    check_scratch_frames,
    check_mixed_camera,
    check_zero_byte_frames,
    check_undersized_frames,
    check_mixed_gain,
    check_mixed_binning,
    check_temp_runaway,
    check_rotation_drift,
    check_all_rejected,
    check_nested_cal_sets,
    check_flats_host_empty,
    check_mixed_exposure,
    check_high_reject_rate,
    check_ms_deepsky_lights,
    check_unknown_filter,
]

SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}


def fmt_exps(vals: str) -> str:
    """Render a GROUP_CONCAT of exposure seconds without trailing .0 noise."""
    return ", ".join(v[:-2] if v.endswith(".0") else v for v in vals.split(","))


def run_checks(con: sqlite3.Connection, include_fs: bool) -> list[Finding]:
    """Run every scrub check and return the combined findings.

    Args:
        con: Open connection to tracker.db.
        include_fs: Also run the filesystem pass (cross-library duplicates).

    Returns:
        Findings sorted errors -> warnings -> info, then by check and ref.
    """
    cur = con.cursor()
    findings: list[Finding] = []
    for check in DB_CHECKS:
        findings.extend(check(cur))
    if include_fs:
        findings.extend(check_cross_library_duplicates(cur))
    findings.sort(key=lambda x: (SEVERITY_ORDER[x[0]], x[1], x[2]))
    return findings


def print_report(findings: list[Finding], summary_only: bool) -> None:
    """Print the scrub report: summary counts, then per-check detail."""
    by = {"error": 0, "warning": 0, "info": 0}
    per_check: dict[str, int] = {}
    for sev, code, _, _ in findings:
        by[sev] += 1
        per_check[code] = per_check.get(code, 0) + 1

    print("Data Health scrub")
    print(f"    {by['error']} errors, {by['warning']} warnings, {by['info']} info\n")
    if not findings:
        print("Clean bill of health — no findings.")
        return

    seen: set[str] = set()
    for sev, code, _, _ in findings:  # first appearance preserves severity order
        if code in seen:
            continue
        seen.add(code)
        print(f"  {per_check[code]:4}  {code} ({sev})")

    if summary_only:
        print("\nRun without --summary for the full list.")
        return
    print()
    current = None
    for sev, code, ref, msg in findings:
        if code != current:
            current = code
            print(f"{code} ({sev})")
        print(f"    {ref}")
        print(f"        {msg}")


def main() -> None:
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(description="Deep Data Health scrub over an existing tracker.db.")
    ap.add_argument("--db", default=os.path.join(here, "tracker.db"))
    ap.add_argument("--summary", action="store_true", help="check-by-check counts only")
    ap.add_argument(
        "--no-fs", action="store_true", help="skip the filesystem pass over the library roots"
    )
    args = ap.parse_args()

    if not os.path.exists(args.db):
        sys.exit(f"Database not found: {args.db}\nRun ingest.py first to build it.")
    con = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    findings = run_checks(con, include_fs=not args.no_fs)
    con.close()
    print_report(findings, summary_only=args.summary)
    sys.exit(1 if any(sev == "error" for sev, _, _, _ in findings) else 0)


if __name__ == "__main__":
    main()
