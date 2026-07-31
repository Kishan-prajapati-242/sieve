# Progress

Phase: 1 (search over one source). Corpus is at target; the Phase 1
acceptance check is the remaining gate item.

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
Era split 90.3% recent (2017+) / 9.7% classics — the specialty topics are
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
