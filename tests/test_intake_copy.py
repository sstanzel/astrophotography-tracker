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
        lambda: {"names": {}, "by_target_night": {}, "libraries": []},
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
    assert any("neither staging nor" in line for line in ctx2["attention"])

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


def test_ignore_block_excludes_session(tmp_path, monkeypatch):
    cfg = make_env(tmp_path, monkeypatch)
    cfg["ignores"] = [{"source": "air", "target": "M 5", "night": dt.date(2026, 7, 8)}]
    scans = scan_all(cfg)

    ctx = intake.decide(cfg, make_args(), scans)

    assert all(s["status"] == "ignored" for s in ctx["plan"]["sessions"])
    code = intake.run_apply(cfg, make_args(), scans, ctx)
    assert code == 0
    assert not (tmp_path / "staging" / "M_5 RASA8 ASI2600MCAir 2026-07-08").exists()
