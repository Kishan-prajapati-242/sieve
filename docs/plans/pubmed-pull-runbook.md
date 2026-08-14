# PubMed pull runbook — staged, not run

**Composition DECIDED (Kishan, 2026-08-13): take the full ~23,000 pool** as
a coverage decision under DECISION-2f's standard, recording the realized
composition as the composition. There is no weighting to honour — the
0.40/0.25/0.20/0.15 appears in no decision record. Taking 9,700 would leave
a one-way door, since a later top-up re-pays the full cascade against that
same precedent.

**Queued behind the UI. Kishan has NOT committed to the labeling session** —
that is his time and it can lag the pull, with the precision number carrying
a labelled gap until it happens.

Every step is a checkpoint. **Wrap the entire session in
`caffeinate -dimsu`** (CLAUDE.md standing rule) and capture stdout.

    caffeinate -dimsu bash -c '<the whole sequence>' 2>&1 | tee pull-$(date +%F).log

## 0. Pre-flight — record the state you are leaving

    docker compose exec -T postgres psql -U sieve -d sieve -c \
      "SELECT count(*) papers, count(pubmed_id) with_pmid, count(embedding) embedded FROM papers;
       SELECT relpages FROM pg_class WHERE relname='papers';"
    git status --short && git rev-parse HEAD

**Expected:** 183,167 / 44,517 / 183,167, 35,348 pages, clean tree.
**Rollback:** n/a. This is the number you restore toward.

## 1. Fetch — ~7 min. REVERSIBLE.

    make seed-pubmed        # enqueues 4 jobs, one per term
    make drain              # one worker until the queue is empty

**Checkpoint:** `source_records` grows by ~23,000; `papers` grows by the
non-duplicate share; `/api/stats` shows `by_source.pubmed`.
**Rollback:** `DELETE FROM papers WHERE id IN (SELECT paper_id FROM
source_records WHERE source='pubmed' AND paper_id IS NOT NULL)` then
`DELETE FROM source_records WHERE source='pubmed'`. Clean because nothing
has merged yet.

## 2. Cascade — ~10 min plan + merge. **HARD TO UNDO.**

    docker compose run --rm ... test python -m bench.dedup_plan --rebuild
    # READ THE PLAN. Then, only if it looks right:
    docker compose run --rm ... test python -m bench.dedup_execute --execute

**Checkpoint:** plan's `groups_merged` and `rows_merged_away` are
plausible; `held_by_dedup_review` reports the existing 179 groups held.
**Rollback:** every merge has a full JSONB snapshot in `merges.merged_from`
and `api/dedup/merge.py::rollback()` restores original ids — proven on 122
groups by DECISION-3c's unwind. But it is per-merge and slow, and it is the
first irreversible-feeling step. **Take a database dump before this step**
if you want a cheap undo:

    docker compose exec -T postgres pg_dump -U sieve sieve | gzip > pre-cascade.sql.gz

## 3. Embed — 21-33 min. REVERSIBLE (recompute).

    docker compose run --rm ... test python -m api.embed.backfill

**Checkpoint:** the windowed rate lines. Watch for
`[CLOCK DISCONTINUITY]` — if it appears, the host slept and the rate is
not a sustained measurement. **This produces the project's first sustained
throughput number; keep the log.**
**Rollback:** `UPDATE papers SET embedding = NULL WHERE ...` and re-run.

## 4. HNSW — 36-41 s. REVERSIBLE.

    docker compose exec -T postgres psql -U sieve -d sieve -c \
      "SET maintenance_work_mem='1GB'; REINDEX INDEX papers_embed_idx;"

**Checkpoint:** no `hnsw graph no longer fits` NOTICE. If it appears,
`maintenance_work_mem` did not take and the index is the slow two-phase
build. **Rollback:** REINDEX again.

## 5. Ground truth — 38 s each, BOTH. REVERSIBLE.

    python -m bench.rebuild_ground_truth
    python -m bench.rebuild_ground_truth --refresh-queries \
        --out bench/labels/exact_top200_refreshed.json

**Checkpoint:** the assert that ground-truth corpus == live corpus passes.
**Rollback:** `exact_top200_wide.superseded_*.json` is written
automatically.

## 6. Re-measure — ~45 min total. REVERSIBLE.

    python -m bench.ef_tradeoff          # ef ladder + embed share
    python -m bench.latency              # per-mode p50/p95/p99
    python -m bench.exact_scan_baseline  # warm baseline
    python -m bench.paired_speedup       # the paired ratios
    python -m bench.vector_recall_at_defaults

Serially, nothing else touching the machine — `method.contention.clean`
must be true or the levels are not publishable.

## 6b. Re-run the demo queries — this is a MEASUREMENT, not housekeeping

    python -m bench.demo_queries

**It is the parked two-shares question firing** (progress.md, P1), not a
demo refresh. The three demo queries were selected on a corpus with a
coverage gap and PubMed fills exactly that gap:

* **de-identification** — hybrid wins because BM25 finds de-identification
  papers without BERT and vector finds BERT papers without de-identification.
  That only holds while few papers match BOTH terms well. De-identification
  of clinical records is core clinical NLP and PubMed is where it lives.
* **medical jargon** — BM25 returning zero IS a coverage gap in lay-language
  health communication, which is PubMed-indexed.

**Report, per query, before and after:** which arm's uniques hybrid adopted
(`hybrid_from_bm25_only`, `hybrid_from_vector_only`), and whether hybrid's
margin narrowed. **If hybrid stops clearly winning anywhere, say so plainly.**
That is a Phase 4 nDCG finding arriving early and evidence on a parked
question — not a demo problem to work around by picking friendlier queries.

The ANIMATION code is corpus-independent (nothing references a paper id,
query or corpus size; rows key on whatever `r.id` the API returns), so only
the query SELECTION needs redoing.

## 7. Labeling draw — see post-pubmed-labeling-draw.md

    make dedup-sample && make label      # 123 pairs, Kishan
    make dedup-precision && make dedup-agreement

---

## Numbers that go stale the moment step 1 commits

**Do not quote any of these between the pull and step 6.** Every one is
measured against 183,167 papers / 35,348 heap pages:

| file / claim | what dies |
|---|---|
| `results_ef_tradeoff.json` | the whole ef ladder, recall AND latency, incl. r@20 0.9238 / r@200 0.9856 |
| `results_mode_latency.json` | bm25 1.0 / vector 2.0 / hybrid 18.2 ms p50 and all tails |
| `results_exact_scan.json` | warm p50 60.9 ms, cold median |
| `results_paired_speedup.json` | **24.1x and 3.8x** |
| `results_vector_recall_defaults.json` | 0.9861 |
| `results_recall_diagnosis.json` | the survivorship split |
| `labels/exact_top200_wide.json` | the ground truth itself |
| composition | no-DOI 6.6%, specialty 62.0%, era 89.7/10.3 |
| cascade wall clock | 10 m 11 s is for this corpus |
| dedup precision/recall | 0.9594 / 0.9662 — already caveated as pre-PubMed |

Safe to quote across the pull: the dedup **method** findings (the sibling
rule, merge ordering, the FK index 1,140 ms -> 0.608 ms), the clock
artifact, the trigram-index rejection, and anything in `findings.md` that
is a mechanism rather than a level.
