"""
catalog.py - file WBPP-built calibration masters into their set folders.
(Formerly file_masters.py.)

WBPP (PixInsight) drops its output INSIDE the Bias/Dark set folder it was
pointed at:

    Dark/ASI585MCPro/-10C/Gain0/300s/Dark 2024-12-19/
        Dark_300.0s_..._0001.fit          <- the raws (untouched)
        master/masterDark_BIN-1_3840x2160_EXPOSURE-300.00s.xisf
        logs/20260712030638.log, ..._ProcessContainer.xpsm

The library convention is: the master lives NEXT TO the raws (never in a
subfolder - ingest's walker would see `master/` and `logs/` as phantom sets),
named so the file is self-describing anywhere it gets copied:

    masterBias_{Camera}_{gain###|ISO####}_{YYYY-MM-DD}.xisf
    masterDark_{Camera}_{exp}_{gain###|ISO####}_{temp}_{YYYY-MM-DD}.xisf
    e.g. masterDark_ASI585MCPro_300s_gain0_-10C_2024-12-19.xisf

Every naming token comes from the set's position in the library tree
(Bias/{Camera}/{Gain}/{Date} - Dark/{Camera}/{Temp}/{Gain}/{Exp}/{Set});
the date falls back to the newest frame timestamp for ASIAir-style set
folders that carry no date (Dark_300.0s_Bin1_..._-20.0C).

For each set holding WBPP output this script:
    1. moves the master out of `master/` to the set folder, renamed to the
       convention (a WBPP-named master already sitting loose in the set
       folder is renamed in place);
    2. deletes the emptied `master/` folder and the `logs/` folder - WBPP
       scratch, recreatable by re-running the stack.

Preview by default; --apply performs the moves/deletes. Safe to re-run:
sets whose master is already canonical produce no actions. Every --apply
appends what it did to `_organization/dev/actions.log` (see astro_config.
log_actions).

Usage:
    python3 catalog.py            # preview
    python3 catalog.py --apply
"""

import argparse
import os
import re
import shutil
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "internal"))
import astro_config  # noqa: E402

CAL_LIBRARY_NAME = "_Calibration Library"
CAL_CLASSES = ("Bias", "Dark")
MASTER_EXTS = (".xisf", ".fit", ".fits")
# WBPP scratch subfolders inside a set folder (never data):
SCRATCH_DIRS = ("master", "logs")
DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")
FRAME_STAMP_RE = re.compile(r"_(\d{4})(\d{2})(\d{2})-\d{6}_")


def is_master_file(name: str) -> bool:
    """True for a stacked master frame file (master* prefix, image extension)."""
    low = name.lower()
    return low.startswith("master") and low.endswith(MASTER_EXTS)


def gain_token(folder: str) -> str:
    """Normalize a gain folder name for the master filename.

    Args:
        folder: the gain-level folder name, e.g. 'Gain100' or 'ISO1600'.

    Returns:
        'gain100' for Gain### folders (lowercase g, matching the bias masters
        built 2026-07-11); ISO#### and anything else unchanged.
    """
    if folder.lower().startswith("gain"):
        return "gain" + folder[4:]
    return folder


def set_date(set_dir: str) -> str | None:
    """Best date for a set: from its folder name, else its newest frame stamp.

    ASIAir-style set folders (Dark_300.0s_Bin1_..._-20.0C) carry no date, but
    their frames do (..._20241205-072705_...). The newest frame dates the set,
    matching how a hand-named 'Dark YYYY-MM-DD' folder is dated.

    Args:
        set_dir: absolute path of the set folder.

    Returns:
        'YYYY-MM-DD', or None if neither the name nor any frame yields one.
    """
    m = DATE_RE.search(os.path.basename(set_dir))
    if m:
        return m.group(1)
    newest = None
    for name in os.listdir(set_dir):
        fm = FRAME_STAMP_RE.search(name)
        if fm:
            stamp = "-".join(fm.groups())
            newest = max(newest, stamp) if newest else stamp
    return newest


def canonical_name(cls: str, rel_parts: list[str], set_dir: str, ext: str) -> str | None:
    """Build the conventional master filename for a set.

    Args:
        cls: 'Bias' or 'Dark'.
        rel_parts: the set's path components below the class root
            (Bias: camera/gain/date - Dark: camera/temp/gain/exp/set).
        set_dir: absolute path of the set folder (for the date fallback).
        ext: extension of the master being filed, e.g. '.xisf'.

    Returns:
        The filename, or None when the tree shape or date can't be resolved.
    """
    date = set_date(set_dir)
    if date is None:
        return None
    if cls == "Bias" and len(rel_parts) == 3:
        camera, gain, _ = rel_parts
        return f"masterBias_{camera}_{gain_token(gain)}_{date}{ext}"
    if cls == "Dark" and len(rel_parts) == 5:
        camera, temp, gain, exp, _ = rel_parts
        return f"masterDark_{camera}_{exp}_{gain_token(gain)}_{temp}_{date}{ext}"
    return None


def find_set_actions(cls_root: str, cls: str) -> tuple[list[dict], list[str]]:
    """Walk one class tree and collect the filing actions for every set.

    Args:
        cls_root: absolute path of the Bias/ or Dark/ tree.
        cls: 'Bias' or 'Dark'.

    Returns:
        (actions, warnings). Each action dict has kind 'move' (src, dst),
        'rename' (src, dst) or 'rmdir' (path, n_files) - in execution order.
    """
    actions: list[dict] = []
    warnings: list[str] = []
    for dirpath, dirnames, filenames in os.walk(cls_root):
        dirnames[:] = [d for d in dirnames if not d.startswith((".", "!"))]
        has_scratch = any(d.lower() in SCRATCH_DIRS for d in dirnames)
        loose = [f for f in filenames if is_master_file(f)]
        if not has_scratch and not loose:
            continue
        rel_parts = os.path.relpath(dirpath, cls_root).split(os.sep)
        actions += set_actions(cls, rel_parts, dirpath, dirnames, loose, warnings)
        # Don't descend into scratch folders we're about to remove.
        dirnames[:] = [d for d in dirnames if d.lower() not in SCRATCH_DIRS]
    return actions, warnings


def set_actions(
    cls: str,
    rel_parts: list[str],
    set_dir: str,
    dirnames: list[str],
    loose: list[str],
    warnings: list[str],
) -> list[dict]:
    """Actions for one set folder: file the master, then sweep WBPP scratch."""
    actions: list[dict] = []
    rel = os.path.join(cls, *rel_parts)

    master_dir = next((d for d in dirnames if d.lower() == "master"), None)
    if master_dir:
        mdir = os.path.join(set_dir, master_dir)
        masters = [f for f in os.listdir(mdir) if is_master_file(f)]
        stragglers = [f for f in os.listdir(mdir) if not is_master_file(f) and f != ".DS_Store"]
        if len(masters) != 1 or stragglers:
            warnings.append(
                f"{rel}: master/ holds {len(masters)} masters"
                + (f" + extra files {stragglers}" if stragglers else "")
                + " - expected exactly one; skipped"
            )
        else:
            dst = canonical_name(cls, rel_parts, set_dir, os.path.splitext(masters[0])[1])
            if dst is None:
                warnings.append(f"{rel}: can't derive the conventional name; skipped")
            elif os.path.exists(os.path.join(set_dir, dst)):
                warnings.append(f"{rel}: {dst} already exists in the set folder; skipped")
            else:
                actions.append(
                    {"kind": "move", "src": os.path.join(mdir, masters[0]), "dst": dst, "rel": rel}
                )
                actions.append({"kind": "rmdir", "path": mdir, "rel": rel})

    for f in loose:
        dst = canonical_name(cls, rel_parts, set_dir, os.path.splitext(f)[1])
        if dst is None:
            warnings.append(f"{rel}: can't derive the conventional name for {f}; skipped")
        elif f != dst:
            if os.path.exists(os.path.join(set_dir, dst)):
                warnings.append(f"{rel}: both {f} and {dst} present; skipped rename")
            else:
                actions.append(
                    {"kind": "rename", "src": os.path.join(set_dir, f), "dst": dst, "rel": rel}
                )

    logs_dir = next((d for d in dirnames if d.lower() == "logs"), None)
    if logs_dir:
        lpath = os.path.join(set_dir, logs_dir)
        n = sum(len(files) for _, _, files in os.walk(lpath))
        actions.append({"kind": "rmdir", "path": lpath, "rel": rel, "n_files": n})
    return actions


def perform(action: dict) -> str:
    """Execute one action from find_set_actions and return its log line."""
    if action["kind"] == "move":  # out of master/ up into the set folder
        set_dir = os.path.dirname(os.path.dirname(action["src"]))
        dst = os.path.join(set_dir, action["dst"])
        shutil.move(action["src"], dst)
        return f"move '{action['src']}' → '{dst}'"
    if action["kind"] == "rename":  # already in the set folder
        dst = os.path.join(os.path.dirname(action["src"]), action["dst"])
        os.rename(action["src"], dst)
        return f"rename '{action['src']}' → '{dst}'"
    n = sum(len(files) for _, _, files in os.walk(action["path"]))
    shutil.rmtree(action["path"])
    return f"rmdir '{action['path']}' ({n} files)"


def describe(action: dict) -> str:
    """One preview/report line for an action.

    Arrows are U+2192, never ASCII '->': a pasted '->' line redirects in a
    shell and creates an empty file named like the master (2026-07-12 strays).
    """
    if action["kind"] == "move":
        return f"  file    {action['rel']}\n          master/{os.path.basename(action['src'])} → {action['dst']}"
    if action["kind"] == "rename":
        return f"  rename  {action['rel']}\n          {os.path.basename(action['src'])} → {action['dst']}"
    label = os.path.basename(action["path"])
    n = action.get("n_files")
    return f"  sweep   {action['rel']}  ({label}/" + (f", {n} scratch files)" if n else ")")


def main() -> int:
    """Preview or apply master filing across every mounted library."""
    ap = argparse.ArgumentParser(description="File WBPP masters into their Bias/Dark set folders.")
    ap.add_argument("--apply", action="store_true", help="perform the moves (default: preview)")
    args = ap.parse_args()

    all_actions: list[dict] = []
    all_warnings: list[str] = []
    for lib in astro_config.load_libraries():
        cal_root = os.path.join(lib["path"], CAL_LIBRARY_NAME)
        if not os.path.isdir(lib["path"]):
            print(f"[{lib['id']}] not mounted - skipped")
            continue
        if not os.path.isdir(cal_root):
            continue
        for cls in CAL_CLASSES:
            cls_root = os.path.join(cal_root, cls)
            if not os.path.isdir(cls_root):
                continue
            actions, warnings = find_set_actions(cls_root, cls)
            all_actions += actions
            all_warnings += warnings

    mode = "APPLY" if args.apply else "PREVIEW (use --apply to perform)"
    print(f"catalog - {mode}\n")
    if not all_actions and not all_warnings:
        print("Nothing to do: no WBPP output found in any Bias/Dark set.")
        return 0
    log_lines: list[str] = []
    for a in all_actions:
        print(describe(a))
        if args.apply:
            log_lines.append(perform(a))
    astro_config.log_actions("catalog", log_lines)
    for w in all_warnings:
        print(f"  WARN    {w}")
    moved = sum(1 for a in all_actions if a["kind"] in ("move", "rename"))
    swept = sum(1 for a in all_actions if a["kind"] == "rmdir")
    print(
        f"\n{moved} master(s) filed, {swept} scratch folder(s) "
        + ("removed." if args.apply else "to remove.")
    )
    if args.apply and moved:
        print("Re-run refresh.py so the tracker picks up the new masters.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
