# Sieve

Literature search and triage system. Hybrid keyword plus semantic retrieval over academic papers from OpenAlex, arXiv, and PubMed.

**The full build brief is at `docs/sieve-project-brief.md`. Read it at the start of every session. It is the spec. This file is the working agreement.**

Current phase and next task are tracked in `docs/progress.md`. Read that second.

---

## Who you are working with

Kishan, MS CS student. Strong on algorithms and Python. Has professional MERN experience, so JavaScript, npm, git, and Docker basics are known. New to information retrieval, pgvector, and backend systems patterns like job queues and idempotency. He is building this to defend it in engineering interviews, so **explanation matters as much as working code.**

---

## Hard rules

1. **No LLM chatbot. Ever.** No "chat with your papers," no RAG assistant, no summarization endpoint. The value of this project is the retrieval and ingestion infrastructure. If asked for one, push back and cite this line.
2. **Never write a number that was not measured.** Every figure that ends up in the README comes from a script in `bench/`. If a number is needed and no script exists, write the script first.
3. **Stop at every `DECISION` point in the brief and make Kishan choose.** Present the options, the tradeoff, and your recommendation, then wait. Do not pick silently. Once he decides, append it to `docs/decisions.md` using the template at the bottom of that file, in his words not yours.
4. **Raw SQL for every search, fusion, dedup, and queue query.** No ORM in those paths. He has to be able to read the query and its `EXPLAIN ANALYZE` output.
5. **Phase gates are real.** Do not start a phase until the previous phase's acceptance criteria in the brief actually pass. Say so plainly if they do not.
6. **Small commits, real messages.** Commit after each working subtask. Messages explain why, not what. Never batch a phase into one commit.
7. **Tests alongside code, not after.** Every new module gets tests in the same commit.
8. **Explain non-obvious code.** A short docstring at the top of each module: what it does, why it is structured this way, and what alternative was rejected. Skip this for boilerplate.

---

## Stack

```
Backend      Python 3.12, FastAPI, Uvicorn, Pydantic v2
DB driver    psycopg 3 with a connection pool. Raw SQL for search paths.
Database     PostgreSQL 16 + pg_trgm + pgvector
Embeddings   sentence-transformers/all-MiniLM-L6-v2 via ONNX Runtime, CPU, 384 dims
Vectors      halfvec(384), HNSW index
Queue        ingest_jobs table, SELECT ... FOR UPDATE SKIP LOCKED. No Redis, no Celery.
Frontend     React 18, Vite, TypeScript, Tailwind, TanStack Query
Tests        pytest, pytest-asyncio, httpx
Lint         ruff, mypy
Load test    k6
Local dev    docker compose (postgres, api, worker)
Deploy       Cloudflare Pages (frontend), Render free tier (API), Neon (subset DB)
```

Do not add a dependency without saying why in the commit message. Do not add Redis, Elasticsearch, Celery, or a vector database. All three are deliberately excluded, and the reasoning is part of the project.

---

## Layout

```
sieve/
├── CLAUDE.md
├── docs/
│   ├── sieve-project-brief.md    the spec, read first
│   ├── progress.md               current phase and next task
│   ├── decisions.md              DECISION records, Kishan's words
│   ├── findings.md               bug log: symptom, how found, cause, fix, measured
│   └── plans/                    EXPLAIN ANALYZE before/after, committed
├── api/
│   ├── main.py
│   ├── search/                   bm25.py, vector.py, fusion.py
│   ├── ingest/                   openalex.py, arxiv.py, pubmed.py, ratelimit.py
│   ├── dedup/                    cascade.py, normalize.py
│   ├── queue/                    claim.py, worker.py, backoff.py
│   ├── embed/                    onnx_encoder.py
│   └── db/                       pool.py, migrations/
├── bench/                        every number in the README comes from here
│   ├── latency.py
│   ├── hnsw_recall_sweep.py
│   ├── dedup_precision.py
│   ├── queue_throughput.py
│   ├── eval_ndcg.py
│   └── labels/                   hand-labeled relevance judgments and dup pairs
├── web/
└── tests/
```

---

## Conventions

- Timestamps are `TIMESTAMPTZ`, always UTC.
- Migrations are numbered SQL files, forward-only. No auto-generated migrations.
- Every external HTTP call: explicit timeout, retry with full jitter, and a per-source token bucket. No bare `requests.get`.
- Latency is reported as p50, p95, p99. Never a mean.
- Every diagnosed bug gets an entry in `docs/findings.md`: symptom, how it was found, root cause, fix, verified before/after.
- Log structured JSON. Include a request ID.
- Secrets in `.env`, never committed. `.env.example` stays current.
- The README is written by Kishan. Draft only the architecture diagram and the benchmark tables when asked; leave prose alone.

---

## Session protocol

Start of session: read `docs/sieve-project-brief.md`, then `docs/progress.md`, then state the phase and the next task before touching code.

End of session: update `docs/progress.md` with what shipped, what is next, and anything that is broken or half-done. Commit it.

If the session has been running long or context feels thin, say so and suggest committing and starting fresh rather than pushing through.

---

## When you disagree

If a request conflicts with these rules or with the brief, say so and explain why before doing it. Do not silently comply. Kishan needs to hear the objection, because the objection is usually the interview answer.
