#!/usr/bin/env python3
"""
populate_notes.py - back-populate the [sky] and [weather] sections of every
per-session notes.toml in the astrophotography libraries.

What it fills
-------------
[sky]      moon_phase, moon_illumination, moon_age_days   (offline astronomy math)
[weather]  temperature_c, humidity_pct, dewpoint_c, cloud_cover_pct,
           wind_kph, pressure_hpa, conditions             (Open-Meteo archive API)

It NEVER touches: location, allsky_logged, [weather].source, seeing,
transparency, [allsky], [observation], [processing], [future_processing].

Idempotent
----------
Only fields whose current value is "" get written. Re-running is safe, and any
value you hand-correct is left alone. Moon fields and weather fields are written
independently - if the weather API has no data for a recent night, the moon
fields still get filled.

How the night window is found
------------------------------
The capture time is read from the FITS *filenames* (ASIAir and NINA both stamp
local capture time into the name). The session's observing window is
[earliest frame, latest frame + its exposure], in the site's local time. Moon
is computed at the window midpoint; weather is averaged across the window.
Sessions with no parseable FITS (DSLR nights) fall back to a 21:00-05:00 window
on the folder date.

Run natively on the Mac (needs the mounted volumes and internet):
    python3 "populate_notes.py"                 # full run
    python3 "populate_notes.py" --dry-run       # report only, write nothing
    python3 "populate_notes.py" --no-weather    # moon only, no API calls
    python3 "populate_notes.py" --only "M_81"   # limit to matching folders
    python3 "populate_notes.py" --verbose
"""

from __future__ import annotations
import argparse, datetime as dt, glob, json, math, os, re, sys, time, urllib.parse, urllib.request

# =============================================================================
# Configuration
# =============================================================================
# Library paths come from config.toml (via astro_config); locations.toml is
# found relative to the scripts. Nothing here is hardcoded to a machine.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import astro_config  # noqa: E402

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
API_PAUSE_S = 0.4  # be polite between Open-Meteo calls
SYNODIC_MONTH = 29.530588853

# Fallback window (local time) for sessions with no parseable FITS timestamps.
FALLBACK_START_H = 21  # 21:00 on the folder date
FALLBACK_END_H = 5 + 24  # 05:00 the next morning


# =============================================================================
# locations.toml
# =============================================================================
def load_locations(path):
    """Return {site_name: {'lat':float,'lon':float}}. Minimal TOML-subset parser
    so the script has zero dependencies and runs on any Python 3."""
    sites = {}
    if not os.path.exists(path):
        sys.exit(f"locations.toml not found: {path}")
    current = None
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            m = re.match(r'\[\s*"?(.+?)"?\s*\]\s*$', s)
            if m:
                current = m.group(1)
                sites[current] = {}
                continue
            if current is None:
                continue
            m = re.match(r"(\w+)\s*=\s*(.+?)\s*(?:#.*)?$", s)
            if m:
                key, raw = m.group(1), m.group(2).strip()
                if raw and raw[0] in "\"'":
                    val = raw.strip("\"'")
                else:
                    try:
                        val = float(raw)
                    except ValueError:
                        val = raw
                sites[current][key] = val
    out = {}
    for name, d in sites.items():
        if "latitude" in d and "longitude" in d:
            out[name] = {"lat": float(d["latitude"]), "lon": float(d["longitude"])}
    return out


# =============================================================================
# Moon: phase / illumination / age   (no network)
# =============================================================================
def julian_day(d: dt.datetime) -> float:
    """Julian Day for a UTC datetime."""
    y, m = d.year, d.month
    day = d.day + d.hour / 24 + d.minute / 1440 + d.second / 86400
    if m <= 2:
        y -= 1
        m += 12
    a = y // 100
    b = 2 - a + a // 4
    return math.floor(365.25 * (y + 4716)) + math.floor(30.6001 * (m + 1)) + day + b - 1524.5


def moon_state(when_utc: dt.datetime):
    """Return (phase_name, illumination_pct_int, age_days_float) for a UTC time.

    Low-precision lunar/solar longitudes (Meeus, ch. 25/47). Illumination is
    accurate to well under 1%; age to ~0.1 day - ample for session notes.
    """
    d = julian_day(when_utc) - 2451545.0  # days since J2000.0

    def norm(x):
        return x % 360.0

    rad = math.radians
    # --- Sun: ecliptic longitude ---
    g = norm(357.529 + 0.98560028 * d)  # mean anomaly
    q = norm(280.459 + 0.98564736 * d)  # mean longitude
    sun_lon = norm(q + 1.915 * math.sin(rad(g)) + 0.020 * math.sin(rad(2 * g)))

    # --- Moon: ecliptic longitude (main periodic terms) ---
    Lm = norm(218.316 + 13.176396 * d)  # mean longitude
    Mm = norm(134.963 + 13.064993 * d)  # mean anomaly
    D = norm(297.850 + 12.190749 * d)  # mean elongation
    F = norm(93.272 + 13.229350 * d)  # argument of latitude
    moon_lon = norm(
        Lm
        + 6.289 * math.sin(rad(Mm))
        + 1.274 * math.sin(rad(2 * D - Mm))
        + 0.658 * math.sin(rad(2 * D))
        + 0.214 * math.sin(rad(2 * Mm))
        - 0.186 * math.sin(rad(g))
        - 0.114 * math.sin(rad(2 * F))
    )

    elong = norm(moon_lon - sun_lon)  # 0 = new, 180 = full
    illum = (1 - math.cos(rad(elong))) / 2.0  # illuminated fraction 0..1
    age = elong / 360.0 * SYNODIC_MONTH

    names = [
        (1.84566, "new moon"),
        (5.53699, "waxing crescent"),
        (9.22831, "first quarter"),
        (12.91963, "waxing gibbous"),
        (16.61096, "full moon"),
        (20.30228, "waning gibbous"),
        (23.99361, "last quarter"),
        (27.68493, "waning crescent"),
        (29.53059, "new moon"),
    ]
    phase = next(n for limit, n in names if age < limit)
    return phase, int(round(illum * 100)), round(age, 1)


# =============================================================================
# Session folder + FITS filename inspection
# =============================================================================
DATE_IN_FOLDER = re.compile(r"(\d{4})-(\d{2})-(\d{2})\s*$")
ASIAIR_DT = re.compile(r"_(\d{8})-(\d{6})_")  # _YYYYMMDD-HHMMSS_
NINA_DT = re.compile(r"_(\d{4}-\d{2}-\d{2})_(\d{2}-\d{2}-\d{2})_")
EXP_IN_NAME = re.compile(r"_(\d+(?:\.\d+)?)(s|ms)_", re.I)


def folder_date(name):
    m = DATE_IN_FOLDER.search(name)
    if not m:
        return None
    return dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))


def frame_local_time(fname):
    """Parse the *local* capture datetime stamped in a FITS filename, plus the
    exposure in seconds. Returns (datetime, exp_seconds) or (None, None)."""
    m = ASIAIR_DT.search(fname)
    if m:
        d, t = m.group(1), m.group(2)
        when = dt.datetime(
            int(d[:4]), int(d[4:6]), int(d[6:8]), int(t[:2]), int(t[2:4]), int(t[4:6])
        )
    else:
        m = NINA_DT.search(fname)
        if not m:
            return None, None
        when = dt.datetime.strptime(m.group(1) + " " + m.group(2), "%Y-%m-%d %H-%M-%S")
    exp = None
    em = EXP_IN_NAME.search(fname)
    if em:
        exp = float(em.group(1)) / (1000.0 if em.group(2).lower() == "ms" else 1.0)
    return when, exp


def _span(times):
    start = min(t[0] for t in times)
    end = max(t[0] + dt.timedelta(seconds=t[1]) for t in times)
    if end <= start:
        end = start + dt.timedelta(hours=1)
    return start, end, start + (end - start) / 2


def session_window(session_dir, fdate):
    """Return (start_local, end_local, midpoint_local, n_frames, status).

    status is one of:
      "ondate"   - window built from frames captured on the folder-date night
      "offdate"  - folder date has no matching frames; window built from the
                   frames' OWN capture night instead (folder name likely
                   mis-dated - the camera timestamps are ground truth)
      "fallback" - no parseable FITS at all; 21:00->05:00 on the folder date

    Only science (Light) frames define the observing window."""
    night0 = fdate  # evening of the folder date
    night1 = fdate + dt.timedelta(days=1)  # into the next morning
    on_date = []  # frames captured on the folder-date night
    all_lt = []  # every parseable light frame in the folder
    for root, _dirs, files in os.walk(session_dir):
        for fn in files:
            if not re.search(r"(?i)\.(fit|fits|xisf)$", fn):
                continue
            if re.match(r"(?i)light", fn) is None:
                continue
            when, exp = frame_local_time(fn)
            if when is None:
                continue
            rec = (when, exp or 0.0)
            all_lt.append(rec)
            if night0 <= when.date() <= night1:
                on_date.append(rec)
    if on_date:
        start, end, mid = _span(on_date)
        return start, end, mid, len(on_date), "ondate"
    if all_lt:
        start, end, mid = _span(all_lt)
        return start, end, mid, len(all_lt), "offdate"
    # fallback - no parseable FITS at all
    base = dt.datetime(fdate.year, fdate.month, fdate.day)
    start = base + dt.timedelta(hours=FALLBACK_START_H)
    end = base + dt.timedelta(hours=FALLBACK_END_H)
    mid = start + (end - start) / 2
    return start, end, mid, 0, "fallback"


# =============================================================================
# Weather: Open-Meteo historical archive
# =============================================================================
HOURLY_VARS = (
    "temperature_2m",
    "relative_humidity_2m",
    "dew_point_2m",
    "cloud_cover",
    "wind_speed_10m",
    "surface_pressure",
    "weather_code",
    "precipitation",
)


def fetch_weather(lat, lon, start_local, end_local):
    """Return a dict of averaged weather values for the [start,end] local window,
    or None if no data is available. Raises on network errors (caller handles)."""
    d0 = start_local.date()
    d1 = end_local.date()
    params = {
        "latitude": f"{lat:.5f}",
        "longitude": f"{lon:.5f}",
        "start_date": d0.isoformat(),
        "end_date": d1.isoformat(),
        "hourly": ",".join(HOURLY_VARS),
        "wind_speed_unit": "kmh",
        "timezone": "auto",
    }
    url = ARCHIVE_URL + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "astro-notes/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    hourly = data.get("hourly") or {}
    stamps = hourly.get("time") or []
    if not stamps:
        return None

    # select hourly indices inside the observing window
    keep = []
    for i, ts in enumerate(stamps):
        t = dt.datetime.fromisoformat(ts)
        if start_local <= t <= end_local:
            keep.append(i)
    if not keep:
        # window fell between samples; take the hour nearest the midpoint
        mid = start_local + (end_local - start_local) / 2
        best = min(
            range(len(stamps)), key=lambda i: abs(dt.datetime.fromisoformat(stamps[i]) - mid)
        )
        keep = [best]

    def avg(var):
        col = hourly.get(var) or []
        vals = [col[i] for i in keep if i < len(col) and col[i] is not None]
        return sum(vals) / len(vals) if vals else None

    temp = avg("temperature_2m")
    if temp is None:
        return None  # archive has no real data for this date yet

    codes = [(hourly.get("weather_code") or [None])[i] for i in keep]
    codes = [c for c in codes if c is not None]
    precip_col = hourly.get("precipitation") or []
    precip = sum(precip_col[i] for i in keep if i < len(precip_col) and precip_col[i] is not None)

    return {
        "temperature_c": round(temp, 1),
        "humidity_pct": _int(avg("relative_humidity_2m")),
        "dewpoint_c": _round1(avg("dew_point_2m")),
        "cloud_cover_pct": _int(avg("cloud_cover")),
        "wind_kph": _round1(avg("wind_speed_10m")),
        "pressure_hpa": _int(avg("surface_pressure")),
        "conditions": describe(avg("cloud_cover"), max(codes) if codes else 0, precip),
    }


def _int(x):
    return int(round(x)) if x is not None else None


def _round1(x):
    return round(x, 1) if x is not None else None


def describe(cloud, wcode, precip_mm):
    """Short human conditions string from mean cloud cover + WMO weather code."""
    if cloud is None:
        base = "unknown"
    elif cloud < 10:
        base = "clear"
    elif cloud < 30:
        base = "mostly clear"
    elif cloud < 70:
        base = "partly cloudy"
    elif cloud < 90:
        base = "mostly cloudy"
    else:
        base = "overcast"
    extra = ""
    if precip_mm and precip_mm > 0.1:
        if wcode in (71, 73, 75, 77, 85, 86):
            extra = ", snow"
        elif wcode in (95, 96, 99):
            extra = ", thunderstorms"
        else:
            extra = ", precipitation"
    elif wcode in (45, 48):
        extra = ", fog"
    return base + extra


# =============================================================================
# notes.toml editing  (section-aware, fill-blanks-only, comment-preserving)
# =============================================================================
SKY_FIELDS = ("moon_phase", "moon_illumination", "moon_age_days")
WEATHER_FIELDS = (
    "temperature_c",
    "humidity_pct",
    "dewpoint_c",
    "cloud_cover_pct",
    "wind_kph",
    "pressure_hpa",
    "conditions",
)
STRING_FIELDS = {"moon_phase", "conditions"}


def toml_literal(key, value):
    if key in STRING_FIELDS:
        return '"' + str(value) + '"'
    return str(value)


def read_location(text):
    m = re.search(r'^location\s*=\s*"([^"]*)"', text, re.MULTILINE)
    return m.group(1) if m else None


def apply_updates(text, section_values):
    """section_values = {'sky': {key:val,...}, 'weather': {key:val,...}}.
    Only blank ("") fields get written. Returns (new_text, [filled_keys])."""
    lines = text.splitlines(keepends=True)
    section = None
    filled = []
    for idx, line in enumerate(lines):
        sm = re.match(r"\s*\[(\w+)\]", line)
        if sm:
            section = sm.group(1)
            continue
        if section not in section_values:
            continue
        fm = re.match(r'(\s*)(\w+)(\s*=\s*)""(.*?)(\r?\n?)$', line)
        if not fm:
            continue
        key = fm.group(2)
        if key not in section_values[section]:
            continue
        val = section_values[section][key]
        if val is None:
            continue
        literal = toml_literal(key, val)
        lines[idx] = fm.group(1) + key + fm.group(3) + literal + fm.group(4) + fm.group(5)
        filled.append(f"{section}.{key}={literal}")
    return "".join(lines), filled


# =============================================================================
# Robust write  (network volumes occasionally reject a plain truncate-write)
# =============================================================================
def safe_write(path, text):
    """Write text to path, returning True on success. Tries a plain write
    first, then a same-directory temp file + atomic replace. Network/SMB
    shares sometimes raise OSError (Errno 22) on a direct truncating open -
    usually a stale handle after a rename; remounting the share clears it."""
    try:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return True
    except OSError:
        pass
    try:
        import tempfile

        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
        return True
    except OSError:
        try:
            os.unlink(tmp)
        except Exception:
            pass
        return False


# =============================================================================
# Main
# =============================================================================
def main():
    ap = argparse.ArgumentParser(description="Back-populate notes.toml sky/weather.")
    ap.add_argument("--dry-run", action="store_true", help="report only, write nothing")
    ap.add_argument("--no-weather", action="store_true", help="moon only, skip the API")
    ap.add_argument("--only", default="", help="limit to session folders containing this substring")
    ap.add_argument(
        "--config", default=None, help="path to config.toml (default: next to this script)"
    )
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    sites = load_locations(astro_config.org_path("locations.toml"))
    print(f"Loaded {len(sites)} sites from locations.toml: {', '.join(sites)}")

    notes = []
    for lib in astro_config.load_libraries(args.config):
        root = lib["path"]
        if not os.path.isdir(root):
            print(f"  ! library '{lib['id']}' not mounted, skipping: {root}")
            continue
        notes += sorted(glob.glob(os.path.join(root, "*", "*", "*notes.toml")))
    # skip anything staged for deletion or under a leading-underscore utility folder
    notes = [
        n for n in notes if "_to_delete" not in n and os.sep + "_organization" + os.sep not in n
    ]
    if args.only:
        notes = [n for n in notes if args.only in n]
    print(f"Found {len(notes)} session notes.toml to process\n")

    n_sky = n_weather = n_skipped_weather = n_loc_unknown = n_offdate = 0
    n_have_weather = 0  # already filled — API skipped
    n_write_fail = 0
    write_failed = []
    weather_cache = {}  # (site, d0, d1) -> result, avoids duplicate API calls

    for path in notes:
        session_dir = os.path.dirname(path)
        session = os.path.basename(session_dir)
        fdate = folder_date(session)
        if fdate is None:
            print(f"  ? no date in folder name, skipping: {session}")
            continue

        text = open(path, encoding="utf-8").read()
        loc = read_location(text) or "Home"
        site = sites.get(loc)
        if site is None:
            n_loc_unknown += 1
            print(f"  ? location '{loc}' not in locations.toml: {session}")
            continue

        start, end, mid, nframes, status = session_window(session_dir, fdate)
        if status == "offdate":
            n_offdate += 1
            print(
                f"  ! {session}: folder name is dated {fdate}, but its "
                f"{nframes} light frames were captured {start:%Y-%m-%d} "
                f"-> using the frames' date; folder name likely needs a fix"
            )

        # --- sky (offline) ---
        # convert local midpoint to UTC with a longitude-based offset estimate
        utc_offset_h = round(site["lon"] / 15.0)
        mid_utc = mid - dt.timedelta(hours=utc_offset_h)
        phase, illum, age = moon_state(mid_utc)
        sky_vals = {"moon_phase": phase, "moon_illumination": illum, "moon_age_days": age}

        # --- weather (network) ---
        # Skip the API entirely if this session's [weather] is already filled
        # (temperature_c has a value). Blank sections — including recent nights
        # the archive can't cover yet — are retried on the next run.
        weather_vals = {}
        if not args.no_weather and re.search(r"^\s*temperature_c\s*=\s*-?[\d.]+", text, re.M):
            n_have_weather += 1
        elif not args.no_weather:
            key = (loc, start.date().isoformat(), end.date().isoformat())
            if key in weather_cache:
                weather_vals = weather_cache[key] or {}
            else:
                try:
                    w = fetch_weather(site["lat"], site["lon"], start, end)
                    weather_cache[key] = w
                    weather_vals = w or {}
                    if w is None:
                        n_skipped_weather += 1
                except Exception as e:
                    n_skipped_weather += 1
                    if args.verbose:
                        print(f"    weather fetch failed ({session}): {e}")
                time.sleep(API_PAUSE_S)

        new_text, filled = apply_updates(text, {"sky": sky_vals, "weather": weather_vals})

        if filled and not args.dry_run:
            if not safe_write(path, new_text):
                n_write_fail += 1
                write_failed.append(path)
                print(
                    f"  ! WRITE FAILED ({session}) - skipped, re-run after "
                    f"remounting the volume"
                )
                continue
        if any(f.startswith("sky.") for f in filled):
            n_sky += 1
        if any(f.startswith("weather.") for f in filled):
            n_weather += 1

        tag = "DRY " if args.dry_run else ""
        src = {
            "ondate": f"{nframes} frames",
            "offdate": f"{nframes} frames (off folder date)",
            "fallback": "no FITS (fallback window)",
        }[status]
        if args.verbose or filled:
            print(f"  {tag}{session}")
            print(f"      {loc} | window {start:%Y-%m-%d %H:%M}->{end:%H:%M} " f"local | {src}")
            print(f"      moon: {phase}, {illum}% illum, age {age}d")
            if weather_vals:
                print(
                    f"      weather: {weather_vals.get('temperature_c')}C "
                    f"{weather_vals.get('humidity_pct')}%RH "
                    f"cloud {weather_vals.get('cloud_cover_pct')}% "
                    f"\"{weather_vals.get('conditions')}\""
                )
            if filled:
                print(f"      filled: {', '.join(filled)}")
            else:
                print(f"      (nothing to fill - already populated)")

    print(f"\n{'DRY RUN - no files written' if args.dry_run else 'Done'}")
    print(f"  sessions with sky fields filled    : {n_sky}")
    print(f"  sessions with weather fields filled: {n_weather}")
    if n_have_weather:
        print(f"  sessions already had weather (skipped API): {n_have_weather}")
    if n_skipped_weather:
        print(f"  sessions with no weather data      : {n_skipped_weather}")
    if n_loc_unknown:
        print(f"  sessions with unknown location     : {n_loc_unknown}")
    if n_offdate:
        print(f"  sessions with mis-dated folder name: {n_offdate}  (see ! lines above)")
    if n_write_fail:
        print(f"  sessions that FAILED to write      : {n_write_fail}")
        print(
            f"  -> remount the volume and re-run; the script is idempotent "
            f"and will only touch these:"
        )
        for p in write_failed:
            print(f"       {p}")


if __name__ == "__main__":
    main()
