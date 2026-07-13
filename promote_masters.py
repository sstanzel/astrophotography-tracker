#!/usr/bin/env python3
"""
promote_masters.py - copy keeper files into their Results folders.

Keepers are the integrated **master** (the stacked light) and any Photoshop
**.psd** edit — both belong in Results, but PixInsight / PI Magic Studio leave
them inside the working folders (PI Process / PI Magic) that clean_processing.py
is free to delete. This script walks the libraries, finds the keepers in each
session's or integration's working folders, and copies them to the sibling
"{name} Results" folder if not already there — so they are safe before the
working folders are swept.

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

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "internal"))
import astro_config  # noqa: E402

WORKING_FOLDERS = ("PI Process", "PI Magic")
MASTER_EXTS = (".xisf", ".fit", ".fits", ".tif", ".tiff")

# A file is the integrated master if its name matches INCLUDE and not EXCLUDE.
# INCLUDE catches PI Magic Studio ("..._stacked_RGB"), WBPP ("masterLight_...")
# and manual PixInsight ("Stacked_..."). EXCLUDE drops rejection maps, the
# calibration masters, calibrated subs, plate-solve caches and weight images.
MASTER_INCLUDE = re.compile(r"(stacked|masterlight)", re.I)
MASTER_EXCLUDE = re.compile(
    r"(rejection|master\s*dark|master\s*flat|master\s*bias|_cal_|_wcs|weight)", re.I
)


def is_keeper(name: str) -> bool:
    """True if a file belongs in Results — the integrated master, or a .psd edit.

    A Photoshop .psd is always a keeper. Otherwise the file must look like an
    integrated master (name matches INCLUDE and not EXCLUDE, image extension)
    rather than a working product.

    Args:
        name: file basename.

    Returns:
        Whether the file should be copied to Results.
    """
    low = name.lower()
    if low.endswith(".psd"):
        return True
    if not low.endswith(MASTER_EXTS):
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


def find_keepers(container: str) -> list[str]:
    """Return keeper files (master + .psd) in a container's working folders.

    Args:
        container: absolute path of the session or integration folder.

    Returns:
        Absolute paths of keepers under PI Process / PI Magic.
    """
    keepers = []
    for wf in WORKING_FOLDERS:
        wpath = os.path.join(container, wf)
        if not os.path.isdir(wpath):
            continue
        for root, _dirs, files in os.walk(wpath):
            for f in files:
                if not f.startswith("._") and is_keeper(f):
                    keepers.append(os.path.join(root, f))
    return keepers


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
            dirs[:] = [d for d in dirs if d not in WORKING_FOLDERS and d != f"{base} Results"]


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
            keepers = find_keepers(container)
            if not keepers:
                continue
            rdir = results_dir_for(container)
            present = set(os.listdir(rdir)) if os.path.isdir(rdir) else set()
            for m in keepers:
                dst = os.path.join(rdir, os.path.basename(m))
                plan.append((m, dst, os.path.basename(m) in present))
    return plan


def main() -> None:
    """Preview or apply the master → Results copies across the libraries."""
    ap = argparse.ArgumentParser(
        description="Copy keepers (integrated master + .psd) into Results folders."
    )
    ap.add_argument(
        "--only", default=None, help="limit to containers whose path contains this substring"
    )
    ap.add_argument("--apply", action="store_true", help="actually copy (default: preview only)")
    ap.add_argument("--config", default=None, help="path to config.toml")
    args = ap.parse_args()

    libraries = astro_config.load_libraries(args.config)
    plan = plan_copies(libraries, args.only)
    pending = [p for p in plan if not p[2]]

    if not plan:
        print("No keepers (master / .psd) found in any working folder.")
        return

    for src, dst, present in plan:
        rel = os.path.dirname(dst)
        tag = "already in Results" if present else "COPY →"
        print(f"[{tag}] {os.path.basename(src)}")
        if not present:
            print(f"           into {rel}")

    print(f"\n{len(pending)} keeper(s) to copy, " f"{len(plan) - len(pending)} already in Results.")
    if not args.apply:
        print("DRY RUN — nothing copied. Re-run with --apply.")
        return

    total, copied = len(pending), 0
    log_lines: list[str] = []
    for src, dst, present in plan:
        if present:
            continue
        copied += 1
        mb = os.path.getsize(src) / (1024 * 1024)
        print(f"  [{copied}/{total}] {os.path.basename(src)} ({mb:.0f} MB)…", flush=True)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
        log_lines.append(f"copy '{src}' → '{dst}'")
    astro_config.log_actions("promote_masters", log_lines)
    print(f"Copied {copied} keeper(s) into their Results folders.")


if __name__ == "__main__":
    main()
