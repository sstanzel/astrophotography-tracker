"""intake_scan.py — read-only discovery + classification of device dump trees.

Walks each [[source]] root from intake.toml and gives every file exactly one
disposition (the census invariant intake.py prints and enforces):

    science     — filename parsed by a fits_parser grammar
    logs        — capture-software logs (ASIAir Autorun + PHD2 guide logs)
    non-science — deliberate snapshots/previews (fits_parser.is_non_science)
    ignored     — files that are neither frames nor logs (jpg, json, txt, …)
    junk        — macOS/CCC noise (.DS_Store, ._*, CCC markers, *.part)
    quarantine  — FITS-extension files matching NO grammar: reported, never
                  copied, never guessed

Directories in a layout profile's prune set (_CCC SafetyNet, Preview, …) are
not entered at all; the census names them so nothing is silently invisible.

Nothing here writes anything, ever.
"""

import datetime as dt
import os
import re
import sys
import zoneinfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import astro_config  # noqa: E402
import fits_parser  # noqa: E402
from fits_parser import frame_kind, is_non_science, safe  # noqa: E402
from scan import RAW_IMAGE_EXT  # noqa: E402  (single source for raw formats)

WILDCARD_CAMERA = "*"

FITS_EXTS = (".fit", ".fits", ".xisf")

JUNK_BASENAMES = {".DS_Store"}
JUNK_PREFIXES = ("._", ".com.bombich.ccc")
JUNK_SUFFIXES = (".part",)

# Frames captured before local noon belong to the previous evening's civil
# night — the same convention as the session folder date and the
# next-morning-flats rule (see preflight.MAX_FRAME_DATE_SKEW_DAYS).
CIVIL_NOON_HOUR = 12

# Per-layout walk profiles. prune_dirs are never entered (CCC's SafetyNet
# archive, ASIAir preview/video buckets, NINA's non-capture folders);
# log_dirs route their .txt files to the log collector instead of "ignored".
LAYOUT_PROFILES = {
    "asiair": {
        "prune_dirs": {"_CCC SafetyNet", "Preview", "Live", "Video"},
        "log_dirs": {"log"},
    },
    "nina": {
        "prune_dirs": {
            "_CCC SafetyNet",
            "Targets",
            "profile-backup",
            "advanced_sequencer",
            "Templates",
            "horizon",
        },
        "log_dirs": set(),
    },
    # Bare camera-card dumps (Canon R5 CR3s and kin): no capture software, no
    # metadata in the filenames — night comes from mtime, semantics from the
    # dump folder's name (see _classify_dslr).
    "dslr": {
        "prune_dirs": {"_CCC SafetyNet"},
        "log_dirs": set(),
    },
}

# A dslr dump folder whose name carries one of these tokens holds calibration
# frames (reported, never staged); any other name is read as a target name.
DSLR_CAL_TOKENS = ("calibration", "dark", "bias", "flat")

# ASIAir writes a Chinese duplicate of every Autorun log.
CHN_LOG_SUFFIX = "_CHN.txt"
LOG_NAME_PREFIXES = ("Autorun_Log", "Plan_Log", "PHD2_GuideLog")

GRAMMAR_NAMES = {
    fits_parser.NINA_V2: "nina v2",
    fits_parser.NINA_LEGACY: "nina legacy",
    fits_parser.ASIAIR_SCI_ROTFIRST: "asiair science (rot-first)",
    fits_parser.ASIAIR_CAL_ROTFIRST: "asiair calibration (rot-first)",
    fits_parser.ASIAIR_SCI: "asiair science",
    fits_parser.ASIAIR_CAL: "asiair calibration",
    fits_parser.ASIAIR_DSLR_SCI: "asiair dslr science",
    fits_parser.ASIAIR_DSLR_CAL: "asiair dslr calibration",
}


def frame_timestamp(m) -> dt.datetime | None:
    """Capture timestamp from a parsed frame match (device-local time).

    Both stamp families write the capture device's local wall clock: ASIAir
    'dt' tokens (verified against library evening sessions — a 22:36 stamp on
    the session's own date) and NINA 'date'+'time' tokens (the capture PC's
    clock). No timezone conversion is ever needed for filename stamps; the
    [intake] timezone matters only for mtime-dated card dumps.

    Args:
        m: regex match from fits_parser.parse().

    Returns:
        Naive datetime, or None when the digits are not a real calendar time.
    """
    stamp = safe(m, "dt")
    try:
        if stamp:
            return dt.datetime.strptime(stamp, "%Y%m%d-%H%M%S")
        date, time = safe(m, "date"), safe(m, "time")
        if date and time:
            return dt.datetime.strptime(f"{date} {time}", "%Y-%m-%d %H-%M-%S")
    except ValueError:
        return None
    return None


def load_timezone(name: str) -> dt.tzinfo:
    """The configured capture timezone ([intake] timezone).

    Only mtime-dated files (dslr card dumps) need it: an mtime is an absolute
    epoch instant, and turning it into a civil-night date requires the wall
    clock of wherever the shutter fired. Filename stamps are already local.

    Args:
        name: IANA zone name, e.g. "America/Denver".

    Raises:
        SystemExit: on an unknown zone name (config error, fail loud).
    """
    try:
        return zoneinfo.ZoneInfo(name)
    except (zoneinfo.ZoneInfoNotFoundError, ValueError):
        raise SystemExit(f"[intake] timezone {name!r} is not a known IANA timezone")


def civil_night(ts: dt.datetime) -> dt.date:
    """The civil night a local timestamp belongs to (local-noon cutoff)."""
    if ts.hour < CIVIL_NOON_HOUR:
        return ts.date() - dt.timedelta(days=1)
    return ts.date()


def _is_junk(name: str) -> bool:
    return (
        name in JUNK_BASENAMES
        or name.startswith(JUNK_PREFIXES)
        or name.endswith(JUNK_SUFFIXES)
    )


def _classify_log(name: str, copy_chn_logs: bool) -> str:
    """Disposition for a file inside a log dir: 'logs', 'junk' (CHN dupe) or
    'ignored' (unrecognized)."""
    if name.endswith(CHN_LOG_SUFFIX):
        return "logs" if copy_chn_logs else "junk"
    if name.startswith(LOG_NAME_PREFIXES) and name.endswith(".txt"):
        return "logs"
    return "ignored"


def scan_source(source: dict, settings: dict) -> dict:
    """Walk one device root and classify every file. Read-only.

    Args:
        source: a [[source]] dict (id/label/path/layout).
        settings: the [intake] settings dict (copy_chn_logs matters here).

    Returns:
        Dict with per-disposition record lists ('science', 'logs',
        'non_science', 'ignored', 'junk', 'quarantine'), plus 'scanned'
        (total file count), 'bytes' (total size of scanned files) and
        'pruned_dirs' (names of directories never entered). Science records
        carry kind/grammar/cam/target/ts/night/exp/unit for grouping;
        every record carries relpath/size/mtime_ns.
    """
    root = source["path"]
    layout = source["layout"]
    profile = LAYOUT_PROFILES[layout]
    copy_chn = settings.get("copy_chn_logs", False)
    tz = load_timezone(settings.get("timezone") or astro_config.DEFAULT_TIMEZONE)

    out: dict = {
        "science": [],
        "logs": [],
        "non_science": [],
        "ignored": [],
        "junk": [],
        "quarantine": [],
        "scanned": 0,
        "bytes": 0,
        "pruned_dirs": [],
    }

    for dirpath, dirnames, filenames in os.walk(root):
        pruned = sorted(d for d in dirnames if d in profile["prune_dirs"])
        if pruned:
            rel = os.path.relpath(dirpath, root)
            prefix = "" if rel == "." else rel + "/"
            out["pruned_dirs"].extend(prefix + d for d in pruned)
            dirnames[:] = [d for d in dirnames if d not in profile["prune_dirs"]]

        in_log_dir = os.path.basename(dirpath) in profile["log_dirs"]
        for name in sorted(filenames):
            fpath = os.path.join(dirpath, name)
            try:
                st = os.stat(fpath)
            except OSError:
                continue  # vanished mid-walk (never expected on a snapshot)
            rec = {
                "relpath": os.path.relpath(fpath, root),
                "size": st.st_size,
                "mtime_ns": st.st_mtime_ns,
            }
            out["scanned"] += 1
            out["bytes"] += st.st_size
            if layout == "dslr":
                dump = rec["relpath"].split(os.sep, 1)
                dump_label = dump[0] if len(dump) == 2 else os.path.basename(root)
                cls = _classify_dslr(name, rec, dump_label, tz)
            else:
                cls = _classify(name, in_log_dir, copy_chn, rec)
            out[cls].append(rec)

    # Optional [[source]] logs = "<dir>" — an extra folder walked entirely as
    # a log directory, for setups whose guiding logs live outside the source
    # root (PHD2 on a NINA PC). Records keep relpaths anchored to the source
    # root (they may start with "../"), so copy/ledger paths work unchanged.
    logs_dir = source.get("logs") or ""
    if logs_dir and not os.path.isdir(logs_dir):
        out["logs_dir_missing"] = logs_dir
    elif logs_dir:
        for dirpath, _dirnames, filenames in os.walk(logs_dir):
            for name in sorted(filenames):
                fpath = os.path.join(dirpath, name)
                try:
                    st = os.stat(fpath)
                except OSError:
                    continue
                rec = {
                    "relpath": os.path.relpath(fpath, root),
                    "size": st.st_size,
                    "mtime_ns": st.st_mtime_ns,
                }
                out["scanned"] += 1
                out["bytes"] += st.st_size
                cls = "junk" if _is_junk(name) else _classify_log(name, copy_chn)
                out[cls].append(rec)
    return out


def _classify(name: str, in_log_dir: bool, copy_chn: bool, rec: dict) -> str:
    """Give one file its disposition; science records gain parse fields.

    Science records carry 'ts' (device-local capture time, drives the civil
    night) and 'sig' — the stamp exactly as written in the filename, the
    identity token the library dedupe probe compares across renames.
    """
    if _is_junk(name):
        return "junk"
    if in_log_dir:
        return _classify_log(name, copy_chn)
    if not name.lower().endswith(FITS_EXTS):
        return "ignored"
    if is_non_science(name):
        return "non_science"
    m = fits_parser.parse(name)
    if m is None:
        rec["reason"] = "no filename grammar matched"
        return "quarantine"
    ts = frame_timestamp(m)
    if ts is None:
        rec["reason"] = "timestamp is not a real calendar time"
        return "quarantine"
    rec.update(
        kind=frame_kind(m),
        grammar=GRAMMAR_NAMES.get(m.re, "?"),
        cam=safe(m, "cam", "?"),
        target=safe(m, "target", ""),
        ts=ts,
        night=civil_night(ts),
        sig=ts.isoformat(),
        exp=m.group("exp"),
        unit=safe(m, "unit", "s"),
        gain=safe(m, "gain", "?"),
        temp=safe(m, "temp", "?"),
    )
    return "science"


def _classify_dslr(name: str, rec: dict, dump_label: str, tz: dt.tzinfo) -> str:
    """Disposition for a file in a dslr card-dump source.

    Raw camera files (CR3 and kin) carry nothing in their names, so the night
    comes from mtime (camera-written capture time, preserved by CCC/Finder
    copies and by intake's own copy protocol) and the meaning comes from the
    dump folder's name: a calibration-token name is a calibration dump
    (reported, never staged), anything else is a target name. FITS files in a
    dslr source (an ASIAir-run DSLR night copied to the same card) still go
    through the normal grammar classifier — without a log dir.
    """
    if _is_junk(name):
        return "junk"
    if name.lower().endswith(FITS_EXTS):
        return _classify(name, False, False, rec)
    if not name.lower().endswith(RAW_IMAGE_EXT):
        return "ignored"
    mtime_s = rec["mtime_ns"] // 1_000_000_000
    ts = dt.datetime.fromtimestamp(mtime_s, tz=tz).replace(tzinfo=None)
    label_l = dump_label.lower()
    is_cal = any(tok in label_l for tok in DSLR_CAL_TOKENS)
    # The camera token is the filename prefix (R5__5328.CR3 → "R5"); map rigs
    # with camera = "*" when a card names files differently.
    rec.update(
        kind="raw cal" if is_cal else "light",
        grammar="raw card dump",
        cam=name.split("_")[0] or "raw",
        target="" if is_cal else dump_label,
        ts=ts,
        night=civil_night(ts),
        sig=str(mtime_s),
        exp="?",
        unit="",
        gain="?",
        temp="?",
    )
    return "science"


# ==========================================================================
# Rig resolution — which Scope+Sensor a (source, camera, night) maps to
# ==========================================================================
def rig_is_dated(rig: dict) -> bool:
    """True when a [[rig]] entry carries a from/to bound."""
    return rig["from"] is not None or rig["to"] is not None


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
         and rig_is_dated(r) and in_range(r)],
        [r for r in rigs if r["source"] == source_id and r["camera"] == WILDCARD_CAMERA
         and rig_is_dated(r) and in_range(r)],
        [r for r in rigs if r["source"] == source_id and r["camera"] == camera
         and not rig_is_dated(r)],
        [r for r in rigs if r["source"] == source_id and r["camera"] == WILDCARD_CAMERA
         and not rig_is_dated(r)],
    )
    for tier in tiers:
        if tier:
            r = tier[0]
            if rig_is_dated(r):
                rule = f"dated rule {r['from'] or '…'} → {r['to'] or '…'}"
            else:
                rule = "open-ended rule"
            if r["camera"] == WILDCARD_CAMERA:
                rule += ", any-camera"
            return r, rule
    return None, None


# ==========================================================================
# Grouping — science frames + logs → planned session folders
# ==========================================================================
# ASIAir log stamps: Autorun_Log_2026-04-19_223348.txt (PHD2 logs likewise).
LOG_TS_RE = re.compile(r"(\d{4}-\d{2}-\d{2})_(\d{2})(\d{2})(\d{2})")

# NINA sources nest captures under a per-night date folder; a folder date that
# disagrees with the computed civil night means a clock or $$DATE$$-token
# problem worth surfacing.
DATE_DIR_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Current library conventions (July 2026 hand-filed sessions): lights sit
# directly in Light/, session-local calibration in "Flat <Scope> <Sensor>
# <date>"/Flat + /Dark Flat, logs in log/.
LIGHT_SUBDIR = "Light"
DARKFLAT_SUBDIR = "Dark Flat"
FLAT_SUBDIR = "Flat"
LOG_SUBDIR = "log"


def log_night(name: str) -> dt.date | None:
    """The civil night an ASIAir/PHD2 log filename belongs to, or None."""
    m = LOG_TS_RE.search(name)
    if not m:
        return None
    try:
        ts = dt.datetime.strptime(f"{m.group(1)} {m.group(2)}{m.group(3)}{m.group(4)}",
                                  "%Y-%m-%d %H%M%S")
    except ValueError:
        return None
    return civil_night(ts)


def _night_selected(night: dt.date, since: dt.date | None, nights: set | None) -> bool:
    if since and night < since:
        return False
    if nights and night not in nights:
        return False
    return True


ADJACENT_SUFFIX = "_adjacent"  # matches preflight.ADJACENT_SUFFIX


def session_folder_name(target_token: str, rig: dict, night: dt.date) -> str:
    """The 4-token session folder name for a light group (adjacent-aware).

    A rig marked adjacent appends the suffix ONLY when the capture software's
    target name doesn't already carry it (NINA targets like "M 106 adjacent"
    normalize to an already-suffixed id).
    """
    target_id = target_token.replace(" ", "_")
    if rig["adjacent"] and not target_id.lower().endswith(ADJACENT_SUFFIX):
        target_id += ADJACENT_SUFFIX
    return f"{target_id} {rig['scope']} {rig['sensor']} {night.isoformat()}"


def flat_folder_name(rig: dict, night: dt.date) -> str:
    """The session-local calibration folder name, current library convention."""
    return f"Flat {rig['scope']} {rig['sensor']} {night.isoformat()}"


def _date_dir_mismatches(records: list[dict]) -> int:
    """Count frames whose top-level NINA date folder ≠ computed civil night."""
    n = 0
    for rec in records:
        top = rec["relpath"].split(os.sep, 1)[0]
        if DATE_DIR_RE.match(top) and dt.date.fromisoformat(top) != rec["night"]:
            n += 1
    return n


def group_sessions(
    scans: dict[str, dict],
    rigs: list[dict],
    since: dt.date | None = None,
    nights: set | None = None,
) -> dict:
    """Group scanned science frames + logs into planned session folders.

    Every selected science record lands in exactly ONE of: a session's
    lights/flats/darkflats, a calibration set (library darks/bias — reported,
    not staged), 'quarantine' (light with no target token), 'unmapped' (no
    [[rig]] entry covers the camera), or 'unattached' (flats with no light
    session to host them). intake.py's plan equation enforces this.

    Args:
        scans: source id → scan_source() result.
        rigs: parsed [[rig]] blocks.
        since: keep only civil nights >= this date.
        nights: keep only these civil nights.

    Returns:
        {'sessions': [...], 'calibration': [...], 'quarantine': [...],
         'unmapped': [...], 'unattached': [...], 'selected': int,
         'filtered_out': int} — sessions sorted by (source, night, name).
    """
    out: dict = {
        "sessions": [],
        "calibration": [],
        "quarantine": [],
        "unmapped": [],
        "unattached": [],
        "selected": 0,
        "filtered_out": 0,
    }

    for sid, scan in sorted(scans.items()):
        selected: list[dict] = []
        for rec in scan["science"]:
            if _night_selected(rec["night"], since, nights):
                selected.append(rec)
            else:
                out["filtered_out"] += 1
        out["selected"] += len(selected)

        # Resolve the rig once per (camera, night); unmapped groups drop out
        # whole so the plan can show the observed camera token verbatim.
        by_cam_night: dict[tuple, list[dict]] = {}
        for rec in selected:
            by_cam_night.setdefault((rec["cam"], rec["night"]), []).append(rec)

        sessions_by_key: dict[tuple, dict] = {}
        pending_cal: list[tuple[dict, dict, list[dict]]] = []  # (rig, rule, recs) per group
        for (cam, night), recs in sorted(by_cam_night.items()):
            rig, rule = resolve_rig(rigs, sid, cam, night)
            if rig is None:
                out["unmapped"].append(
                    {"source": sid, "cam": cam, "night": night, "records": recs}
                )
                continue

            flat_exposures = {
                (r["exp"], r["unit"]) for r in recs if r["kind"] == "flat"
            }
            cal_records: list[dict] = []
            for rec in recs:
                kind = rec["kind"]
                if kind == "light":
                    if not rec["target"]:
                        rec["source"] = sid
                        rec["reason"] = "light frame with no target token"
                        out["quarantine"].append(rec)
                        continue
                    # Key on the normalized folder name, not the raw token —
                    # "M 12" and "M_12" are the same target and must not
                    # produce two sessions with one name.
                    name = session_folder_name(rec["target"], rig, night)
                    key = (cam, night, name)
                    sess = sessions_by_key.get(key)
                    if sess is None:
                        sess = sessions_by_key[key] = {
                            "source": sid,
                            "cam": cam,
                            "night": night,
                            "rig": rig,
                            "rule": rule,
                            "target_token": rec["target"],
                            "name": name,
                            "lights": [],
                            "flats": [],
                            "darkflats": [],
                            "logs": [],
                        }
                    sess["lights"].append(rec)
                elif kind in ("flat", "darkflat"):
                    cal_records.append(rec)
                elif kind == "dark" and (rec["exp"], rec["unit"]) in flat_exposures:
                    # ASIAir writes dark-flats as plain Dark_* files; a dark at
                    # a flat exposure that night is a dark-flat by construction.
                    rec["kind"] = "darkflat"
                    cal_records.append(rec)
                else:  # library material: long darks, bias
                    rec["source"] = sid
                    out["calibration"].append(rec)
            pending_cal.append(((cam, night), rig, cal_records))

        # Attach session-local calibration to ONE host per (camera, night):
        # the last-ending session, same convention resolve_flats() expects.
        def last_ts(sess: dict) -> dt.datetime:
            return max(r["ts"] for r in sess["lights"])

        for (cam, night), rig, cal_records in pending_cal:
            if not cal_records:
                continue
            hosts = [s for s in sessions_by_key.values() if s["cam"] == cam and s["night"] == night]
            if not hosts:
                for rec in cal_records:
                    rec["source"] = sid
                    rec["reason"] = f"no light session on {night} to host session calibration"
                out["unattached"].extend(cal_records)
                continue
            host = max(hosts, key=last_ts)
            for rec in cal_records:
                host["flats" if rec["kind"] == "flat" else "darkflats"].append(rec)

        # Logs: that night's last session for the source, any camera.
        for rec in scan["logs"]:
            night = log_night(os.path.basename(rec["relpath"]))
            if night is None:
                rec["source"] = sid
                rec["reason"] = "no timestamp recognized in log filename"
                out["unattached"].append(rec)
                continue
            if not _night_selected(night, since, nights):
                continue
            hosts = [s for s in sessions_by_key.values() if s["night"] == night]
            if not hosts:
                rec["source"] = sid
                rec["reason"] = f"no light session on {night} to host this log"
                out["unattached"].append(rec)
                continue
            max(hosts, key=last_ts)["logs"].append(rec)

        for sess in sessions_by_key.values():
            sess["date_dir_mismatches"] = _date_dir_mismatches(sess["lights"])
            out["sessions"].append(sess)

    out["sessions"].sort(key=lambda s: (s["source"], s["night"], s["name"]))
    return out


def calibration_sets(records: list[dict]) -> list[dict]:
    """Summarize library-calibration records (darks/bias) into display sets.

    Grouped by (source, camera, kind, exposure, gain, night) — deliberately
    NOT by exact temperature: uncooled dark-library nights drift a degree at
    a time and would otherwise explode into one line per frame. The set shows
    its temperature as a min…max range instead.

    Args:
        records: calibration-bucket records from group_sessions() (each
            tagged with its source id).

    Returns:
        One dict per set, with count/bytes/temp range, sorted for display.
    """
    sets: dict[tuple, dict] = {}
    for rec in records:
        sid = rec.get("source", "?")
        key = (sid, rec["cam"], rec["kind"], rec["exp"], rec["unit"], rec["gain"], rec["night"])
        entry = sets.get(key)
        if entry is None:
            entry = sets[key] = {
                "source": sid,
                "cam": rec["cam"],
                "kind": rec["kind"],
                "exp": f"{rec['exp']}{rec['unit']}",
                "gain": rec["gain"],
                "night": rec["night"],
                "count": 0,
                "bytes": 0,
                "_temps": [],
            }
        entry["count"] += 1
        entry["bytes"] += rec["size"]
        try:
            entry["_temps"].append(float(rec["temp"]))
        except (TypeError, ValueError):
            pass
    out = sorted(sets.values(), key=lambda e: (e["source"], e["night"], e["kind"], e["exp"]))
    for entry in out:
        temps = entry.pop("_temps")
        if not temps:
            entry["temp"] = "?"
        elif min(temps) == max(temps):
            entry["temp"] = f"{min(temps):g}"
        else:
            entry["temp"] = f"{min(temps):g}…{max(temps):g}"
    return out
