# Progress

Phase: 1 (search over one source).
Next task: OpenAlex client — pagination, explicit timeouts, retry with full
jitter, per-source token bucket — then the synchronous ingestion script
pulling ~50K papers. After that: `POST /api/search` (mode=bm25), then the
plain React frontend.

Corpus domain (Kishan, 2026-07-28): natural language processing,
clinical/biomedical NLP, text simplification, and mental health NLP.
Assemble via OpenAlex concept filters plus keyword queries.

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
