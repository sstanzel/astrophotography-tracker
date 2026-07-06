#!/usr/bin/env python3
"""
validate.py — run only the data-validation pass against an existing tracker.db.

A fast re-check after a fix: no library walk, no re-ingest. It re-runs every
check that works from the database — the Tier 1/2/3 checks on sessions, frames,
targets and calibration_masters — and rebuilds the validation_findings table.

Three filesystem-structural checks (UNPARSED_SESSION_NAME, CAL_EMPTY,
CAL_NAMING) only run inside a full ingest.py run, because they depend on
walking folders that have no row in the database. Run ingest.py for those.

Usage:
    python3 validate.py [--db PATH]
"""
import argparse
import os
import sqlite3
import sys

# validate.py shares ingest.py's validation function and helpers.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ingest import validate, load_locations  # noqa: E402
import astro_config  # noqa: E402


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(
        description="Re-run the tracker data-validation pass against an existing DB.")
    ap.add_argument("--db", default=os.path.join(here, "tracker.db"),
                    help="path to tracker.db (default: next to this script)")
    args = ap.parse_args()

    if not os.path.exists(args.db):
        sys.exit(f"Database not found: {args.db}\nRun ingest.py first to build it.")

    con = sqlite3.connect(args.db)
    con.execute("PRAGMA foreign_keys = ON")

    # locations.toml lives in the _organization folder, next to the scripts.
    locations = load_locations(astro_config.org_path("locations.toml"))

    print(f"Validating {args.db}")
    by = validate(con, locations, {}, print)
    print(f"\n{by['error']} errors, {by['warning']} warnings, {by['info']} info")
    print("(UNPARSED_SESSION_NAME / CAL_EMPTY / CAL_NAMING run only in a full "
          "ingest.py run.)")
    con.close()


if __name__ == "__main__":
    main()
