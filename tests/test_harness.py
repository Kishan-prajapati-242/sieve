"""The harness rules that make a percentile a percentile (findings.md
2026-07-31: n=20 shipped a max labeled p99 — these tests pin the guard)."""

from bench.harness import interleaved, method_record, percentile, summarize


def test_percentile_refuses_thin_samples() -> None:
    twenty = [float(i) for i in range(20)]
    # The original sin: n=20 has 1 sample beyond p95 and 0 beyond p99.
    assert percentile(twenty, 0.95) is None
    assert percentile(twenty, 0.99) is None
    assert percentile(twenty, 0.50) == 9.0  # 10 samples beyond: fine


def test_percentile_thresholds_match_min_beyond() -> None:
    assert percentile([1.0] * 99, 0.95) is None  # 4.95 beyond < 5
    assert percentile([1.0] * 100, 0.95) == 1.0  # exactly 5 beyond
    assert percentile([1.0] * 499, 0.99) is None
    assert percentile([1.0] * 500, 0.99) == 1.0


def test_percentile_is_nearest_rank_not_max() -> None:
    samples = [float(i) for i in range(1, 601)]  # 1..600
    assert percentile(samples, 0.99) == 594.0  # ceil(0.99*600)=594th, not 600
    assert percentile(samples, 0.95) == 570.0
    assert max(samples) == 600.0  # and the max is visibly not the p99


def test_summarize_says_what_it_cannot_report() -> None:
    s = summarize([float(i) for i in range(30)])
    assert s["p50_ms"] is not None
    assert s["p95_ms"] is None and s["p99_ms"] is None
    assert "p95, p99" in s["not_reportable"]
    assert s["min_ms"] == 0.0 and s["max_ms"] == 29.0


def test_interleaved_spreads_repetitions() -> None:
    order = interleaved(3, 2)
    assert order == [0, 1, 2, 0, 1, 2]  # never [0, 0, 1, 1, 2, 2]


def test_method_record_demands_a_timing_window() -> None:
    """A number without its window is not a measurement: the field is a
    required keyword, and it lands first in the record."""
    rec = method_record(timing_window="execute()+fetchall() round trip", n=3)
    assert rec["timing_window"] == "execute()+fetchall() round trip"
    assert rec["n"] == 3
    assert "hardware" in rec and "database" in rec
