#!/usr/bin/env python3
"""
mark_pimagic.py — record (or clear) PI Magic Studio initial processing on a session.

Manages a dedicated `[pi_magic]` table in the session's `{name} notes.toml`,
which ingest.py re-reads on every run and never overwrites. The freeform
`[processing]`/`[future_processing]` sections in the same file are left
untouched. This is the low-friction way to keep the tracker's PI Magic Studio
column current while processing across machines.

The session folders live on the shared NAS (Stream/Peak), so this works from any
machine that has the folder mounted. On a machine without this script (e.g. a
Windows PC), the same effect is achieved by hand-editing the session's
notes.toml — add:

    [pi_magic]
    pi_magic_studio = true
    pi_magic_machine = "Alienware"
    pi_magic_date = "2026-07-10"

Usage:
    python3 mark_pimagic.py "<session folder path>" --machine MacMini [--date 2026-07-10]
    python3 mark_pimagic.py "<session folder path>" --clear
    # Machine defaults to this computer's hostname when --machine is omitted.
"""
from __future__ import annotations   # lazy annotations: run on Python 3.9 too

import argparse
import os
import re
import socket
import sys
from typing import Optional

PROCESSING_KEYS = ("pi_magic_studio", "pi_magic_machine", "pi_magic_date")


def session_notes_path(session_dir: str) -> str:
    """Return the path to a session folder's `{name} notes.toml`.

    Args:
        session_dir: absolute path to the session folder.

    Returns:
        Absolute path to the notes.toml (whether or not it exists yet).
    """
    name = os.path.basename(os.path.normpath(session_dir))
    return os.path.join(session_dir, f"{name} notes.toml")


def strip_pimagic(text: str) -> str:
    """Remove the managed `[pi_magic]` table and any stray PI Magic keys.

    Leaves every other section (including the freeform `[processing]` notes)
    untouched, so the marker can be rewritten idempotently.

    Args:
        text: current notes.toml contents.

    Returns:
        The text with the `[pi_magic]` table and loose PI Magic keys removed.
    """
    # Drop the whole [pi_magic] table (until the next table header or EOF).
    text = re.sub(r'(?ms)^\[pi_magic\][^\n]*\n(?:(?!^\[).*\n?)*', "", text)
    # Drop any PI Magic keys that were written outside a section.
    for key in PROCESSING_KEYS:
        text = re.sub(rf'(?m)^\s*{key}\s*=.*\n?', "", text)
    return text


def build_section(machine: Optional[str], date: Optional[str]) -> str:
    """Render the `[pi_magic]` section for a completed PI Magic Studio run.

    Args:
        machine: machine name that ran PI Magic Studio (None omits the key).
        date: YYYY-MM-DD the run happened (None omits the key).

    Returns:
        A TOML section string ending in a newline.
    """
    lines = ["[pi_magic]", "pi_magic_studio = true"]
    if machine:
        lines.append(f'pi_magic_machine = "{machine}"')
    if date:
        lines.append(f'pi_magic_date = "{date}"')
    return "\n".join(lines) + "\n"


def main() -> None:
    """Parse arguments and update the session's notes.toml in place."""
    ap = argparse.ArgumentParser(
        description="Mark (or clear) PI Magic Studio processing on a session.")
    ap.add_argument("session_dir", help="path to the session folder")
    ap.add_argument("--machine", default=None,
                    help="machine that ran it (default: this computer's hostname)")
    ap.add_argument("--date", default=None, help="YYYY-MM-DD it was run (optional)")
    ap.add_argument("--clear", action="store_true",
                    help="remove the PI Magic Studio marker instead of setting it")
    args = ap.parse_args()

    if not os.path.isdir(args.session_dir):
        sys.exit(f"Not a folder: {args.session_dir}")

    path = session_notes_path(args.session_dir)
    text = open(path, encoding="utf-8").read() if os.path.isfile(path) else ""

    base = strip_pimagic(text).rstrip("\n")
    if args.clear:
        new_text = (base + "\n") if base else ""
        action = "cleared"
    else:
        machine = args.machine or socket.gethostname().split(".")[0]
        section = build_section(machine, args.date)
        new_text = (base + "\n\n" + section) if base else section
        action = f"set (machine={machine}"
        action += f", date={args.date})" if args.date else ")"

    # Atomic write: temp file in the same dir, then replace.
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(new_text)
    os.replace(tmp, path)
    print(f"PI Magic Studio {action}")
    print(f"  {path}")


if __name__ == "__main__":
    main()
