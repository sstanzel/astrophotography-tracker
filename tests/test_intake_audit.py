"""Audit tests: clean pass, truncation, bit-flip, deletion (synthetic trees)."""

import os

import pytest

import intake
from test_intake_copy import make_args, make_env, scan_all


def applied_env(tmp_path, monkeypatch):
    """A synthetic source fully applied to scratch staging."""
    cfg = make_env(tmp_path, monkeypatch)
    scans = scan_all(cfg)
    ctx = intake.decide(cfg, make_args(), scans)
    intake.run_apply(cfg, make_args(), scans, ctx)
    staged_light = (
        tmp_path / "staging" / "M_5 RASA8 ASI2600MCAir 2026-07-08" / "Light"
    )
    return cfg, staged_light


def run_audit(cfg, deep=False):
    with pytest.raises(SystemExit) as exc:
        intake.run_audit(cfg, make_args(deep=deep))
    return exc.value.code


def test_audit_clean_passes(tmp_path, monkeypatch):
    cfg, _ = applied_env(tmp_path, monkeypatch)

    assert run_audit(cfg) == 0
    assert run_audit(cfg, deep=True) == 0


def test_audit_catches_truncation(tmp_path, monkeypatch):
    cfg, staged_light = applied_env(tmp_path, monkeypatch)
    victim = staged_light / sorted(os.listdir(staged_light))[0]
    victim.write_text("")  # truncated

    assert run_audit(cfg) == 1


def test_audit_deep_catches_bit_flip(tmp_path, monkeypatch):
    cfg, staged_light = applied_env(tmp_path, monkeypatch)
    victim = staged_light / sorted(os.listdir(staged_light))[0]
    original = victim.read_bytes()
    flipped = bytes([original[0] ^ 0x01]) + original[1:]  # same size, one bit off
    victim.write_bytes(flipped)

    assert run_audit(cfg) == 0  # size check alone can't see it
    assert run_audit(cfg, deep=True) == 1  # the rehash does


def test_audit_catches_deletion(tmp_path, monkeypatch):
    cfg, staged_light = applied_env(tmp_path, monkeypatch)
    os.remove(staged_light / sorted(os.listdir(staged_light))[0])

    assert run_audit(cfg) == 1
