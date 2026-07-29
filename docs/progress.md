# Progress

Phase: 1 (search over one source).
Next task: top up the corpus to 50K (needs Kishan's go-ahead:
`--check-budget` first, then `python -m api.ingest.openalex --limit 50000`,
~1,870 credits measured), then the Phase 1 acceptance check.

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
  pgvector image. Note: no GitHub remote is configured yet, so CI has never
  actually run — verify on first push.
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
