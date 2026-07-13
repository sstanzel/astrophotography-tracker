#!/usr/bin/env python3
"""
mark_integrated.py - record what you just stacked into an integration's master.

After you (re)build an integration in PixInsight / PI Magic Studio, run this on
its folder. It snapshots the sessions currently matching the integration's rule
into the [built].sessions list, so the tracker knows exactly what is in the
current master (built hours) versus what has been captured since (the "stale"
gap). data_through is derived by ingest from the newest built session, and how
it was stacked (PixInsight vs PI Magic) is auto-detected — you record nothing
but the session list.

The rest of the manifest (the [membership] rule, [pipeline] flags, [notes]) is
left untouched. Preview by default; pass --apply to write.

    python3 mark_integrated.py "<integration folder>"            # preview
    python3 mark_integrated.py "<integration folder>" --apply
    python3 mark_integrated.py "<integration folder>" --apply --clear   # empty [built]

For a pinned integration the snapshot is the pinned member list; adjust by hand
if you deliberately stacked a different subset.
"""

import argparse
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "internal"))
import ingest  # noqa: E402  (read_integration_toml, resolve_auto_members, SESSION_RE)


def resolve_available(integration_dir, man):
    """Return the session folder names this integration should contain now.

    Args:
        integration_dir: absolute path of the integration folder.
        man: the parsed integration.toml dict.

    Returns:
        Sorted list of member session folder names.
    """
    target_path = os.path.dirname(os.path.dirname(os.path.normpath(integration_dir)))
    mode = man["mode"] or ("pinned" if man["members"] else "auto")
    if mode == "pinned":
        return list(man["members"] or man["built_sessions"])
    return ingest.resolve_auto_members(target_path, man["rig"], man["span"], man["exclude"])


def set_array(text, key, items):
    """Replace `key = [ ... ]` with a formatted list. Returns (text, replaced?)."""
    block = f"{key} = [\n" + "".join(f'  "{it}",\n' for it in items) + "]"
    new, n = re.subn(r"(?ms)^" + key + r"\s*=\s*\[.*?\]", lambda _m: block, text)
    return new, n > 0


def apply_built(text, sessions):
    """Write the [built] sessions list into the manifest text.

    Falls back to appending a fresh [built] section if the key is absent.

    Args:
        text: current manifest contents.
        sessions: member folder names to record as built.

    Returns:
        Updated manifest text.
    """
    text, ok = set_array(text, "sessions", sessions)
    if ok:
        return text
    section = ["", "[built]", "sessions = ["] + [f'  "{s}",' for s in sessions] + ["]", ""]
    return text.rstrip("\n") + "\n" + "\n".join(section)


def main():
    """Parse arguments and snapshot the built sessions into the manifest."""
    ap = argparse.ArgumentParser(
        description="Record the sessions stacked into an integration's master."
    )
    ap.add_argument("integration_dir", help="path to the integration folder")
    ap.add_argument("--clear", action="store_true", help="empty [built] (mark nothing as stacked)")
    ap.add_argument(
        "--apply", action="store_true", help="write the manifest (default: preview only)"
    )
    args = ap.parse_args()

    idir = args.integration_dir
    mpath = os.path.join(idir, "integration.toml")
    if not os.path.isfile(mpath):
        sys.exit(f"No integration.toml in: {idir}")

    man = ingest.read_integration_toml(mpath)
    sessions = [] if args.clear else resolve_available(idir, man)

    dates = [
        ingest.SESSION_RE.match(s).group("date") for s in sessions if ingest.SESSION_RE.match(s)
    ]
    data_through = max(dates) if dates else None

    print(f"Integration : {os.path.basename(os.path.normpath(idir))}")
    print(
        f"Built       : {len(sessions)} session(s)"
        + (f", data through {data_through}" if data_through else "")
    )
    for s in sessions:
        print(f"  {s}")

    if not args.apply:
        print(
            "\nDRY RUN — nothing written. Re-run with --apply to record this, "
            "then run ingest.py."
        )
        return

    text = open(mpath, encoding="utf-8").read()
    new_text = apply_built(text, sessions)
    tmp = mpath + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(new_text)
    os.replace(tmp, mpath)
    print(f"\nRecorded in {mpath}\nRe-run ingest.py to update the tracker.")


if __name__ == "__main__":
    main()
