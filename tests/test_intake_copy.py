"""Copy-protocol and apply tests (synthetic trees, scratch staging/ledger)."""

import argparse
import datetime as dt
import hashlib
import os

import astro_config
import intake
from conftest import write_file


# --------------------------------------------------------------------------
# copy_verified
# --------------------------------------------------------------------------
def test_copy_verified_ok_atomic_and_hash(tmp_path):
    src = tmp_path / "a.fit"
    src.write_text("frame-bytes")
    dest = tmp_path / "out" / "a.fit"
    dest.parent.mkdir()

    status, sha = intake.copy_verified(str(src), str(dest), "sha256")

    assert status == "ok"
    assert sha == hashlib.sha256(b"frame-bytes").hexdigest()
    assert dest.read_text() == "frame-bytes"
    assert not os.path.exists(str(dest) + ".part")
    assert os.stat(dest).st_mtime_ns == os.stat(src).st_mtime_ns


def test_copy_verified_source_changed_mid_copy_leaves_nothing(tmp_path, monkeypatch):
    src = tmp_path / "a.fit"
    src.write_text("frame-bytes")
    dest = tmp_path / "a-copy.fit"
    real_stat = os.stat
    calls = {"n": 0}

    def moving_stat(path, *a, **kw):
        st = real_stat(path, *a, **kw)
        if str(path) == str(src):
            calls["n"] += 1
            if calls["n"] > 1:  # re-stat after the copy: pretend CCC touched it
                return os.stat_result(
                    (st.st_mode, st.st_ino, st.st_dev, st.st_nlink, st.st_uid,
                     st.st_gid, st.st_size + 1, st.st_atime, st.st_mtime, st.st_ctime)
                )
        return st

    monkeypatch.setattr(os, "stat", moving_stat)

    status, sha = intake.copy_verified(str(src), str(dest), "sha256")

    assert status == "source-changed" and sha is None
    assert not dest.exists() and not os.path.exists(str(dest) + ".part")


def test_copy_verified_hash_mismatch_refused(tmp_path, monkeypatch):
    src = tmp_path / "a.fit"
    src.write_text("frame-bytes")
    dest = tmp_path / "a-copy.fit"
    real_new = hashlib.new
    hashers = []

    def sabotaged_new(name):
        h = real_new(name)
        hashers.append(h)
        if len(hashers) == 2:  # the .part re-read hasher sees different bytes
            h.update(b"corruption")
        return h

    monkeypatch.setattr(hashlib, "new", sabotaged_new)

    status, sha = intake.copy_verified(str(src), str(dest), "sha256")

    assert status == "verify-failed" and sha is None
    assert not dest.exists() and not os.path.exists(str(dest) + ".part")


# --------------------------------------------------------------------------
# decide + run_apply end-to-end on a synthetic source
# --------------------------------------------------------------------------
LIGHT = "Light_M 5_300.0s_Bin1_2600MC_gain100_20260708-22{i:02d}00_-10.0C_00{i:02d}.fit"


def make_env(tmp_path, monkeypatch, n_lights=3):
    """Synthetic source + scratch staging/ledger + neutralized library deps."""
    src_root = tmp_path / "device"
    for i in range(n_lights):
        write_file(str(src_root / "Autorun" / "Light" / "M 5" / LIGHT.format(i=i)))
    write_file(str(src_root / "log" / "Autorun_Log_2026-07-08_220000.txt"))
    template = tmp_path / "Session Template.pxiproject"
    (template / "sub").mkdir(parents=True)
    (template / "project.xosm").write_text("opaque")
    (template / "sub" / "data").write_text("opaque2")

    cfg = {
        "settings": {
            "staging": str(tmp_path / "staging"),
            "pxiproject_template": str(template),
            "hash": "sha256",
            "copy_chn_logs": False,
            "ledger": str(tmp_path / "ledger.db"),
        },
        "sources": [
            {"id": "air", "label": "test", "path": str(src_root), "layout": "asiair"}
        ],
        "rigs": [
            {"source": "air", "camera": "2600MC", "scope": "RASA8",
             "sensor": "ASI2600MCAir", "adjacent": False, "from": None, "to": None}
        ],
        "ignores": [],
    }
    monkeypatch.setattr(
        intake, "build_library_index",
        lambda: {"names": {}, "by_folder_night": {}, "libraries": []},
    )
    monkeypatch.setattr(
        intake, "load_registry_vocab",
        lambda: {"targets": {"M_5": "M 5 globular cluster"}, "scopes": {"RASA8"},
                 "sensors": {"ASI2600MCAir"}, "combos": {"RASA8_ASI2600MCAir"}},
    )
    monkeypatch.setattr(astro_config, "load_libraries", lambda config_path=None: [])
    monkeypatch.setattr(astro_config, "log_actions", lambda script, lines: None)
    return cfg


def make_args(**overrides):
    base = dict(config="x", source=[], since=None, night=[], apply=True,
                reimport=False, verbose=False)
    base.update(overrides)
    return argparse.Namespace(**base)


def scan_all(cfg):
    import intake_scan

    return {s["id"]: intake_scan.scan_source(s, cfg["settings"]) for s in cfg["sources"]}


def test_apply_creates_session_stamps_and_ledgers(tmp_path, monkeypatch):
    cfg = make_env(tmp_path, monkeypatch)
    args = make_args()
    scans = scan_all(cfg)

    ctx = intake.decide(cfg, args, scans)
    code = intake.run_apply(cfg, args, scans, ctx)

    assert code == 0
    sdir = tmp_path / "staging" / "M_5 RASA8 ASI2600MCAir 2026-07-08"
    assert sorted(os.listdir(sdir / "Light")) == [LIGHT.format(i=i) for i in range(3)]
    assert (sdir / "log" / "Autorun_Log_2026-07-08_220000.txt").exists()
    assert (sdir / "M_5 RASA8 ASI2600MCAir 2026-07-08 notes.toml").exists()
    assert (sdir / "M_5 RASA8 ASI2600MCAir 2026-07-08.pxiproject" / "sub" / "data").exists()
    rows = intake.intake_ledger.all_copied_rows(ctx["con"])
    assert len(rows) == 4  # 3 lights + 1 log
    assert all(r["sha"] for r in rows)


def test_stamp_session_creates_full_posthaste_skeleton(tmp_path, monkeypatch):
    # A staged session must match a hand-created (PostHaste) one: the two
    # template stamps PLUS the four empty working folders.
    cfg = make_env(tmp_path, monkeypatch)
    scans = scan_all(cfg)
    ctx = intake.decide(cfg, make_args(), scans)
    intake.run_apply(cfg, make_args(), scans, ctx)

    name = "M_5 RASA8 ASI2600MCAir 2026-07-08"
    sdir = tmp_path / "staging" / name
    for folder in (f"{name} Results", "PI Magic", "PI Process", "Rejected"):
        assert (sdir / folder).is_dir(), f"missing skeleton folder: {folder}"
        assert os.listdir(sdir / folder) == []  # stamped empty, never populated


def test_stamp_session_idempotent_preserves_working_files(tmp_path):
    # Re-stamping an existing session (interrupted-run resume) must not touch
    # what's already there — including files inside the working folders.
    staging = tmp_path / "staging"
    name = "M_5 RASA8 ASI2600MCAir 2026-07-08"
    keeper = staging / name / f"{name} Results" / "master.xisf"
    write_file(str(keeper), "precious")
    settings = {"pxiproject_template": ""}

    lines = intake.stamp_session(str(staging), name, settings)
    relined = intake.stamp_session(str(staging), name, settings)

    assert keeper.read_text() == "precious"
    assert any("PI Magic" in ln for ln in lines)  # first pass stamped the rest
    assert not any("Results" in ln for ln in lines)  # pre-existing dir not re-stamped
    assert not any("PI Magic" in ln or "PI Process" in ln or "Rejected" in ln
                   for ln in relined)  # second pass stamps no folders


def test_apply_rerun_skips_everything(tmp_path, monkeypatch):
    cfg = make_env(tmp_path, monkeypatch)
    scans = scan_all(cfg)
    ctx = intake.decide(cfg, make_args(), scans)
    intake.run_apply(cfg, make_args(), scans, ctx)

    ctx2 = intake.decide(cfg, make_args(apply=False), scans)

    sess = next(s for s in ctx2["plan"]["sessions"] if s["status"] == "new")
    assert all(f["decision"] == "skip" for f in sess["files"])


def test_apply_unledgered_dest_collision_held(tmp_path, monkeypatch):
    cfg = make_env(tmp_path, monkeypatch)
    scans = scan_all(cfg)
    collided = (
        tmp_path / "staging" / "M_5 RASA8 ASI2600MCAir 2026-07-08" / "Light" / LIGHT.format(i=1)
    )
    write_file(str(collided), "SOMEONE ELSES BYTES")

    ctx = intake.decide(cfg, make_args(), scans)

    sess = next(s for s in ctx["plan"]["sessions"] if s["status"] == "new")
    decisions = {os.path.basename(f["dest_rel"]): f["decision"] for f in sess["files"]}
    assert decisions[LIGHT.format(i=1)] == "hold"
    assert decisions[LIGHT.format(i=0)] == "copy"
    assert any("never overwritten" in line for line in ctx["attention"])

    intake.run_apply(cfg, make_args(), scans, ctx)

    assert collided.read_text() == "SOMEONE ELSES BYTES"  # untouched


def test_vanished_staging_aggregates_attention_per_session(tmp_path, monkeypatch):
    # Renamed/deleted staging is ONE event: the plan must hold every file but
    # emit a single per-session attention line (plus the ledger reconciliation
    # line), never one line per file.
    import shutil as sh

    cfg = make_env(tmp_path, monkeypatch)
    scans = scan_all(cfg)
    ctx = intake.decide(cfg, make_args(), scans)
    intake.run_apply(cfg, make_args(), scans, ctx)
    sh.move(str(tmp_path / "staging"), str(tmp_path / "staging-snapshot"))

    ctx2 = intake.decide(cfg, make_args(apply=False), scans)

    sess = next(s for s in ctx2["plan"]["sessions"] if s["status"] == "new")
    held = [f for f in sess["files"] if f["decision"] == "hold"]
    assert len(held) == 4  # every copied file (3 lights + 1 log) held
    per_file = [a for a in ctx2["attention"] if "but the copy is gone" in a]
    assert per_file == []
    aggregated = [a for a in ctx2["attention"]
                  if "4 previously-imported file(s) missing from staging" in a]
    assert len(aggregated) == 1 and "--reimport" in aggregated[0]


def test_reimport_aggregates_attention_per_session(tmp_path, monkeypatch):
    import shutil as sh

    cfg = make_env(tmp_path, monkeypatch)
    scans = scan_all(cfg)
    ctx = intake.decide(cfg, make_args(), scans)
    intake.run_apply(cfg, make_args(), scans, ctx)
    sh.rmtree(str(tmp_path / "staging"))

    ctx2 = intake.decide(cfg, make_args(reimport=True), scans)

    sess = next(s for s in ctx2["plan"]["sessions"] if s["status"] == "new")
    assert all(f["decision"] == "copy" for f in sess["files"] if f["label"] == "lights")
    per_file = [a for a in ctx2["attention"] if "previous copy vanished" in a]
    assert per_file == []
    assert any("re-importing" in a and "(--reimport)" in a
               for a in ctx2["attention"])


def test_apply_deleted_staged_copy_needs_reimport(tmp_path, monkeypatch):
    cfg = make_env(tmp_path, monkeypatch)
    scans = scan_all(cfg)
    ctx = intake.decide(cfg, make_args(), scans)
    intake.run_apply(cfg, make_args(), scans, ctx)
    victim = (
        tmp_path / "staging" / "M_5 RASA8 ASI2600MCAir 2026-07-08" / "Light" / LIGHT.format(i=2)
    )
    os.remove(victim)

    ctx2 = intake.decide(cfg, make_args(), scans)
    sess = next(s for s in ctx2["plan"]["sessions"] if s["status"] == "new")
    decisions = {os.path.basename(f["dest_rel"]): f["decision"] for f in sess["files"]}
    assert decisions[LIGHT.format(i=2)] == "hold"
    assert any("previously-imported file(s) missing from staging" in line
               for line in ctx2["attention"])
    # the plan line covers it — no duplicate ledger-reconciliation line
    assert not any("neither staging nor" in line for line in ctx2["attention"])

    ctx3 = intake.decide(cfg, make_args(reimport=True), scans)
    sess = next(s for s in ctx3["plan"]["sessions"] if s["status"] == "new")
    decisions = {os.path.basename(f["dest_rel"]): f["decision"] for f in sess["files"]}
    assert decisions[LIGHT.format(i=2)] == "copy"

    intake.run_apply(cfg, make_args(reimport=True), scans, ctx3)
    assert victim.exists()


def test_apply_changed_source_reoffered_with_warning(tmp_path, monkeypatch):
    cfg = make_env(tmp_path, monkeypatch)
    scans = scan_all(cfg)
    ctx = intake.decide(cfg, make_args(), scans)
    intake.run_apply(cfg, make_args(), scans, ctx)
    src_file = tmp_path / "device" / "Autorun" / "Light" / "M 5" / LIGHT.format(i=0)
    src_file.write_text("rewritten at source")
    staged = (
        tmp_path / "staging" / "M_5 RASA8 ASI2600MCAir 2026-07-08" / "Light" / LIGHT.format(i=0)
    )
    scans = scan_all(cfg)  # rescan picks up the new size/mtime

    ctx2 = intake.decide(cfg, make_args(), scans)

    sess = next(s for s in ctx2["plan"]["sessions"] if s["status"] == "new")
    f = next(x for x in sess["files"] if os.path.basename(x["dest_rel"]) == LIGHT.format(i=0))
    # Destination still holds the verified old copy → held, never overwritten.
    assert f["decision"] == "hold"
    assert "changed at source" in f["note"]
    assert staged.exists()


def test_stale_parts_cleaned_at_apply(tmp_path, monkeypatch):
    cfg = make_env(tmp_path, monkeypatch)
    scans = scan_all(cfg)
    stale = tmp_path / "staging" / "old.fit.part"
    write_file(str(stale))

    ctx = intake.decide(cfg, make_args(), scans)
    intake.run_apply(cfg, make_args(), scans, ctx)

    assert not stale.exists()


def test_apply_external_logs_dir_lands_in_session(tmp_path, monkeypatch):
    # A [[source]] logs= dir outside the root: the ../-anchored relpath must
    # survive the copy path and the ledger round-trip.
    cfg = make_env(tmp_path, monkeypatch)
    phd2 = tmp_path / "PHD2"
    write_file(str(phd2 / "PHD2_GuideLog_2026-07-08_221500.txt"))
    cfg["sources"][0]["logs"] = str(phd2)
    scans = scan_all(cfg)

    ctx = intake.decide(cfg, make_args(), scans)
    intake.run_apply(cfg, make_args(), scans, ctx)

    landed = (
        tmp_path / "staging" / "M_5 RASA8 ASI2600MCAir 2026-07-08" / "log"
        / "PHD2_GuideLog_2026-07-08_221500.txt"
    )
    assert landed.exists()
    row = next(
        r for r in intake.intake_ledger.all_copied_rows(ctx["con"])
        if "PHD2_GuideLog" in r["relpath"]
    )
    assert row["relpath"].startswith("..")
    # And a rerun offers nothing new for it.
    ctx2 = intake.decide(cfg, make_args(apply=False), scans)
    sess = next(s for s in ctx2["plan"]["sessions"] if s["status"] == "new")
    log_files = [f for f in sess["files"] if "PHD2_GuideLog" in f["dest_rel"]]
    assert log_files and all(f["decision"] == "skip" for f in log_files)


def test_twin_guard_same_folder_night_different_name(tmp_path, monkeypatch):
    # The NGC 3718/3729 case: the night is in the library under the companion
    # galaxy's name — same destination folder + night, different session name.
    cfg = make_env(tmp_path, monkeypatch)
    monkeypatch.setattr(
        intake, "build_library_index",
        lambda: {
            "names": {},
            "by_folder_night": {
                ("M 5 globular cluster", "2026-07-08"): [
                    ("M_5_companion RASA8 ASI2600MCAir 2026-07-08", False)
                ]
            },
            "libraries": [],
        },
    )
    scans = scan_all(cfg)

    ctx = intake.decide(cfg, make_args(apply=False), scans)

    assert any(
        "already in the library as M_5_companion" in line and "duplicate" in line
        for line in ctx["attention"]
    )


def test_twin_guard_exempts_adjacent_pairing(tmp_path, monkeypatch):
    # Two rigs legitimately share a folder+night when one is the adjacent-
    # field rig — an _adjacent library session must not flag the main one.
    cfg = make_env(tmp_path, monkeypatch)
    monkeypatch.setattr(
        intake, "build_library_index",
        lambda: {
            "names": {},
            "by_folder_night": {
                ("M 5 globular cluster", "2026-07-08"): [
                    ("M_5_adjacent Redcat51 minicam8 2026-07-08", True)
                ]
            },
            "libraries": [],
        },
    )
    scans = scan_all(cfg)

    ctx = intake.decide(cfg, make_args(apply=False), scans)

    assert not any("duplicate" in line for line in ctx["attention"])


def test_ignore_block_excludes_session(tmp_path, monkeypatch):
    cfg = make_env(tmp_path, monkeypatch)
    cfg["ignores"] = [{"source": "air", "target": "M 5", "night": dt.date(2026, 7, 8)}]
    scans = scan_all(cfg)

    ctx = intake.decide(cfg, make_args(), scans)

    assert all(s["status"] == "ignored" for s in ctx["plan"]["sessions"])
    code = intake.run_apply(cfg, make_args(), scans, ctx)
    assert code == 0
    assert not (tmp_path / "staging" / "M_5 RASA8 ASI2600MCAir 2026-07-08").exists()
