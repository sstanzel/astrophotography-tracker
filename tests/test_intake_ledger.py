"""Ledger unit tests (interruption semantics; the apply path covers the rest)."""

import intake_ledger


def test_relabel_stale_runs_marks_crashed_run(tmp_path):
    con = intake_ledger.open_ledger(str(tmp_path / "ledger.db"))
    run_id = intake_ledger.begin_run(con, "--apply")

    relabeled = intake_ledger.relabel_stale_runs(con)

    assert relabeled == 1
    status = con.execute("SELECT status FROM runs WHERE run_id=?", (run_id,)).fetchone()[0]
    assert status == "interrupted"


def test_relabel_stale_runs_leaves_complete_runs_alone(tmp_path):
    con = intake_ledger.open_ledger(str(tmp_path / "ledger.db"))
    run_id = intake_ledger.begin_run(con, "--apply")
    intake_ledger.finish_run(con, run_id, files=2, n_bytes=100)

    assert intake_ledger.relabel_stale_runs(con) == 0
    status = con.execute("SELECT status FROM runs WHERE run_id=?", (run_id,)).fetchone()[0]
    assert status == "complete"


def test_known_files_returns_newest_row_per_relpath(tmp_path):
    con = intake_ledger.open_ledger(str(tmp_path / "ledger.db"))
    run_id = intake_ledger.begin_run(con, "--apply")
    rec_v1 = {"relpath": "Light/a.fit", "size": 10, "mtime_ns": 1}
    rec_v2 = {"relpath": "Light/a.fit", "size": 12, "mtime_ns": 2}
    intake_ledger.record_copy(con, run_id, "air", rec_v1, "S", "S/Light/a.fit", "sha-old")
    intake_ledger.record_copy(con, run_id, "air", rec_v2, "S", "S/Light/a.fit", "sha-new")

    known = intake_ledger.known_files(con, "air")

    assert known["Light/a.fit"]["sha"] == "sha-new"
    assert known["Light/a.fit"]["size"] == 12
    assert intake_ledger.known_files(con, "other-source") == {}
