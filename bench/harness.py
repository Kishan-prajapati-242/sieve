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
import random
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


PAIRED_RESAMPLES = 2000
PAIRED_SEED = 20260812


def paired_ratio(
    pairs: list[tuple[float, float]],
    *,
    window: str,
    resamples: int = PAIRED_RESAMPLES,
    seed: int = PAIRED_SEED,
) -> dict[str, Any]:
    """Speedup from baseline and candidate measured back to back on the
    SAME query, inside one run — never divided across two runs.

    A cross-run ratio silently carries every difference between the two
    sessions into the number. On 2026-08-12 that stopped being
    hypothetical: the exact-scan denominator drifted ~15% slower for
    reasons still unexplained, and the published end-to-end speedup rose
    from 6.3x to 7.2x on the strength of it. The ratio improved because
    the baseline got worse.

    Pairing fixes it by construction. Thermal state, VM scheduling, and
    page-cache contents are shared by two calls microseconds apart, so
    they enter both sides of each ratio and divide out. What survives is
    the difference between the two plans, which is the thing being
    measured.

    Point estimate is the MEDIAN of per-query ratios (not the ratio of
    medians — that reintroduces an unpaired comparison), with a
    percentile bootstrap CI resampled over queries, since repetitions of
    one query are correlated and are collapsed by the caller first.
    """
    if not pairs:
        raise ValueError("paired_ratio needs at least one pair")
    if any(c <= 0 for _, c in pairs):
        raise ValueError("candidate latency of 0 — timer resolution too coarse to pair")
    ratios = sorted(b / c for b, c in pairs)
    rng = random.Random(seed)
    n = len(ratios)
    boot = sorted(
        statistics.median([ratios[rng.randrange(n)] for _ in range(n)]) for _ in range(resamples)
    )
    lo, hi = boot[int(0.025 * resamples)], boot[int(0.975 * resamples) - 1]
    return {
        "speedup": round(statistics.median(ratios), 1),
        "ci95": [round(lo, 1), round(hi, 1)],
        "window": window,
        "paired": True,
        "n_pairs": n,
        "ratio_p10": round(ratios[int(0.10 * n)], 1),
        "ratio_p90": round(ratios[int(0.90 * n)], 1),
        "method": "median of per-query baseline/candidate ratios, both timed inside "
        "one run with alternating order; percentile bootstrap over queries "
        f"({resamples} resamples, seed {seed})",
    }


def speedup(
    baseline_ms: float, candidate_ms: float, *, baseline_window: str, candidate_window: str
) -> dict[str, Any]:
    """RETIRED 2026-08-12 in favour of paired_ratio(). Kept because the
    window guard below is still the right check and the retired numbers
    have to stay readable, but a cross-run ratio inherits every
    difference between the two runs — see paired_ratio's docstring for
    the instance that forced the change.

    A ratio of latencies is only a speedup when both sides measure the
    same window (2026-07-31: 55ms scan-only was divided by 9.9ms
    end-to-end, understating the honest end-to-end ratio 6.3x as 5.5x —
    that one happened to run conservative; the rule exists for the times
    it wouldn't). Refuses mismatched windows outright.

    Note the guard's limit, exposed by the same 2026-08-12 review: it
    compares window STRINGS. Passing one hand-written string for two
    genuinely different windows (a 50-row (id, distance) scan against a
    20-full-row search) satisfies it. paired_ratio runs one function on
    both sides so the window is identical by construction, not by
    assertion."""
    if baseline_window != candidate_window:
        raise ValueError(
            f"window mismatch: baseline={baseline_window!r} vs "
            f"candidate={candidate_window!r} — a ratio across different windows "
            "is not a speedup"
        )
    return {
        "speedup": round(baseline_ms / candidate_ms, 1),
        "window": baseline_window,
        "baseline_ms": baseline_ms,
        "candidate_ms": candidate_ms,
    }


def carry_superseded(
    old: dict[str, Any] | None,
    *,
    key: str | None = None,
    why: str = "",
    keep: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Prior published numbers stay visible, marked superseded — deleting a
    published number is worse than correcting it.

    Returns the `superseded_*` blocks for the new results file: every one
    already present in `old`, plus (when `key` is given) `key` holding the
    old file's `keep` blocks and the reason they no longer apply.

    Generalized from a hand-written v1/v2 chain in exact_scan_baseline.py
    that could only recognize the two format changes it was written for.
    When the corpus shrank 7% under dedup on 2026-08-02 it produced no
    block at all, and the pre-dedup numbers were only recoverable from
    git — which is the same failure as the ground truth going stale: a
    measurement that does not record what it measured.
    """
    out: dict[str, Any] = {}
    if not old:
        return out
    out.update({k: v for k, v in old.items() if k.startswith("superseded_")})
    if key and key not in out:
        out[key] = {"why": why, **{k: old[k] for k in keep if k in old}}
    return out


def corpus_size(conn: Any) -> int:
    """Row count of papers, for the method block. A latency number that
    doesn't say how big the table was isn't comparable to the next one."""
    row = conn.execute("SELECT count(*) FROM papers").fetchone()
    return int(row[0])


def pinned_connection(dsn: str, *, gucs: dict[str, str] | None = None) -> Any:
    """A connection whose planner settings are fixed BEFORE its first query,
    and never changed again.

    This exists because of a measurement that quietly stopped measuring
    anything (findings.md 2026-08-12). Toggling `enable_indexscan` between
    calls on one connection works for about ten executions; then psycopg's
    automatic PREPARE (prepare_threshold=5) plus PostgreSQL's switch to a
    generic plan means the server reuses the plan it cached while the index
    was still allowed. The GUC still reads `off`, EXPLAIN of the same SQL
    still shows a Seq Scan — but the prepared statement executes the index
    plan, and a forced-exact baseline silently becomes the candidate.

    So: one connection per plan, GUC applied at open, alternate between
    connections instead of toggling one. Both sides keep prepared
    statements, which is also what the API does in production.
    """
    import psycopg

    conn = psycopg.connect(dsn, autocommit=True)
    for name, value in (gucs or {}).items():
        conn.execute(f"SET {name} = {value}")  # noqa: S608 — names are literals in bench code
    return conn


def db_state(conn: Any) -> dict[str, Any]:
    """What the measurement measured: rows AND physical layout.

    Row count alone is not enough. On 2026-08-12 a VACUUM FULL took the
    heap from 44,059 to 35,348 pages and rebuilt every index (the FTS GIN
    dropped 118 MB -> 83 MB) at an unchanged row count — the same
    query against a materially different table. Keying supersession on
    this dict means a rebuild retires the old numbers automatically.
    """
    row = conn.execute(
        "SELECT count(*) FROM papers",
    ).fetchone()
    pages = conn.execute(
        "SELECT relpages, pg_relation_size(oid) FROM pg_class WHERE relname = 'papers'"
    ).fetchone()
    indexes = conn.execute(
        "SELECT indexrelname, pg_relation_size(indexrelid) FROM pg_stat_user_indexes"
        " WHERE relname = 'papers' ORDER BY indexrelname"
    ).fetchall()
    return {
        "corpus_size": int(row[0]),
        "heap_pages": int(pages[0]),
        "heap_bytes": int(pages[1]),
        "index_bytes": {name: int(size) for name, size in indexes},
    }


def state_key(state: dict[str, Any]) -> str:
    """Stable supersede key for a db_state: rows and heap pages."""
    return f"superseded_{state['corpus_size']}rows_{state['heap_pages']}pages"


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


def load_ground_truth(path: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Read a ground-truth file, tolerating both shapes.

    v1 files are a bare list of query entries. From 2026-08-02 the file is a
    dict carrying a `method` block (corpus size, EXPLAIN proof, timestamp)
    alongside `queries`, because a ground truth that does not state which
    corpus it describes is how the last one went stale unnoticed.
    """
    import json
    from pathlib import Path

    data = json.loads(Path(path).read_text())
    if isinstance(data, list):
        return data, {"note": "v1 file: no method block, corpus unknown"}
    return data["queries"], data.get("method", {})
