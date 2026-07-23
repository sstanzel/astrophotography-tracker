"""[capture] stamping + target_base tests (pure functions, no disk/DB)."""

from populate_notes import stamp_capture
from scan import target_base

NOTES = """# session notes
location = "Home"
culled = false

[calibration]
flats_match = "here"

[future_processing]
todo = []
"""


def stats(kept, rejected, hours=0.0):
    return {"lights_kept": kept, "lights_rejected": rejected, "kept_exposure_hours": hours}


def test_stamp_capture_creates_section():
    text, changed = stamp_capture(NOTES, stats(0, 41))

    assert "[capture]" in text
    assert "lights_captured = 41" in text
    assert "lights_kept = 0" in text
    assert "lights_rejected = 41" in text
    assert len(changed) == 4


def test_stamp_capture_rerun_unchanged_writes_nothing():
    text, _ = stamp_capture(NOTES, stats(0, 41))

    text2, changed = stamp_capture(text, stats(0, 41))

    assert changed == []
    assert text2 == text


def test_stamp_capture_high_water_survives_partial_deletion():
    text, _ = stamp_capture(NOTES, stats(0, 41))

    # 30 rejected raws deleted from disk; 11 exemplars kept in Rejected/.
    text2, changed = stamp_capture(text, stats(0, 11))

    assert "lights_captured = 41" in text2  # high-water never decreases
    assert "lights_rejected = 11" in text2  # current disk truth
    assert any("lights_rejected=11" in c for c in changed)


def test_stamp_capture_zero_lights_frozen():
    text, _ = stamp_capture(NOTES, stats(0, 41))

    # Every raw deleted: the record must be left exactly as it was.
    text2, changed = stamp_capture(text, stats(0, 0))

    assert changed == []
    assert "lights_captured = 41" in text2


def test_stamp_capture_never_creates_all_zero_record():
    text, changed = stamp_capture(NOTES, stats(0, 0))

    assert changed == []
    assert "[capture]" not in text


def test_stamp_capture_normal_session_updates_after_culling():
    text, _ = stamp_capture(NOTES, stats(60, 0, 5.0))

    # 8 frames culled to Rejected/ later: captured stays 60.
    text2, _ = stamp_capture(text, stats(52, 8, 4.33))

    assert "lights_captured = 60" in text2
    assert "lights_kept = 52" in text2
    assert "kept_exposure_hours = 4.33" in text2


def test_stamp_capture_leaves_hand_sections_alone():
    text, _ = stamp_capture(NOTES, stats(0, 41))

    assert 'flats_match = "here"' in text
    assert "todo = []" in text
    assert 'location = "Home"' in text


def test_target_base_normalizes_spellings():
    # Adjacent suffix and all separator/case habits collapse to one key.
    assert target_base("M 12") == "m12"
    assert target_base("M_12 adjacent") == "m12"
    assert target_base("M_12_adjacent") == "m12"
    assert target_base("M 106 adjacent") == "m106"
    assert target_base("M31") == target_base("M_31")
    assert target_base("SH 2- 108") == target_base("SH2_108")
    assert target_base("SH2-216") == target_base("SH2_216")
    assert target_base("IC1396") == target_base("IC_1396")
    # Real disagreements still differ.
    assert target_base("NGC_3729") != target_base("NGC 3718")
    assert target_base("Menkib") != target_base("HR_1228")
    assert target_base("SH2-223") != target_base("SH2_233")
