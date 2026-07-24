"""Shared-metrics tests: one definition, both faces, identical output."""

from metrics import summary_metrics
from test_session_lifecycle import make_db

EXPECTED_LABELS = [
    "Deep-sky hours",
    "Deep-sky sessions",
    "Targets imaged",
    "Kept light frames",
    "Other-capture sessions",
    "Calibration sets",
    "Calibration needs attention",
    "Multi-session integrations",
    "Targets not published",
    "Data health findings",
]


def test_summary_metrics_labels_and_order(tmp_path):
    con = make_db(tmp_path)

    metrics = summary_metrics(con)

    assert [m["label"] for m in metrics] == EXPECTED_LABELS
    assert all(set(m) == {"label", "value", "warn", "fmt"} for m in metrics)


def test_summary_metrics_values_from_fixture(tmp_path):
    con = make_db(tmp_path)  # one session: 60 kept + 2 rejected, 7200 s

    by_label = {m["label"]: m["value"] for m in summary_metrics(con)}

    assert by_label["Deep-sky hours"] == 2
    assert by_label["Deep-sky sessions"] == 1
    assert by_label["Other-capture sessions"] == 0
    # Kept lights counts FRAMES; the fixture has session counters but no
    # frame rows, so 0 here — the definition is frames-table truth.
    assert by_label["Kept light frames"] == 0
    assert by_label["Data health findings"] == 0


def test_summary_metrics_warn_flags(tmp_path):
    con = make_db(tmp_path)

    warn_labels = {m["label"] for m in summary_metrics(con) if m["warn"]}

    assert warn_labels == {
        "Calibration needs attention",
        "Targets not published",
        "Data health findings",
    }
