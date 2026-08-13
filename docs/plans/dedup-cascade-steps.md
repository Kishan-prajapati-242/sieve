# Dedup cascade: EXPLAIN per step, 2026-08-13

Corpus 183,167 papers / 199,382 source_records, heap 35,348 pages (276 MB),
post-VACUUM-FULL. MacBook Air M1 8GB, podman VM 4 vCPU / 4 GB, PostgreSQL
16.14, shared_buffers 128 MB.

Captured because a full `--rebuild` measured **3 h 55 m 11 s** and the cost
had been attributed, wrongly, from `pg_stat_activity` elapsed time. The
rule this restates: read the plan.

## Summary — where the four hours are

| step | rows out | planner estimate | actual | verdict |
|---|---|---|---|---|
| `dd_preprints` | 27,904 | 100,491 rows | **11.5 s** cold, 3.4 s warm | not the cost |
| `dd_sn` | 737,487 | 608,370 rows | **1.44 s** | not the cost |
| `dd_sn_pp` | 140,910 | — | join of two built tables | not the cost |
| `dd_abs` | 1,331 | — | 411 groups, largest 16 | not the cost |
| **`dd_scored`** | **1,616** | **393,412 rows** | **46,730,069 candidate pairs** | **the cost** |
| `dd_scored_pp` | 261 | — | same shape, preprint subset | small |

Everything except `dd_scored` completes in seconds.

## `dd_preprints` — 11.5 s, and the `LIKE` is the cheap part

```
Seq Scan on papers p  (cost=0.00..1586476.35 rows=100491)
                      (actual time=7689.131..11034.482 rows=27904 loops=1)
  Filter: ((arxiv_id IS NOT NULL) OR (doi ~~ '%/preprints.%') OR (venue ~~* '%arxiv%')
           OR ... OR (hashed SubPlan 2))
  Rows Removed by Filter: 155263
  Buffers: shared hit=628433 read=161135
  SubPlan 2
    ->  Gather  (actual time=30.211..7343.184 rows=27492 loops=1)
          ->  Parallel Seq Scan on source_records sr  (actual time=259.372..7281.822 rows=9164 loops=3)
                Filter: ((source = 'arxiv') OR ((raw ->> 'type') = 'preprint'))
                Rows Removed by Filter: 57297
JIT: Total 1027.818 ms   (Inlining 821.713 ms)
Execution Time: 11536.006 ms
```

**7.3 of the 11.5 seconds is the correlated `EXISTS`**, extracting
`raw->>'type'` from 199,382 JSONB documents. The leading-wildcard `LIKE`
that was originally blamed is one of eight OR'd predicates and among the
cheapest. JIT adds a further 1.0 s of pure compilation overhead.

Re-run as an actual `CREATE TABLE AS`, warm, write included: **3.4 s**. A
concurrent `pg_stat_activity` sampler caught only `IO/DataFileRead` — no
lock waits, no spills.

## `dd_sn` — 1.44 s

```
Unique  (cost=41688.61..95076.29 rows=608370) (actual time=255.465..1419.367 rows=737487)
  ->  Incremental Sort  (actual rows=758146)
        ->  Nested Loop  (actual rows=758146)
              ->  Gather Merge  (actual rows=168552)
                    ->  Sort  Sort Method: external merge  Disk: 13344kB
                          ->  Parallel Seq Scan on papers p  (actual rows=56184 loops=3)
              ->  Function Scan on unnest a  (actual rows=4 loops=168552)
Execution Time: 1443.506 ms
```

Spills 13 MB per worker to a temp file and is still fast. Estimate 608,370
against 737,487 actual — 1.2x low, fine.

## `dd_scored` — the whole cost, and a 119x row misestimate

Plan only; `ANALYZE` would cost ~3.2 h.

```
Unique  (cost=139205.20..253892.63 rows=944190)
  ->  Gather Merge
        ->  Sort  Sort Key: s1.id, s2.id
              ->  Nested Loop  (cost=0.42..93575.02 rows=393412)
                    ->  Parallel Seq Scan on dd_sn s1  (rows=102429)
```

**The planner expects 393,412 pairs out of the join. Measured, the blocking
produces 46,730,069.**

| | |
|---|---|
| rows in `dd_sn` | 737,487 |
| (surname, year) blocks | 322,447 |
| largest block | wang / 2025 — 1,966 rows |
| candidate pairs `sum(n*(n-1)/2)` | **46,730,069** |
| rows surviving `sim >= 0.92` | **1,616** |

Largest blocks: wang/2025 1,931,595 pairs; wang/2024 1,586,871; li/2025
1,415,403; zhang/2025 1,383,616. The top eight blocks are ~11.4M pairs,
24% of the total.

At ~0.25 ms per trigram `similarity()` call, 46.7M calls is ≈3.2 h, which
accounts for the measured 3 h 55 m.

**Why the planner is 119x low:** it assumes `surname` and `year` are
independent and roughly uniform. They are neither — a common surname in a
recent year is a 2,000-row block, and block cost is quadratic in that
number. With a 393K estimate the planner has no reason to consider a plan
suited to 46.7M rows.

## Directions, none applied

Each of these changes which duplicates are FOUND, so each needs its own
precision measurement rather than a wall-clock argument:

1. **Extend the blocking key** so common surnames split — e.g. (surname,
   year, first character of `title_norm`). Directly attacks block size,
   which is the quadratic term. Changes recall.
2. **Cap block size** and route oversized blocks to review, consistent with
   how oversized groups are already handled. Changes recall, predictably.
3. **Prefilter before scoring** on something cheaper than trigram
   similarity — the length band is already in the join condition but prunes
   only after enumeration.
4. **`(new x all)` on rebuild** instead of `(all x all)`. No new invariants
   and strictly less work, but at ~12% new that is roughly a 4x reduction,
   not an order of magnitude. Block size dominates; join shape multiplies it.
5. **`CREATE STATISTICS (surname, year) ON dd_sn`** would fix the
   correlation estimate. Cheap and non-behavioural, but it only lets the
   planner cost the work correctly — it does not remove the work.
