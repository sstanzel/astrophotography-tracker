"""
fix_rotfirst_names.py - one-time rename of rot-first ASIAir frame names.

Between 2025-12-30 and 2026-03-05 (the window between the CAA joining the
rigs and a ZWO ASIAir firmware update) the ASIAir wrote the rotator angle
right after the type/target token:

    Light_M 81_63deg_180.0s_Bin1_585MC_gain200_20260226-213045_-20.0C_0012.fit

The reported angle jitters 1-2 degrees from plate-solve noise (and
legitimately jumps 180 at a meridian flip), so alphabetical order - the only
order PixInsight Blink offers - groups frames by angle and scrambles the
chronology. The post-update grammar puts the angle after the timestamp,
which sorts chronologically no matter how the angle wobbles:

    Light_M 81_180.0s_Bin1_585MC_gain200_20260226-213045_63deg_-20.0C_0012.fit

This script renames every rot-first frame (fits_parser grammars 8/9) to the
canonical grammar (1/2) - a pure token reorder; every value, including the
angle, is kept byte-identical (a 125<->305 pair is a real meridian flip, not
noise to normalize away). Scope: ~52 sessions / ~4,800 files on Stream+Peak.

Preview by default; --apply performs the renames and appends each one to
_organization/dev/actions.log. Idempotent: canonical names don't match the
rot-first grammars, so a re-run finds nothing. fits_parser keeps grammars
8/9 either way, as a safety net for anything this pass never saw.

After --apply, run refresh.py (ingest re-inserts each session's frames from
scratch, so the renamed paths simply replace the old rows).

Usage:
    python3 fix_rotfirst_names.py              # preview, per-folder counts
    python3 fix_rotfirst_names.py --verbose    # preview, every rename
    python3 fix_rotfirst_names.py --apply
"""

import argparse
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "internal"))
import astro_config  # noqa: E402
import fits_parser  # noqa: E402

# Recreatable/derived containers - never hold raw frames worth renaming.
SCRATCH_DIRS = ("pi process", "pi magic")
# Top-level `_`-prefixed folders are skipped (ingest convention), except the
# calibration library: its Dark sets from the epoch can carry rot-first names.
TOPLEVEL_KEEP = ("_Calibration Library",)

CANONICAL_GRAMMARS = (fits_parser.ASIAIR_SCI, fits_parser.ASIAIR_CAL)


def canonical_name(name: str) -> str | None:
    """Return the canonical (timestamp-first) spelling of a rot-first name.

    Matches the filename against fits_parser's rot-first grammars (8/9) and
    rebuilds it with the {rot}deg token moved to after the timestamp - the
    post-update ASIAir grammar. Every token value is reused byte-for-byte.

    Args:
        name: a frame filename (basename, not a path).

    Returns:
        The new filename, or None when the name is not rot-first or the
        rebuilt name fails to round-trip through the canonical grammar.
    """
    m = fits_parser.ASIAIR_SCI_ROTFIRST.match(name) or fits_parser.ASIAIR_CAL_ROTFIRST.match(name)
    if not m:
        return None
    g = m.groupdict()
    target = f"{g['target']}_" if g.get("target") else ""
    filt = f"_{g['filter']}" if g.get("filter") else ""
    new = (
        f"{g['type']}_{target}{g['exp']}{g['unit']}_Bin{g['bin']}_{g['cam']}_"
        f"gain{g['gain']}_{g['dt']}_{g['rot']}deg_{g['temp']}C{filt}_{g['idx']}.{g['ext']}"
    )
    if not _round_trips(g, new):
        return None
    return new


def _round_trips(old_groups: dict, new: str) -> bool:
    """True when the rebuilt name parses canonically with identical tokens."""
    m2 = fits_parser.parse(new)
    if m2 is None or m2.re not in CANONICAL_GRAMMARS:
        return False
    g2 = m2.groupdict()
    return all(g2.get(k) == old_groups.get(k) for k in old_groups)


def _keep_dir(name: str, at_top_level: bool) -> bool:
    """Whether the walker should descend into a directory."""
    if name.startswith("."):
        return False
    low = name.lower()
    if low in SCRATCH_DIRS or low == "results" or low.endswith(" results"):
        return False
    if at_top_level and name.startswith("_"):
        return name in TOPLEVEL_KEEP
    return True


def collect(root: str) -> tuple[list[dict], list[str]]:
    """Walk one library and collect every rot-first rename.

    Args:
        root: absolute path of a capture-library root.

    Returns:
        (actions, warnings). Each action dict has src/dst (absolute paths)
        and rel_dir (the containing folder, relative to root, for display).
    """
    actions: list[dict] = []
    warnings: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        at_top = dirpath == root
        dirnames[:] = sorted(d for d in dirnames if _keep_dir(d, at_top))
        rel_dir = os.path.relpath(dirpath, root)
        for name in sorted(filenames):
            new = canonical_name(name)
            if new is None:
                continue
            if new in filenames or os.path.exists(os.path.join(dirpath, new)):
                warnings.append(f"{rel_dir}: {new} already exists; skipped {name}")
                continue
            actions.append(
                {
                    "src": os.path.join(dirpath, name),
                    "dst": os.path.join(dirpath, new),
                    "rel_dir": rel_dir,
                }
            )
    return actions, warnings


def describe(actions: list[dict], verbose: bool) -> None:
    """Print the collected renames, grouped per containing folder.

    Arrows are U+2192, never ASCII '->': a pasted '->' line redirects in a
    shell and creates an empty stray file (the 2026-07-12 incident).
    """
    counts = Counter(a["rel_dir"] for a in actions)
    last_dir = None
    for a in actions:
        if a["rel_dir"] != last_dir:
            print(f"  {a['rel_dir']}  ({counts[a['rel_dir']]} file(s))")
            last_dir = a["rel_dir"]
        if verbose:
            print(f"          {os.path.basename(a['src'])} → {os.path.basename(a['dst'])}")


def main() -> int:
    """Preview or apply the rot-first rename across every mounted library."""
    ap = argparse.ArgumentParser(
        description="Rename rot-first ASIAir frames to the timestamp-first grammar."
    )
    ap.add_argument("--apply", action="store_true", help="perform the renames (default: preview)")
    ap.add_argument("--verbose", action="store_true", help="print every rename, not just counts")
    args = ap.parse_args()

    all_actions: list[dict] = []
    all_warnings: list[str] = []
    for lib in astro_config.load_libraries():
        if not os.path.isdir(lib["path"]):
            print(f"[{lib['id']}] not mounted - skipped")
            continue
        actions, warnings = collect(lib["path"])
        all_actions += actions
        all_warnings += warnings

    mode = "APPLY" if args.apply else "PREVIEW (use --apply to perform)"
    print(f"fix_rotfirst_names - {mode}\n")
    if not all_actions and not all_warnings:
        print("Nothing to do: no rot-first frame names found.")
        return 0

    describe(all_actions, args.verbose)
    log_lines: list[str] = []
    for a in all_actions:
        if args.apply:
            os.rename(a["src"], a["dst"])
            log_lines.append(f"rename '{a['src']}' → '{a['dst']}'")
    astro_config.log_actions("fix_rotfirst_names", log_lines)

    for w in all_warnings:
        print(f"  WARN    {w}")
    folders = len({a["rel_dir"] for a in all_actions})
    print(
        f"\n{len(all_actions)} file(s) "
        + ("renamed" if args.apply else "to rename")
        + f" across {folders} folder(s)."
    )
    if args.apply and all_actions:
        print("Re-run refresh.py so the tracker picks up the new names.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
