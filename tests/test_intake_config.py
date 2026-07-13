"""Config loading + rig resolution tests for intake.py."""

import datetime as dt

import pytest

from intake import load_intake_config, resolve_rig


def _load_err(intake_toml, body: str) -> str:
    """Load a config expected to fail; return the SystemExit message."""
    with pytest.raises(SystemExit) as exc:
        load_intake_config(intake_toml(body))
    return str(exc.value)


def test_load_intake_config_minimal_ok(minimal_config):
    config_path, src = minimal_config

    cfg = load_intake_config(config_path)

    assert cfg["settings"]["hash"] == "sha256"
    assert cfg["settings"]["copy_chn_logs"] is False
    assert [s["id"] for s in cfg["sources"]] == ["air1"]
    assert cfg["sources"][0]["path"] == src
    assert cfg["rigs"][0]["scope"] == "RASA8"
    assert cfg["rigs"][0]["adjacent"] is False


def test_load_intake_config_missing_file_raises_clear_error(tmp_path):
    msg = ""
    with pytest.raises(SystemExit) as exc:
        load_intake_config(str(tmp_path / "nope.toml"))
    msg = str(exc.value)
    assert "not found" in msg and "intake.example.toml" in msg


def test_load_intake_config_bad_layout_rejected(intake_toml):
    msg = _load_err(
        intake_toml,
        """
        [[source]]
        id = "a"
        path = "/x"
        layout = "sharpcap"
        """,
    )
    assert "layout 'sharpcap'" in msg


def test_load_intake_config_duplicate_source_id_rejected(intake_toml):
    msg = _load_err(
        intake_toml,
        """
        [[source]]
        id = "a"
        path = "/x"
        layout = "asiair"
        [[source]]
        id = "a"
        path = "/y"
        layout = "nina"
        """,
    )
    assert "duplicate id 'a'" in msg


def test_load_intake_config_rig_unknown_source_rejected(intake_toml):
    msg = _load_err(
        intake_toml,
        """
        [[source]]
        id = "a"
        path = "/x"
        layout = "asiair"
        [[rig]]
        source = "ghost"
        camera = "2600MC"
        scope = "RASA8"
        sensor = "ASI2600MCAir"
        """,
    )
    assert "source 'ghost'" in msg


def test_load_intake_config_scope_with_space_rejected(intake_toml):
    msg = _load_err(
        intake_toml,
        """
        [[source]]
        id = "a"
        path = "/x"
        layout = "asiair"
        [[rig]]
        source = "a"
        camera = "2600MC"
        scope = "RASA 8"
        sensor = "ASI2600MCAir"
        """,
    )
    assert "contains a space" in msg


def test_load_intake_config_overlapping_dated_ranges_rejected(intake_toml):
    msg = _load_err(
        intake_toml,
        """
        [[source]]
        id = "a"
        path = "/x"
        layout = "asiair"
        [[rig]]
        source = "a"
        camera = "2600MC"
        scope = "RASA8"
        sensor = "ASI2600MCAir"
        from = 2026-01-01
        to = 2026-03-01
        [[rig]]
        source = "a"
        camera = "2600MC"
        scope = "Pleiades111"
        sensor = "ASI2600MCAir"
        from = 2026-02-15
        """,
    )
    assert "dated ranges overlap" in msg


def test_load_intake_config_two_open_ended_rejected(intake_toml):
    msg = _load_err(
        intake_toml,
        """
        [[source]]
        id = "a"
        path = "/x"
        layout = "asiair"
        [[rig]]
        source = "a"
        camera = "2600MC"
        scope = "RASA8"
        sensor = "ASI2600MCAir"
        [[rig]]
        source = "a"
        camera = "2600MC"
        scope = "Pleiades111"
        sensor = "ASI2600MCAir"
        """,
    )
    assert "more than one open-ended entry" in msg


# --------------------------------------------------------------------------
# resolve_rig precedence
# --------------------------------------------------------------------------
RIGS = [
    {
        "source": "a", "camera": "2600MC", "scope": "RASA8", "sensor": "S",
        "adjacent": False, "from": None, "to": None,
    },
    {
        "source": "a", "camera": "2600MC", "scope": "Pleiades111", "sensor": "S",
        "adjacent": False, "from": None, "to": dt.date(2026, 4, 18),
    },
    {
        "source": "a", "camera": "*", "scope": "WO50", "sensor": "S",
        "adjacent": False, "from": None, "to": None,
    },
]


def test_resolve_rig_dated_entry_wins_in_range():
    rig, rule = resolve_rig(RIGS, "a", "2600MC", dt.date(2026, 3, 1))

    assert rig["scope"] == "Pleiades111"
    assert "dated rule" in rule


def test_resolve_rig_open_ended_wins_out_of_range():
    rig, rule = resolve_rig(RIGS, "a", "2600MC", dt.date(2026, 5, 1))

    assert rig["scope"] == "RASA8"
    assert rule == "open-ended rule"


def test_resolve_rig_wildcard_catches_unknown_camera():
    rig, rule = resolve_rig(RIGS, "a", "585MC", dt.date(2026, 5, 1))

    assert rig["scope"] == "WO50"
    assert "any-camera" in rule


def test_resolve_rig_unmapped_returns_none():
    rig, rule = resolve_rig(RIGS, "other-source", "2600MC", dt.date(2026, 5, 1))

    assert rig is None and rule is None
