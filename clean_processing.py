#!/usr/bin/env python3
"""
clean_processing.py - empty the PI Process and PI Magic working folders.

Those two folders hold PixInsight intermediate/scratch files. The keeper output
(stacked / integrated images) belongs in each session's "{session} Results"
folder, so once a session is processed its PI Process and PI Magic folders are
safe to clear. This script empties their *contents* but leaves the (now empty)
folders in place, since they are part of the PostHaste session template.

SAFE BY DEFAULT: a plain run only previews. Nothing is deleted until you pass
--apply. And as a safety net, any container whose integrated master is not yet
in its "{name} Results" folder is SKIPPED and reported — run promote_masters.py
first, or pass --promote to copy the master to Results before cleaning.

Run natively on the Mac:
    python3 "clean_processing.py"                # preview (dry run)
    python3 "clean_processing.py" --apply         # empty the folders
    python3 "clean_processing.py" --promote --apply   # copy master→Results, then empty
    python3 "clean_processing.py" --only "M_81"   # limit to matching folders
"""
from __future__ import annotations
import argparse, os, shutil, sys

# =============================================================================
# Configuration
# =============================================================================
# Library paths come from config.toml (via astro_config) — not hardcoded.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import astro_config   # noqa: E402
from promote_masters import find_masters, results_dir_for   # noqa: E402

WORKING_FOLDERS = ("PI Process", "PI Magic")   # folders whose contents get cleared
SKIP_TOPLEVEL = {"_organization", "_Calibration Library", "_sessions to organize"}


def human(n):
    """Bytes -> human-readable string."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024


def tree_size(path):
    """(file_count, total_bytes) under path, recursively."""
    fc = sz = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            fc += 1
            try:
                sz += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return fc, sz


def unpromoted_masters(container):
    """Return integrated masters in a container's working folders not yet in Results.

    The keeper master must reach the "{name} Results" folder before the working
    folders (PI Process / PI Magic) are emptied. Any master still only in the
    working folders is returned so the caller can gate or promote it.

    Args:
        container: absolute path of the session or integration folder.

    Returns:
        List of master file paths under the working folders whose basename is
        not already present in the Results folder.
    """
    rdir = results_dir_for(container)
    present = set(os.listdir(rdir)) if os.path.isdir(rdir) else set()
    return [m for m in find_masters(container)
            if os.path.basename(m) not in present]


def report_prunable_integrations():
    """Print multi-session integration lineages that have more than one version.
    Older versions are prune candidates once the latest is confirmed good.
    Read-only — reads tracker.db (next to this script); silent if absent."""
    import sqlite3
    db = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tracker.db")
    if not os.path.exists(db):
        return
    con = sqlite3.connect(db)
    try:
        rows = con.execute(
            "SELECT target_id, scope, sensor, span, version_count, "
            "latest_version, folders FROM v_integration_prune").fetchall()
    except sqlite3.OperationalError:
        con.close()
        return                      # older DB without the view
    con.close()
    print()
    if not rows:
        print("Prunable multi-session integrations: none.")
        return
    print(f"Prunable multi-session integrations — {len(rows)} lineage(s) with "
          f"multiple versions (keep the latest, the rest can be cleaned up):")
    for tid, scope, sensor, span, vcount, latest, folders in rows:
        rig = f"{scope} {sensor}" if scope else "composite"
        print(f"  {tid} · {rig} · {span}: {vcount} versions, latest v{latest}")
        print(f"      {folders}")


def empty_folder(path):
    """Delete everything inside path, keep path itself. Returns files removed."""
    removed = 0
    for entry in os.listdir(path):
        p = os.path.join(path, entry)
        if os.path.isdir(p) and not os.path.islink(p):
            fc, _ = tree_size(p)
            shutil.rmtree(p)
            removed += fc
        else:
            os.remove(p)
            removed += 1
    return removed


# =============================================================================
# Main
# =============================================================================
def main():
    ap = argparse.ArgumentParser(
        description="Empty the PI Process / PI Magic working folders.")
    ap.add_argument("--apply", action="store_true",
                    help="actually delete (default is a preview only)")
    ap.add_argument("--only", default="",
                    help="limit to session folders containing this substring")
    ap.add_argument("--promote", action="store_true",
                    help="copy any master not yet in Results into Results first, "
                         "instead of skipping that folder")
    ap.add_argument("--config", default=None,
                    help="path to config.toml (default: next to this script)")
    args = ap.parse_args()

    libraries = astro_config.load_libraries(args.config)
    lib_paths = [L["path"] for L in libraries]
    targets = []      # (working_folder_path, file_count, bytes)
    skipped = []      # (working_folder_path, a master still only in working)
    promoted = []     # (master_src, results_dir) copied to Results first

    for lib in libraries:
        libroot = lib["path"]
        if not os.path.isdir(libroot):
            print(f"  ! library '{lib['id']}' not mounted, skipping: {libroot}")
            continue
        for tname in sorted(os.listdir(libroot)):
            tpath = os.path.join(libroot, tname)
            if tname.startswith((".", "_")) or tname in SKIP_TOPLEVEL \
                    or not os.path.isdir(tpath):
                continue
            # Processing folders live in both session folders and the
            # integration folders under {target}/integrations/.
            parents = []
            for sname in sorted(os.listdir(tpath)):
                spath = os.path.join(tpath, sname)
                if not os.path.isdir(spath):
                    continue
                if sname == "integrations":
                    for iname in sorted(os.listdir(spath)):
                        ipath = os.path.join(spath, iname)
                        if os.path.isdir(ipath) and not iname.startswith("."):
                            parents.append((iname, ipath))
                else:
                    parents.append((sname, spath))
            for pname, ppath in parents:
                if args.only and args.only not in pname:
                    continue
                # Gate: the integrated master must be in Results before the
                # working folders are emptied. If it is not, either promote it
                # (--promote) or skip this whole container.
                pending = unpromoted_masters(ppath)
                if pending and not args.promote:
                    for wf in WORKING_FOLDERS:
                        wpath = os.path.join(ppath, wf)
                        if os.path.isdir(wpath) and tree_size(wpath)[0] > 0:
                            skipped.append((wpath, pending[0]))
                    continue
                if pending and args.promote:
                    rdir = results_dir_for(ppath)
                    for m in pending:
                        dst = os.path.join(rdir, os.path.basename(m))
                        if args.apply:
                            os.makedirs(rdir, exist_ok=True)
                            shutil.copy2(m, dst)
                        promoted.append((m, rdir))
                for wf in WORKING_FOLDERS:
                    wpath = os.path.join(ppath, wf)
                    if not os.path.isdir(wpath):
                        continue
                    fc, sz = tree_size(wpath)
                    if fc == 0:
                        continue                      # already empty
                    targets.append((wpath, fc, sz))

    # ---- report ----
    total_files = sum(t[1] for t in targets)
    total_bytes = sum(t[2] for t in targets)
    mode = "APPLY" if args.apply else "DRY RUN"
    print(f"clean_processing.py — {mode}")
    print(f"Working folders to empty: {len(targets)}  "
          f"({total_files} files, {human(total_bytes)})\n")
    for wpath, fc, sz in targets:
        rel = wpath
        for lp in lib_paths:
            if wpath.startswith(lp):
                rel = wpath[len(lp) + 1:]
        print(f"  {fc:5} files  {human(sz):>9}   {rel}")

    if promoted:
        tag = "promoted" if args.apply else "would promote"
        print(f"\n  MASTER → Results ({tag} before cleaning):")
        for src, rdir in promoted:
            print(f"    {os.path.basename(src)}  →  {rdir}")

    if skipped:
        print(f"\n  SKIPPED — the integrated master is not in Results yet "
              f"(run promote_masters.py, or re-run with --promote):")
        for wpath, master in skipped:
            print(f"    {wpath}")
            print(f"      master still only in working: {os.path.basename(master)}")

    # ---- prunable multi-session integrations (read-only report) ----
    report_prunable_integrations()

    # ---- act ----
    if not args.apply:
        print(f"\nDRY RUN — nothing deleted. Re-run with --apply to empty "
              f"{len(targets)} folder(s).")
        return

    print()
    removed_files = removed_folders = 0
    for wpath, fc, sz in targets:
        try:
            removed_files += empty_folder(wpath)
            removed_folders += 1
        except OSError as e:
            print(f"  ! could not empty {wpath}: {e}")
    print(f"Done — emptied {removed_folders} folder(s), "
          f"removed {removed_files} files, reclaimed {human(total_bytes)}.")
    if promoted:
        print(f"Promoted {len(promoted)} master(s) to Results first.")
    if skipped:
        print(f"{len(skipped)} folder(s) skipped (master not in Results).")


if __name__ == "__main__":
    main()
