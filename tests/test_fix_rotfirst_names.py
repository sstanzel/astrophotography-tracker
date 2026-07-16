"""Tests for fix_rotfirst_names.py - the one-time rot-first rename pass.

All tree tests run against synthetic folders under tmp_path, never real
volumes (same convention as the intake suite).
"""

import os

import fits_parser
from conftest import write_file
from fix_rotfirst_names import canonical_name, collect

ROTFIRST_LIGHT = "Light_M 81_63deg_180.0s_Bin1_585MC_gain200_20260226-213045_-20.0C_0012.fit"
CANONICAL_LIGHT = "Light_M 81_180.0s_Bin1_585MC_gain200_20260226-213045_63deg_-20.0C_0012.fit"


# --------------------------------------------------------------------------
# canonical_name
# --------------------------------------------------------------------------
def test_canonical_name_light_moves_rot_after_timestamp():
    assert canonical_name(ROTFIRST_LIGHT) == CANONICAL_LIGHT


def test_canonical_name_light_with_filter_keeps_filter_after_temp():
    old = "Light_NGC 3718_74deg_120.0s_Bin1_585MC_gain252_20260224-011157_-18.7C_LQuadE_0054.fit"
    new = "Light_NGC 3718_120.0s_Bin1_585MC_gain252_20260224-011157_74deg_-18.7C_LQuadE_0054.fit"

    assert canonical_name(old) == new


def test_canonical_name_flat_ms_unit_no_target():
    old = "Flat_156deg_108.3ms_Bin1_585MC_gain200_20260215-084517_-19.5C_0008.fit"
    new = "Flat_108.3ms_Bin1_585MC_gain200_20260215-084517_156deg_-19.5C_0008.fit"

    assert canonical_name(old) == new


def test_canonical_name_dark_rotfirst_renamed():
    old = "Dark_63deg_300.0s_Bin1_2600MC_gain100_20260130-080000_-20.0C_0010.fit"
    new = "Dark_300.0s_Bin1_2600MC_gain100_20260130-080000_63deg_-20.0C_0010.fit"

    assert canonical_name(old) == new


def test_canonical_name_preserves_angle_value_exactly():
    # Meridian-flip / wrap values are information - never snapped or dropped.
    old = "Light_NGC 1501_360.0deg_60.0s_Bin1_2600MC_gain100_20251230-213045_-9.9C_0001.fit"

    new = canonical_name(old)

    assert new is not None and "_360.0deg_" in new


def test_canonical_name_already_canonical_returns_none():
    assert canonical_name(CANONICAL_LIGHT) is None


def test_canonical_name_rename_is_idempotent():
    new = canonical_name(ROTFIRST_LIGHT)

    assert canonical_name(new) is None


def test_canonical_name_non_matching_names_return_none():
    for name in (
        "Light_M 81_300.0s_Bin1_2600MC_gain100_20260420-044507_-20.0C_0061.fit",  # no rot at all
        "Light_M51_300.0s_Bin1_ISO1600_20240605-221706_38.0C_R5_0001.fit",  # DSLR
        "LIGHT_M 106_300.00s_1x1_Poseidon-C PRO_125_2026-05-12_23-13-37_288.99_-20.10__0003.fits",
        "Preview_M 81_5.0s_Bin2_2600MC_gain0_20260419-221114_-20.1C.fit",
        "masterDark_ASI585MCPro_300s_gain0_-10C_2024-12-19.xisf",
        "notes.toml",
    ):
        assert canonical_name(name) is None, name


def test_canonical_name_output_parses_under_canonical_grammar():
    new = canonical_name(ROTFIRST_LIGHT)

    m = fits_parser.parse(new)

    assert m is not None and m.re is fits_parser.ASIAIR_SCI
    assert m.group("rot") == "63" and m.group("dt") == "20260226-213045"


# --------------------------------------------------------------------------
# collect (synthetic library trees)
# --------------------------------------------------------------------------
def _session(tmp_path, *parts) -> str:
    p = os.path.join(str(tmp_path), *parts)
    os.makedirs(p, exist_ok=True)
    return p


def test_collect_finds_lights_rejected_and_session_calibration(tmp_path):
    sess = "M 81 Bodes Galaxy/M_81 Redcat51 ASI585MCPro 2026-02-26"
    write_file(os.path.join(_session(tmp_path, sess, "Light"), ROTFIRST_LIGHT))
    write_file(
        os.path.join(
            _session(tmp_path, sess, "Light", "Rejected"),
            "Light_M 81_64deg_180.0s_Bin1_585MC_gain200_20260226-221000_-20.0C_0019.fit",
        )
    )
    write_file(
        os.path.join(
            _session(tmp_path, sess, "Flat"),
            "Flat_63deg_108.3ms_Bin1_585MC_gain200_20260227-084517_-19.5C_0001.fit",
        )
    )

    actions, warnings = collect(str(tmp_path))

    assert len(actions) == 3 and warnings == []
    assert {os.path.basename(a["dst"]) for a in actions} == {
        CANONICAL_LIGHT,
        "Light_M 81_180.0s_Bin1_585MC_gain200_20260226-221000_64deg_-20.0C_0019.fit",
        "Flat_108.3ms_Bin1_585MC_gain200_20260227-084517_63deg_-19.5C_0001.fit",
    }


def test_collect_skips_results_and_scratch_folders(tmp_path):
    sess = "M 81 Bodes Galaxy/M_81 Redcat51 ASI585MCPro 2026-02-26"
    write_file(os.path.join(_session(tmp_path, sess, "M_81 Results"), ROTFIRST_LIGHT))
    write_file(os.path.join(_session(tmp_path, sess, "PI Process"), ROTFIRST_LIGHT))
    write_file(os.path.join(_session(tmp_path, sess, "PI Magic"), ROTFIRST_LIGHT))

    actions, warnings = collect(str(tmp_path))

    assert actions == [] and warnings == []


def test_collect_skips_underscore_toplevel_except_calibration_library(tmp_path):
    write_file(
        os.path.join(
            _session(tmp_path, "_organization snapshot", "Light"),
            ROTFIRST_LIGHT,
        )
    )
    cal_dark = "Dark_63deg_300.0s_Bin1_2600MC_gain100_20260130-080000_-20.0C_0010.fit"
    write_file(
        os.path.join(
            _session(tmp_path, "_Calibration Library", "Dark", "ASI2600MCAir", "-20C"),
            cal_dark,
        )
    )

    actions, _ = collect(str(tmp_path))

    assert [os.path.basename(a["src"]) for a in actions] == [cal_dark]


def test_collect_collision_warns_and_skips(tmp_path):
    light_dir = _session(tmp_path, "T/T Redcat51 ASI585MCPro 2026-02-26", "Light")
    write_file(os.path.join(light_dir, ROTFIRST_LIGHT))
    write_file(os.path.join(light_dir, CANONICAL_LIGHT))  # target name taken

    actions, warnings = collect(str(tmp_path))

    assert actions == []
    assert len(warnings) == 1 and "already exists" in warnings[0]


def test_collect_then_rename_applies_cleanly(tmp_path):
    light_dir = _session(tmp_path, "T/T Redcat51 ASI585MCPro 2026-02-26", "Light")
    write_file(os.path.join(light_dir, ROTFIRST_LIGHT))

    actions, _ = collect(str(tmp_path))
    for a in actions:
        os.rename(a["src"], a["dst"])

    assert sorted(os.listdir(light_dir)) == [CANONICAL_LIGHT]
    # Second pass finds nothing - the rename is one-way.
    assert collect(str(tmp_path)) == ([], [])
