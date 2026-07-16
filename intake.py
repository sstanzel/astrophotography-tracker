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
import hashlib
import os
import shutil
import sys
import tomllib

# preflight/ingest are imported lazily from the top level; the shared modules
# (astro_config, intake_scan, intake_ledger, fits_parser) live in internal/.
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(_HERE, "internal"))
import astro_config  # noqa: E402
import intake_ledger  # noqa: E402
import intake_scan  # noqa: E402

from intake_scan import (  # noqa: E402
    calibration_sets,
    flat_folder_name,
    group_sessions,
    resolve_rig,  # noqa: F401  (re-exported for tests and callers)
    rig_is_dated,
    LIGHT_SUBDIR,
    FLAT_SUBDIR,
    DARKFLAT_SUBDIR,
    LOG_SUBDIR,
)

VALID_LAYOUTS = ("asiair", "nina")
VALID_HASHES = ("sha256", "sha1", "md5")
DEFAULT_LEDGER_NAME = "intake_ledger.db"


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

    ignores: list[dict] = []
    for i, b in enumerate(raw.get("ignore", [])):
        where = f"[[ignore]] #{i + 1}"
        entry = {
            "source": str(b.get("source", "") or "").strip(),
            "target": str(b.get("target", "") or "").strip(),
            "night": _as_date(b.get("night"), f"{where}: night", errors),
        }
        if not (entry["source"] or entry["target"] or entry["night"]):
            errors.append(f"{where}: needs at least one of source/target/night")
        if entry["source"] and seen_ids and entry["source"] not in seen_ids:
            errors.append(f"{where}: source {entry['source']!r} does not match any [[source]] id")
        ignores.append(entry)

    _check_rig_conflicts(rigs, errors)
    if errors:
        raise SystemExit(f"{path}: {len(errors)} problem(s):\n  " + "\n  ".join(errors))
    return {"settings": settings, "sources": sources, "rigs": rigs, "ignores": ignores}


def ignore_match(ignores: list[dict], session: dict) -> dict | None:
    """The first [[ignore]] block matching a planned session, or None.

    All keys present in a block must match (AND); target compares against the
    session's normalized target id, case-insensitive, underscores or spaces.
    """
    sess_target = session["name"].split(" ")[0].lower()
    for ig in ignores:
        if ig["source"] and ig["source"] != session["source"]:
            continue
        if ig["target"] and ig["target"].replace(" ", "_").lower() != sess_target:
            continue
        if ig["night"] and ig["night"] != session["night"]:
            continue
        return ig
    return None


def _check_rig_conflicts(rigs: list[dict], errors: list[str]) -> None:
    """Flag ambiguous [[rig]] sets: overlapping dated ranges or duplicate
    open-ended entries for the same (source, camera)."""
    by_key: dict[tuple[str, str], list[dict]] = {}
    for r in rigs:
        by_key.setdefault((r["source"], r["camera"]), []).append(r)
    for (source, camera), group in by_key.items():
        open_ended = [r for r in group if not rig_is_dated(r)]
        if len(open_ended) > 1:
            errors.append(f"[[rig]] {source}/{camera}: more than one open-ended entry")
        dated = [r for r in group if rig_is_dated(r)]
        for a_i, a in enumerate(dated):
            for b in dated[a_i + 1 :]:
                a_from = a["from"] or dt.date.min
                a_to = a["to"] or dt.date.max
                b_from = b["from"] or dt.date.min
                b_to = b["to"] or dt.date.max
                if a_from <= b_to and b_from <= a_to:
                    errors.append(f"[[rig]] {source}/{camera}: dated ranges overlap")


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
        if rig_is_dated(r):
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
# Library dedupe index + preflight projection
# ==========================================================================
def build_library_index() -> dict:
    """Index every session folder in every mounted configured library.

    A projected session that already exists anywhere is `already in library`
    (never copied); a same-target same-night session under a DIFFERENT name
    (rig renamed, hand-named differently) is surfaced for review instead of
    silently duplicated.

    Returns:
        {'names': {session name → absolute path},
         'by_target_night': {(base target_id, date) → [session names]},
         'libraries': [(label, path, mounted)]}
    """
    from scan import SESSION_RE, parse_target_folder  # noqa: F401
    from preflight import ADJACENT_SUFFIX

    names: dict[str, str] = {}
    by_tn: dict[tuple, list[str]] = {}
    libraries: list[tuple[str, str, bool]] = []
    for lib in astro_config.load_libraries():
        mounted = os.path.isdir(lib["path"])
        libraries.append((lib["label"], lib["path"], mounted))
        if not mounted:
            continue
        for tf in sorted(os.listdir(lib["path"])):
            tfpath = os.path.join(lib["path"], tf)
            if tf.startswith((".", "_")) or not os.path.isdir(tfpath):
                continue
            for entry in sorted(os.listdir(tfpath)):
                m = SESSION_RE.match(entry)
                if not m or not os.path.isdir(os.path.join(tfpath, entry)):
                    continue
                names[entry] = os.path.join(tfpath, entry)
                base = m.group("target")
                if base.lower().endswith(ADJACENT_SUFFIX):
                    base = base[: -len(ADJACENT_SUFFIX)]
                key = (base, m.group("date"))
                by_tn.setdefault(key, []).append(entry)
    return {"names": names, "by_target_night": by_tn, "libraries": libraries}


# Recreatable working folders inside a session — copies of frames in there
# (PI Magic's Discarded/, PixInsight scratch) must not inflate the dedupe
# light count. Same concern as audit.py's SCRATCH_FRAME check.
SCRATCH_DIR_MARKERS = ("PI Process", "PI Magic")
SCRATCH_DIR_SUFFIXES = (".pxiproject", " Results")


def count_library_lights(session_path: str) -> int:
    """Light frames (kept + Rejected) in an existing library session,
    excluding processing-scratch and results folders."""
    from fits_parser import frame_kind, parse

    n = 0
    for root, dirs, files in os.walk(session_path):
        dirs[:] = [
            d
            for d in dirs
            if d not in SCRATCH_DIR_MARKERS and not d.endswith(SCRATCH_DIR_SUFFIXES)
        ]
        for f in files:
            if f.startswith("._") or not f.lower().endswith((".fit", ".fits", ".xisf")):
                continue
            m = parse(f)
            if m is not None and frame_kind(m) == "light":
                n += 1
    return n


def load_registry_vocab() -> dict:
    """Registry vocabularies used by the projected preflight verdict."""
    from scan import parse_target_folder
    from preflight import registry_names

    return {
        "targets": {
            parse_target_folder(name)["target_id"]: name
            for name in registry_names("target folders")
        },
        "scopes": registry_names("scope_values"),
        "sensors": registry_names("sensor_values"),
        "combos": registry_names("scope+sensor_values"),
    }


def projected_preflight(session: dict, vocab: dict, staged_names: set[str]) -> tuple[str, list[str]]:
    """The disk-independent subset of preflight's checks for a PLANNED session.

    (The full check_session() needs the folder on disk; --apply runs the real
    thing after copying. This projection covers name grammar, registry
    membership and staging collisions so problems show before any copy.)

    Returns:
        (verdict 'ok'|'warning'|'fail', reason lines)
    """
    from scan import SESSION_RE
    from preflight import ADJACENT_SUFFIX

    reasons: list[str] = []
    verdict = "ok"
    m = SESSION_RE.match(session["name"])
    if not m:  # unreachable by construction; a fail here is an intake bug
        return "fail", [f"name does not parse: {session['name']!r}"]

    base = m.group("target")
    if base.lower().endswith(ADJACENT_SUFFIX):
        base = base[: -len(ADJACENT_SUFFIX)]
    dest = vocab["targets"].get(base)
    if dest:
        reasons.append(f"destination: {dest}/")
    else:
        verdict = "fail"
        line = f"target {base!r} not in the registry target folders/"
        import difflib

        close = difflib.get_close_matches(base, vocab["targets"], n=1, cutoff=0.75)
        if close:
            line += f" — did you mean {close[0]!r}?"
        reasons.append(line)

    rig = session["rig"]
    for value, vocab_key, label in (
        (rig["scope"], "scopes", "scope_values"),
        (rig["sensor"], "sensors", "sensor_values"),
        (f"{rig['scope']}_{rig['sensor']}", "combos", "scope+sensor_values"),
    ):
        if value not in vocab[vocab_key]:
            verdict = "warning" if verdict == "ok" else verdict
            reasons.append(f"{value!r} not in registry {label}/")

    if session["name"] in staged_names:
        verdict = "fail"
        reasons.append("a folder with this name is already sitting in staging")
    return verdict, reasons


# ==========================================================================
# Copy protocol — .part, hash-verify, atomic rename (see USAGE.md)
# ==========================================================================
COPY_CHUNK_BYTES = 1024 * 1024


def copy_verified(src: str, dest: str, hash_name: str) -> tuple[str, str | None]:
    """Copy one file with verification; never leaves a partial dest.

    Streams src → dest+'.part' hashing the source read, fsyncs, re-stats the
    source (a change means something wrote it mid-copy — the .part is deleted
    and the file stays new for the next run), re-reads the .part to verify the
    bytes that landed on the destination volume, then atomically renames.

    Args:
        src: absolute source path (opened read-only).
        dest: absolute final destination (must not exist).
        hash_name: digest name from [intake] hash.

    Returns:
        ('ok', hexdigest) on success; ('source-changed', None) or
        ('verify-failed', None) with the .part removed otherwise.
    """
    st0 = os.stat(src)
    part = dest + ".part"
    h = hashlib.new(hash_name)
    with open(src, "rb") as fin, open(part, "wb") as fout:
        while chunk := fin.read(COPY_CHUNK_BYTES):
            h.update(chunk)
            fout.write(chunk)
        fout.flush()
        os.fsync(fout.fileno())
    st1 = os.stat(src)
    if (st1.st_size, st1.st_mtime_ns) != (st0.st_size, st0.st_mtime_ns):
        os.remove(part)
        return "source-changed", None
    h2 = hashlib.new(hash_name)
    with open(part, "rb") as fin:
        while chunk := fin.read(COPY_CHUNK_BYTES):
            h2.update(chunk)
    if h2.hexdigest() != h.hexdigest():
        os.remove(part)
        return "verify-failed", None
    os.rename(part, dest)
    os.utime(dest, ns=(st0.st_atime_ns, st0.st_mtime_ns))
    try:  # best-effort directory durability (SMB volumes may refuse)
        dfd = os.open(os.path.dirname(dest), os.O_RDONLY)
        os.fsync(dfd)
        os.close(dfd)
    except OSError:
        pass
    return "ok", h.hexdigest()


# ==========================================================================
# Plan decisions (shared by the plan display and --apply)
# ==========================================================================
def _session_groups(session: dict) -> list[tuple[str, list[dict], str]]:
    """(label, records, dest subdir) for each mapping group of a session."""
    flat_dir = flat_folder_name(session["rig"], session["night"])
    return [
        ("lights", session["lights"], LIGHT_SUBDIR),
        ("flats", session["flats"], f"{flat_dir}/{FLAT_SUBDIR}"),
        ("dark flats", session["darkflats"], f"{flat_dir}/{DARKFLAT_SUBDIR}"),
        ("logs", session["logs"], LOG_SUBDIR),
    ]


def _filed_path(lib_index: dict, session_name: str, dest_relpath: str) -> str | None:
    """Where a ledger row's file would live if preflight already filed its
    session into a library — or None when the session isn't filed."""
    lib_session = lib_index["names"].get(session_name)
    if not lib_session:
        return None
    inner = dest_relpath.split("/", 1)
    return os.path.join(lib_session, inner[1]) if len(inner) == 2 else None


def decide(cfg: dict, args, scans: dict[str, dict]) -> dict:
    """Annotate every planned session and file with its copy decision.

    Read-only. Decisions per file of a NEW session: 'copy' (fresh, or changed
    at source with a free destination, or --reimport of a missing dest),
    'skip' (verified copy already in staging or filed into the library), or
    'hold' (needs a human: destination occupied by an unledgered file, or a
    previously-imported copy vanished and --reimport wasn't given).

    Returns:
        The full plan context consumed by render_plan()/run_apply().
    """
    settings = cfg["settings"]
    staging = resolve_staging(settings)
    ledger_path = resolve_ledger(settings)
    con = intake_ledger.open_ledger(ledger_path)
    interrupted = intake_ledger.relabel_stale_runs(con)

    lib_index = build_library_index()
    vocab = load_registry_vocab()
    since = dt.date.fromisoformat(args.since) if args.since else None
    nights = {dt.date.fromisoformat(n) for n in args.night} if args.night else None
    plan = group_sessions(scans, cfg["rigs"], since=since, nights=nights)

    attention: list[str] = []
    known_by_source = {sid: intake_ledger.known_files(con, sid) for sid in scans}

    for sess in plan["sessions"]:
        sess["files"] = []
        sess["status_note"] = ""
        if ignore_match(cfg["ignores"], sess):
            sess["status"] = "ignored"
            continue

        existing = lib_index["names"].get(sess["name"])
        if existing:
            sess["status"] = "already"
            lib_lights = count_library_lights(existing)
            if lib_lights == len(sess["lights"]):
                sess["status_note"] = f"already in library — counts match ({lib_lights} lights)"
            else:
                sess["status_note"] = (
                    f"already in library — count mismatch: source {len(sess['lights'])} "
                    f"lights, library {lib_lights}"
                )
                attention.append(f"{sess['name']}: {sess['status_note']}")
            continue

        sess["status"] = "new"
        known = known_by_source[sess["source"]]
        for label, records, dest_sub in _session_groups(sess):
            for rec in records:
                dest_rel = f"{sess['name']}/{dest_sub}/{os.path.basename(rec['relpath'])}"
                dest_abs = os.path.join(staging, dest_rel)
                row = known.get(rec["relpath"])
                decision, note = "copy", ""
                if row and (row["size"], row["mtime_ns"]) == (rec["size"], rec["mtime_ns"]):
                    filed = _filed_path(lib_index, row["session"], row["dest_relpath"])
                    if os.path.exists(os.path.join(staging, row["dest_relpath"])):
                        decision = "skip"
                    elif filed and os.path.exists(filed):
                        decision, note = "skip", "filed"
                    elif args.reimport:
                        note = "reimport — previous copy vanished"
                    else:
                        decision = "hold"
                        note = "previously imported (run %d) but the copy is gone" % row["run_id"]
                elif row:
                    if os.path.exists(dest_abs):
                        decision, note = "hold", "changed at source AND destination occupied"
                    else:
                        note = f"changed at source since run {row['run_id']}"
                elif os.path.exists(dest_abs):
                    decision = "hold"
                    note = "destination exists but is not in the ledger — never overwritten"
                if note and decision != "skip":
                    attention.append(f"{sess['name']}: {os.path.basename(rec['relpath'])} — {note}")
                sess["files"].append(
                    {
                        "rec": rec,
                        "label": label,
                        "dest_rel": dest_rel,
                        "decision": decision,
                        "note": note,
                    }
                )

        base = sess["name"].split(" ")[0]
        base = base[: -len("_adjacent")] if base.lower().endswith("_adjacent") else base
        twins = [
            n
            for n in lib_index["by_target_night"].get((base, sess["night"].isoformat()), [])
            if n != sess["name"]
        ]
        if twins and any(f["decision"] == "copy" for f in sess["files"]):
            attention.append(
                f"{sess['name']}: same target+night already in the library under a different "
                f"name: {', '.join(twins)} — copying would duplicate that night"
            )

    # Reconciliation: every ledger row's copy must still exist somewhere.
    missing_by_session: dict[str, int] = {}
    for row in intake_ledger.all_copied_rows(con):
        if os.path.exists(os.path.join(staging, row["dest_relpath"])):
            continue
        filed = _filed_path(lib_index, row["session"], row["dest_relpath"])
        if filed and os.path.exists(filed):
            continue
        missing_by_session[row["session"]] = missing_by_session.get(row["session"], 0) + 1
    for session_name, n in sorted(missing_by_session.items()):
        attention.append(
            f"ledger: {n} file(s) imported into {session_name!r} are in neither staging nor "
            f"the library — deleted by hand? re-offer with --reimport"
        )

    return {
        "staging": staging,
        "ledger_path": ledger_path,
        "con": con,
        "interrupted": interrupted,
        "lib_index": lib_index,
        "vocab": vocab,
        "plan": plan,
        "attention": attention,
    }


# ==========================================================================
# Plan rendering
# ==========================================================================
def render_plan(cfg: dict, args, scans: dict[str, dict], ctx: dict) -> int:
    """Print the full intake plan; return nonzero when the equation breaks."""
    settings = cfg["settings"]
    staging, plan = ctx["staging"], ctx["plan"]
    staged_names = set()
    if staging and os.path.isdir(staging):
        staged_names = {d for d in os.listdir(staging) if not d.startswith(".")}

    print("Intake plan")
    print(f"Config  : {args.config}")
    state = "" if staging and os.path.isdir(staging) else "  (does not exist yet — created on --apply)"
    print(f"Staging : {staging}{state}")
    n_known = sum(1 for _ in intake_ledger.all_copied_rows(ctx["con"]))
    print(f"Ledger  : {ctx['ledger_path']}  ({n_known:,} copies recorded)")
    if ctx["interrupted"]:
        print(f"          {ctx['interrupted']} interrupted run(s) relabeled — their files re-offer below")
    for label, _path, mounted in ctx["lib_index"]["libraries"]:
        note = "mounted" if mounted else "NOT MOUNTED — its sessions are invisible to dedupe"
        print(f"Dedupe  : library {label} — {note}")

    n_copy = n_skip = n_hold = n_already = n_ignored = 0
    log_copy = 0
    total_bytes = 0
    new_sessions = 0

    for sid in sorted(scans):
        sessions = [s for s in plan["sessions"] if s["source"] == sid]
        source = next(s for s in cfg["sources"] if s["id"] == sid)
        print(f"\n[{sid}] {source['label']} — {len(sessions)} session(s) in scope")

        for sess in sessions:
            n_frames = sum(len(sess[k]) for k in ("lights", "flats", "darkflats"))
            if sess["status"] == "already":
                n_already += n_frames
                print(f"  {sess['name']:<52} {sess['status_note']}")
                continue
            if sess["status"] == "ignored":
                n_ignored += n_frames
                print(f"  {sess['name']:<52} ignored (config)")
                continue

            counts = {"copy": 0, "skip": 0, "hold": 0}
            for f in sess["files"]:
                counts[f["decision"]] += 1
                is_science = f["label"] != "logs"
                if f["decision"] == "copy":
                    total_bytes += f["rec"]["size"]
                    if is_science:
                        n_copy += 1
                    else:
                        log_copy += 1
                elif is_science:
                    n_skip += f["decision"] == "skip"
                    n_hold += f["decision"] == "hold"
            if counts["copy"] == 0:
                status = "nothing to copy — all files already imported"
                print(f"  {sess['name']:<52} {status}")
                continue

            new_sessions += 1
            print(f"\n  {sess['name']}    new session folder")
            print(
                f"      rig        {sess['cam']} on {sid} → "
                f"{sess['rig']['scope']} + {sess['rig']['sensor']} ({sess['rule']})"
            )
            by_group: dict[tuple[str, str], list[dict]] = {}
            for f in sess["files"]:
                if f["decision"] != "copy":
                    continue
                src_dir = os.path.dirname(f["rec"]["relpath"]) or "."
                dest_dir = os.path.dirname(f["dest_rel"]).split("/", 1)[1]
                by_group.setdefault((f["label"], f"{src_dir}/ → {dest_dir}/"), []).append(f)
            for (label, arrow), fs in sorted(by_group.items(), key=lambda kv: kv[0]):
                size = sum(f["rec"]["size"] for f in fs)
                print(f"      {label:<10} {arrow}   {len(fs):,} file(s)  {_gb(size)}")
            if counts["skip"]:
                print(f"      skipped    {counts['skip']:,} file(s) already imported (ledger)")
            if counts["hold"]:
                print(f"      held       {counts['hold']:,} file(s) need a decision — see attention")
            stamps = f"{sess['name']} notes.toml"
            if settings["pxiproject_template"]:
                stamps += f" · {sess['name']}.pxiproject (from template)"
            else:
                stamps += " · no pxiproject template configured — project not stamped"
            print(f"      stamps     {stamps}")
            if sess["date_dir_mismatches"]:
                print(
                    f"      warning    {sess['date_dir_mismatches']} frame(s) sit in a NINA "
                    f"date folder that differs from the computed civil night"
                )
            verdict, reasons = projected_preflight(sess, ctx["vocab"], staged_names)
            print(
                f"      preflight (projected) {verdict}"
                + (f" — {reasons[0]}" if reasons else "")
            )
            for extra in reasons[1:]:
                print(f"          {extra}")

    cal_sets = calibration_sets(plan["calibration"])
    if cal_sets:
        print(
            "\ncalibration — reported only, not staged "
            "(library routing arrives in a later milestone)"
        )
        for c in cal_sets:
            print(
                f"  {c['source']:<8} {c['kind']:<5} {c['exp']:<8} gain{c['gain']} "
                f"{c['temp']}C  night {c['night']}  {c['count']:,} file(s)  {_gb(c['bytes'])}"
            )

    if plan["quarantine"]:
        print("\nquarantine — nothing copied; fix at the source or ignore")
        by_key = _grouped_counter(
            plan["quarantine"],
            lambda r: (
                f"{r.get('source', '?')}  {os.path.dirname(r['relpath']) or '.'}/ — "
                f"{r.get('reason', '?')}"
            ),
        )
        for line, n in by_key:
            print(f"  {line}  ({n:,} file(s))")

    if plan["unmapped"]:
        print("\nunmapped cameras — no [[rig]] entry covers these; add one to intake.toml")
        for grp in plan["unmapped"]:
            print(
                f"  {grp['source']}: camera {grp['cam']!r} on {grp['night']} — "
                f"{len(grp['records']):,} file(s)"
            )

    if plan["unattached"]:
        print("\nunattached — no session to host these")
        by_key = _grouped_counter(
            plan["unattached"], lambda r: f"{r.get('source', '?')}: {r.get('reason', '?')}"
        )
        for line, n in by_key:
            print(f"  {line}  ({n:,} item(s))")

    if ctx["attention"]:
        print("\nattention")
        for line in ctx["attention"]:
            print(f"  {line}")

    n_cal = len(plan["calibration"])
    n_quar = len(plan["quarantine"])
    n_unmapped = sum(len(g["records"]) for g in plan["unmapped"])
    n_unatt = sum(1 for r in plan["unattached"] if "kind" in r)
    remainder = plan["selected"] - (
        n_copy + n_skip + n_hold + n_already + n_ignored + n_cal + n_quar + n_unmapped + n_unatt
    )
    print(
        f"\ntotals: {new_sessions} session folder(s) to create/fill, "
        f"{n_copy:,} frame(s) + {log_copy} log(s), {_gb(total_bytes)} to copy"
    )
    print(
        f"plan equation: {plan['selected']:,} science in scope = {n_copy:,} to copy + "
        f"{n_skip:,} already copied (ledger) + {n_already:,} already in library + "
        f"{n_ignored:,} ignored + {n_cal:,} calibration + {n_quar:,} quarantine + "
        f"{n_unmapped:,} unmapped + {n_unatt:,} unattached + {n_hold:,} held"
        f" · remainder {remainder}"
        + (f" · {plan['filtered_out']:,} outside the night filter" if plan["filtered_out"] else "")
    )
    if remainder:
        print("ERROR plan equation remainder is not zero — grouping bug, do not trust this plan")
        return 1
    return 0



# ==========================================================================
# Audit — is everything still where the ledger says, byte-for-byte?
# ==========================================================================
def _hash_file(path: str, hash_name: str) -> str:
    h = hashlib.new(hash_name)
    with open(path, "rb") as fin:
        while chunk := fin.read(COPY_CHUNK_BYTES):
            h.update(chunk)
    return h.hexdigest()


def run_audit(cfg: dict, args) -> None:
    """Verify every ledger row against reality.

    Default: each copied file exists at its staged or filed location with a
    matching size (fast — safe to run habitually). --deep additionally
    re-hashes each file against the stored digest (rereads everything ever
    imported; for occasional full assurance).
    """
    settings = cfg["settings"]
    staging = resolve_staging(settings)
    con = intake_ledger.open_ledger(resolve_ledger(settings))
    lib_index = build_library_index()
    rows = intake_ledger.all_copied_rows(con)

    print(f"Intake audit{' (deep — re-hashing)' if args.deep else ''}")
    print(f"Ledger  : {resolve_ledger(settings)} — {len(rows):,} copied file(s) to verify")
    for label, _path, mounted in lib_index["libraries"]:
        if not mounted:
            print(f"warning : library {label} NOT MOUNTED — its filed sessions can't be verified")

    n_ok = 0
    failures: list[str] = []
    missing_by_session: dict[str, int] = {}
    for row in rows:
        staged = os.path.join(staging, row["dest_relpath"])
        filed = _filed_path(lib_index, row["session"], row["dest_relpath"])
        path = staged if os.path.exists(staged) else (filed if filed and os.path.exists(filed) else None)
        if path is None:
            missing_by_session[row["session"]] = missing_by_session.get(row["session"], 0) + 1
            continue
        size = os.path.getsize(path)
        if size != row["size"]:
            failures.append(
                f"size mismatch: {path} — ledger {row['size']:,} bytes, on disk {size:,}"
            )
            continue
        if args.deep and _hash_file(path, settings["hash"]) != row["sha"]:
            failures.append(f"hash mismatch: {path} — content differs from the verified copy")
            continue
        n_ok += 1

    for session, n in sorted(missing_by_session.items()):
        failures.append(
            f"missing: {n} file(s) of {session!r} in neither staging nor the library"
        )
    print(f"\n{n_ok:,} of {len(rows):,} verified ok")
    for line in failures:
        print(f"  FAIL  {line}")
    if failures:
        print(f"\naudit FAILED — {len(failures)} problem(s) above")
        sys.exit(1)
    print("audit ok — every ledgered copy is present" + (" and hash-verified" if args.deep else ""))
    sys.exit(0)


# ==========================================================================
# Apply
# ==========================================================================
def _clean_stale_parts(staging: str) -> int:
    """Delete leftover *.part files under staging (incomplete by construction)."""
    n = 0
    for root, _dirs, files in os.walk(staging):
        for f in files:
            if f.endswith(".part"):
                os.remove(os.path.join(root, f))
                n += 1
    return n


def stamp_session(staging: str, session_name: str, settings: dict) -> list[str]:
    """Stamp notes.toml and the .pxiproject template into a new session.

    Both stamps are skip-if-present (idempotent resume) and the pxiproject is
    copied opaquely — its internals are the user's to maintain, never edited.

    Returns:
        Action-log lines for what was actually stamped.
    """
    lines: list[str] = []
    sdir = os.path.join(staging, session_name)

    notes_src = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates", "notes.toml")
    notes_dest = os.path.join(sdir, f"{session_name} notes.toml")
    if os.path.exists(notes_src) and not os.path.exists(notes_dest):
        shutil.copyfile(notes_src, notes_dest)
        lines.append(f"stamp '{notes_dest}'")

    template = settings["pxiproject_template"]
    if template and os.path.isdir(template):
        proj_dest = os.path.join(sdir, f"{session_name}.pxiproject")
        if not os.path.exists(proj_dest):
            shutil.copytree(template, proj_dest + ".part")
            os.rename(proj_dest + ".part", proj_dest)
            lines.append(f"stamp '{proj_dest}' (from template)")
    elif template:
        print(f"      note      pxiproject template not found: {template} — skipped")
    return lines


def _real_preflight_verdict(staging: str, session_name: str, vocab: dict) -> str:
    """Run preflight's actual check_session on a just-created staged folder."""
    from preflight import check_session, library_target_map

    libs = astro_config.load_libraries()
    working = next(
        (lib for lib in libs if lib["role"] == "working" and os.path.isdir(lib["path"])), None
    )
    if not working:
        return "preflight: no mounted working library — run preflight.py once one is mounted"
    targets = library_target_map(working["path"])
    errors, warnings, _infos, _dest = check_session(
        os.path.join(staging, session_name),
        session_name,
        targets,
        vocab["targets"],
        vocab["scopes"],
        vocab["sensors"],
        vocab["combos"],
        working["path"],
    )
    verdict = "FAIL" if errors else ("WARN" if warnings else "OK")
    detail = "; ".join(errors + warnings) or "clean"
    return f"preflight (real) {verdict} — {detail}"


def run_apply(cfg: dict, args, scans: dict[str, dict], ctx: dict) -> int:
    """Execute the plan's copy decisions. Returns the process exit code."""
    staging, con = ctx["staging"], ctx["con"]
    to_do = [
        s
        for s in ctx["plan"]["sessions"]
        if s["status"] == "new" and any(f["decision"] == "copy" for f in s["files"])
    ]
    if not to_do:
        print("\napply: nothing to copy — every planned file is already imported.")
        return 0

    os.makedirs(staging, exist_ok=True)
    stale = _clean_stale_parts(staging)
    if stale:
        print(f"\napply: removed {stale} stale .part file(s) from an interrupted run")

    run_id = intake_ledger.begin_run(con, " ".join(sys.argv[1:]))
    print(f"\napply: ledger run {run_id}")
    log_lines: list[str] = []
    n_files = n_bytes = n_failed = 0

    for sess in to_do:
        sdir = os.path.join(staging, sess["name"])
        source = next(s for s in cfg["sources"] if s["id"] == sess["source"])
        if not os.path.isdir(sdir):
            os.makedirs(sdir)
            intake_ledger.record_dir(con, run_id, sess["name"])
            log_lines.append(f"mkdir '{sdir}'")
        print(f"\n  {sess['name']}")

        copied_here = 0
        for f in sess["files"]:
            if f["decision"] != "copy":
                continue
            src = os.path.join(source["path"], f["rec"]["relpath"])
            dest = os.path.join(staging, f["dest_rel"])
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            if os.path.exists(dest):  # decided free at plan time; never overwrite
                print(f"      HELD   {f['dest_rel']} — destination appeared since planning")
                n_failed += 1
                continue
            status, sha = copy_verified(src, dest, cfg["settings"]["hash"])
            if status != "ok":
                print(f"      {status.upper()}  {f['rec']['relpath']} — not copied, stays new")
                n_failed += 1
                continue
            intake_ledger.record_copy(
                con, run_id, sess["source"], f["rec"], sess["name"], f["dest_rel"], sha
            )
            copied_here += 1
            n_files += 1
            n_bytes += f["rec"]["size"]

        size = sum(f["rec"]["size"] for f in sess["files"] if f["decision"] == "copy")
        print(f"      copied     {copied_here:,} file(s)  {_gb(size)}  (hash-verified)")
        log_lines.append(
            f"copy {copied_here} file(s) ({_gb(size)}) '{source['path']}' → '{sdir}' "
            f"(ledger run {run_id})"
        )
        log_lines.extend(stamp_session(staging, sess["name"], cfg["settings"]))
        print(f"      {_real_preflight_verdict(staging, sess['name'], ctx['vocab'])}")

    intake_ledger.finish_run(con, run_id, n_files, n_bytes)
    astro_config.log_actions("intake", log_lines)
    print(
        f"\napply complete: {n_files:,} file(s), {_gb(n_bytes)}, run {run_id} recorded in the "
        f"ledger" + (f" — {n_failed} file(s) NOT copied (see above)" if n_failed else "")
    )
    print("next: python3 preflight.py  (validate + file), then refresh.py --notes")
    return 1 if n_failed else 0


def run_plan(cfg: dict, args) -> None:
    """Scan the selected sources, print the plan, optionally apply it."""
    sources = [s for s in cfg["sources"] if not args.source or s["id"] in args.source]
    if not sources:
        raise SystemExit(f"no source matches {args.source}")
    scans: dict[str, dict] = {}
    for source in sources:
        if not os.path.isdir(source["path"]):
            print(f"[{source['id']}] {source['label']} — NOT MOUNTED, skipped")
            continue
        scans[source["id"]] = intake_scan.scan_source(source, cfg["settings"])
    if not scans:
        raise SystemExit("no mounted sources — nothing to plan")

    ctx = decide(cfg, args, scans)
    problems = render_plan(cfg, args, scans, ctx)
    if problems:
        sys.exit(1)  # a broken plan equation must never proceed to copies
    if not args.apply:
        print("\nplan only — nothing was copied. Add --apply to execute this plan.")
        sys.exit(0)
    sys.exit(run_apply(cfg, args, scans, ctx))


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
    ap.add_argument(
        "--since",
        help="limit to civil nights on/after this date (YYYY-MM-DD)",
    )
    ap.add_argument(
        "--night",
        action="append",
        default=[],
        help="limit to this civil night (YYYY-MM-DD, repeatable)",
    )
    ap.add_argument(
        "--apply",
        action="store_true",
        help="execute the plan: copy + verify + ledger + stamp (default: plan only)",
    )
    ap.add_argument(
        "--reimport",
        action="store_true",
        help="re-offer ledgered files whose staged/filed copy has vanished",
    )
    ap.add_argument(
        "--audit",
        action="store_true",
        help="verify every ledgered copy exists with matching size (see --deep)",
    )
    ap.add_argument(
        "--deep",
        action="store_true",
        help="with --audit: also re-hash every copy against its stored digest",
    )
    args = ap.parse_args()

    cfg = load_intake_config(args.config)
    if args.show_config:
        show_config(cfg, args.config)
        return
    if args.census:
        run_census(cfg, args)
        return
    if args.audit:
        run_audit(cfg, args)
        return
    run_plan(cfg, args)


if __name__ == "__main__":
    main()
