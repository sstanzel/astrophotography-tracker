"""Classification + census tests for intake_scan.py (synthetic trees only)."""

import datetime as dt
import os

import pytest

from conftest import write_file
from intake_scan import civil_night, scan_source

SETTINGS = {"copy_chn_logs": False}


def make_asiair_source(root: str) -> dict:
    """A tiny fake ASIAir dump exercising every disposition."""
    light = "Light_M 81_300.0s_Bin1_2600MC_gain100_20260420-2145{i:02d}_-20.0C_00{i:02d}.fit"
    for i in range(3):
        write_file(os.path.join(root, "Autorun", "Light", "M 81", light.format(i=i)))
    write_file(
        os.path.join(root, "Flat", "Flat_108.3ms_Bin1_2600MC_gain100_20260421-063546_21.5C_0001.fit")
    )
    write_file(
        os.path.join(root, "Dark", "Dark_300.0s_Bin1_2600MC_gain100_20260421-080000_-20.0C_0001.fit")
    )
    write_file(os.path.join(root, "log", "Autorun_Log_2026-04-20_214500.txt"))
    write_file(os.path.join(root, "log", "Autorun_Log_2026-04-20_214500_CHN.txt"))
    write_file(os.path.join(root, "log", "PHD2_GuideLog_2026-04-20_214000.txt"))
    write_file(os.path.join(root, "Autorun", "Light", "M 81", "not_a_frame_grammar.fit"))
    write_file(os.path.join(root, "Autorun", "Light", "M 81", "thumbnail.jpg"))
    write_file(
        os.path.join(root, "Preview", "M 81", "Preview_M 81_5.0s_Bin2_2600MC_gain0_x.fit")
    )
    write_file(os.path.join(root, "_CCC SafetyNet", "log", "Autorun_Log_old.txt"))
    write_file(os.path.join(root, ".DS_Store"))
    write_file(os.path.join(root, ".com.bombich.ccc.helper.casetest"))
    # A snapshot frame outside the pruned Preview dir
    write_file(os.path.join(root, "Autorun", "Light", "M 81", "snapshot_test.fit"))
    return {"id": "air", "label": "test", "path": root, "layout": "asiair"}


def make_nina_source(root: str) -> dict:
    """A tiny fake NINA dump with a date folder and an orphan-target frame."""
    name = (
        "LIGHT_M 106_300.00s_Bin1x1_Poseidon-C PRO_gain125_2026-05-12_22-56-1{i}_"
        "288.99deg_-20.00C__HFR2.83_RMS0.42_LQuadE_000{i}.fits"
    )
    for i in range(2):
        write_file(os.path.join(root, "2026-05-12", "LIGHT", name.format(i=i)))
    write_file(
        os.path.join(
            root,
            "2026-05-12",
            "FLAT",
            "FLAT__1.20s_Bin1x1_Poseidon-C PRO_gain125_2026-05-13_08-00-00_"
            "288.99deg_-20.00C__HFR_RMS_LQuadE_0001.fits",
        )
    )
    write_file(os.path.join(root, "$$TARGETNAME$$", "LIGHT", "2026-06-05_04-16-20__13.80_300.00s_0042.fits"))
    write_file(os.path.join(root, "Targets", "M 101.json"))
    return {"id": "mele", "label": "test", "path": root, "layout": "nina"}


def test_scan_source_asiair_census_balances(tmp_path):
    source = make_asiair_source(str(tmp_path / "air"))

    scan = scan_source(source, SETTINGS)

    assert len(scan["science"]) == 5  # 3 lights + 1 flat + 1 dark
    assert len(scan["logs"]) == 2  # CHN dupe goes to junk
    assert len(scan["non_science"]) == 1  # snapshot_ outside Preview/
    assert len(scan["quarantine"]) == 1  # not_a_frame_grammar.fit
    assert len(scan["ignored"]) == 1  # thumbnail.jpg
    assert len(scan["junk"]) == 3  # .DS_Store + ccc marker + CHN log
    assert any(p.endswith("Preview") for p in scan["pruned_dirs"])
    assert any(p.endswith("_CCC SafetyNet") for p in scan["pruned_dirs"])
    buckets = ("science", "logs", "non_science", "ignored", "junk", "quarantine")
    assert scan["scanned"] == sum(len(scan[k]) for k in buckets)


def test_scan_source_asiair_chn_logs_copied_when_enabled(tmp_path):
    source = make_asiair_source(str(tmp_path / "air"))

    scan = scan_source(source, {"copy_chn_logs": True})

    assert len(scan["logs"]) == 3
    assert len(scan["junk"]) == 2


def test_scan_source_nina_census_balances(tmp_path):
    source = make_nina_source(str(tmp_path / "nina"))

    scan = scan_source(source, SETTINGS)

    assert len(scan["science"]) == 3  # 2 lights + 1 flat
    assert len(scan["quarantine"]) == 1  # custom pattern, no grammar
    assert not any("Targets" in r["relpath"] for k in ("science", "ignored") for r in scan[k])
    lights = [r for r in scan["science"] if r["kind"] == "light"]
    assert all(r["target"] == "M 106" for r in lights)
    assert all(r["cam"] == "Poseidon-C PRO" for r in lights)


def test_scan_source_science_records_carry_night(tmp_path):
    source = make_nina_source(str(tmp_path / "nina"))

    scan = scan_source(source, SETTINGS)

    flat = next(r for r in scan["science"] if r["kind"] == "flat")
    # Shot 08:00 on the 13th — before noon, so it belongs to the night of the 12th.
    assert flat["night"] == dt.date(2026, 5, 12)


@pytest.mark.parametrize(
    "ts,expected",
    [
        (dt.datetime(2026, 7, 8, 23, 50), dt.date(2026, 7, 8)),
        (dt.datetime(2026, 7, 9, 0, 10), dt.date(2026, 7, 8)),
        (dt.datetime(2026, 7, 9, 11, 59), dt.date(2026, 7, 8)),
        (dt.datetime(2026, 7, 9, 12, 1), dt.date(2026, 7, 9)),
    ],
)
def test_civil_night_noon_boundary(ts, expected):
    assert civil_night(ts) == expected
