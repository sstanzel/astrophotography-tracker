#!/usr/bin/env python3
"""
preflight.py — validate staged session folders, then (optionally) file them.

Read-only by default: never touches tracker.db or the staging folder. With
--apply it FILES the passing sessions into the library — creating the target
folder from the registry when the library doesn't have it yet. The registry's
`target folders/` is the single source of truth for target names: libraries
carry no empty target folders; a folder appears in a library the first time a
session for it is filed.

Reuses the exact grammars scan.py applies (SESSION_RE, parse_target_folder,
fits_parser.parse), so a clean pre-flight means ingest will parse the session
the same way after the move.

Checks per staged folder:
  ERROR  name does not parse as `<Target_id> <Scope> <Sensor> <YYYY-MM-DD>`
  ERROR  target token matches neither a library target folder nor a registry entry
  ERROR  destination target folder already has a same-named session (collision)
  WARN   scope / sensor / scope+sensor combo not in the _organization registry
  WARN   no kept light frames found
  WARN   frame filenames that parse to a different target than the folder name
  WARN   frame capture dates more than ±1 day from the session date
  INFO   unparsed FITS filenames, ms-unit lights (excluded from integration)

Usage:
    python3 preflight.py                  # staging + library from config.toml
    python3 preflight.py --staging PATH --library PATH
    python3 preflight.py --apply          # file the OK sessions
    python3 preflight.py --apply --force  # ...including the WARN ones
"""

import argparse
import datetime as dt
import os
import shutil
import sys
from collections import Counter

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "internal"))
import astro_config  # noqa: E402
from scan import SESSION_RE, parse_target_folder, walk_fits  # noqa: E402
from fits_parser import frame_kind, safe, exposure_seconds, is_non_science  # noqa: E402

# Session date is the local civil evening; UTC frame stamps can roll past
# midnight, so anything within one day of the folder date is normal.
MAX_FRAME_DATE_SKEW_DAYS = 1

STAGING_DIRNAME = "_sessions to organize"

# Second-rig sessions shot on a field NEXT TO the target carry this suffix
# (e.g. "M_12_adjacent Redcat51 minicam8 2026-07-06") until both scopes are
# co-aligned on the mount. They file under the BASE target's folder: ingest
# keys sessions to the parent target folder, and a separate "M 12 adjacent"
# folder would parse to target_id M_12 and collide with the real target.
ADJACENT_SUFFIX = "_adjacent"


def registry_names(subfolder: str) -> set[str]:
    """Return the controlled-vocabulary names under an _organization folder.

    Args:
        subfolder: registry folder name, e.g. "scope_values".

    Returns:
        Set of directory names (the names ARE the data), excluding the
        "."/"!"-prefixed entries scan.py also excludes.
    """
    path = astro_config.org_path(subfolder)
    if not os.path.isdir(path):
        return set()
    return {
        d
        for d in os.listdir(path)
        if not d.startswith((".", "!")) and os.path.isdir(os.path.join(path, d))
    }


def library_target_map(library_root: str) -> dict[str, str]:
    """Map target_id -> target folder name for a capture library root.

    Args:
        library_root: absolute path of the library (parent of target folders).

    Returns:
        Dict like {"M_81": "M 81 Bodes Galaxy", ...} using the same
        parse_target_folder() scan.py uses.
    """
    out: dict[str, str] = {}
    for name in sorted(os.listdir(library_root)):
        if name.startswith((".", "_")) or not os.path.isdir(os.path.join(library_root, name)):
            continue
        out[parse_target_folder(name)["target_id"]] = name
    return out


def check_session(
    spath: str,
    sname: str,
    targets: dict[str, str],
    reg_targets: dict[str, str],
    scopes: set[str],
    sensors: set[str],
    combos: set[str],
    library_root: str,
) -> tuple[list[str], list[str], list[str], str | None]:
    """Run every pre-flight check against one staged session folder.

    Args:
        spath: absolute path of the staged session folder.
        sname: folder basename.
        targets: target_id -> folder name map for the destination library.
        reg_targets: target_id -> folder name map for the registry
            (`_organization/target folders/`) — the single source of truth;
            used when the library has no folder for the target yet.
        scopes/sensors/combos: registry vocabularies.
        library_root: destination library root (for collision check).

    Returns:
        (errors, warnings, infos, dest_folder) — findings plus the target
        folder name the session files into (None when the name is unusable).
    """
    errors: list[str] = []
    warnings: list[str] = []
    infos: list[str] = []
    dest: str | None = None

    m = SESSION_RE.match(sname)
    if not m:
        errors.append("name does not parse as `<Target_id> <Scope> <Sensor> <YYYY-MM-DD>`")
        return errors, warnings, infos, dest

    target_tok, scope, sensor, sdate = (
        m.group("target"),
        m.group("scope"),
        m.group("sensor"),
        m.group("date"),
    )
    try:
        session_date = dt.date.fromisoformat(sdate)
    except ValueError:
        errors.append(f"date token {sdate!r} is not a real calendar date")
        return errors, warnings, infos, dest

    # -- destination target folder ------------------------------------------
    # Adjacent-field sessions resolve to the BASE target's folder (see
    # ADJACENT_SUFFIX note above). The library folder wins when it exists;
    # otherwise the registry names the folder to create at filing time.
    lookup_tok = target_tok
    if target_tok.lower().endswith(ADJACENT_SUFFIX):
        lookup_tok = target_tok[: -len(ADJACENT_SUFFIX)]
    suffix_note = " (adjacent-field session → base target)" if lookup_tok != target_tok else ""
    tfolder = targets.get(lookup_tok)
    if tfolder is not None:
        dest = tfolder
        infos.append(f"destination: {tfolder}/{suffix_note}")
        if os.path.isdir(os.path.join(library_root, tfolder, sname)):
            errors.append(f"collision: {tfolder}/{sname} already exists in the library")
    elif lookup_tok in reg_targets:
        dest = reg_targets[lookup_tok]
        infos.append(f"destination: {dest}/ (created from the registry){suffix_note}")
    else:
        errors.append(
            f"target token {lookup_tok!r} matches neither a library "
            f"target folder nor a registry entry"
        )

    # -- registry vocabularies ----------------------------------------------
    if scope not in scopes:
        warnings.append(f"scope {scope!r} not in registry scope_values/")
    if sensor not in sensors:
        warnings.append(f"sensor {sensor!r} not in registry sensor_values/")
    if f"{scope}_{sensor}" not in combos:
        warnings.append(f"combo {scope}_{sensor} not in registry scope+sensor_values/")

    # -- frames ---------------------------------------------------------------
    kinds: Counter[str] = Counter()
    unparsed: list[str] = []
    ms_lights = 0
    light_secs = 0.0
    rejected = 0
    frame_targets: Counter[str] = Counter()
    date_skew: Counter[str] = Counter()

    for fpath, is_rej, fm in walk_fits(spath):
        fname = os.path.basename(fpath)
        if fm is None:
            if not is_non_science(fname):
                unparsed.append(fname)
            continue
        kind = frame_kind(fm)
        kinds[kind] += 1
        if is_rej:
            rejected += 1
        if kind == "light":
            secs = exposure_seconds(fm)
            if secs is None:
                ms_lights += 1
            elif not is_rej:
                light_secs += secs
            ft = safe(fm, "target")
            if ft:
                frame_targets[ft] += 1
        fdt = safe(fm, "dt") or safe(fm, "date")
        if fdt:
            try:
                fdate = dt.datetime.strptime(fdt[:8], "%Y%m%d").date()
                if abs((fdate - session_date).days) > MAX_FRAME_DATE_SKEW_DAYS:
                    date_skew[str(fdate)] += 1
            except ValueError:
                pass

    kept_lights = kinds.get("light", 0) - rejected
    if kinds.get("light", 0) == 0:
        warnings.append("no light frames found")
    else:
        infos.append(
            f"frames: {dict(kinds)} | rejected: {rejected} | "
            f"kept integration: {light_secs / 3600:.2f} h"
        )

    for ft, n in frame_targets.items():
        if ft.replace(" ", "_") != target_tok:
            warnings.append(f"{n} light frame(s) name target {ft!r} ≠ folder target {target_tok!r}")
    if date_skew:
        warnings.append(
            f"frame dates >±{MAX_FRAME_DATE_SKEW_DAYS}d from session date: " f"{dict(date_skew)}"
        )
    if ms_lights:
        infos.append(f"{ms_lights} ms-unit light(s) — excluded from integration totals")
    if unparsed:
        infos.append(f"{len(unparsed)} unparsed FITS filename(s), e.g. {unparsed[0]!r}")

    return errors, warnings, infos, dest


def main() -> None:
    """Parse arguments, run pre-flight over the staging folder, print a report."""
    libs = astro_config.load_libraries()
    working = next((l for l in libs if l["role"] == "working" and os.path.isdir(l["path"])), None)
    default_library = working["path"] if working else None
    default_staging = os.path.join(default_library, STAGING_DIRNAME) if default_library else None

    ap = argparse.ArgumentParser(
        description="Validate staged sessions before moving " "them into a capture library."
    )
    ap.add_argument(
        "--staging",
        default=default_staging,
        help=f"staged-sessions folder (default: <first mounted working library>/{STAGING_DIRNAME})",
    )
    ap.add_argument(
        "--library",
        default=default_library,
        help="destination library root (default: the first mounted working library)",
    )
    ap.add_argument(
        "--apply",
        action="store_true",
        help="file the passing sessions into the library (creates "
        "the target folder from the registry when missing)",
    )
    ap.add_argument(
        "--force",
        action="store_true",
        help="with --apply: also file sessions that only have warnings",
    )
    args = ap.parse_args()

    if not args.staging or not os.path.isdir(args.staging):
        sys.exit(f"Staging folder not found: {args.staging}")
    if not args.library or not os.path.isdir(args.library):
        sys.exit(f"Destination library not found: {args.library}")

    targets = library_target_map(args.library)
    reg_targets = {
        parse_target_folder(name)["target_id"]: name for name in registry_names("target folders")
    }
    scopes = registry_names("scope_values")
    sensors = registry_names("sensor_values")
    combos = registry_names("scope+sensor_values")

    print(f"Pre-flight: {args.staging}")
    print(f"Library   : {args.library}")
    print(
        f"Registry  : {len(scopes)} scopes, {len(sensors)} sensors, {len(combos)} combos, "
        f"{len(targets)} library targets\n"
    )

    n_err = n_warn = n_filed = 0
    log_lines: list[str] = []
    entries = sorted(
        d
        for d in os.listdir(args.staging)
        if not d.startswith(".") and os.path.isdir(os.path.join(args.staging, d))
    )
    for sname in entries:
        spath = os.path.join(args.staging, sname)
        errors, warnings, infos, dest = check_session(
            spath, sname, targets, reg_targets, scopes, sensors, combos, args.library
        )
        verdict = "FAIL" if errors else ("WARN" if warnings else "OK")
        print(f"[{verdict}] {sname}")
        for e in errors:
            print(f"    ERROR {e}")
        for w in warnings:
            print(f"    WARN  {w}")
        for i in infos:
            print(f"    info  {i}")
        n_err += len(errors)
        n_warn += len(warnings)

        if args.apply and dest and not errors and (not warnings or args.force):
            tdir = os.path.join(args.library, dest)
            if not os.path.isdir(tdir):
                os.makedirs(tdir)
                log_lines.append(f"mkdir '{tdir}'")
            shutil.move(spath, os.path.join(tdir, sname))
            log_lines.append(f"move '{spath}' → '{os.path.join(tdir, sname)}'")
            targets[parse_target_folder(dest)["target_id"]] = dest
            n_filed += 1
            print(f"    FILED → {dest}/{sname}")

    astro_config.log_actions("preflight", log_lines)
    summary = f"\n{len(entries)} folder(s): {n_err} error(s), {n_warn} warning(s)"
    if args.apply:
        summary += f", {n_filed} filed"
        if n_filed:
            summary += " — run refresh.py to pick them up"
    print(summary)
    sys.exit(1 if n_err else 0)


if __name__ == "__main__":
    main()
