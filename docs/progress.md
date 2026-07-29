# Progress

Phase: 1 (search over one source).
BLOCKED on Kishan adding OPENALEX_API_KEY to .env (OpenAlex went
usage-based in 2026: $0.01/day anonymous, $1/day with a free key — see
brief Part 4). Then: `--check-budget` to confirm allowance, the full ~50K
pull (`python -m api.ingest.openalex --limit 50000`, ~300 requests, well
inside one day's keyed budget), then `POST /api/search` (mode=bm25), then
the plain React frontend. Note: today's anonymous budget is exhausted
until midnight UTC regardless.

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
