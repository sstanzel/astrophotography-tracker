#!/usr/bin/env python3
"""file_flats.py - one-time filing pass: move legacy flat sets into their sessions.

Walks each library's `_Flat older/{Scope_Sensor}/Flat {Scope}_{Sensor} {date}`
sets and moves every set that matches a session (same rig, same date - falling
back to the previous night for next-morning flats) into that session's folder,
where per-session flats live by convention.

On a shared-flat night (several sessions, one rig, one flat set) the set goes
to ONE host session - preferring a session that has no flats yet, then the one
with the most kept lights - and every other flat-less session that night gets a
`[calibration] flats = "<host folder>"` pointer stamped into its notes.toml.
The tracker's resolve_flats() reads both the files and the pointers, so after
this pass plus a re-ingest every session knows where its flats are.

Sets that match no session (orphans) are left in place and reported. When the
host session already holds a folder with the set's name: an empty placeholder
is filled, a byte-identical copy (same file names + sizes) means the _Flat
older set is redundant and is deleted, and anything else is skipped for human
review.

Usage:
    python3 file_flats.py            # dry run - print the full plan, move nothing
    python3 file_flats.py --apply    # move the sets + stamp the pointers

Run a full ingest afterwards (refresh.py) so flats counts and the Flats
column pick up the new locations.
"""

import argparse
import datetime
import os
import re
import shutil
import sqlite3

import astro_config

FLAT_DIRNAME = "_Flat older"
SET_RE = re.compile(r"^Flat (\S+)_(\S+) (\d{4}-\d{2}-\d{2})$")
CAL_SECTION = (
    "\n\n[calibration]\n"
    "# Shared-flat night: this session's flats live with the sibling session below.\n"
    'flats = "{}"\n'
)


def prev_day(date_str: str) -> str:
    """Return the ISO date one day before date_str (next-morning flats)."""
    return (datetime.date.fromisoformat(date_str) - datetime.timedelta(days=1)).isoformat()


def tree_size(path: str) -> int:
    """Total size in bytes of all files under path."""
    total = 0
    for r, _d, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(r, f))
            except OSError:
                pass
    return total


def inventory(path: str) -> set:
    """Set of (relative path, size) for every real file under path.

    .DS_Store and AppleDouble (._*) files are ignored: an existing destination
    that holds only those is an empty placeholder, not data.
    """
    out = set()
    for r, _d, files in os.walk(path):
        for f in files:
            if f == ".DS_Store" or f.startswith("._"):
                continue
            fp = os.path.join(r, f)
            try:
                out.add((os.path.relpath(fp, path), os.path.getsize(fp)))
            except OSError:
                pass
    return out


def load_sessions(db_path: str) -> dict:
    """Index sessions by (scope, sensor, date).

    Returns:
        dict mapping (scope, sensor, session_date) to a list of session dicts
        (target_id, folder_path, library_id, lights_kept, flats_count).
    """
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    out: dict = {}
    for r in con.execute("""SELECT target_id, scope, sensor, session_date, folder_path,
                  library_id, lights_kept, flats_count FROM sessions"""):
        out.setdefault((r["scope"], r["sensor"], r["session_date"]), []).append(dict(r))
    con.close()
    return out


def find_flat_sets(roots: dict) -> list:
    """Collect every parsed flat set under each library's _Flat older tree.

    Args:
        roots: {library_id: root_path} for mounted libraries.

    Returns:
        List of dicts (scope, sensor, date, abs_path, leaf).
    """
    sets = []
    for _lib_id, root in roots.items():
        flat_root = os.path.join(root, FLAT_DIRNAME)
        if not os.path.isdir(flat_root):
            continue
        for combo in sorted(os.listdir(flat_root)):
            combodir = os.path.join(flat_root, combo)
            if not os.path.isdir(combodir) or combo.startswith((".", "!")):
                continue
            for leaf in sorted(os.listdir(combodir)):
                m = SET_RE.match(leaf)
                if m:
                    scope, sensor, date = m.groups()
                    sets.append(
                        dict(
                            scope=scope,
                            sensor=sensor,
                            date=date,
                            abs_path=os.path.join(combodir, leaf),
                            leaf=leaf,
                        )
                    )
    return sets


def pick_host(matches: list) -> dict:
    """Choose the session that receives a shared flat set.

    Prefers a session with no flats of its own (the set is likely theirs),
    then the most kept lights, then the first target id.
    """
    return sorted(matches, key=lambda s: (s["flats_count"] > 0, -s["lights_kept"], s["target_id"]))[
        0
    ]


def stamp_notes(session_abs: str, host_folder: str, apply: bool) -> str:
    """Write a [calibration] flats pointer into a sibling session's notes.toml.

    Args:
        session_abs: absolute path of the sibling session folder.
        host_folder: folder name of the session holding the shared flats.
        apply: False = report only, True = write the file.

    Returns:
        One-word outcome for the report: stamped | already | no-notes.
    """
    name = os.path.basename(session_abs)
    p = os.path.join(session_abs, f"{name} notes.toml")
    if not os.path.isfile(p):
        return "no-notes"
    txt = open(p, encoding="utf-8").read()
    m = re.search(r'^flats\s*=\s*"([^"]*)"', txt, re.M)
    if m:
        if m.group(1) == host_folder:
            return "already"
        new = txt[: m.start()] + f'flats = "{host_folder}"' + txt[m.end() :]
    elif re.search(r"^\[calibration\]\s*$", txt, re.M):
        new = re.sub(
            r"^\[calibration\]\s*$",
            f'[calibration]\nflats = "{host_folder}"',
            txt,
            count=1,
            flags=re.M,
        )
    else:
        new = txt.rstrip("\n") + CAL_SECTION.format(host_folder)
    if apply:
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(new)
    return "stamped"


def prune_empty_dirs(roots: dict, apply: bool) -> int:
    """Remove now-empty {Scope_Sensor} folders under each _Flat older tree."""
    removed = 0
    for _lib_id, root in roots.items():
        flat_root = os.path.join(root, FLAT_DIRNAME)
        if not os.path.isdir(flat_root):
            continue
        for combo in os.listdir(flat_root):
            d = os.path.join(flat_root, combo)
            if not os.path.isdir(d) or combo.startswith(("!", ".")):
                continue
            leftovers = [x for x in os.listdir(d) if x not in (".DS_Store",)]
            if not leftovers:
                if apply:
                    shutil.rmtree(d)
                removed += 1
    return removed


def main() -> None:
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default=os.path.join(here, "tracker.db"))
    ap.add_argument("--config", default=None, help="path to config.toml")
    ap.add_argument("--apply", action="store_true", help="move sets + stamp pointers")
    args = ap.parse_args()

    libraries = astro_config.load_libraries(args.config)
    roots = {lib["id"]: lib["path"] for lib in libraries if os.path.isdir(lib["path"])}
    for lib in libraries:
        if lib["id"] not in roots:
            raise SystemExit(f"Library '{lib['id']}' is not mounted ({lib['path']}) - aborting.")

    sessions = load_sessions(args.db)
    flat_sets = find_flat_sets(roots)
    mode = "APPLY" if args.apply else "DRY RUN (nothing moved; use --apply)"
    print(f"file_flats - {mode}")
    print(f"Flat sets found: {len(flat_sets)}\n")

    moved = stamped = orphans = skipped = duplicates = 0
    moved_bytes = 0
    for fs in flat_sets:
        rig_day = (fs["scope"], fs["sensor"], fs["date"])
        matches = sessions.get(rig_day) or sessions.get(
            (fs["scope"], fs["sensor"], prev_day(fs["date"]))
        )
        label = f"{fs['date']} {fs['scope']}_{fs['sensor']}"
        if not matches:
            print(f"ORPHAN  {label}: no session that night (or the night before) - left in place")
            orphans += 1
            continue
        host = pick_host(matches)
        host_abs = os.path.join(roots[host["library_id"]], host["folder_path"])
        dest = os.path.join(host_abs, fs["leaf"])
        merge_empty = False
        if os.path.exists(dest):
            dst_inv = inventory(dest)
            if not dst_inv:
                merge_empty = True  # placeholder folder with no real files
            elif dst_inv == inventory(fs["abs_path"]):
                print(f"DUP     {label}: session holds an identical copy - deleting the legacy set")
                if args.apply:
                    shutil.rmtree(fs["abs_path"])
                duplicates += 1
                continue
            else:
                print(f"SKIP    {label}: destination exists with different content - {dest}")
                skipped += 1
                continue
        size = tree_size(fs["abs_path"])
        moved_bytes += size
        host_name = os.path.basename(host["folder_path"])
        note = " (fills empty placeholder)" if merge_empty else ""
        print(f"MOVE    {label} ({size / 2**30:.1f} GB) -> {host['library_id']}:{host_name}{note}")
        if args.apply:
            if merge_empty:
                shutil.rmtree(dest)
            shutil.move(fs["abs_path"], dest)
        moved += 1
        for sib in matches:
            if sib is host or sib["flats_count"] > 0:
                continue
            sib_abs = os.path.join(roots[sib["library_id"]], sib["folder_path"])
            outcome = stamp_notes(sib_abs, host_name, args.apply)
            print(f"  point {os.path.basename(sib['folder_path'])}: {outcome}")
            if outcome == "stamped":
                stamped += 1

    pruned = prune_empty_dirs(roots, args.apply)
    print(
        f"\nSummary: {moved} sets ({moved_bytes / 2**30:.1f} GB) moved, "
        f"{stamped} sibling pointers stamped, {duplicates} duplicates deleted, "
        f"{skipped} skipped, {orphans} orphans left, "
        f"{pruned} empty rig folders pruned."
    )
    if args.apply:
        print("Now run refresh.py (full ingest) to pick up the new locations.")


if __name__ == "__main__":
    main()
