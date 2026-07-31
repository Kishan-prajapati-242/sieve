"""Shared latency-measurement harness for every bench/ script.

Exists because the first exact-scan baseline shipped a max labeled as a
p99 (findings.md 2026-07-31): with 20 samples, nearest-rank p95 and p99
are both the last value. The rules this module enforces:

  - A percentile is only reported when at least MIN_BEYOND samples lie
    beyond it (n * (1 - p) >= MIN_BEYOND). Otherwise it is None, and the
    summary says so explicitly — no number is better than a fake one.
  - Repetitions of the same query are interleaved (a b c a b c), never
    clustered (a a b b c c), so per-query cache effects spread across the
    run instead of correlating with time.
  - Every result carries a method record: sample counts, warmup, cache
    state, whether the plan was forced, and the hardware it ran on. A
    number without its method is not a measurement.

Convention (CLAUDE.md): p50/p95/p99, never a mean.
"""

import math
import statistics
from typing import Any

# At least this many samples must lie beyond a percentile for it to be
# reportable: p50 needs n>=10, p95 n>=100, p99 n>=500.
MIN_BEYOND = 5


def percentile(samples_ms: list[float], p: float) -> float | None:
    """Nearest-rank percentile, or None when n*(1-p) < MIN_BEYOND."""
    n = len(samples_ms)
    if n * (1 - p) < MIN_BEYOND:
        return None
    rank = math.ceil(p * n)  # 1-indexed nearest rank
    return sorted(samples_ms)[rank - 1]


def summarize(samples_ms: list[float]) -> dict[str, Any]:
    """p50/p95/p99 with honest Nones, plus min/max as raw tail context."""
    out: dict[str, Any] = {"n_samples": len(samples_ms)}
    for label, p in (("p50", 0.50), ("p95", 0.95), ("p99", 0.99)):
        value = percentile(samples_ms, p)
        out[label + "_ms"] = round(value, 1) if value is not None else None
    unreportable = [k for k, v in out.items() if v is None]
    if unreportable:
        out["not_reportable"] = (
            f"{', '.join(k.removesuffix('_ms') for k in unreportable)}: fewer than "
            f"{MIN_BEYOND} samples beyond the percentile at n={len(samples_ms)}"
        )
    ordered = sorted(samples_ms)
    out["min_ms"] = round(ordered[0], 1)
    out["max_ms"] = round(ordered[-1], 1)
    return out


def interleaved(n_queries: int, reps: int) -> list[int]:
    """Query indices in interleaved order: 0,1,..,n-1, 0,1,..,n-1, ..."""
    return [i for _ in range(reps) for i in range(n_queries)]


# A percentile gets a point estimate only when it reproduces across runs.
STABILITY_MAX_RATIO = 1.3


def across_runs(runs: list[list[float]]) -> dict[str, Any]:
    """Multi-run summary with a stability gate (2026-07-31: a single-run
    p99 of 95.8 was published from the favorable end of an observed
    95.8-406.9 spread — same species as the 20-sample p99).

    Rule, applied to every percentile identically: the point estimate is
    the MEDIAN of per-run values, reported only when max/min across runs
    <= STABILITY_MAX_RATIO; otherwise the value is None and the observed
    per-run range is reported instead. Environmental tails differ between
    runs, so pooling samples would blend distributions — range over pool.
    """
    per_run = [summarize(r) for r in runs]
    out: dict[str, Any] = {
        "n_runs": len(runs),
        "samples_per_run": [len(r) for r in runs],
        "stability_rule": "median across runs, only when max/min <= "
        f"{STABILITY_MAX_RATIO}; otherwise the observed range, no point estimate",
        "per_run": per_run,
    }
    for key in ("p50_ms", "p95_ms", "p99_ms"):
        values = [r[key] for r in per_run]
        if any(v is None for v in values):
            out[key] = None
            out[key.removesuffix("_ms") + "_note"] = "not reportable within a run (see per_run)"
            continue
        if max(values) / min(values) <= STABILITY_MAX_RATIO:
            out[key] = round(statistics.median(values), 1)
        else:
            out[key] = None
            out[key.removesuffix("_ms") + "_unstable_range_ms"] = [min(values), max(values)]
    return out


def method_record(*, timing_window: str, **fields: Any) -> dict[str, Any]:
    """The measurement's passport. Hardware context is constant for this
    project and stated once here rather than re-typed per script.

    timing_window is MANDATORY (2026-07-31): a latency number that doesn't
    state where its clock starts and stops isn't a measurement — the first
    baseline's p50 shift got misattributed to embedding cost that was
    never inside the window."""
    return {
        "timing_window": timing_window,
        "hardware": "MacBook Air M1 8GB (fanless), podman VM 4 vCPU / 4GB",
        "database": "PostgreSQL 16.14, pgvector 0.8.5, shared_buffers 128MB, in compose",
        **fields,
    }
