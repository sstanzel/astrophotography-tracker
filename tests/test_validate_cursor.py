"""Regression tests: validate() checks must fire for EVERY row, not just the first.

The 2026-07-24 bug: loops iterating `for ... in cur.execute(...)` ran nested
lookups on the same cursor, which resets it and ends the loop after one row —
so DATE_MISMATCH / MULTI_NIGHT_SPAN / FUTURE_DATE, UNKNOWN_SCOPE /
UNKNOWN_SENSOR, and CAL_UNKNOWN_CAMERA were only ever evaluated for the first
session (or calibration set). These tests put the violation on the SECOND and
later rows, which the shared-cursor version silently skipped.

Layout-agnostic on purpose (identical file on main and intake): the module is
`scan` in internal/ after the 2026-07 reorg, `ingest` at the root before it.
"""

import os
import sqlite3
import sys

_TRACKER_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_TRACKER_ROOT, os.path.join(_TRACKER_ROOT, "internal")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    import scan as scan_mod  # post-reorg layout (internal/scan.py)
except ImportError:
    import ingest as scan_mod  # pre-reorg layout (ingest.py at the root)

_SCHEMA_CANDIDATES = (
    os.path.join(_TRACKER_ROOT, "internal", "schema.sql"),
    os.path.join(_TRACKER_ROOT, "schema.sql"),
)
SCHEMA = next(p for p in _SCHEMA_CANDIDATES if os.path.exists(p))

CLEAN_SESSION = "M 5 globular cluster/M_5 RASA8 ASI2600MCAir 2026-03-01"
MISFILED_SESSION = "NGC 1499 California Nebula/NGC_1499 RASA8 ASI2600MCAir 2026-03-08"


def make_db(tmp_path):
    """A registry-clean library with one well-formed session dated 2026-03-01."""
    con = sqlite3.connect(str(tmp_path / "tracker.db"))
    con.executescript(open(SCHEMA, encoding="utf-8").read())
    con.execute(
        "INSERT INTO libraries(library_id, root_path, label, role)"
        " VALUES('lib1','/x','L1','working')"
    )
    con.execute("INSERT INTO scopes(scope, from_registry) VALUES('RASA8', 1)")
    con.execute("INSERT INTO sensors(sensor, from_registry) VALUES('ASI2600MCAir', 1)")
    con.execute(
        "INSERT INTO targets(target_id, catalog, number, common_name, folder_name)"
        " VALUES('M_5','M','5','globular cluster','M 5 globular cluster')"
    )
    con.execute(
        "INSERT INTO targets(target_id, catalog, number, common_name, folder_name)"
        " VALUES('NGC_1499','NGC','1499','California Nebula','NGC 1499 California Nebula')"
    )
    add_session(con, "M_5", CLEAN_SESSION, "2026-03-01", ["2026-03-01"])
    return con


def add_session(con, target_id, folder_path, session_date, frame_dates,
                scope="RASA8", sensor="ASI2600MCAir"):
    """Insert a session with one 300s kept light per date in frame_dates."""
    cur = con.execute(
        "INSERT INTO sessions(target_id, scope, sensor, session_date, library_id,"
        " folder_path, lights_kept, integration_s)"
        " VALUES(?,?,?,?, 'lib1', ?, ?, ?)",
        (target_id, scope, sensor, session_date, folder_path,
         len(frame_dates), 300 * len(frame_dates)),
    )
    sid = cur.lastrowid
    for i, d in enumerate(frame_dates):
        con.execute(
            "INSERT INTO frames(session_id, frame_type, exp_value, exp_unit, exp_s,"
            " binning, camera_short, captured_at_utc, grammar, file_path)"
            " VALUES(?, 'light', 300.0, 's', 300.0, '1', '2600MC', ?, 'asiair_sci', ?)",
            (sid, f"{d} 03:00:00", f"{folder_path}/Light/frame_{i:04d}.fit"),
        )
    con.commit()
    return sid


def run_validate(con):
    logs = []
    scan_mod.validate(con, {}, {}, logs.append)
    return {
        (code, ref)
        for code, ref in con.execute("SELECT code, ref_path FROM validation_findings")
    }


def test_multi_night_span_flagged_on_second_session(tmp_path):
    con = make_db(tmp_path)
    add_session(
        con, "NGC_1499", MISFILED_SESSION, "2026-03-08",
        ["2026-02-26", "2026-02-27", "2026-03-04", "2026-03-08", "2026-03-09"],
    )

    findings = run_validate(con)

    assert ("MULTI_NIGHT_SPAN", MISFILED_SESSION) in findings


def test_date_mismatch_flagged_on_second_session(tmp_path):
    con = make_db(tmp_path)
    # Folder says 03-08 but every frame was captured a week earlier.
    add_session(con, "NGC_1499", MISFILED_SESSION, "2026-03-08", ["2026-03-01"])

    findings = run_validate(con)

    assert ("DATE_MISMATCH", MISFILED_SESSION) in findings


def test_unknown_scope_and_sensor_flagged_on_second_session(tmp_path):
    con = make_db(tmp_path)
    con.execute("INSERT INTO scopes(scope, from_registry) VALUES('Mystery8', 0)")
    con.execute("INSERT INTO sensors(sensor, from_registry) VALUES('MysteryCam', 0)")
    off_registry = "NGC 1499 California Nebula/NGC_1499 Mystery8 MysteryCam 2026-03-02"
    add_session(con, "NGC_1499", off_registry, "2026-03-02", ["2026-03-02"],
                scope="Mystery8", sensor="MysteryCam")

    findings = run_validate(con)

    assert ("UNKNOWN_SCOPE", off_registry) in findings
    assert ("UNKNOWN_SENSOR", off_registry) in findings


def test_cal_unknown_camera_flagged_beyond_first_set(tmp_path):
    con = make_db(tmp_path)
    for cam in ("BogusCamA", "BogusCamB"):
        con.execute("INSERT INTO sensors(sensor, from_registry) VALUES(?, 0)", (cam,))
        con.execute(
            "INSERT INTO calibration_masters(library_id, class, folder_path, camera)"
            " VALUES('lib1', 'dark', ?, ?)",
            (f"_Calibration Library/Dark/{cam}", cam),
        )
    con.commit()

    findings = run_validate(con)

    flagged = {ref for code, ref in findings if code == "CAL_UNKNOWN_CAMERA"}
    assert flagged == {
        "_Calibration Library/Dark/BogusCamA",
        "_Calibration Library/Dark/BogusCamB",
    }


def test_every_session_is_checked_not_just_the_first(tmp_path):
    # Five sessions, ALL misfiled: each folder date has no frames on it.
    # The shared-cursor bug reported exactly one of these.
    con = make_db(tmp_path)
    for n in range(2, 7):
        add_session(
            con, "NGC_1499",
            f"NGC 1499 California Nebula/NGC_1499 RASA8 ASI2600MCAir 2026-04-{n:02d}",
            f"2026-04-{n:02d}", ["2026-03-01"],
        )

    findings = run_validate(con)

    mismatches = [ref for code, ref in findings if code == "DATE_MISMATCH"]
    assert len(mismatches) == 5
