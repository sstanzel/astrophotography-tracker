"""Tests for audit.py DB checks (in-memory sqlite, minimal schema)."""

import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import audit  # noqa: E402


def _db_with_session(session_date, light_times, is_other_capture=0):
    """One session + its light frames, minimal columns the checks read."""
    con = sqlite3.connect(":memory:")
    con.executescript(
        """
        CREATE TABLE sessions (
            session_id INTEGER PRIMARY KEY, folder_path TEXT,
            session_date TEXT, is_other_capture INTEGER DEFAULT 0);
        CREATE TABLE frames (
            session_id INTEGER, frame_type TEXT, captured_at_utc TEXT);
        """
    )
    con.execute(
        "INSERT INTO sessions VALUES (1, ?, ?, ?)",
        (f"T/T Scope Sensor {session_date}", session_date, is_other_capture),
    )
    con.executemany(
        "INSERT INTO frames VALUES (1, 'light', ?)", [(t,) for t in light_times]
    )
    return con


def test_night_of_date_flags_morning_dated_session():
    # The M 63 case: lights 00:04-05:59 on the folder's own date — the
    # night-of convention dates this session one day earlier.
    con = _db_with_session(
        "2026-06-06", ["2026-06-06 00:04:00", "2026-06-06 03:30:00", "2026-06-06 05:59:00"]
    )

    findings = audit.check_night_of_date(con.cursor())

    assert len(findings) == 1
    sev, code, ref, msg = findings[0]
    assert (sev, code) == ("warning", "NIGHT_OF_DATE")
    assert "2026-06-05" in msg


def test_night_of_date_accepts_evening_start_session():
    # An evening start rolling past midnight is correctly dated already.
    con = _db_with_session(
        "2026-07-08", ["2026-07-08 22:36:00", "2026-07-09 03:09:00"]
    )

    assert audit.check_night_of_date(con.cursor()) == []


def test_night_of_date_exempts_other_capture():
    # Daytime Moon/Sun sessions legitimately capture before local noon.
    con = _db_with_session(
        "2026-03-09", ["2026-03-09 08:59:00", "2026-03-09 09:06:00"], is_other_capture=1
    )

    assert audit.check_night_of_date(con.cursor()) == []
