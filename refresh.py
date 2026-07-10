#!/usr/bin/env python3
"""
refresh.py - one command to update the tracker end to end.

Runs, in order: ingest.py (rescan the libraries into tracker.db) ->
export_html.py (dashboard) -> export_xlsx.py (workbook) -> copy both generated
files to the offline mirror (the [mirror] path in config.toml, e.g. OneDrive).

    python3 refresh.py                # full refresh
    python3 refresh.py --no-ingest    # only re-render + mirror (after editing a
                                      #   manifest/notes; skips the library scan)
    python3 refresh.py --no-mirror    # ingest + render, but don't copy to mirror

Any step failing stops the run (so you never mirror a stale/half-built file).
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import astro_config   # noqa: E402

DASHBOARD = "tracker_dashboard.html"
XLSX = "Astrophotography tracker (generated).xlsx"


def run_step(script: str) -> None:
    """Run a sibling tracker script, inheriting stdout; raise on failure.

    Args:
        script: filename of the script to run (next to this one).
    """
    print(f"\n=== {script} ===", flush=True)
    subprocess.run([sys.executable, os.path.join(HERE, script)], check=True)


def mirror(mirror_dir: str) -> None:
    """Copy the generated dashboard + xlsx into the mirror directory.

    Args:
        mirror_dir: destination directory (e.g. a OneDrive folder).
    """
    for name in (DASHBOARD, XLSX):
        shutil.copy2(os.path.join(HERE, name), os.path.join(mirror_dir, name))
    print(f"Mirrored dashboard + xlsx → {mirror_dir}")


def main() -> None:
    """Chain ingest, the two exports, and the offline-mirror copy."""
    ap = argparse.ArgumentParser(
        description="Ingest, regenerate the dashboard + xlsx, and mirror them.")
    ap.add_argument("--no-ingest", action="store_true",
                    help="skip the library scan; only re-render and mirror")
    ap.add_argument("--no-mirror", action="store_true",
                    help="don't copy the outputs to the [mirror] path")
    ap.add_argument("--notes", action="store_true",
                    help="first back-fill sky/weather in session notes.toml "
                         "(moon always; weather only for recent nights)")
    args = ap.parse_args()

    if args.notes:
        run_step("populate_notes.py")
    if not args.no_ingest:
        run_step("ingest.py")
    run_step("export_html.py")
    run_step("export_xlsx.py")

    if args.no_mirror:
        print("\nMirror skipped (--no-mirror).")
        return
    mdir = astro_config.mirror_path()
    if not mdir:
        print("\nNo [mirror] path in config.toml — skipped mirror copy.")
    elif not os.path.isdir(mdir):
        print(f"\nMirror dir not mounted, skipped: {mdir}")
    else:
        print()
        mirror(mdir)


if __name__ == "__main__":
    main()
