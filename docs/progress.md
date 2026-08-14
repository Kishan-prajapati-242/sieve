# Progress

Phase: 3 IN PROGRESS. Dedup work is COMPLETE and MEASURED. The UI is built
and the four visual steers are landed; the functional list and the PubMed
pull are what remain.

### 2026-08-14 — the motion had never fired; row rebuilt; count labelled

**The marquee interaction was animating nothing.** Instrumented rather than
read: tag every `<li>` with a JS property, toggle mode, read back — 0 of 20
nodes survived. The results block gated on `!isFetching`, so a mode change
unmounted the list and React built fresh nodes. Layout animation moves
EXISTING DOM. `placeholderData` fixed it; the probe now reports 5 of 5
survivors, reordered. Every earlier motion decision had been made against
frames that could not have been produced by the code.

**Choreography re-derived at the real k=20.** The earlier pick was tuned on
a top-8 slice the app never performs (3 arrivals); k=20 bm25 -> hybrid is 0
leaving / 5 staying / 15 arriving, and bm25 -> vector is a total
replacement. Survivors now move alone (420 ms), arrivals are gated on that
completing and fill bottom-up over <=250 ms, so the top stays quiet while
the tail populates. Total ~800 ms, stated not buried. No survivors -> the
gate collapses to a pure staggered entrance.

**Static legibility, not hover.** One hue per arm (amber keyword, violet
semantic). Verified under `prefers-reduced-motion: reduce`: top rows show
both chips coloured, tail rows show one greyed with an em-dash. A missing
arm renders muted rather than being omitted. Works in a screenshot and on a
phone, which is what a Phase 4 demo URL will actually be opened on.

**Row anatomy rebuilt.** Title leads and is the DOI link, provenance below,
three type levels, hairline dividers instead of cards. Six results per
1000 px, up from four.

**The count is resolved per mode** — 5 keyword matches / 183,167 papers
ranked / 202 fused candidates — with the `kind` travelling alongside the
integer so the UI cannot render a bare N. Two bugs found doing it: the
overlap query ran at the default ef=40 while fusion runs at 600 (it
reported overlap 1 while three visible rows carried both ranks), and the
naive counts cost 47x on the modes they labelled. Both fixed and in
findings.md. Timings breakdown now shows embed/retrieve/serialize/ef in the
product.

Warm p50 after the fix: bm25 1.9 ms, vector 14.9 ms e2e (1.9 retrieve),
hybrid 32.9 ms e2e (29.1 retrieve). 196 backend tests + 25 frontend green.

**Next:** the functional list in the accepted order — pagination, matched-term
highlighting via `ts_headline`, sort control, faceted sidebar with counts,
bulk add-to-collection, abstract preview. Then the PubMed pull, which is
staged in `plans/pubmed-pull-runbook.md` and still NOT started.

### 2026-08-12 — retrieval re-measured, speedup methodology changed

Ground truth was stale: 8.2% of the ids it referenced had been deleted by
dedup, 99% of queries had a dead id in their top-200. Rebuilt against
183,167 papers, EXPLAIN-verified, and the file now records the corpus it
describes.

`VACUUM FULL` on papers: heap 44,059 → 35,348 pages, FTS GIN 118 → 83 MB,
title trgm 70 → 39 MB. The corpus carried ~20% bloat, well beyond what
dedup deleted. Every percentile steadied afterwards.

Speedups are now paired (DECISION-3d): both sides timed back to back on
the same query inside one run, ratio per query, bootstrap CI. The
cross-run ratios in `results_speedups.json` are retired in place.

**Current published numbers** (183,167 rows / 35,348 pages):

| | p50 | p95 | p99 |
|---|---|---|---|
| bm25 sql | 1.0 | 25.2 | 47.2 |
| vector sql (ef=40) | 2.0 | 3.0 | 3.7 |
| vector e2e | 8.6 | 15.0 | 21.7 |
| hybrid sql (200/600) | ~~18.2~~ WITHDRAWN | [53.4, 84.7] | [161.7, 249.3] |
| hybrid e2e | 25.5 | [61.4, 100.0] | [172.4, 253.3] |
| exact scan warm | 60.9 | 111.5 | [164.7, 251.2] |

Paired speedup vs exact scan, with 2026-08-14 re-runs beside them:

| | published | re-run 08-14 | verdict |
|---|---|---|---|
| retrieval-only ef=40 | 24.1x [23.3, 25.0] | 23.0x [21.7, 24.3] | holds, CIs overlap |
| retrieval-only ef=600 | 3.8x [3.6, 3.9] | 3.5x [3.4, 3.6] | holds at the edge |
| e2e ef=600 | 2.9x [2.8, 3.0] | 2.9x [2.8, 3.0] | reproduces exactly |
| e2e ef=40 | **7.1x [6.9, 7.3]** | **8.7x [8.4, 9.0]** | **CIs disjoint — see below** |

The e2e ef=40 move is not a retrieval change. e2e adds the query-embed cost
to BOTH arms, and a constant added to both arms of a ratio does not cancel —
it pulls the ratio toward 1. Embed drifted 8.0 -> 6.1 ms between the runs,
and (6.1+70.4)/(6.1+3.1) = 8.3 against (8.0+70.4)/(8.0+3.0) = 7.1. The
arithmetic closes. **e2e ratios are a function of a component that is not
the thing being compared;** the retrieval-only ratios are the robust ones. Vector recall@200 at shipped
defaults: 0.9861 (se 0.0010).

**RESOLVED 2026-08-14 — 18.2 is withdrawn.** The drift was 13.6 → 15.8 →
15.3 → 18.2, and a clean re-run (api/web/worker stopped) produced per-run
p50s of [18.9, 14.7, 14.3]. Max/min is 1.32, so the stability gate refuses a
point estimate and there is no replacement figure. But two of three runs sit
BELOW 18.2 and none reproduce it, so the standalone hybrid p50 now has
evidence against it and none for it. It was never the paired ratios'
baseline arm — those pair `exact` against `search_vector` at ef=600, not
hybrid — so nothing downstream depends on it. Withdrawn rather than
replaced; hybrid sql p50 is unpublished until a run passes the gate.

**Still open:** the paired treatment needs extending to
`search_hybrid()` before any hybrid before/after claim is published.


### 2026-08-12 (later) — Phase 3 build: source three, the queue, screening

**PubMed client** (`api/ingest/pubmed.py`, 14 tests). esearch for PMIDs +
efetch for records, MEDLINE XML via stdlib ElementTree, structured
abstracts keeping their section labels, is_retracted from PublicationType
("Retracted Publication", NOT "Retraction of Publication"). Bucket at
2.5/s against NCBI's documented 3/s — they block rather than throttle.
Live smoke run: 12 articles, rerun changed no counts. `get_response()` now
holds the retry loop with `get_json`/`get_text` as wrappers, so the XML
path gets the same full-jitter discipline.

**Queue** (`api/queue/`, migrations 0010-0011, 17 + 7 tests). `SELECT ...
FOR UPDATE SKIP LOCKED` claim, full-jitter backoff via `run_after`,
dead-lettering at `max_attempts`, `reap_stale()` for jobs whose worker was
SIGKILLed. Work and completion commit in ONE transaction; failure recording
runs in its own, because a failure written inside the aborted transaction
rolls back with the work and the job spins forever. Ingestion is converted
one job per PAGE: the page is the idempotent unit, its dedupe_key is
(source, query, offset), and each page enqueues its successor inside its
own transaction — so the queue holds the crawl's position, committed with
the data. Verified: 8 threads racing for 200 jobs claim each exactly once;
a worker killed mid-crawl loses no pages and creates no duplicates.

OpenAlex is deliberately NOT converted: cursor pagination means page N+1 is
only reachable by holding page N's cursor, so a page is not addressable by
a dedupe key. Its client keeps its own resumable loop.

**Screening + export** (`api/collections/`, migration 0012, 17 tests).
Collections, include/exclude/maybe as an upsert on (collection_id,
paper_id), BibTeX export with TeX escaping, brace-protected acronyms, and
stable collision-suffixed keys so the same collection exports byte
identically. `@misc` when there is no venue — a preprint is not an article
with a blank journal. `ON DELETE CASCADE` from collections but NOT from
papers: dedup deletes paper rows, and a human decision must not vanish
because its paper was merged.

**`/api/stats`** now also reports per-source counts, merges by strategy,
queue depth with `oldest_pending_age_s` and `stale_running`, and screening
totals.

**Deferred, with reasons:** cross-process rate limiting (N workers hold N
buckets, so scale workers for embedding throughput not fetch throughput);
embed_batch and dedup_batch handlers; the full PubMed pull, which will
change the corpus and invalidate every latency number above.

### Post-pull measurement procedure (settled 2026-08-13, before the pull)

The corpus change and the query-set change are separate effects and must
not be confounded — untangling exactly that confound took two turns on the
last rebuild, where a degenerate query weighted 28x moved a decision's
apparent margin by five points.

So after the pull, run the ground-truth rebuild TWICE, 38 s each:

    python -m bench.rebuild_ground_truth
        # same 493 queries, new corpus -> isolates the CORPUS effect,
        # directly comparable with everything measured this session

    python -m bench.rebuild_ground_truth --refresh-queries \
        --out bench/labels/exact_top200_refreshed.json
        # new titles drawn from the post-pull corpus -> represents a corpus
        # a third of whose sources are new; NOT comparable with the above,
        # and that is the point

Recall is reported over DISTINCT query strings in both.

Also standing, per CLAUDE.md: `caffeinate -dimsu` around the whole pull,
and capture the encode's stdout — `backfill.py` now logs windowed rates
with both clocks and flags any window where wall time outruns monotonic
time, which is how this project finally gets a sustained throughput number.

## PARKED — open, not blocking, revisit on the stated trigger

Neither of these blocks the PubMed pull. They are here so they stop
appearing in every status summary.

**P1. The two-shares question.** DECISION-2's knob is the
CLINICAL-INFORMATICS share (10%), tuned so hard negatives keep the Phase 4
comparison meaningful; DECISION-2f's protected quantity is the OVERALL
specialty share (62.2%), defended against general-nlp dilution. The PubMed
pull moves the second up, 62.0% -> 65.2% at the pool. Whether that helps
or hurts under the FIRST has never been tested — DECISION-2 sorts specialty
mass into "on-topic mass" (welcomed) and "not random hard negatives" (cut
in half), and nothing places PubMed's terms on either side. It is an
argument, not a measurement.
*Trigger, now SHARPER (Kishan, 2026-08-13): the post-pull re-run of
`bench/demo_queries.py`. The three demo queries were chosen on a corpus with
a coverage gap that PubMed fills precisely — de-identification of clinical
records and lay-language health communication are both PubMed-indexed. So
hybrid's win on the de-identification query depends on few papers matching
BOTH "BERT" and "de-identification" well, and bm25's zero on the jargon
query IS the gap. Report per query which arm's uniques hybrid adopted before
and after, and whether the margin narrowed. If hybrid stops clearly winning
anywhere, that is evidence here, not a demo to re-pick.*

**P2. The order-dependent cap.** A group's cap binds on its ATTRIBUTED
strategy — the earliest contributing strategy in ORDER — and abstract_hash
precedes title_exact, so a group carrying both is capped at 8 rather than
2. Order-invariance (bind on the strictest contributor) is a desirable
property; the claim that attribution varies with run history was withdrawn
as unsupported. The measurement objection stands: `dedup_sample` stratified
on the same attribution, so the 1.000 stratum contains title_exact-edged
groups and the rule cannot be chosen from existing labels.
*Trigger: the post-PubMed labeling draw, whose
`acc_abstract_hash_x_title_exact` stratum (n=20) is designed to settle it.*

## Phase 3 status (2026-08-01)

DONE:
- **arXiv client** (api/ingest/arxiv.py): 1 req/3s bucket, --limit, Atom
  parsing, version-stripped ids, idempotency verified live (100 entries
  twice -> identical counts). 8 tests.
- **Refresh propagation + DECISION-3a**: refresh now moves title, abstract,
  citation_count and is_retracted onto papers; the embedding is nulled at
  every text-write site, in ONE shared store layer (api/ingest/store.py) so
  no source client can forget it. The retraction case is the correctness
  driver, not embedding freshness.
- **Boilerplate blocklist** (migration 0007 + bench/seed_boilerplate.py):
  933 hashes / 3,847 papers embed title-only. Governs EMBEDDING policy
  only; merging is the sibling rule's job.
- **Dedup cascade** (api/dedup/{rules,cascade,merge}.py, executed): corpus
  196,988 -> 183,167 papers. 13,445 merges, all with rollback snapshots.
  Phase 1 duplicate baseline 3/20 -> **0/20**, verified in bm25 and hybrid.
- **Reversibility, exercised on production data**: DECISION-3c unwound 122
  title_exact groups, restoring 314 papers, 0 errors, 0 orphaned records.
- **Precision/recall measured** (DECISION-3c): precision 0.957
  [0.904, 0.998], recall-among-candidates 0.973 [0.959, 0.988], F1 0.965,
  from 120 blind hand-labeled pairs. Harness: bench/dedup_{sample,label,
  precision,agreement,unwind}.py, `make label` / `make dedup-precision` /
  `make dedup-agreement`. Inter-annotator kappa 0.905.

CURRENT STATE: 183,167 papers, all embedded, HNSW 204 MB, db ~2.25 GB.
199,382 source_records (openalex + arxiv), 0 orphaned. 179 groups in
dedup_review awaiting human judgment (57 size-capped versioned releases +
122 unwound title_exact groups).

REMAINING IN PHASE 3, in order:
1. **PubMed E-utilities client** — 3 req/s without a key, XML. Same
   discipline as arXiv: --limit, per-source bucket, idempotency verified by
   running twice. Will exercise id_exact (pubmed_id), which has proposed 0
   pairs so far because no PubMed IDs repeat yet.
2. **Queue conversion** — ingest_jobs with SELECT ... FOR UPDATE SKIP
   LOCKED, backoff with full jitter, dead-lettering after max_attempts, N
   concurrent workers. Tests: concurrent workers claim disjoint jobs, a job
   failing 5 times lands in `dead`.
3. **Collections, screening decisions, BibTeX export**, and the /api/stats
   expansion the brief asks for (per-source counts, merges by strategy,
   queue depth, dead jobs).

KNOWN GAPS, measured and deliberately open:
- **ref_below_threshold_preprint misses 83% of sampled pairs** — the Ascle
  gap as a population fact. Fix is more mechanical publisher DOI rules
  (jmir_doi is the first, 524 merges), NOT a threshold tuned to a fixture.
- 179 dedup_review groups are unmerged, so the COVID Twitter dataset still
  returns 141 times. Collapsing versioned releases in the UI without
  merging records is the Phase 4 product answer.
- Label drift caveat on the 0.957: 10 of 120 labels were corrected on
  review (docs/findings.md).

Phase 2 was **CLOSED** by Kishan 2026-07-31 (arXiv + PubMed
clients, dedup cascade, SKIP LOCKED queue, collections/screening/BibTeX,
stats expansion). Phase 3 inputs already recorded: dedup BEFORE fusion
(RRF double-pays twins), exact-abstract-hash cascade step before trigram
(560 groups / 1,273 papers), no-DOI stratum (12,036 papers, 6.6% — re-counted 2026-08-12) needs
its own precision measurement, dedup baseline = 3/20 redundant on the
"clinical text simplification" query.

Gate decisions (docs/decisions.md): DECISION-2e — hybrid defaults depth
200 / ef 600, shipped and live-verified; carries the prediction that
ef's real cost surfaces once Phase 4 fixes the bm25 tail (test then).
DECISION-2f — no corpus top-up; 196,893 stands, in Kishan's words.
Per-mode latency percentiles re-measured at the shipped defaults
(bench/results_mode_latency.json). The brief calls this the resume
checkpoint.

## Phase 2 acceptance check (2026-07-31)

Build list: embedding pipeline (resumable, SIGKILL-proven) DONE; HNSW
DONE (0006, 211MB); mode=vector + mode=hybrid RRF in raw SQL DONE;
frontend mode toggle + per-result keyword/semantic/fused breakdown DONE
(12 web tests); latency instrumentation DONE (per-request decomposition
+ bench/latency.py). Tests: RRF arithmetic on fixture, mode switching,
embedding idempotency — all present, 106 backend + 12 web green.

Acceptance: (1) all three modes verified live over the corpus — which is
**196,893 papers, 98.4% of the 200K target** (nominal 205K budgets
exhausted; a ~3.5K general-nlp top-up ≈ ~25 credits would cross 200K,
needs Kishan's go). (2) Demo queries: bm25 wins "i2b2 2010 relation
extraction challenge" (exact identifier anchors; vector drifts to
generic relation extraction); vector wins "reducing the reading
difficulty of health leaflets for people with low literacy" (bm25
AND-semantics returns ZERO rows; vector top-4 on-point); hybrid beats
both on "making medical documents easier to understand for patients"
(hybrid top-3 = bm25's Paper Plain — which vector missed — plus vector's
patient-friendly-notes pair — where bm25 drifted 3/5). (3) Latency
percentiles per mode (bench/results_mode_latency.json, as-shipped
defaults vector ef=40 / hybrid depth=100: bm25 SQL 1.9/24.8/37.8;
vector SQL 1.9/~3.5/~4.5 (tails gated), e2e 8.9/16.2/28.6; hybrid SQL
6.3/26.7/46.5, e2e 13.7/31.0/62.6 — bm25 and hybrid tails are the
match-count driver, findings.md).

N/ef recommendation (circular reason removed): **N=200, hybrid ef=600**
— recall@200 = .9857±.0011 at ef=600 vs .9431 at ef=200 (+4.3 points for
a p50 cost inside run-to-run noise: 13.6ms stable vs [12.5,19.9] range);
ef=800 buys +0.4 more for +4ms. Non-circular support for N=200: input
recall to the fuser and the .781 identical-order rate (order is what
nDCG scores). Revisit both under Phase 4 labels. Defaults in code remain
40/100 until Kishan calls it.

Joint depth/ef sweep done (2026-07-31, bench/fusion_depth_sweep.py,
results json alongside): vector-CTE recall@N vs exact top-200 runs
0.930/0.906/0.926/0.943/0.982 at N=20/50/100/200/500. TWO CLAIMS
WITHDRAWN (Kishan; recorded in the results file's withdrawn_claims):
the "N=50 dip proves the coupling" reading is confounded (both k and ef
moved between rows — the clean evidence that ef buys recall at fixed k
is the fixed-k sweep: recall@50 .896/.970/.984 at ef 40/160/320, plus
the ef-at-fixed-depth sweep); and the top-20-overlap justification for
N=200 is circular (overlap vs the deepest tested ranking reaches 1.0 by
construction — the identical-order rate, .781 at N=200, is the honest
column since order is what nDCG scores). Convergence is gradual, no
clean stopping depth below 500. Latency (hybrid SQL p50): 5.5→18.4ms;
e2e-with-embed p50 14.2→27.3ms; embed-cached (=SQL) 5.5→18.4ms. Fused
QUALITY is explicitly not measurable pre-labels (method record says so).
Phase 4 revisits flagged: N and rrf_k under nDCG; ef>N (measured in
bench/ef_at_fixed_depth.py); label-based quality to replace the proxy.

ef_search default HELD AT 40 (Kishan, 2026-07-31) — the sweep's ef=160
elbow answers k=10, which is not the production operating point. Two
reasons the decision is not decidable yet, recorded so the sweep isn't
read as settled: (1) ef_search is the candidate-list width, and recall@50
< recall@10 at every ef >= 10 shows that asking k > ef asks for more than
the search breadth — fusion over-fetches top-N per ranker, so what
matters is recall at candidate depth N (if N=200, ef must be >= 200 and
160 is already too narrow); the hybrid path auto-raises ef to >= depth
for exactly this reason. (2) The "embed floor makes 160 cheap" argument
dies under Phase 4's query-embedding cache: on a cache hit, ef=160's
3.2ms vs ef=40's 2.1ms is +52% of the dominant component, not +14% of
the total. Candidate depth and ef_search are ONE coupled decision, made
with fusion sweep data.

Recall sweep done (2026-07-31, bench/hnsw_recall_sweep.py; table in
results json, curve in bench/plots/hnsw_recall_sweep.png; n=520 queries,
tie-aware recall, ground truth bench/labels/exact_top50_wide.json):
strict_order at ef 40/160/320/640 gives r@10 0.939/0.976/0.983/0.989.
**Ceiling: 0.989@10 / 0.992@50 at ef=640 — NOT 1.0**; that residual ~1%
is the m=16/efc=64 construction ceiling, unreachable by search depth. A
rebuild at higher efc costs ~40s (measured) if Phase 4 ever shows recall
as the bottleneck. iterative_scan does not change recall where ef >= 50;
below that, off under-returns (ef=5: 5.3 rows, r@50 0.10) and strict
rescues (r@50 0.79). Recommendation pending Kishan's call: **ef=160**
(r@10 0.976±0.004, sql p50 3.2ms, e2e p50 ~9.6ms). DEFAULT_EF_SEARCH
stays 40 until he decides. Fusion untouched.

Shipped 2026-07-31 (late): mode=vector, measured under the protocol
below (bench/vector_latency.py, prewarm + 520 distinct queries + 1
discarded warmup run + 3 measured runs). Components at k=10, ef_search
40: **embed p50 7.6 ms (77% of end-to-end p50 9.9 ms — the fixed floor
HNSW cannot reduce), SQL p50 2.3 ms / p95 4.5 ms**; tails gated to
ranges per the stability rule (e2e p95 range 13.7-18.3). Ratios, same window on both sides (harness speedup() now refuses
anything else): retrieval-only 55/2.3 ~= 24x; end-to-end
(7.6+55)/9.9 = **6.3x** — the originally reported 5.5x divided
scan-only by end-to-end, a window mismatch that ran conservative
(findings.md). The prefix contract
is enforced INSIDE embed_query (bypass-proof by construction) and pinned
end-to-end by a stubbed-encoder test; iterative_scan=strict_order fixes
year-filter underfill (test forces the HNSW path; found along the way:
for SELECTIVE year filters the planner correctly skips HNSW for
papers_year_idx + sort — exact, no underfill). ef_search is request-
tunable, echoed per response, logged. Next (Kishan's go): recall sweep
(bench/hnsw_recall_sweep.py against bench/labels/exact_top50.json), then
fusion.

HNSW measurement protocol for mode=vector (agreed 2026-07-31, before any
index numbers): interleaved repetition homogenizes cache for a seq scan
(every query touches the whole table) but NOT for HNSW, where queries
traverse different graph regions and per-query cache locality is real.
So: (1) primary warm numbers only after pg_prewarm of papers_embed_idx +
the heap, making cache state uniform across queries instead of an
artifact of repetition order; (2) distinct-query-heavy samples (hundreds
of distinct queries, few reps) so percentiles describe the query
distribution, not one query's cache luck; (3) cold cache stays the
single-shot restart+drop_caches protocol; (4) everything through
bench/harness.py with its mandatory timing_window. took_ms in the API now
decomposes into embed/retrieve/serialize — vector mode must fill
embed_ms, the fixed floor HNSW cannot reduce.

Shipped 2026-07-31: full corpus embedded (196,893/196,893 — Kishan's
overnight run, 10+ h, see findings.md thermal entry) and the HNSW index
is BUILT (migration 0006): m=16, ef_construction=64, parallel 2 workers,
maintenance_work_mem 1GB session-scoped. Measured: build ~36-41 s (vs
5-15 min estimated), index 211 MB (estimate 200-260), db 2,024 MB
(estimate 2.0-2.1 GB), VM available never below 2,086 MB. Verified
`Index Scan using papers_embed_idx` on the ORDER BY <=> LIMIT shape.
Two findings from the pre-build probe: (1) parallel workers share ONE
maintenance_work_mem-sized area (pgvector v0.8.5 hnswbuild.c + live
probe), so no per-worker OOM math; (2) that area is a single POSIX shm
segment allocated up front, and the container /dev/shm default of 64MB
kills the build — postgres now runs with shm_size: 2g in compose.
Pre-index baseline captured FIRST (bench/exact_scan_baseline.py):
exact-scan p50 61.9 ms / p95 98.7 / mean 67.7 over 20 domain queries,
196,893 rows scanned each, EXPLAIN archived; exact top-50 ground truth
in bench/labels/exact_top50.json (query vectors inline — the recall
sweep never re-embeds). Next: mode=vector endpoint (query_text() prefix,
hnsw.ef_search, iterative_scan for year filters), then the recall sweep,
then fusion.

Phase 2 pre-encode prerequisites (2026-07-30): BOTH PASSED, full encode
awaiting Kishan's go. (a) Container benchmark (bench/encode_throughput.py
inside the compose test container — the podman VM, not the host): 13.2
docs/s fp32, **248 min projected for 196,893 papers**, peak RSS 2.05 GB
in the 4 GB VM (no memory raise needed; `podman machine set --memory` is
the knob if wanted). Sustained rates in real runs measured 8.8-12.7
docs/s, so plan for ~4.5-6 h wall clock with throttling. (b) Live
kill-proof: SIGKILL at 2,304 committed rows -> exactly 2,304 durable on a
fresh connection; resume --limit 2696 -> exactly 5,000 total, 0 gaps
below the high-water mark, all 2,304 pre-kill vectors byte-identical
(md5 diff). Those 5,000 real embeddings stay — the full encode converges
on top of them. THE PROOF CAUGHT A REAL BUG first (findings.md
2026-07-30): psycopg default connections made per-batch commits into
savepoints; jobs now require autocommit connections, guarded loudly.
Model files live in ./models/bge-small-en-v1.5/ (gitignored):
tokenizer.json + onnx/model.onnx from BAAI/bge-small-en-v1.5. Full
encode runbook, when approved:
`docker compose run --rm --no-deps -v ./models/bge-small-en-v1.5:/models
-e EMBED_MODEL_DIR=/models test python -m api.embed.backfill`
(resumable; rerun after any interruption). HNSW index comes AFTER it.

Phase 2 prep (2026-07-29, decided + measured, NOT built): DECISION-2b
(bge-small-en-v1.5, title+abstract, 512 window, one vector per paper) and
DECISION-2c (dataset-type stays; 0 of 120 top-20 slots polluted) are in
docs/decisions.md. The bge query-prefix contract is pinned in
api/embed/texts.py + tests — the Phase 2 encoder and search path MUST
route through it. Encode throughput measured on 1,000 real papers (ONNX,
CPU, length-sorted batching, batch 32): fp32 14 docs/s -> ~230 min
projected for 197K; **int8 dynamic-quantized 29 docs/s -> ~114 min**,
int8-vs-fp32 cosine mean 0.9977 / min 0.9906, peak RSS 1.73 GB.
Projections assume no thermal throttling — the fanless M1 Air will
throttle on a sustained run, so the encoder must checkpoint and resume
(the brief requires resumability anyway). Full encode NOT started, per
Kishan. Bench deps (tokenizers, onnxruntime, numpy) live only in the host
venv for the probe; they enter pyproject with the real encoder.

Shipped 2026-07-29 (DECISION-2, corpus composition v2): the full pull ran.
**196,893 papers / 199,285 source records, 1,435 credits (1,040 requests),
~24 minutes.** Composition from GET /api/stats (papers, first-fetch
provenance): biomedical-clinical-text 62,751 (31.9%), general-nlp 59,452
(30.2%), mental-health-nlp 21,895 (11.1%), clinical-informatics 18,465
(9.4%), text-simplification 18,055 (9.2%), phrase core 1,265 (0.6%),
unattributed 15,010 (7.6%). **Specialty share 62.2%** against the >25%
goal. The unattributed bucket is old-corpus records no new query refetched;
they cannot be characterized by topic because the old SELECT_FIELDS had no
topics field, and provenance is never inferred — the old crawl was ~75%
general concept, so that bucket skews general, making 62.2% conservative.
Coverage: 4,056 null venue (2.1%), 655 null authors (0.3%), 28 null
abstract, 173 retracted (flagged, visible), 181,635 distinct DOIs.
Era split 89.7% recent (2017+) / 10.3% classics (re-counted 2026-08-12 at 183,167; was 90.3/9.7 at 196,893) — the specialty topics are
recency-skewed, so classics slices underfilled and the run reported it.
Junk types skipped: 1,499 (peer-review 398, editorial 377, paratext 368,
erratum 172, supplementary-materials 166, retraction 18).
DB is now 1.55 GB (papers 730 MB, source_records 815 MB) — worth watching
against the 8 GB M1 Air and the Neon free-tier subset plan in Phase 5.
Search on the 197K corpus: 46-157 ms for k=10 (GIN index, no tuning yet);
Phase 2's bench/latency.py replaces these smoke numbers with p50/p95/p99.

Nominal budget was 205K works; 196,031 were fetched because several
queries exhausted year slices below budget (biomedical-clinical-text
66,013 of 70,000; text-simplification 23,925 of 25,000; mental-health
22,503 of 25,000; the phrase queries far below, as expected). Fetches
reconcile exactly: 170,656 new papers + 582 DOI-linked + 23,102 refreshed
+ 192 no-title + 1,499 junk-type = 196,031.

Note on reading /api/stats: `per_query` in a run's own output counts works
FETCHED by that query (a work already in the corpus counts again as a
refresh), while /api/stats counts records/papers by FIRST-fetch query. They
differ where pools overlap — text-simplification fetched 23,925 works but
owns 18,055 papers, the difference being works another query saw first.
Both are correct; they answer different questions.

Shipped 2026-07-29 (independent verification): `make test` runs the pytest
suite in Docker (Dockerfile dev target -> compose `test` service,
profile-gated out of `up`); `make test-web` and `make lint` (CI's exact
commands) likewise. No host Python/Node needed — green counts no longer
depend on anyone's say-so. The scratch_db DROP DATABASE guard stays
local-only by default; the test service opts in to host "postgres" via
SIEVE_TESTS_ALLOW_DB_HOST (verified both ways: 75 pass with it, 32 skip
without). Stage order keeps a target-less `docker build .` (= Render) on
the runtime image — verified pytest absent there. GitHub remote now
exists; remote main is at a2800f3 (27 commits behind), which already
carried ci.yml, so Actions presumably ran on that push — result not
checked from this machine. Next push runs both CI jobs (python + web).

Shipped 2026-07-29 (task 6): the frontend, all-Docker (DECISION-1d,
docs/decisions.md). `docker compose up` now brings up postgres → migrate →
api → web; the page is at localhost:5173. web/ is Vite + React 18 + TS +
Tailwind + TanStack Query, served by a stock node:20-alpine container with
the source bind-mounted — hot reload works but only via polling
(watch.usePolling in web/vite.config.ts; inotify doesn't cross the podman
VM boundary, same as the uvicorn --reload note below). /api proxies to the
api service, so there is no CORS config anywhere. node_modules lives in a
named volume, NOT on the host — consequence: VS Code shows unresolved
imports in web/src (cosmetic, accepted in DECISION-1d; a host
`npm install` would fix it but requires installing Node).
package-lock.json was generated inside the container and is committed; CI
got a web job (npm ci, tsc, vitest). 9 component tests pin the result-card
contract: retraction banner (DECISION-1c), collapsed abstract, DOI link,
author truncation, year-bound payload.

Papers also gained an `authors TEXT[]` column with this task (migration
0004) — the result card needed it. Extraction at ingest from
authorships[].author.display_name; existing corpus backfilled from the raw
records for zero credits (26,167 of 26,237 covered; the 70 NULLs have no
author data at OpenAlex). Deliberately not in fts — author search is a
different feature, and stemming mangles names.

Shipped 2026-07-29 (task 5): `POST /api/search`, mode=bm25 only —
ts_rank_cd over the generated fts column (honestly NOT real BM25; see
api/search/bm25.py docstring), websearch_to_tsquery parsing, year_from/
year_to via the param-is-NULL pattern, k 1..100, per-result rank + score.
Structured JSON logging + request-ID middleware landed with it (the
deferred convention). Verified live: 58 ms top-3 on the dev corpus, all
on-topic. 68 tests green.

Corpus: ~26.4K papers ingested so far (Kishan's pull, 2026-07-29). Phase 1
acceptance wants 50K — top up with `--check-budget` then
`python -m api.ingest.openalex --limit 50000` (idempotent; reruns
converge and continue). Specialty queries exhaust below nominal budgets,
so most of the gap fills from nlp-concept.

DECISION-1c resolved (2026-07-29, docs/decisions.md): six junk types are
skipped at ingest (per-type skip counts in run stats); 141 existing
junk-type papers deleted, raw records kept (corpus 26,378 -> 26,237).
is_retracted papers stay, flagged and surfaced in search — the UI (next
task) should render a retraction warning from that field. Venue backfill
done (8,295 -> 471 nulls).

## Phase 3 dependency, undecided (noted 2026-07-31, do not decide now)

Which side wins a preprint/published merge — title, abstract, citation
count, venue, DOI — is still open. DECISION-3a's embedding policy (null
the vector wherever text is written) works either way, so the merge-side
question can be decided when the cascade is designed. Measured context
for that decision: 524 JMIR preprint/published pairs, published side
averages +34.6 citations, and ZERO of the pairs share an abstract.

## Phase 3 inputs from fusion (Kishan, 2026-07-31 — record, don't build)

1. **Dedup must run BEFORE fusion, not after.** The twin pair at hybrid
   ranks 9 and 10 ("Towards more patient friendly clinical notes...",
   b=43/v=10 vs b=44/v=12) had adjacent ranks in BOTH rankers, so
   duplicates will consume two slots in every hybrid top-10 — and RRF
   actively reinforces them, since a paper present twice gets two
   reciprocal-rank contributions.
2. **Hybrid is ~3x vector-only latency** (29.0 vs 9.9 ms warm, single
   observations). Fusion's cost belongs in the README next to its
   quality win.

## Phase 3 note (from the 2026-07-29 dup-abstract measurement)

Add exact-abstract-hash as a dedup cascade step BEFORE trigram: 560
duplicate-abstract groups / 1,273 papers measured, overwhelmingly
preprint/article twins with different titles that trigram will never
catch and DOI matching only partially covers. Cheap (md5 join), high
precision. Kishan approved the idea 2026-07-29 — do not build until
Phase 3.

Baseline to beat (findings.md, 2026-07-29): query "clinical text
simplification", 3 of the top 20 results are redundant copies (2 dup
groups, 5/20 rows involved). Two requirements the brief missed, from that
measurement: dedup must work WITHIN a source (three OpenAlex works for one
paper), and preprint/published merges need a version-preference rule (the
published row carries the real citation count), not just a merge.

Note: uvicorn --reload does NOT fire inside the podman VM (bind-mount
file events don't propagate) — `docker compose restart api` after editing
api/ locally.

OPENALEX_API_KEY is set in Kishan's .env (required since OpenAlex went
usage-based: $0.01/day anonymous, $1/day keyed). Every ingest run prints
budget up front and measured credits per query at the end.

DECISION-1b resolved (2026-07-29, docs/decisions.md): corpus crawls are
year-stratified — each query's budget splits evenly across the last 10
years plus a pre-2017 classics slice, citation-sorted within a slice.
--limit is a per-query budget (concept 40%, specialties 15% each,
largest-remainder rounding); per-query counts print at the end of each run.
Note: specialty queries will exhaust some year slices below budget at 50K —
the run prints those shortfalls; expected, not a bug.

Corpus domain (Kishan, 2026-07-28): natural language processing,
clinical/biomedical NLP, text simplification, and mental health NLP.
Assemble via OpenAlex concept filters plus keyword queries.

## Shipped (2026-07-29) — task 3, OpenAlex client

- api/ingest/ratelimit.py: per-source token bucket, injectable clock/sleep.
  Testing it caught a real spin: sleeping the exact token deficit can round
  below the clock's float resolution; acquire() now adds a 1µs margin.
- api/ingest/http.py: get_json, the single outbound path — bucket, explicit
  timeout, full-jitter retries on 429/5xx/transport errors, immediate raise
  on other 4xx.
- api/ingest/openalex.py: cursor pagination, `select`-trimmed raw records
  (~2 KB vs 10-20 KB per work), polite-pool User-Agent (Kishan's email),
  QUERIES table for the corpus domain, papers derived 1:1 with DOI-collision
  linking audited via merges. `--limit N` for smoke runs.
- Verified live twice with --limit 100: rerun gave new_papers=0,
  refreshed=99, row counts unchanged. Dev DB currently holds that 100-work
  smoke corpus.
- 39 tests green.

## Shipped (2026-07-28)

- DECISION-1 recorded: Python/FastAPI, single service (docs/decisions.md).
- Scaffold: FastAPI app, /healthz doing a real DB round-trip, lazy psycopg
  pool, pyproject carrying ruff/mypy/pytest config.
- Docker Compose: pgvector/pgvector:pg16 + one-shot migrate service + API,
  health-gated; boots from a clean clone with no .env (compose carries the
  dev defaults).
- Migration runner: numbered forward-only SQL, one transaction per run,
  pg_advisory_xact_lock (safe under Neon/Supabase transaction pooling),
  errors on empty migrations dir and unpadded filenames.
- 0002: papers, source_records, merges, ingest_jobs per brief Part 5.
  Deliberately NO HNSW index yet — it goes in Phase 2 after the bulk load
  so 500K inserts don't each pay graph maintenance; the migration carries a
  comment marking exactly where it goes.
- CI workflow: ruff, mypy (api + tests), migrate, pytest against the same
  pgvector image. (Remote since added and a2800f3 pushed — see the
  2026-07-29 verification entry above.)
- 11 tests green locally (runner semantics, schema behavior, health/pool
  lifecycle).

## Environment notes

- Docker Desktop on this Mac is the Intel build and refuses to start on the
  M1 ("This is the Intel version of Docker Desktop" error dialog). Current
  workaround: `podman machine start podman-machine-default` — it forwards a
  Docker-compatible socket and `docker compose` works unchanged. Durable
  fix: install the Apple Silicon build from
  <https://desktop.docker.com/mac/main/arm64/Docker.dmg>
- VS Code shows unresolved-import warnings until .venv/bin/python is
  selected as the interpreter.

## Deferred, deliberately

- Structured JSON logging + request IDs: lands with the first real endpoint
  (search), where there is something worth logging.
- pytest-asyncio: with the first async test.
- web/ scaffold: after the search endpoint exists to call.
