"""intake_ledger.py — the durable record of every verified intake copy.

A standalone SQLite database (default `_organization/intake_ledger.db` —
deliberately NOT inside tracker.db, which is derived and rebuildable; the
ledger is primary state and is never regenerated). A `files` row exists if
and only if that copy was hash-verified and atomically renamed into place,
so interrupted runs need no repair: whatever never reached the ledger is
simply still new on the next plan.

WAL mode, one commit per verified file.
"""

import datetime as dt
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  run_id       INTEGER PRIMARY KEY,
  started_utc  TEXT NOT NULL,
  finished_utc TEXT,
  status       TEXT NOT NULL,     -- running | complete | interrupted | undone
  argv         TEXT,
  files_copied INTEGER DEFAULT 0,
  bytes_copied INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS files (
  file_id      INTEGER PRIMARY KEY,
  run_id       INTEGER NOT NULL REFERENCES runs(run_id),
  source_id    TEXT NOT NULL,
  relpath      TEXT NOT NULL,     -- relative to the source root
  size         INTEGER NOT NULL,
  mtime_ns     INTEGER NOT NULL,
  sha          TEXT NOT NULL,
  session      TEXT NOT NULL,     -- session folder name
  dest_relpath TEXT NOT NULL,     -- relative to the staging root at copy time
  status       TEXT NOT NULL,     -- copied | reverted
  copied_utc   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_files_lookup ON files(source_id, relpath);
CREATE TABLE IF NOT EXISTS dirs (
  run_id  INTEGER NOT NULL,
  session TEXT NOT NULL
);
"""


def _utcnow() -> str:
    return dt.datetime.now(dt.UTC).isoformat(timespec="seconds")


def open_ledger(path: str) -> sqlite3.Connection:
    """Open (creating if needed) the ledger database.

    Args:
        path: ledger file path; parent directories are created.

    Returns:
        A WAL-mode connection with the schema in place.
    """
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    con = sqlite3.connect(path)
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(SCHEMA)
    con.commit()
    return con


def relabel_stale_runs(con: sqlite3.Connection) -> int:
    """Mark crashed runs: any run still 'running' becomes 'interrupted'.

    Returns:
        How many runs were relabeled (reported in the plan header).
    """
    cur = con.execute(
        "UPDATE runs SET status='interrupted', finished_utc=? WHERE status='running'",
        (_utcnow(),),
    )
    con.commit()
    return cur.rowcount


def known_files(con: sqlite3.Connection, source_id: str) -> dict[str, dict]:
    """Every 'copied' ledger row for one source, newest row per relpath.

    Args:
        con: ledger connection.
        source_id: the [[source]] id.

    Returns:
        {relpath: {'size','mtime_ns','sha','session','dest_relpath','run_id'}}
    """
    out: dict[str, dict] = {}
    rows = con.execute(
        "SELECT relpath, size, mtime_ns, sha, session, dest_relpath, run_id"
        "  FROM files WHERE source_id=? AND status='copied' ORDER BY file_id",
        (source_id,),
    )
    for relpath, size, mtime_ns, sha, session, dest_relpath, run_id in rows:
        out[relpath] = {
            "size": size,
            "mtime_ns": mtime_ns,
            "sha": sha,
            "session": session,
            "dest_relpath": dest_relpath,
            "run_id": run_id,
        }
    return out


def begin_run(con: sqlite3.Connection, argv: str) -> int:
    """Open a run row (status 'running') and return its id."""
    cur = con.execute(
        "INSERT INTO runs (started_utc, status, argv) VALUES (?, 'running', ?)",
        (_utcnow(), argv),
    )
    con.commit()
    return cur.lastrowid


def record_dir(con: sqlite3.Connection, run_id: int, session: str) -> None:
    """Note a session folder this run created (undo/reconciliation support)."""
    con.execute("INSERT INTO dirs (run_id, session) VALUES (?, ?)", (run_id, session))
    con.commit()


def record_copy(
    con: sqlite3.Connection,
    run_id: int,
    source_id: str,
    rec: dict,
    session: str,
    dest_relpath: str,
    sha: str,
) -> None:
    """Insert one verified-copy row and commit. Call ONLY after the copy was
    hash-verified and renamed into place — a row must never precede its file."""
    con.execute(
        "INSERT INTO files (run_id, source_id, relpath, size, mtime_ns, sha,"
        "                   session, dest_relpath, status, copied_utc)"
        " VALUES (?,?,?,?,?,?,?,?, 'copied', ?)",
        (
            run_id,
            source_id,
            rec["relpath"],
            rec["size"],
            rec["mtime_ns"],
            sha,
            session,
            dest_relpath,
            _utcnow(),
        ),
    )
    con.commit()


def finish_run(con: sqlite3.Connection, run_id: int, files: int, n_bytes: int) -> None:
    """Close a run as complete with its copy totals."""
    con.execute(
        "UPDATE runs SET status='complete', finished_utc=?, files_copied=?, bytes_copied=?"
        " WHERE run_id=?",
        (_utcnow(), files, n_bytes, run_id),
    )
    con.commit()


def all_copied_rows(con: sqlite3.Connection) -> list[dict]:
    """Every 'copied' row (for --audit and reconciliation), oldest first."""
    rows = con.execute(
        "SELECT source_id, relpath, size, mtime_ns, sha, session, dest_relpath, run_id"
        "  FROM files WHERE status='copied' ORDER BY file_id"
    )
    return [
        {
            "source_id": r[0],
            "relpath": r[1],
            "size": r[2],
            "mtime_ns": r[3],
            "sha": r[4],
            "session": r[5],
            "dest_relpath": r[6],
            "run_id": r[7],
        }
        for r in rows
    ]
