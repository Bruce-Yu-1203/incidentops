"""Range summaries lead with the current value and date the peak (ADR-029)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from incidentops.agent.baseline import _first, _last, _max
from incidentops.tools.backends import Series
from incidentops.tools.executors import summarize_series

T0 = int(datetime(2026, 11, 14, 21, 0, tzinfo=UTC).timestamp())


def _series(values: Sequence[float]) -> Series:
    return Series({}, [(T0 + 60 * i, v) for i, v in enumerate(values)])


def test_an_old_peak_is_dated_relative_to_now() -> None:
    # A previous trial's lock: 1 at 21:02, gone since; the latest sample is 21:14.
    summary, rows = summarize_series([_series([0, 0, 1, 0] + [0] * 11)], "locks")
    assert summary == (
        "total: now last=0 locks; peak max=1 at 21:02:00Z, 12 min before the latest sample; first=0"
    )
    assert (rows[0]["peak_at"], rows[0]["latest_at"]) == (T0 + 120, T0 + 840)


def test_a_current_peak_says_so() -> None:
    summary, _ = summarize_series([_series([0, 0, 1, 1])], "locks")
    assert "now last=1 locks; peak max=1 at 21:03:00Z (the latest sample)" in summary


def test_labels_and_no_data() -> None:
    summary, _ = summarize_series([Series({"mode": "accessexclusivelock"}, [(T0, 2.0)])], "locks")
    assert summary.startswith("mode=accessexclusivelock: now last=2.0 locks")
    assert summarize_series([Series({}, [])], "locks") == ("no data", [])


def test_rules_baseline_still_reads_first_last_and_max() -> None:
    summary, _ = summarize_series([_series([5, 9, 7])], "ms")
    assert (_first(summary), _last(summary), _max(summary)) == (5.0, 7.0, 9.0)
