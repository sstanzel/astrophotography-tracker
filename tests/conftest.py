"""Shared fixtures for the intake test suite.

All tests run against synthetic trees under tmp_path — never against real
volumes. Fake frame files are a few bytes (content = the filename, so every
file hashes differently).
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def write_file(path, content: str | None = None) -> None:
    """Create a small file (content defaults to its own basename)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content if content is not None else os.path.basename(path))


@pytest.fixture
def intake_toml(tmp_path):
    """Write an intake.toml with the given body and return its path."""

    def _write(body: str) -> str:
        p = tmp_path / "intake.toml"
        p.write_text(body, encoding="utf-8")
        return str(p)

    return _write


MINIMAL_CONFIG = """
[[source]]
id = "air1"
label = "ASIAir"
path = "{src}"
layout = "asiair"

[[rig]]
source = "air1"
camera = "2600MC"
scope = "RASA8"
sensor = "ASI2600MCAir"
"""


@pytest.fixture
def minimal_config(intake_toml, tmp_path):
    """A valid single-source config; returns (config_path, source_root)."""
    src = tmp_path / "device"
    src.mkdir()
    return intake_toml(MINIMAL_CONFIG.format(src=src)), str(src)
