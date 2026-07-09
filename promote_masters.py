#!/usr/bin/env python3
"""
promote_masters.py - copy integrated master files into their Results folders.

The integrated master (the stacked light) is a keeper, but PixInsight / PI Magic
Studio leave it inside the working folders (PI Process / PI Magic) that
clean_processing.py is free to delete. This script walks the libraries, finds the
master in each session's or integration's working folders, and copies it to the
sibling "{name} Results" folder if it is not already there — so the keeper is safe
before the working folders are swept.

Model: raw data lives in Light/ + calibration; keepers (master + flat exports)
live in Results/; PI Process / PI Magic are recreatable scratch. This script is
the "make sure the master reached Results" step; clean_processing.py enforces it
before deleting.

Copies (never moves) and never overwrites an existing Results file. Dry run by
default; pass --apply to actually copy.

    python3 promote_masters.py                 # preview every pending copy
    python3 promote_masters.py --apply
    python3 promote_masters.py --only "M_51"    # limit to matching folders
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import astro_config   # noqa: E402

WORKING_FOLDERS = ("PI Process", "PI Magic")
MASTER_EXTS = (".xisf", ".fit", ".fits", ".tif", ".tiff")

# A file is the integrated master if its name matches INCLUDE and not EXCLUDE.
# INCLUDE catches PI Magic Studio ("..._stacked_RGB"), WBPP ("masterLight_...")
# and manual PixInsight ("Stacked_..."). EXCLUDE drops rejection maps, the
# calibration masters, calibrated subs, plate-solve caches and weight images.
MASTER_INCLUDE = re.compile(r"(stacked|masterlight)", re.I)
MASTER_EXCLUDE = re.compile(
    r"(rejection|master\s*dark|master\s*flat|master\s*bias|_cal_|_wcs|weight)", re.I)


def is_master(name: str) -> bool:
    """True if a filename looks like an integrated master (a keeper stack).

    Args:
        name: file basename.

    Returns:
        Whether it is the integrated master rather than a working product.
    """
    if not name.lower().endswith(MASTER_EXTS):
        return False
    return bool(MASTER_INCLUDE.search(name)) and not MASTER_EXCLUDE.search(name)


def results_dir_for(container: str) -> str:
    """Return the '{name} Results' folder path for a session/integration folder.

    Args:
        container: absolute path of the session or integration folder.

    Returns:
        Absolute path of its Results folder (may not exist yet).
    """
    return os.path.join(container, f"{os.path.basename(container)} Results")


def find_masters(container: str) -> list[str]:
    """Return master files found in a container's working folders.

    Args:
        container: absolute path of the session or integration folder.

    Returns:
        Absolute paths of master files under PI Process / PI Magic.
    """
    masters = []
    for wf in WORKING_FOLDERS:
        wpath = os.path.join(container, wf)
        if not os.path.isdir(wpath):
            continue
        for root, _dirs, files in os.walk(wpath):
            for f in files:
                if not f.startswith("._") and is_master(f):
                    masters.append(os.path.join(root, f))
    return masters


def iter_containers(library_root: str):
    """Yield every processing container (folder holding PI Process / PI Magic).

    Args:
        library_root: absolute path of a capture library.

    Yields:
        Absolute paths of session and integration folders.
    """
    for root, dirs, _files in os.walk(library_root):
        # Never descend into leading-underscore/dot utility folders.
        dirs[:] = [d for d in dirs if not d.startswith((".", "_"))]
        base = os.path.basename(root)
        if any(os.path.isdir(os.path.join(root, wf)) for wf in WORKING_FOLDERS):
            yield root
            # Don't descend into this container's own working/Results subtrees.
            dirs[:] = [d for d in dirs
                       if d not in WORKING_FOLDERS and d != f"{base} Results"]


def plan_copies(libraries, only: str | None):
    """Build the list of (master_src, results_dst) copies that are pending.

    Args:
        libraries: library dicts from astro_config.load_libraries().
        only: optional substring; limit to containers whose path matches it.

    Returns:
        List of (source_path, dest_path, already_present) tuples.
    """
    plan = []
    for lib in libraries:
        root = lib["path"]
        if not os.path.isdir(root):
            print(f"  ! library '{lib['id']}' not mounted, skipping: {root}")
            continue
        for container in iter_containers(root):
            if only and only not in container:
                continue
            masters = find_masters(container)
            if not masters:
                continue
            rdir = results_dir_for(container)
            present = set(os.listdir(rdir)) if os.path.isdir(rdir) else set()
            for m in masters:
                dst = os.path.join(rdir, os.path.basename(m))
                plan.append((m, dst, os.path.basename(m) in present))
    return plan


def main() -> None:
    """Preview or apply the master → Results copies across the libraries."""
    ap = argparse.ArgumentParser(
        description="Copy integrated masters into their Results folders.")
    ap.add_argument("--only", default=None,
                    help="limit to containers whose path contains this substring")
    ap.add_argument("--apply", action="store_true",
                    help="actually copy (default: preview only)")
    ap.add_argument("--config", default=None, help="path to config.toml")
    args = ap.parse_args()

    libraries = astro_config.load_libraries(args.config)
    plan = plan_copies(libraries, args.only)
    pending = [p for p in plan if not p[2]]

    if not plan:
        print("No masters found in any working folder.")
        return

    for src, dst, present in plan:
        rel = os.path.dirname(dst)
        tag = "already in Results" if present else "COPY →"
        print(f"[{tag}] {os.path.basename(src)}")
        if not present:
            print(f"           into {rel}")

    print(f"\n{len(pending)} master(s) to copy, "
          f"{len(plan) - len(pending)} already in Results.")
    if not args.apply:
        print("DRY RUN — nothing copied. Re-run with --apply.")
        return

    copied = 0
    for src, dst, present in plan:
        if present:
            continue
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
        copied += 1
    print(f"Copied {copied} master(s) into their Results folders.")


if __name__ == "__main__":
    main()
