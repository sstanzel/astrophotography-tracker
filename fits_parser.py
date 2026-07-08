"""
fits_parser.py — single source of truth for parsing science and calibration
frame filenames out of Steve's astrophotography library.

Seven filename grammars are supported.  parse(name) tries each in order and
returns the first regex Match, or None.

1. ASIAir science      — Light_{target}_{exp}{s|ms}_Bin{N}_{cam}_gain{G}_{dt}_[{rot}deg_]{temp}C[_{filter}]_{idx}.fit
2. ASIAir calibration  — {type}_{exp}{s|ms}_Bin{N}_{cam}_gain{G}_{dt}_[{rot}deg_]{temp}C[_{filter}]_{idx}.fit
3. NINA legacy         — LIGHT_{target}_{exp}s_{NxN}_{cam-with-spaces}_{gain}_{date}_{time}_{rot}_{temp}__{idx}.fits
4. NINA v2 science     — LIGHT_{target}_{exp}s_Bin{NxN}_{cam}_gain{G}_{date}_{time}_{rot}deg_{temp}C__HFR{?}_RMS{?}_{filter}_{idx}.fits
5. NINA v2 calibration — same as #4 but target may be empty (FLAT__... etc.)
6. ASIAir DSLR science     — Light_{target}_{exp}{s|ms}_Bin{N}_ISO{iso}_{dt}_[{rot}deg_]{temp}C_{cam}_{idx}.fit
7. ASIAir DSLR calibration — {type}_{exp}{s|ms}_Bin{N}_ISO{iso}_{dt}_[{rot}deg_]{temp}C_{cam}_{idx}.fit

For DSLR captures the ISO value lands in the 'gain' group (ASIAir's ISO is the
DSLR analogue of gain), and 'cam' is the free-text setup name the user typed
into the ASIAir (e.g. 'R5', 'First_setup') — it can contain underscores.

Common named groups across all grammars: type, target (may be None or empty),
exp, unit (s or ms; defaults to s for NINA), cam, gain, dt or date+time, rot,
temp, filter (may be None or empty), idx, ext.
"""
import re

# 1) ASIAir science — target present, optional filter, optional rotation
ASIAIR_SCI = re.compile(
    r"^(?P<type>Light)_"
    r"(?P<target>.+?)_"
    r"(?P<exp>[\d.]+)(?P<unit>s|ms)_"
    r"Bin(?P<bin>\d+)_"
    r"(?P<cam>[^_]+)_"
    r"gain(?P<gain>-?\d+)_"
    r"(?P<dt>\d{8}-\d{6})_"
    r"(?:(?P<rot>-?[\d.]+)deg_)?"
    r"(?P<temp>-?[\d.]+)C"
    r"(?:_(?P<filter>[A-Za-z][\w]*))?"
    r"_(?P<idx>\d+)\.(?P<ext>fit|fits|xisf)$",
    re.IGNORECASE,
)

# 2) ASIAir calibration — same shape but no target token
ASIAIR_CAL = re.compile(
    r"^(?P<type>Flat|Dark|Bias|DarkFlat|Dark Flat)_"
    r"(?P<exp>[\d.]+)(?P<unit>s|ms)_"
    r"Bin(?P<bin>\d+)_"
    r"(?P<cam>[^_]+)_"
    r"gain(?P<gain>-?\d+)_"
    r"(?P<dt>\d{8}-\d{6})_"
    r"(?:(?P<rot>-?[\d.]+)deg_)?"
    r"(?P<temp>-?[\d.]+)C"
    r"(?:_(?P<filter>[A-Za-z][\w]*))?"
    r"_(?P<idx>\d+)\.(?P<ext>fit|fits|xisf)$",
    re.IGNORECASE,
)

# 3) NINA legacy — what Steve's two existing PoseidenCPro sessions use
NINA_LEGACY = re.compile(
    r"^(?P<type>LIGHT|DARK|FLAT|BIAS|DARKFLAT)_"
    r"(?P<target>.*?)_"
    r"(?P<exp>[\d.]+)s_"
    r"(?P<binx>\d+)x(?P<biny>\d+)_"
    r"(?P<cam>[^_]+)_"
    r"(?P<gain>-?\d+)_"
    r"(?P<date>\d{4}-\d{2}-\d{2})_(?P<time>\d{2}-\d{2}-\d{2})_"
    r"(?P<rot>-?[\d.]+)_"
    r"(?P<temp>-?[\d.]+)__"
    r"(?P<idx>\d+)\.(?P<ext>fit|fits|xisf)$",
    re.IGNORECASE,
)

# 4) NINA v2 — Steve's new agreed pattern, with HFR & RMS QC fields
#    Pattern: IMAGETYPE_TARGETNAME_EXPs_BinBxB_CAMERA_gainG_DATE_TIME_ROTdeg_TEMPC__HFR{x}_RMS{x}_FILTER_FRAMENR
#    Note: the literal between TEMPC and HFR is TWO underscores (Steve wrote __HFR)
#    HFR, RMS, FILTER may be empty when not populated by NINA.
NINA_V2 = re.compile(
    r"^(?P<type>LIGHT|DARK|FLAT|BIAS|DARKFLAT)_"
    r"(?P<target>.*?)_"
    r"(?P<exp>[\d.]+)s_"
    r"Bin(?P<binx>\d+)x(?P<biny>\d+)_"
    r"(?P<cam>.+?)_"
    r"gain(?P<gain>-?\d+)_"
    r"(?P<date>\d{4}-\d{2}-\d{2})_(?P<time>\d{2}-\d{2}-\d{2})_"
    r"(?P<rot>-?[\d.]*)deg_"      # rot may be empty (no rotator): "..._deg_..."
    r"(?P<temp>-?[\d.]+)C__"
    r"HFR(?P<hfr>[\d.]*)_"
    r"RMS(?P<rms>[\d.]*)_"
    r"(?P<filter>\w*)_"
    r"(?P<idx>\d+)\.(?P<ext>fit|fits|xisf)$",
    re.IGNORECASE,
)

# 6) ASIAir DSLR science — Canon/DSLR shot through an ASIAir. ISO replaces
#    {cam}_gain{G}, and a free-text setup token sits AFTER the temperature.
#    The setup token can contain underscores ("First_setup"), so it is matched
#    non-greedily up to the trailing _{idx}.
ASIAIR_DSLR_SCI = re.compile(
    r"^(?P<type>Light)_"
    r"(?P<target>.+?)_"
    r"(?P<exp>[\d.]+)(?P<unit>s|ms)_"
    r"Bin(?P<bin>\d+)_"
    r"ISO(?P<gain>\d+)_"
    r"(?P<dt>\d{8}-\d{6})_"
    r"(?:(?P<rot>-?[\d.]+)deg_)?"
    r"(?P<temp>-?[\d.]+)C_"
    r"(?P<cam>.+?)_"
    r"(?P<idx>\d+)\.(?P<ext>fit|fits|xisf)$",
    re.IGNORECASE,
)

# 7) ASIAir DSLR calibration — same shape, no target token.
ASIAIR_DSLR_CAL = re.compile(
    r"^(?P<type>Flat|Dark|Bias|DarkFlat|Dark Flat)_"
    r"(?P<exp>[\d.]+)(?P<unit>s|ms)_"
    r"Bin(?P<bin>\d+)_"
    r"ISO(?P<gain>\d+)_"
    r"(?P<dt>\d{8}-\d{6})_"
    r"(?:(?P<rot>-?[\d.]+)deg_)?"
    r"(?P<temp>-?[\d.]+)C_"
    r"(?P<cam>.+?)_"
    r"(?P<idx>\d+)\.(?P<ext>fit|fits|xisf)$",
    re.IGNORECASE,
)

PARSERS = (NINA_V2, NINA_LEGACY, ASIAIR_SCI, ASIAIR_CAL,
           ASIAIR_DSLR_SCI, ASIAIR_DSLR_CAL)


def parse(name):
    """Return the first matching regex.Match, or None.

    The match's .groupdict() contains all named groups; some will be None
    depending on which grammar matched. Use safe_get(m, 'unit', 's') style
    access if you need a default for missing keys.
    """
    for r in PARSERS:
        m = r.match(name)
        if m:
            return m
    return None


def safe(m, key, default=None):
    """Safely pull a named group from a match (None if the group didn't exist)."""
    try:
        v = m.group(key)
        return v if v else default
    except IndexError:
        return default


def frame_kind(m):
    """Normalise the type token to one of: light, flat, dark, bias, darkflat."""
    return m.group("type").lower().replace(" ", "")


def exposure_seconds(m):
    """Return exposure in seconds as a float, or None if unit is ms (use ms_value())."""
    if not m:
        return None
    unit = (safe(m, "unit", "s") or "s").lower()
    if unit != "s":
        return None
    return float(m.group("exp"))


if __name__ == "__main__":
    # Smoke test
    samples = [
        # ASIAir science
        ("Light_M 81_300.0s_Bin1_2600MC_gain100_20260420-044507_-20.0C_0061.fit", "ASIAIR_SCI", "Light"),
        ("Light_M 66_300.0s_Bin1_2600MC_gain100_20260309-035017_172deg_-19.9C_0025.fit", "ASIAIR_SCI", "Light"),
        ("Light_NGC 3718_120.0s_Bin1_585MC_gain252_20260424-011157_74deg_-18.7C_LQuadE_0054.fit", "ASIAIR_SCI", "Light"),
        ("Light_M 81_180.0s_Bin1_585MC_gain200_20260419-015230_74deg_-20.0C_LQuadEnhance_0066.fit", "ASIAIR_SCI", "Light"),
        ("Light_Moon_10.0ms_Bin1_2600MC_gain-25_20260422-210144_-14.5C_0035.fit", "ASIAIR_SCI", "Light"),
        # ASIAir calibration
        ("Flat_108.3ms_Bin1_585MC_gain200_20260309-084517_156deg_-19.5C_0008.fit", "ASIAIR_CAL", "Flat"),
        ("Dark_300.0s_Bin1_2600MC_gain100_20260420-080000_-20.0C_0010.fit", "ASIAIR_CAL", "Dark"),
        # NINA legacy
        ("LIGHT_M 106_300.00s_1x1_Poseidon-C PRO_125_2026-05-12_23-13-37_288.99_-20.10__0003.fits", "NINA_LEGACY", "LIGHT"),
        ("LIGHT_NGC 4406_120.00s_1x1_Poseidon-C PRO_125_2026-05-14_22-08-12_45.50_-20.00__0001.fits", "NINA_LEGACY", "LIGHT"),
        # NINA v2 — Steve's new pattern
        ("LIGHT_M 106_300.00s_Bin1x1_Poseidon-C PRO_gain125_2026-05-12_22-56-19_288.99deg_-20.00C__HFR2.83_RMS0.42_LQuadE_0001.fits", "NINA_V2", "LIGHT"),
        ("LIGHT_M 106_300.00s_Bin1x1_Poseidon-C PRO_gain125_2026-05-12_22-56-19_288.99deg_-20.00C__HFR_RMS__0002.fits", "NINA_V2", "LIGHT"),  # empty HFR/RMS/filter
        ("FLAT__1.20s_Bin1x1_Poseidon-C PRO_gain125_2026-05-12_18-00-00_288.99deg_-20.00C__HFR_RMS_LQuadE_0001.fits", "NINA_V2", "FLAT"),    # flat, no target
        # ASIAir DSLR
        ("Light_M51_300.0s_Bin1_ISO1600_20240605-221706_38.0C_R5_0001.fit", "ASIAIR_DSLR_SCI", "Light"),
        ("Light_M44_300.0s_Bin1_ISO1600_20240517-222003_31.0C_First_setup_0001.fit", "ASIAIR_DSLR_SCI", "Light"),  # underscored cam token
        ("Flat_41.7ms_Bin1_ISO1600_20240605-070350_35.0C_First_setup_0001.fit", "ASIAIR_DSLR_CAL", "Flat"),
        # Should NOT parse
        ("Preview_M 81_5.0s_Bin2_2600MC_gain0_20260419-221114_-20.1C.fit", "FAIL", None),
        ("Light_IC 342_300.0s_Bin1_585MC_gain200_20260331-015432_158deg_-19.8C_0011_c_d.xisf", "FAIL", None),  # PI Process debayered
    ]
    width = max(len(s[0]) for s in samples) + 2
    print(f"{'Filename':{width}s}  Result")
    print("-" * (width + 25))
    for fn, expected_grammar, expected_type in samples:
        m = parse(fn)
        if m is None:
            verdict = "FAIL (no match)" if expected_grammar != "FAIL" else "FAIL (correctly rejected)"
        else:
            ftype = m.group("type")
            verdict = f"{ftype:5s} exp={m.group('exp')}s filter={safe(m,'filter','-')}"
        flag = "  ✗" if (m is None) != (expected_grammar == "FAIL") else "  ✓"
        print(f"{fn:{width}s}  {verdict}{flag}")
