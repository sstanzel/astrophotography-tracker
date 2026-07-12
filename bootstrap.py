#!/usr/bin/env python3
"""bootstrap.py - stamp out a fresh _organization/ skeleton around this repo.

For a fresh start (new user, or a new machine from a bare `git clone`): the
tracker expects to live at `<anything>/_organization/tracker/`, with the
registry and planning tomls one level up. This script creates that skeleton:

  * the five registry directories (controlled vocabularies as empty
    directories whose NAMES are the data):
        filter_values/  scope_values/  sensor_values/
        scope+sensor_values/  target folders/
  * each templates/*.example.toml copied one level up, `.example` stripped
    (locations.toml, plans.toml, target_goals.toml, calibration_thresholds.toml)
  * templates/config.example.toml copied to tracker/config.toml
    (config.toml is gitignored - it is the one per-machine file)

Idempotent: nothing that already exists is touched, so it is safe to run on a
populated installation - it will simply report "exists" for everything.

Usage:
    python3 bootstrap.py              # create whatever is missing
    python3 bootstrap.py --dry-run    # report only, write nothing

After bootstrapping: edit config.toml (library paths), fill in the copied
tomls, add your cameras/scopes/targets to the registry directories, then run
`python3 refresh.py`. See USAGE.md "Fresh start".
"""

from __future__ import annotations

import argparse
import os
import shutil

TRACKER_DIR = os.path.dirname(os.path.abspath(__file__))
ORG_DIR = os.path.dirname(TRACKER_DIR)  # _organization/ by position
TEMPLATES_DIR = os.path.join(TRACKER_DIR, "templates")

REGISTRY_DIRS = [
    "filter_values",
    "scope_values",
    "sensor_values",
    "scope+sensor_values",
    "target folders",
]


def plan_actions() -> list[tuple[str, str, str | None]]:
    """Build the (kind, destination, source) work list for the skeleton.

    Returns:
        One tuple per skeleton item: kind is "dir" or "copy", destination is
        an absolute path, and source is the file to copy (None for dirs).
    """
    actions: list[tuple[str, str, str | None]] = []
    for d in REGISTRY_DIRS:
        actions.append(("dir", os.path.join(ORG_DIR, d), None))
    for f in sorted(os.listdir(TEMPLATES_DIR)):
        if not f.endswith(".example.toml"):
            continue
        src = os.path.join(TEMPLATES_DIR, f)
        name = f.replace(".example", "")
        # config.toml belongs beside the scripts; everything else one level up
        dest_dir = TRACKER_DIR if name == "config.toml" else ORG_DIR
        actions.append(("copy", os.path.join(dest_dir, name), src))
    return actions


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true", help="report only, write nothing")
    args = ap.parse_args()

    created = 0
    print(f"_organization: {ORG_DIR}\n")
    for kind, dest, src in plan_actions():
        rel = os.path.relpath(dest, ORG_DIR)
        if os.path.exists(dest):
            print(f"  exists   {rel}")
            continue
        if not args.dry_run:
            if kind == "dir":
                os.makedirs(dest)
            else:
                shutil.copyfile(src, dest)
        created += 1
        print(f"  {'would create' if args.dry_run else 'created':12} {rel}")

    print(
        f"\n{'DRY RUN - ' if args.dry_run else ''}{created} item(s) "
        f"{'to create' if args.dry_run else 'created'}"
    )
    if created and not args.dry_run:
        print(
            "\nNext: edit tracker/config.toml (library paths), fill in the "
            "copied tomls,\nadd cameras/scopes/targets to the registry "
            "directories, then run: python3 refresh.py"
        )


if __name__ == "__main__":
    main()
