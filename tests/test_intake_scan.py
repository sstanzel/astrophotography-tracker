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


def test_scan_source_external_logs_dir(tmp_path):
    # PHD2 on a NINA PC writes logs OUTSIDE the source root ([[source]] logs=).
    source = make_nina_source(str(tmp_path / "nina"))
    phd2 = tmp_path / "PHD2"
    write_file(str(phd2 / "PHD2_GuideLog_2026-05-12_223000.txt"))
    write_file(str(phd2 / "PHD2_DebugLog_2026-05-12_223000.txt"))
    write_file(str(phd2 / ".DS_Store"))
    source["logs"] = str(phd2)

    scan = scan_source(source, SETTINGS)

    logs = [os.path.basename(r["relpath"]) for r in scan["logs"]]
    assert logs == ["PHD2_GuideLog_2026-05-12_223000.txt"]
    assert scan["logs"][0]["relpath"].startswith("..")  # anchored to source root
    assert any("DebugLog" in r["relpath"] for r in scan["ignored"])
    assert not scan.get("logs_dir_missing")
    buckets = ("science", "logs", "non_science", "ignored", "junk", "quarantine")
    assert scan["scanned"] == sum(len(scan[k]) for k in buckets)


def test_scan_source_missing_logs_dir_flagged(tmp_path):
    source = make_nina_source(str(tmp_path / "nina"))
    source["logs"] = str(tmp_path / "nowhere")

    scan = scan_source(source, SETTINGS)

    assert scan["logs_dir_missing"] == str(tmp_path / "nowhere")
    assert scan["logs"] == []


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


# --------------------------------------------------------------------------
# Device-local stamps + the dslr card-dump layout
# --------------------------------------------------------------------------
def test_asiair_evening_stamp_dates_the_same_night(tmp_path):
    # ASIAir 'dt' tokens are device-local (a 21:45 stamp is 21:45 at the
    # scope): an evening frame belongs to its own calendar date, unconverted.
    source = make_asiair_source(str(tmp_path / "air"))

    scan = scan_source(source, SETTINGS)

    lights = [r for r in scan["science"] if r["kind"] == "light"]
    assert lights and all(r["night"] == dt.date(2026, 4, 20) for r in lights)
    # The dedupe signature is the stamp itself, ISO-normalized.
    assert all(r["sig"].startswith("2026-04-20T21:45") for r in lights)


def _epoch(zone: str, *ymdhm: int) -> float:
    import zoneinfo

    return dt.datetime(*ymdhm, tzinfo=zoneinfo.ZoneInfo(zone)).timestamp()


def make_dslr_source(root: str) -> dict:
    """A card dump: a calibration folder + a target folder spanning midnight."""
    frames = [
        ("R5 calibration/R5__0001.CR3", _epoch("America/Denver", 2026, 7, 13, 14, 0)),
        ("R5 calibration/R5__0002.CR3", _epoch("America/Denver", 2026, 7, 13, 14, 5)),
        ("M 31/R5__0101.CR3", _epoch("America/Denver", 2026, 7, 13, 22, 30)),
        ("M 31/R5__0102.CR3", _epoch("America/Denver", 2026, 7, 14, 1, 15)),
    ]
    for rel, epoch in frames:
        path = os.path.join(root, rel)
        write_file(path)
        os.utime(path, (epoch, epoch))
    write_file(os.path.join(root, "M 31", "notes.txt"))
    return {"id": "r5", "label": "R5 card", "path": root, "layout": "dslr", "logs": ""}


def test_dslr_layout_dates_by_mtime_and_folder_semantics(tmp_path):
    source = make_dslr_source(str(tmp_path / "card"))

    scan = scan_source(source, {"timezone": "America/Denver"})

    by_kind = {}
    for r in scan["science"]:
        by_kind.setdefault(r["kind"], []).append(r)
    # Calibration-token folder -> calibration dump records.
    assert len(by_kind["raw cal"]) == 2
    assert all(r["target"] == "" for r in by_kind["raw cal"])
    # Target folder -> lights; evening + after-midnight join ONE civil night.
    assert len(by_kind["light"]) == 2
    assert {r["night"] for r in by_kind["light"]} == {dt.date(2026, 7, 13)}
    assert all(r["target"] == "M 31" and r["cam"] == "R5" for r in by_kind["light"])
    # Signatures are epoch seconds (mtime survives CCC + intake copies).
    assert all(r["sig"].isdigit() for r in scan["science"])
    assert any("notes.txt" in r["relpath"] for r in scan["ignored"])


def test_dslr_night_follows_configured_timezone(tmp_path):
    # 08:00 July 14 in Denver (-> night 07-13) is 14:00 in Reykjavik
    # (-> night 07-14): the [intake] timezone decides mtime-dated nights.
    root = str(tmp_path / "card")
    path = os.path.join(root, "M 31", "R5__0201.CR3")
    write_file(path)
    epoch = _epoch("America/Denver", 2026, 7, 14, 8, 0)
    os.utime(path, (epoch, epoch))
    source = {"id": "r5", "label": "x", "path": root, "layout": "dslr", "logs": ""}

    denver = scan_source(source, {"timezone": "America/Denver"})
    reykjavik = scan_source(source, {"timezone": "Atlantic/Reykjavik"})

    assert denver["science"][0]["night"] == dt.date(2026, 7, 13)
    assert reykjavik["science"][0]["night"] == dt.date(2026, 7, 14)


def test_unknown_timezone_fails_loud(tmp_path):
    source = make_dslr_source(str(tmp_path / "card"))
    with pytest.raises(SystemExit, match="timezone"):
        scan_source(source, {"timezone": "Mars/Olympus_Mons"})
