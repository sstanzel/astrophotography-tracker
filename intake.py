#!/usr/bin/env python3
"""intake.py — plan-first importer: CCC device dumps → staged session folders.

The pipeline step BEFORE preflight. Each capture device (ASIAir, NINA PC) is
cloned by Carbon Copy Cloner into an import area that is NEVER modified here —
intake only reads it. From those device trees intake groups science frames
into correctly-named session folders (`<Target_id> <Scope> <Sensor>
<YYYY-MM-DD>`), copies them into `_sessions to organize`, and stamps the
per-session templates. preflight.py stays the gate that files sessions into
the library.

Safety invariants:
  * The source tree is opened read-only everywhere; the only writes are
    `.part` files + renames under staging, and ledger rows after verification.
  * Every scanned file gets exactly one disposition; the census equation is
    printed every run and a nonzero remainder is a bug (exit 1).

All device / rig / naming specifics live in `_organization/intake.toml`
(template: templates/intake.example.toml) — none in this code.

Usage:
    python3 intake.py                    # plan only (default, read-only)
    python3 intake.py --show-config      # parse + print the resolved config
    python3 intake.py --config PATH      # alternate config (dev/test)
"""

import argparse
import datetime as dt
import os
import sys
import tomllib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import astro_config  # noqa: E402
import intake_scan  # noqa: E402

VALID_LAYOUTS = ("asiair", "nina")
VALID_HASHES = ("sha256", "sha1", "md5")
DEFAULT_LEDGER_NAME = "intake_ledger.db"

# A [[rig]] block with from/to bounds is "dated"; dated entries beat the
# open-ended entry for the same (source, camera) when the night is in range.
WILDCARD_CAMERA = "*"


# ==========================================================================
# Config loading
# ==========================================================================
def _as_date(value, where: str, errors: list[str]):
    """Coerce a TOML date or ISO string to datetime.date (None passes through)."""
    if value is None or isinstance(value, dt.date):
        return value
    try:
        return dt.date.fromisoformat(str(value))
    except ValueError:
        errors.append(f"{where}: {value!r} is not a date (use YYYY-MM-DD)")
        return None


def load_intake_config(path: str) -> dict:
    """Parse and validate intake.toml.

    Args:
        path: absolute path of the config file.

    Returns:
        {'settings': dict, 'sources': [dict], 'rigs': [dict]} — sources carry
        id/label/path/layout; rigs carry source/camera/scope/sensor/adjacent
        plus optional from/to dates.

    Raises:
        SystemExit: on a missing/unparseable file or any validation problem —
            every problem is listed, one line each, so one run fixes them all.
    """
    if not os.path.exists(path):
        raise SystemExit(
            f"Intake config not found:\n  {path}\n\n"
            f"Copy templates/intake.example.toml to _organization/intake.toml "
            f"and edit the [[source]] and [[rig]] blocks."
        )
    try:
        with open(path, "rb") as fh:
            raw = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise SystemExit(f"{path}: TOML syntax error: {exc}")

    errors: list[str] = []
    settings_raw = raw.get("intake", {})
    settings = {
        "staging": str(settings_raw.get("staging", "") or ""),
        "pxiproject_template": str(settings_raw.get("pxiproject_template", "") or ""),
        "hash": str(settings_raw.get("hash", "sha256") or "sha256").lower(),
        "copy_chn_logs": bool(settings_raw.get("copy_chn_logs", False)),
        "ledger": str(settings_raw.get("ledger", "") or ""),
    }
    if settings["hash"] not in VALID_HASHES:
        errors.append(f"[intake] hash {settings['hash']!r} not one of {VALID_HASHES}")

    sources: list[dict] = []
    seen_ids: set[str] = set()
    for i, b in enumerate(raw.get("source", [])):
        where = f"[[source]] #{i + 1}"
        sid = str(b.get("id", "") or "").strip()
        spath = str(b.get("path", "") or "").strip()
        layout = str(b.get("layout", "") or "").strip().lower()
        if not sid:
            errors.append(f"{where}: missing id")
        elif sid in seen_ids:
            errors.append(f"{where}: duplicate id {sid!r}")
        seen_ids.add(sid)
        if not spath:
            errors.append(f"{where} ({sid}): missing path")
        if layout not in VALID_LAYOUTS:
            errors.append(f"{where} ({sid}): layout {layout!r} not one of {VALID_LAYOUTS}")
        sources.append(
            {
                "id": sid,
                "label": str(b.get("label", "") or sid).strip(),
                "path": spath,
                "layout": layout,
            }
        )
    if not sources:
        errors.append("no [[source]] blocks — nothing to import from")

    rigs: list[dict] = []
    for i, b in enumerate(raw.get("rig", [])):
        where = f"[[rig]] #{i + 1}"
        source = str(b.get("source", "") or "").strip()
        camera = str(b.get("camera", "") or "").strip()
        scope = str(b.get("scope", "") or "").strip()
        sensor = str(b.get("sensor", "") or "").strip()
        if not source or (seen_ids and source not in seen_ids):
            errors.append(f"{where}: source {source!r} does not match any [[source]] id")
        for key, val in (("camera", camera), ("scope", scope), ("sensor", sensor)):
            if not val:
                errors.append(f"{where} ({source}/{camera or '?'}): missing {key}")
        # Scope/sensor become session-name tokens, which are whitespace-split.
        for key, val in (("scope", scope), ("sensor", sensor)):
            if " " in val:
                errors.append(f"{where} ({source}/{camera}): {key} {val!r} contains a space")
        rigs.append(
            {
                "source": source,
                "camera": camera,
                "scope": scope,
                "sensor": sensor,
                "adjacent": bool(b.get("adjacent", False)),
                "from": _as_date(b.get("from"), f"{where}: from", errors),
                "to": _as_date(b.get("to"), f"{where}: to", errors),
            }
        )

    _check_rig_conflicts(rigs, errors)
    if errors:
        raise SystemExit(f"{path}: {len(errors)} problem(s):\n  " + "\n  ".join(errors))
    return {"settings": settings, "sources": sources, "rigs": rigs}


def _rig_is_dated(rig: dict) -> bool:
    return rig["from"] is not None or rig["to"] is not None


def _check_rig_conflicts(rigs: list[dict], errors: list[str]) -> None:
    """Flag ambiguous [[rig]] sets: overlapping dated ranges or duplicate
    open-ended entries for the same (source, camera)."""
    by_key: dict[tuple[str, str], list[dict]] = {}
    for r in rigs:
        by_key.setdefault((r["source"], r["camera"]), []).append(r)
    for (source, camera), group in by_key.items():
        open_ended = [r for r in group if not _rig_is_dated(r)]
        if len(open_ended) > 1:
            errors.append(f"[[rig]] {source}/{camera}: more than one open-ended entry")
        dated = [r for r in group if _rig_is_dated(r)]
        for a_i, a in enumerate(dated):
            for b in dated[a_i + 1 :]:
                a_from = a["from"] or dt.date.min
                a_to = a["to"] or dt.date.max
                b_from = b["from"] or dt.date.min
                b_to = b["to"] or dt.date.max
                if a_from <= b_to and b_from <= a_to:
                    errors.append(f"[[rig]] {source}/{camera}: dated ranges overlap")


def resolve_rig(rigs: list[dict], source_id: str, camera: str, night: dt.date):
    """Pick the [[rig]] entry for a (source, camera token, civil night).

    Precedence: dated exact-camera → dated wildcard → open-ended exact →
    open-ended wildcard. Dated entries match only when the night is in range.

    Args:
        rigs: parsed [[rig]] blocks.
        source_id: the [[source]] id the frame came from.
        camera: the camera token exactly as parsed from the filename.
        night: the civil night being resolved.

    Returns:
        (rig, rule) — the winning entry and a human-readable one-liner naming
        the rule (shown in the plan so a wrong mapping is visible before
        --apply) — or (None, None) when no entry matches.
    """
    def in_range(r: dict) -> bool:
        return (r["from"] or dt.date.min) <= night <= (r["to"] or dt.date.max)

    tiers = (
        [r for r in rigs if r["source"] == source_id and r["camera"] == camera
         and _rig_is_dated(r) and in_range(r)],
        [r for r in rigs if r["source"] == source_id and r["camera"] == WILDCARD_CAMERA
         and _rig_is_dated(r) and in_range(r)],
        [r for r in rigs if r["source"] == source_id and r["camera"] == camera
         and not _rig_is_dated(r)],
        [r for r in rigs if r["source"] == source_id and r["camera"] == WILDCARD_CAMERA
         and not _rig_is_dated(r)],
    )
    for tier in tiers:
        if tier:
            r = tier[0]
            if _rig_is_dated(r):
                span = f"{r['from'] or '…'} → {r['to'] or '…'}"
                rule = f"dated rule {span}"
            else:
                rule = "open-ended rule"
            if r["camera"] == WILDCARD_CAMERA:
                rule += ", any-camera"
            return r, rule
    return None, None


# ==========================================================================
# Resolved paths
# ==========================================================================
def resolve_staging(settings: dict) -> str | None:
    """The staging directory: [intake] staging, or the working library's
    `_sessions to organize` (preflight's default)."""
    if settings["staging"]:
        return settings["staging"]
    from preflight import STAGING_DIRNAME  # local import: needs config.toml

    libs = astro_config.load_libraries()
    working = next(
        (lib for lib in libs if lib["role"] == "working" and os.path.isdir(lib["path"])), None
    )
    return os.path.join(working["path"], STAGING_DIRNAME) if working else None


def resolve_ledger(settings: dict) -> str:
    """The ledger DB path: [intake] ledger, or _organization/intake_ledger.db."""
    return settings["ledger"] or astro_config.org_path(DEFAULT_LEDGER_NAME)


# ==========================================================================
# --show-config
# ==========================================================================
def show_config(cfg: dict, config_path: str) -> None:
    """Print the resolved configuration: settings, sources, rig table."""
    settings = cfg["settings"]
    staging = resolve_staging(settings)
    template = settings["pxiproject_template"]

    print(f"Intake config : {config_path}")
    print(f"Staging       : {staging or 'NOT RESOLVED — no mounted working library'}")
    print(f"Ledger        : {resolve_ledger(settings)}")
    print(f"Hash          : {settings['hash']}")
    if template:
        state = "" if os.path.isdir(template) else "  (NOT FOUND — stamping will be skipped)"
        print(f"pxiproject    : {template}{state}")
    else:
        print("pxiproject    : not set — session projects will not be stamped")
    print(f"CHN logs      : {'copied' if settings['copy_chn_logs'] else 'skipped'}")

    print(f"\nSources ({len(cfg['sources'])}):")
    for s in cfg["sources"]:
        state = "mounted" if os.path.isdir(s["path"]) else "NOT MOUNTED"
        print(f"  [{s['id']}] {s['label']} — layout {s['layout']}, {state}")
        print(f"      {s['path']}")

    print(f"\nRig mappings ({len(cfg['rigs'])}):")
    for r in cfg["rigs"]:
        span = ""
        if _rig_is_dated(r):
            span = f"  ({r['from'] or '…'} → {r['to'] or '…'})"
        adj = "  [adjacent]" if r["adjacent"] else ""
        print(
            f"  {r['source']} / {r['camera']:<16} → {r['scope']} + {r['sensor']}{span}{adj}"
        )


# ==========================================================================
# --census
# ==========================================================================
def _gb(n_bytes: int) -> str:
    return f"{n_bytes / 1e9:,.1f} GB"


def _census_equation(scan: dict) -> tuple[str, int]:
    """The census invariant for one scanned source.

    Returns:
        (equation line, remainder) — remainder must be 0; anything else means
        a file got zero or two dispositions, which is a bug in the scanner.
    """
    parts = [
        (len(scan["science"]), "science"),
        (len(scan["logs"]), "logs"),
        (len(scan["non_science"]), "non-science"),
        (len(scan["ignored"]), "ignored"),
        (len(scan["junk"]), "junk"),
        (len(scan["quarantine"]), "quarantine"),
    ]
    remainder = scan["scanned"] - sum(n for n, _ in parts)
    eq = " + ".join(f"{n:,} {label}" for n, label in parts)
    return f"census {scan['scanned']:,} = {eq} · remainder {remainder}", remainder


def _grouped_counter(records: list[dict], key) -> list[tuple[str, int]]:
    """Count records by a key function, most-common first."""
    counts: dict[str, int] = {}
    for rec in records:
        counts[key(rec)] = counts.get(key(rec), 0) + 1
    return sorted(counts.items(), key=lambda kv: -kv[1])


def render_census(source: dict, scan: dict, verbose: bool) -> int:
    """Print one source's census block; return the equation remainder."""
    print(f"\n[{source['id']}] {source['label']} — layout {source['layout']}")
    print(f"  scanned {scan['scanned']:,} files · {_gb(scan['bytes'])}")

    science = scan["science"]
    if science:
        kinds = " · ".join(f"{k} {n:,}" for k, n in _grouped_counter(science, lambda r: r["kind"]))
        grammars = ", ".join(
            f"{g} {n:,}" for g, n in _grouped_counter(science, lambda r: r["grammar"])
        )
        cameras = ", ".join(
            f"{c} {n:,}" for c, n in _grouped_counter(science, lambda r: r["cam"])
        )
        nights = sorted({r["night"] for r in science})
        print(f"  science {len(science):,}: {kinds}")
        print(f"    grammars: {grammars}")
        print(f"    cameras : {cameras}")
        print(f"    nights  : {len(nights)} ({nights[0]} → {nights[-1]})")
    else:
        print("  science 0")

    print(
        f"  logs {len(scan['logs']):,} · non-science {len(scan['non_science']):,} · "
        f"ignored {len(scan['ignored']):,} · junk {len(scan['junk']):,} · "
        f"quarantine {len(scan['quarantine']):,}"
    )
    if scan["pruned_dirs"]:
        print(f"  pruned dirs (never entered): {', '.join(scan['pruned_dirs'])}")

    for rec in scan["quarantine"] if verbose else []:
        print(f"    quarantine: {rec['relpath']} — {rec.get('reason', '?')}")
    if scan["quarantine"] and not verbose:
        by_dir = _grouped_counter(
            scan["quarantine"], lambda r: os.path.dirname(r["relpath"]) or "."
        )
        for d, n in by_dir:
            example = next(
                os.path.basename(r["relpath"])
                for r in scan["quarantine"]
                if (os.path.dirname(r["relpath"]) or ".") == d
            )
            print(f"    quarantine: {d}/ — {n:,} file(s), e.g. {example}")
    if scan["ignored"]:
        by_ext = _grouped_counter(
            scan["ignored"], lambda r: os.path.splitext(r["relpath"])[1].lower() or "(none)"
        )
        detail = ", ".join(f"{ext} {n:,}" for ext, n in by_ext)
        print(f"    ignored by extension: {detail}")

    equation, remainder = _census_equation(scan)
    print(f"  {equation}")
    if remainder:
        print("  ERROR census remainder is not zero — scanner bug, do not trust this run")
    return remainder


def run_census(cfg: dict, args) -> None:
    """Scan every (selected, mounted) source and print the census."""
    sources = [s for s in cfg["sources"] if not args.source or s["id"] in args.source]
    if not sources:
        raise SystemExit(f"no source matches {args.source}")

    bad = 0
    for source in sources:
        if not os.path.isdir(source["path"]):
            print(f"\n[{source['id']}] {source['label']} — NOT MOUNTED, skipped")
            continue
        scan = intake_scan.scan_source(source, cfg["settings"])
        bad += 1 if render_census(source, scan, args.verbose) else 0
    print("\ncensus only — nothing was copied.")
    sys.exit(1 if bad else 0)


# ==========================================================================
# main
# ==========================================================================
def main() -> None:
    """Parse arguments and dispatch."""
    ap = argparse.ArgumentParser(
        description="Plan-first importer: CCC device dumps → staged session folders."
    )
    ap.add_argument(
        "--config",
        default=astro_config.org_path("intake.toml"),
        help="intake config file (default: _organization/intake.toml)",
    )
    ap.add_argument(
        "--show-config",
        action="store_true",
        help="print the resolved configuration and exit",
    )
    ap.add_argument(
        "--census",
        action="store_true",
        help="classification census of every source file (read-only, no grouping)",
    )
    ap.add_argument(
        "--source",
        action="append",
        default=[],
        help="limit to this source id (repeatable; default: all)",
    )
    ap.add_argument(
        "--verbose",
        action="store_true",
        help="per-file quarantine listing",
    )
    args = ap.parse_args()

    cfg = load_intake_config(args.config)
    if args.show_config:
        show_config(cfg, args.config)
        return
    if args.census:
        run_census(cfg, args)
        return

    ap.error("the plan mode arrives in milestone M2 — use --census or --show-config")


if __name__ == "__main__":
    main()
