#!/usr/bin/env python3
"""forget.py — remove a vanished session's (or integration's) DB record.

The scan never deletes rows: a session folder missing from every scanned
library raises a SESSION_MISSING warning, because that state cannot
distinguish "deleted on purpose" from "moved to a configured-but-offline
library" (which self-heals when that library is next scanned — the session
natural key is library-agnostic and the upsert re-points moved rows).

This command is the explicit human approval for the deleted-on-purpose case.
It re-verifies the folder is absent from every MOUNTED library, refuses when
it finds the folder anywhere, lists any unmounted libraries it could not
check, and with --apply deletes the row (frames and dependents cascade),
logging the action to _organization/dev/actions.log.

Usage:
    python3 forget.py "SH2_233 Redcat51 ASI585MCPro 2025-12-31"           # preview
    python3 forget.py "SH2_233 Redcat51 ASI585MCPro 2025-12-31" --apply   # delete the record
    python3 forget.py --integration "M_81 RASA8 ASI2600MCAir all" --apply
"""

import argparse
import os
import sqlite3
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(_HERE, "internal"))
import astro_config  # noqa: E402

DEFAULT_DB = os.path.join(_HERE, "tracker.db")


def find_on_disk(folder_name: str, libraries: list[dict]) -> tuple[list[str], list[str]]:
    """Look for a session/integration folder by name in every mounted library.

    Searches each library's target folders (and their integrations/ subdirs) —
    a moved folder may sit under a different target than the DB row records.

    Args:
        folder_name: the session or integration folder basename.
        libraries: astro_config.load_libraries() output.

    Returns:
        (found_paths, unmounted_library_labels)
    """
    found: list[str] = []
    unmounted: list[str] = []
    for lib in libraries:
        root = lib["path"]
        if not os.path.isdir(root):
            unmounted.append(f"{lib['label']} ({root})")
            continue
        for tf in sorted(os.listdir(root)):
            tfpath = os.path.join(root, tf)
            if tf.startswith(".") or not os.path.isdir(tfpath):
                continue
            candidate = os.path.join(tfpath, folder_name)
            if os.path.isdir(candidate):
                found.append(candidate)
            integ = os.path.join(tfpath, "integrations", folder_name)
            if os.path.isdir(integ):
                found.append(integ)
    return found, unmounted


def forget_session(con: sqlite3.Connection, folder_name: str, apply: bool) -> list[str]:
    """Preview or delete the session row(s) matching a folder basename.

    Returns:
        Action-log lines for what was (or would be) removed.
    """
    rows = con.execute(
        "SELECT session_id, folder_path, library_id, lights_kept, lights_rejected,"
        "       integration_s FROM sessions"
    ).fetchall()
    matches = [r for r in rows if os.path.basename(r[1]) == folder_name]
    if not matches:
        sys.exit(f"No session row matches folder name {folder_name!r} — nothing to forget.")

    lines: list[str] = []
    for sid, fp, lib_id, kept, rej, integ_s in matches:
        print(
            f"  session_id {sid}  [{lib_id}] {fp}\n"
            f"    {kept or 0} kept / {rej or 0} rejected lights · "
            f"{(integ_s or 0) / 3600.0:.2f} h — removed from all totals"
        )
        if apply:
            for table in ("frames", "publications", "validation_findings"):
                con.execute(f"DELETE FROM {table} WHERE session_id=?", (sid,))
            con.execute("DELETE FROM sessions WHERE session_id=?", (sid,))
            lines.append(f"forget session '{fp}' (session_id {sid}, was in [{lib_id}])")
    if apply:
        con.commit()
    return lines


def forget_integration(con: sqlite3.Connection, folder_name: str, apply: bool) -> list[str]:
    """Preview or delete the integration row(s) matching a folder basename."""
    rows = con.execute("SELECT integration_id, folder_path, library_id FROM integrations").fetchall()
    matches = [r for r in rows if os.path.basename(r[1]) == folder_name]
    if not matches:
        sys.exit(f"No integration row matches folder name {folder_name!r} — nothing to forget.")

    lines: list[str] = []
    for iid, fp, lib_id in matches:
        print(f"  integration_id {iid}  [{lib_id}] {fp}")
        if apply:
            con.execute("DELETE FROM integrations WHERE integration_id=?", (iid,))
            lines.append(f"forget integration '{fp}' (integration_id {iid}, was in [{lib_id}])")
    if apply:
        con.commit()
    return lines


def main() -> None:
    """Verify absence everywhere mounted, then preview or delete the record."""
    ap = argparse.ArgumentParser(
        description="Explicitly remove a vanished session/integration record from tracker.db."
    )
    ap.add_argument("folder", help="the session (or integration) folder basename")
    ap.add_argument(
        "--integration",
        action="store_true",
        help="the name is an integration folder, not a session",
    )
    ap.add_argument("--db", default=DEFAULT_DB, help="tracker.db (default: at the tracker root)")
    ap.add_argument("--apply", action="store_true", help="delete the record (default: preview)")
    args = ap.parse_args()

    if not os.path.isfile(args.db):
        sys.exit(f"No tracker.db at {args.db} — nothing to forget.")

    libraries = astro_config.load_libraries()
    found, unmounted = find_on_disk(args.folder, libraries)
    if found:
        print("REFUSED — this folder still exists on disk; forgetting it would desynchronize:")
        for p in found:
            print(f"  {p}")
        print("If it should be gone, delete/move the folder first, rescan, then forget.")
        sys.exit(1)
    for label in unmounted:
        print(f"note: could not check unmounted library {label} — if the folder moved there,")
        print("      do NOT forget it; mount that library and run refresh.py instead.")

    con = sqlite3.connect(args.db)
    con.execute("PRAGMA foreign_keys=ON")
    try:
        print(f"{'Forgetting' if args.apply else 'Would forget'} {args.folder!r}:")
        if args.integration:
            lines = forget_integration(con, args.folder, args.apply)
        else:
            lines = forget_session(con, args.folder, args.apply)
    finally:
        con.close()

    astro_config.log_actions("forget", lines)
    if args.apply:
        print("Record removed. Run refresh.py to re-render the dashboard.")
    else:
        print("Preview only — nothing deleted. Re-run with --apply to remove the record.")


if __name__ == "__main__":
    main()
