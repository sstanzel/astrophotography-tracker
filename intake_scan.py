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
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fits_parser  # noqa: E402
from fits_parser import frame_kind, is_non_science, safe  # noqa: E402

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
}

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

    Args:
        m: regex match from fits_parser.parse().

    Returns:
        datetime, or None when the digits are not a real calendar time.
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


def civil_night(ts: dt.datetime) -> dt.date:
    """The civil night a timestamp belongs to (local-noon cutoff)."""
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
    profile = LAYOUT_PROFILES[source["layout"]]
    copy_chn = settings.get("copy_chn_logs", False)

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
            out[_classify(name, in_log_dir, copy_chn, rec)].append(rec)
    return out


def _classify(name: str, in_log_dir: bool, copy_chn: bool, rec: dict) -> str:
    """Give one file its disposition; science records gain parse fields."""
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
        exp=m.group("exp"),
        unit=safe(m, "unit", "s"),
    )
    return "science"
