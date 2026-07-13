"""Grouping tests: sessions, hosts, dark-flat heuristic, filters, logs."""

import datetime as dt

from intake_scan import group_sessions, log_night, session_folder_name

RIG = {
    "source": "air",
    "camera": "2600MC",
    "scope": "RASA8",
    "sensor": "ASI2600MCAir",
    "adjacent": False,
    "from": None,
    "to": None,
}
ADJ_RIG = {**RIG, "source": "mele", "camera": "QHYminiCam8M", "scope": "Redcat51",
           "sensor": "minicam8", "adjacent": True}
RIGS = [RIG, ADJ_RIG]


def rec(kind, target, ts, cam="2600MC", exp="300.0", unit="s", relpath=None, size=100):
    """A minimal science record as scan_source() would emit it."""
    from intake_scan import civil_night

    return {
        "relpath": relpath or f"Autorun/{kind}/{target or 'x'}/{ts:%H%M%S}.fit",
        "size": size,
        "mtime_ns": 0,
        "kind": kind,
        "grammar": "test",
        "cam": cam,
        "target": target,
        "ts": ts,
        "night": civil_night(ts),
        "exp": exp,
        "unit": unit,
        "gain": "100",
        "temp": "-20.0",
    }


def scan_of(science, logs=()):
    return {"science": list(science), "logs": list(logs)}


NIGHT = dt.datetime(2026, 7, 8, 22, 0)


def test_group_sessions_one_target_night_makes_one_session():
    scans = {"air": scan_of([rec("light", "M 5", NIGHT), rec("light", "M 5", NIGHT.replace(hour=23))])}

    plan = group_sessions(scans, RIGS)

    assert len(plan["sessions"]) == 1
    sess = plan["sessions"][0]
    assert sess["name"] == "M_5 RASA8 ASI2600MCAir 2026-07-08"
    assert len(sess["lights"]) == 2
    assert plan["selected"] == 2


def test_group_sessions_after_midnight_lights_join_the_same_night():
    scans = {"air": scan_of([
        rec("light", "M 5", NIGHT),
        rec("light", "M 5", dt.datetime(2026, 7, 9, 1, 30)),
    ])}

    plan = group_sessions(scans, RIGS)

    assert len(plan["sessions"]) == 1
    assert plan["sessions"][0]["night"] == dt.date(2026, 7, 8)


def test_group_sessions_flats_attach_to_last_ending_session():
    scans = {"air": scan_of([
        rec("light", "M 5", NIGHT),                                # ends first
        rec("light", "M 13", dt.datetime(2026, 7, 9, 3, 0)),        # last-ending
        rec("flat", "", dt.datetime(2026, 7, 9, 7, 0), exp="0.108", unit="s"),
    ])}

    plan = group_sessions(scans, RIGS)

    by_name = {s["name"]: s for s in plan["sessions"]}
    assert len(by_name["M_13 RASA8 ASI2600MCAir 2026-07-08"]["flats"]) == 1
    assert len(by_name["M_5 RASA8 ASI2600MCAir 2026-07-08"]["flats"]) == 0


def test_group_sessions_dark_at_flat_exposure_becomes_darkflat():
    scans = {"air": scan_of([
        rec("light", "M 5", NIGHT),
        rec("flat", "", NIGHT.replace(hour=19), exp="108.3", unit="ms"),
        rec("dark", "", NIGHT.replace(hour=20), exp="108.3", unit="ms"),
        rec("dark", "", NIGHT.replace(hour=21), exp="300.0", unit="s"),
    ])}

    plan = group_sessions(scans, RIGS)

    sess = plan["sessions"][0]
    assert len(sess["flats"]) == 1 and len(sess["darkflats"]) == 1
    assert len(plan["calibration"]) == 1  # the 300 s dark is library material
    assert plan["calibration"][0]["source"] == "air"


def test_group_sessions_adjacent_rig_appends_suffix():
    scans = {"mele": scan_of([rec("light", "M 12", NIGHT, cam="QHYminiCam8M")])}

    plan = group_sessions(scans, RIGS)

    assert plan["sessions"][0]["name"] == "M_12_adjacent Redcat51 minicam8 2026-07-08"


def test_group_sessions_target_spelling_variants_merge():
    # "M 12" and "M_12" normalize to the same session — never two folders.
    scans = {"air": scan_of([
        rec("light", "M 12", NIGHT),
        rec("light", "M_12", NIGHT.replace(hour=23)),
    ])}

    plan = group_sessions(scans, RIGS)

    assert len(plan["sessions"]) == 1
    assert len(plan["sessions"][0]["lights"]) == 2


def test_group_sessions_adjacent_suffix_not_doubled():
    # NINA target names like "M 106 adjacent" already carry the suffix.
    scans = {"mele": scan_of([rec("light", "M 106 adjacent", NIGHT, cam="QHYminiCam8M")])}

    plan = group_sessions(scans, RIGS)

    assert plan["sessions"][0]["name"] == "M_106_adjacent Redcat51 minicam8 2026-07-08"


def test_group_sessions_unmapped_camera_reported_not_guessed():
    scans = {"air": scan_of([rec("light", "M 5", NIGHT, cam="MysteryCam")])}

    plan = group_sessions(scans, RIGS)

    assert not plan["sessions"]
    assert plan["unmapped"][0]["cam"] == "MysteryCam"
    assert len(plan["unmapped"][0]["records"]) == 1


def test_group_sessions_no_target_light_quarantined():
    scans = {"air": scan_of([rec("light", "", NIGHT)])}

    plan = group_sessions(scans, RIGS)

    assert not plan["sessions"]
    assert plan["quarantine"][0]["reason"] == "light frame with no target token"


def test_group_sessions_flats_without_lights_unattached():
    scans = {"air": scan_of([rec("flat", "", NIGHT, exp="0.1", unit="s")])}

    plan = group_sessions(scans, RIGS)

    assert not plan["sessions"]
    assert "no light session" in plan["unattached"][0]["reason"]


def test_group_sessions_night_filter_counts_filtered_out():
    scans = {"air": scan_of([
        rec("light", "M 5", NIGHT),
        rec("light", "M 5", dt.datetime(2026, 6, 1, 22, 0)),
    ])}

    plan = group_sessions(scans, RIGS, since=dt.date(2026, 7, 1))

    assert plan["selected"] == 1
    assert plan["filtered_out"] == 1
    assert len(plan["sessions"]) == 1


def test_group_sessions_logs_attach_to_nights_last_session():
    logs = [
        {"relpath": "log/Autorun_Log_2026-07-08_223348.txt", "size": 10, "mtime_ns": 0},
        {"relpath": "log/PHD2_GuideLog_2026-07-09_013000.txt", "size": 10, "mtime_ns": 0},
        {"relpath": "log/Autorun_Log_2026-01-01_010101.txt", "size": 10, "mtime_ns": 0},
    ]
    scans = {"air": scan_of(
        [rec("light", "M 5", NIGHT), rec("light", "M 13", dt.datetime(2026, 7, 9, 2, 0))],
        logs,
    )}

    plan = group_sessions(scans, RIGS)

    by_name = {s["name"]: s for s in plan["sessions"]}
    assert len(by_name["M_13 RASA8 ASI2600MCAir 2026-07-08"]["logs"]) == 2
    assert len(by_name["M_5 RASA8 ASI2600MCAir 2026-07-08"]["logs"]) == 0
    # The January log has no session that night → unattached.
    assert any("no light session" in r["reason"] for r in plan["unattached"])


def test_log_night_parses_asiair_stamp():
    assert log_night("Autorun_Log_2026-04-20_081910.txt") == dt.date(2026, 4, 19)
    assert log_night("PHD2_GuideLog_2026-04-20_214532.txt") == dt.date(2026, 4, 20)
    assert log_night("Autorun_Log_garbled.txt") is None


def test_session_folder_name_matches_grammar():
    from scan import SESSION_RE

    name = session_folder_name("M 5", RIG, dt.date(2026, 7, 8))

    assert name == "M_5 RASA8 ASI2600MCAir 2026-07-08"
    assert SESSION_RE.match(name)
