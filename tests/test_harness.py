"""The harness rules that make a percentile a percentile (findings.md
2026-07-31: n=20 shipped a max labeled p99 — these tests pin the guard)."""

import pytest

from bench.harness import (
    across_runs,
    carry_superseded,
    interleaved,
    method_record,
    paired_ratio,
    percentile,
    speedup,
    summarize,
)


def test_speedup_refuses_mismatched_windows() -> None:
    """The 5.5x-vs-6.3x lesson: scan-only over end-to-end is not a speedup."""
    with pytest.raises(ValueError, match="window mismatch"):
        speedup(55.0, 9.9, baseline_window="sql only", candidate_window="end-to-end")
    ok = speedup(62.6, 9.9, baseline_window="end-to-end", candidate_window="end-to-end")
    assert ok["speedup"] == 6.3
    assert ok["window"] == "end-to-end"


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


def test_across_runs_gates_unstable_percentiles_to_a_range() -> None:
    """The published-favorable-p99 fix: a percentile that doesn't reproduce
    across runs gets a range, never a point estimate from the good run."""
    stable_run = [float(i) for i in range(1, 601)]  # p99=594
    slow_run = [x * 2.0 for x in stable_run]  # p99=1188: 2x spread
    out = across_runs([stable_run, stable_run, slow_run])
    assert out["p99_ms"] is None
    assert out["p99_unstable_range_ms"] == [594.0, 1188.0]
    # p50 fails the same 1.3x gate here — the rule is uniform, not p99-only.
    assert out["p50_ms"] is None
    stable = across_runs([stable_run, stable_run, [x * 1.1 for x in stable_run]])
    assert stable["p99_ms"] is not None  # 1.1x spread: median reported


def test_across_runs_propagates_within_run_unreportability() -> None:
    thin = [float(i) for i in range(100)]  # p99 not reportable at n=100
    out = across_runs([thin, thin])
    assert out["p99_ms"] is None
    assert "per_run" in out and out["per_run"][0]["p99_ms"] is None


def test_method_record_demands_a_timing_window() -> None:
    """A number without its window is not a measurement: the field is a
    required keyword, and it lands first in the record."""
    rec = method_record(timing_window="execute()+fetchall() round trip", n=3)
    assert rec["timing_window"] == "execute()+fetchall() round trip"
    assert rec["n"] == 3
    assert "hardware" in rec and "database" in rec


def test_paired_ratio_is_immune_to_a_drifting_baseline() -> None:
    """The 2026-08-12 lesson: a cross-run ratio improves when the baseline
    degrades. Pairing cancels a shared multiplicative slowdown exactly."""
    clean = [(60.0 + i, 2.0) for i in range(50)]
    # every sample 20% slower — thermal, contention, whatever hits both sides
    throttled = [(b * 1.2, c * 1.2) for b, c in clean]
    window = "sql only"
    assert (
        paired_ratio(clean, window=window)["speedup"]
        == (paired_ratio(throttled, window=window)["speedup"])
    )
    # the cross-run form does NOT cancel it: baseline throttled, candidate not
    drifted = speedup(60.0 * 1.2, 2.0, baseline_window=window, candidate_window=window)
    honest = speedup(60.0, 2.0, baseline_window=window, candidate_window=window)
    assert drifted["speedup"] > honest["speedup"]


def test_paired_ratio_ci_brackets_the_point_estimate() -> None:
    pairs = [(50.0 + (i % 7), 2.0 + (i % 3) * 0.1) for i in range(200)]
    out = paired_ratio(pairs, window="sql only")
    lo, hi = out["ci95"]
    assert lo <= out["speedup"] <= hi
    assert out["n_pairs"] == 200
    assert out["paired"] is True


def test_paired_ratio_refuses_a_zero_candidate() -> None:
    """A 0 ms candidate means the timer, not the query, is being measured."""
    with pytest.raises(ValueError, match="timer resolution"):
        paired_ratio([(50.0, 0.0)], window="sql only")


def test_carry_superseded_preserves_every_prior_block() -> None:
    """Deleting a published number is worse than correcting it."""
    old = {
        "measured_at": "t0",
        "warm": {"p50_ms": 54.6},
        "superseded_v1": {"why": "20-sample p99"},
    }
    out = carry_superseded(
        old, key="superseded_corpus_196893", why="corpus changed", keep=("measured_at", "warm")
    )
    assert out["superseded_v1"] == {"why": "20-sample p99"}
    assert out["superseded_corpus_196893"]["warm"] == {"p50_ms": 54.6}
    assert out["superseded_corpus_196893"]["why"] == "corpus changed"


def test_carry_superseded_never_overwrites_an_existing_block() -> None:
    old = {"superseded_x": {"why": "first"}, "warm": {"p50_ms": 1.0}}
    out = carry_superseded(old, key="superseded_x", why="second", keep=("warm",))
    assert out["superseded_x"] == {"why": "first"}
