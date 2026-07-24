"""Session-row lifecycle tests: SESSION_MISSING finding, forget.py, method stamp."""

import os
import sqlite3

import pytest

import forget
import scan as scan_mod
from populate_notes import stamp_method

SCHEMA = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "internal", "schema.sql"
)


def make_db(tmp_path):
    con = sqlite3.connect(str(tmp_path / "tracker.db"))
    con.executescript(open(SCHEMA, encoding="utf-8").read())
    con.execute(
        "INSERT INTO libraries(library_id, root_path, label, role) VALUES('lib1','/x','L1','working')"
    )
    for scope in ("RASA8", "Pleiades111"):
        con.execute("INSERT INTO scopes(scope) VALUES(?)", (scope,))
    con.execute("INSERT INTO sensors(sensor) VALUES('ASI2600MCAir')")
    con.execute(
        "INSERT INTO targets(target_id, catalog, number, common_name, folder_name)"
        " VALUES('M_5','M','5','globular cluster','M 5 globular cluster')"
    )
    con.execute(
        "INSERT INTO sessions(target_id, scope, sensor, session_date, library_id,"
        " folder_path, lights_kept, lights_rejected, integration_s)"
        " VALUES('M_5','RASA8','ASI2600MCAir','2026-07-08','lib1',"
        " 'M 5 globular cluster/M_5 RASA8 ASI2600MCAir 2026-07-08', 60, 2, 7200)"
    )
    con.commit()
    return con


def run_validate(con, obs):
    logs = []
    scan_mod.validate(con, {}, obs, logs.append)
    return {
        (code, ref)
        for code, ref in con.execute("SELECT code, ref_path FROM validation_findings")
    }


def test_session_missing_flagged_when_library_scanned_but_row_unseen(tmp_path):
    con = make_db(tmp_path)

    findings = run_validate(
        con,
        {"scanned_library_ids": ["lib1"], "seen_session_ids": set(),
         "seen_integration_paths": set()},
    )

    assert any(code == "SESSION_MISSING" for code, _ in findings)
    msg = con.execute(
        "SELECT message FROM validation_findings WHERE code='SESSION_MISSING'"
    ).fetchone()[0]
    assert "forget.py" in msg and "60 kept" in msg


def test_session_missing_not_flagged_when_row_seen(tmp_path):
    con = make_db(tmp_path)
    sid = con.execute("SELECT session_id FROM sessions").fetchone()[0]

    findings = run_validate(
        con,
        {"scanned_library_ids": ["lib1"], "seen_session_ids": {sid},
         "seen_integration_paths": set()},
    )

    assert not any(code == "SESSION_MISSING" for code, _ in findings)


def test_session_missing_not_flagged_when_library_unscanned(tmp_path):
    # Peak offline while traveling: its rows carry no information this pass.
    con = make_db(tmp_path)

    findings = run_validate(
        con,
        {"scanned_library_ids": ["other-lib"], "seen_session_ids": set(),
         "seen_integration_paths": set()},
    )

    assert not any(code == "SESSION_MISSING" for code, _ in findings)


def test_session_missing_hints_at_rename_successor(tmp_path):
    # A re-target (the NGC 3729 → 3718 shape): same rig, same night, same
    # frame count, new target/name — the old row goes missing, the new row is
    # seen, and the finding should name the successor.
    con = make_db(tmp_path)
    con.execute(
        "INSERT INTO targets(target_id, catalog, number, common_name, folder_name)"
        " VALUES('NGC_1','NGC','1','test','NGC 1 test')"
    )
    con.execute(
        "INSERT INTO sessions(target_id, scope, sensor, session_date, library_id,"
        " folder_path, lights_kept, lights_rejected)"
        " VALUES('NGC_1','RASA8','ASI2600MCAir','2026-07-08','lib1',"
        " 'NGC 1 test/NGC_1 RASA8 ASI2600MCAir 2026-07-08', 62, 0)"
    )
    con.commit()
    successor_sid = con.execute("SELECT max(session_id) FROM sessions").fetchone()[0]

    run_validate(
        con,
        {"scanned_library_ids": ["lib1"], "seen_session_ids": {successor_sid},
         "seen_integration_paths": set()},
    )

    msg = con.execute(
        "SELECT message FROM validation_findings WHERE code='SESSION_MISSING'"
    ).fetchone()[0]
    assert "successor" in msg and "NGC_1 RASA8 ASI2600MCAir 2026-07-08" in msg


# --------------------------------------------------------------------------
# forget.py
# --------------------------------------------------------------------------
def test_forget_session_preview_and_apply(tmp_path, monkeypatch):
    con = make_db(tmp_path)
    con.close()
    db = str(tmp_path / "tracker.db")
    lib_root = tmp_path / "lib"
    (lib_root / "M 5 globular cluster").mkdir(parents=True)
    monkeypatch.setattr(
        forget.astro_config, "load_libraries",
        lambda config_path=None: [
            {"id": "lib1", "path": str(lib_root), "label": "L1", "role": "working"},
            {"id": "off", "path": str(tmp_path / "nope"), "label": "Offline", "role": "archive"},
        ],
    )
    monkeypatch.setattr(forget.astro_config, "log_actions", lambda s, ls: None)
    name = "M_5 RASA8 ASI2600MCAir 2026-07-08"

    con = sqlite3.connect(db)
    lines = forget.forget_session(con, name, apply=False)
    assert lines == []
    assert con.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 1

    lines = forget.forget_session(con, name, apply=True)
    assert len(lines) == 1 and "forget session" in lines[0]
    assert con.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
    con.close()


def test_forget_refuses_unknown_name(tmp_path):
    con = make_db(tmp_path)

    with pytest.raises(SystemExit) as exc:
        forget.forget_session(con, "Nonexistent Session 2026-01-01", apply=True)

    assert "nothing to forget" in str(exc.value)
    assert con.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 1


def test_find_on_disk_finds_and_reports_unmounted(tmp_path):
    lib_root = tmp_path / "lib"
    (lib_root / "M 5 globular cluster" / "M_5 RASA8 X 2026-07-08").mkdir(parents=True)
    libs = [
        {"id": "a", "path": str(lib_root), "label": "L1", "role": "working"},
        {"id": "b", "path": str(tmp_path / "gone"), "label": "Offline", "role": "archive"},
    ]

    found, unmounted = forget.find_on_disk("M_5 RASA8 X 2026-07-08", libs)

    assert len(found) == 1 and found[0].endswith("M_5 RASA8 X 2026-07-08")
    assert len(unmounted) == 1 and "Offline" in unmounted[0]


# --------------------------------------------------------------------------
# integration_method stamp
# --------------------------------------------------------------------------
NOTES_WITH_PROCESSING = """location = "Home"

[processing]
notes = \"\"\"
tried PI Magic first
\"\"\"

[future_processing]
todo = []
"""


def test_stamp_method_inserts_into_processing_section():
    text, changed = stamp_method(NOTES_WITH_PROCESSING, "PI Magic")

    assert 'integration_method = "PI Magic"' in text
    assert changed
    # Inside [processing], before [future_processing]:
    assert text.index("[processing]") < text.index("integration_method") < text.index(
        "[future_processing]"
    )


def test_stamp_method_idempotent_and_updates():
    text, _ = stamp_method(NOTES_WITH_PROCESSING, "PI Magic")

    same, changed = stamp_method(text, "PI Magic")
    assert changed == [] and same == text

    text2, changed = stamp_method(text, "PixInsight")
    assert 'integration_method = "PixInsight"' in text2 and changed


def test_stamp_method_creates_section_when_absent():
    text, changed = stamp_method('location = "Home"\n', "PixInsight")

    assert "[processing]" in text and 'integration_method = "PixInsight"' in text


def test_scan_reads_method_from_notes(tmp_path):
    sdir = tmp_path / "M_5 RASA8 ASI2600MCAir 2026-07-08"
    sdir.mkdir()
    (sdir / "M_5 RASA8 ASI2600MCAir 2026-07-08 notes.toml").write_text(
        '[processing]\nintegration_method = "PI Magic"\n', encoding="utf-8"
    )

    notes = scan_mod.read_notes_toml(str(sdir), sdir.name)

    assert notes["integration_method"] == "PI Magic"
