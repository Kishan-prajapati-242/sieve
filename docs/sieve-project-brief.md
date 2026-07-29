# Sieve: Literature Triage and Search System

**Complete build brief. Self-contained. An implementing agent should be able to work from this file alone.**

Repo name options: `sieve`, `trawl`, `winnow`, `sift`. Avoid `paper-search-app` and anything containing `ai-powered`.

---

## PART 0: BRIEF FOR THE IMPLEMENTING AGENT

If you are an AI agent building this, read this section first and follow it literally.

**Non-negotiable rules:**

1. **Do not add an LLM chatbot.** No "chat with your papers." No RAG assistant. The value of this project is the retrieval and ingestion infrastructure. An LLM wrapper makes it indistinguishable from thousands of other projects and destroys its interview value. If the user asks for one later, it goes in as a small clearly-secondary feature, never the headline.
2. **Build in the phase order given in Part 7.** Each phase has acceptance criteria. Do not start a phase until the previous one passes its criteria. The user needs a working, resume-describable system at the end of Phase 2, not at the end of Phase 4.
3. **Every number in the README must come from a script in the repo.** Write the benchmark scripts. Never write a number you did not measure.
4. **Stop and ask the user to make the call** at every point marked `DECISION`. These are the points the user gets interviewed on. Do not silently pick one. Record the answer in `docs/decisions.md` in this format: decision, alternatives considered, why rejected, what would change my mind.
5. **Explain as you go.** After each significant file, write a short comment block at the top explaining what it does and why it is structured that way. The user has to be able to defend this code in an interview.
6. **Commit incrementally with real messages.** Many small commits over multiple days, describing why, not what. A repo with three giant commits reads as generated.
7. **Do not use ORMs for the search queries.** Raw SQL for anything involving full-text search, vector search, or fusion. The user must be able to read and explain the actual query plan.
8. **Write tests as you go**, not at the end. Pytest. At minimum: ingestion idempotency, deduplication correctness, fusion ranking correctness, pagination correctness.

---

## PART 1: WHAT THIS IS

### The problem

Screening academic literature at volume is miserable. To write a review paper you search a term, get 800 hits across three different databases, most are irrelevant, many are the same paper listed three times with different IDs, keyword search misses papers that use different terminology for the same concept, and you have no way to track which of the 800 you already looked at and rejected.

Kishan screened 200+ papers by hand for a review paper published at CML 2025. This is a problem he has personally had, which is the most credible origin story a project can have.

### What the app does

A web application for searching and triaging academic literature.

1. **Search.** Enter a research question or keywords. Get a single ranked, deduplicated list of papers drawn from arXiv, OpenAlex, and PubMed. Filter by year, venue, citation count.
2. **Understand the ranking.** Each result shows *why* it ranked where it did: its keyword-match rank, its semantic-similarity rank, and the fused score. Toggle between keyword-only, semantic-only, and hybrid to see the difference live.
3. **Triage.** Create a collection for a research question. Mark each paper include, exclude, or maybe, with a note. Progress bar. Never see a paper you already judged.
4. **Export.** Download the included set as BibTeX.
5. **Inspect the system.** A stats page showing corpus size, per-source counts, how many duplicate records were merged, index sizes, ingestion queue depth, cache hit rate, and query latency percentiles.

That last feature is unusual and you should keep it. It turns the invisible engineering into something a recruiter can see in 10 seconds.

### Why this shape is resume-strong

Your instinct that a project is "a website with tools that solves a problem" is basically correct, and it is what makes projects like this work. But there is a specific failure mode, and it is where most student web projects die:

> **A CRUD app that wires together tutorials.** React frontend, Express backend, MongoDB, JWT auth, deployed on Vercel. Nothing in it was hard. The interviewer has no question to ask, so they ask a generic one and move on. It parses fine through an ATS and contributes nothing to the hiring decision.

The fix is not to invent an algorithm. The fix is that **one thing inside the app has to be genuinely hard, and you have to have measured it.** You do not need novelty. You need a real constraint that you hit and solved, with a before-and-after number.

This project has five of those, listed in Part 6. Each one is a real interview conversation:

- Keyword search misses papers using different terminology. Semantic search misses exact rare terms like gene symbols and model names. How do you combine two rankings with incomparable score scales?
- Three APIs return the same paper with different IDs, different title punctuation, and different author name formats. How do you decide two records are the same paper, and how do you know your merge rate is correct?
- External APIs rate-limit you and fail randomly. A job crashes halfway through. How do you not create duplicates on retry?
- Your search was 800 ms at 50,000 papers. Now you have 200,000. What is actually slow, and how did you find out?
- You claim hybrid search is better. Prove it with a number.

So: your framing is right, with one correction. **Tools get your resume parsed. Decisions get you hired.** Both matter and they are different sections of the same project.

---

## PART 2: WHY I REPLACED THE DISTRIBUTED KV STORE

Recorded here so the decision is on the record.

The Raft project is a better project in the abstract and a worse project for you right now, for four reasons:

1. **Raft is where experienced distributed systems engineers get things wrong.** The code is not the hard part. AI will produce plausible-looking Raft in an afternoon. Understanding why a leader cannot commit a previous-term entry on majority replication alone is the hard part, and it is the first question anyone who recognizes the project will ask.
2. **The learning is front-loaded and non-negotiable.** Roughly 25 hours of dense reading *before* the first useful line of code, and the concepts do not decompose into pieces you can learn independently. You cannot understand log compaction without understanding replication, which requires understanding elections and terms.
3. **Your August is already full.** ARR submission deadline, two TA appointments, CS 5800 with oral exams at 80 percent of the grade. You do not have 100 focused hours in the next four weeks.
4. **The failure mode is asymmetric.** A Raft plus DST bullet you cannot defend is *worse* than no bullet, because it advertises depth and invites the deepest available probing. An honest hybrid-search bullet you fully own is strictly better than a distributed systems bullet you half own.

Keep Raft on the list for winter break or spring, when you have a clear month. It is a genuinely great project. It is not a four-week project for someone who is hearing "consensus" for the first time.

This project instead has learning that **decomposes**. Every one of the five hard cores can be learned independently in a few hours, and you can ship a working system after learning only the first two.

---

## PART 3: STACK

Chosen for: zero cost, one language on the backend, no service you have to keep alive, and vocabulary that appears in real job descriptions.

```
Frontend      React 18 + Vite + TypeScript + Tailwind CSS
              TanStack Query for server state
              Deployed on Cloudflare Pages (free, unlimited bandwidth)

Backend       Python 3.12 + FastAPI + Uvicorn
              Pydantic for request/response models
              psycopg 3 with a connection pool. Raw SQL for search paths.
              Deployed on Render free tier (512 MB, cold starts acceptable)

Database      PostgreSQL 16 with two extensions:
                pg_trgm    trigram similarity, for deduplication
                vector     pgvector, for dense embeddings + HNSW index
              Full-text search via native tsvector + GIN index
              Local: Docker (no size limit, this is where benchmarks run)
              Demo:  Neon or Supabase free tier (0.5 GB, subset of corpus)

Embeddings    sentence-transformers/all-MiniLM-L6-v2 via ONNX Runtime
              384 dimensions, CPU only, runs on an M1 MacBook Air (8 GB)
              No training. No GPU. No API cost.

Queue         PostgreSQL table + SELECT ... FOR UPDATE SKIP LOCKED
              No Redis, no SQS, no Celery. See DECISION-3.

Cache         In-process LRU (cachetools) first, measured.
              Add Upstash Redis free tier only if the measurement justifies it.

Testing       pytest, pytest-asyncio, httpx test client
Load testing  k6 (single binary, free) or Locust
CI            GitHub Actions: ruff, mypy, pytest, and a smoke load test
Metrics       prometheus-client exposing /metrics, plus a /api/stats endpoint
Container     Docker + docker compose for local dev (Postgres + API + worker)
```

**Why Python and not Node, given the MERN background:** the embedding pipeline is Python-native, and splitting into a Node API plus a Python embedding service doubles the operational surface for no benefit. Also, broadening past MERN is worth something on its own. FastAPI is a completely standard backend choice and reads well.

`DECISION-1` (ask the user): Python/FastAPI or Node/Express with a Python sidecar for embeddings? Record the reasoning.

---

## PART 4: DATA SOURCES

Free tiers, real limits. Respect them — your ingestion pipeline exists partly to demonstrate that you handle them correctly.

| Source | Access | Limit | Notes |
| --- | --- | --- | --- |
| **OpenAlex** | REST, **api_key required** | **metered: $1/day (10,000 credits) free with a key; $0.01/day anonymous** | Primary source. Best coverage and cleanest metadata. Bills per request by class: concept/list filters 1 credit, `.search:` filters 10 credits, page size irrelevant (measured 2026-07-29). Budget resets midnight UTC. See developers.openalex.org. |
| **arXiv** | REST (Atom), no key | roughly 1 request per 3 seconds | Preprints. Slow, so batch and cache aggressively. |
| **PubMed E-utilities** | REST | 3 req/s without a key | Biomedical. Relevant to your clinical NLP interest and gives the dedup problem real teeth. |
| **Crossref** | REST, no key | polite pool | Optional, good for DOI resolution and filling gaps. |

**OpenAlex went usage-based (2026), and it changes the operational math.**
The API bills per request against a daily budget: $0.01/day anonymous,
$1/day = 10,000 credits with a free api_key (developers.openalex.org).
Measured 2026-07-29 by bracketing single requests with free `/rate-limit`
reads: a concept-filter page bills 1 credit (`list` class), a
`title_and_abstract.search:` page bills 10 credits (`search` class), and
page size does not change the price — so 200-work pages are the only
sensible unit, and search-heavy crawling is the cost driver.

Also measured: a bare `search=` param (entity search on /topics, full-text
search on /works) bills 10 credits too, not 1 — the credit meter missed
this at first and under-reported a discovery run 10x (docs/findings.md).

Corpus cost, ACTUAL, from the 200K pull on 2026-07-29 (DECISION-2 topic
composition, 11 year slices, 200-work pages): **1,435 credits for 196,031
works across 1,040 requests — 14% of one keyed day**, reconciling exactly
with the server's `credits_remaining` delta. The five topic queries did the
work at 995 credits for 192,441 works (**193 works per credit**); the four
retained phrase queries cost 440 credits for 3,590 works (**8 works per
credit**, 24x worse) because `.search:` filters bill 10x and exhaust year
slices below budget, paying full page price for partial pages. That ratio
is why DECISION-2 moved the bulk of the corpus onto topic filters. Earlier
estimate for the old concept+phrase table was ~6,600 credits for the same
200K, i.e. the migration cut corpus cost ~4.6x.

WITHOUT the key the same pull dies after ~100 requests with 429s that look
like a rate bug and are actually billing. For exactly that reason the
ingest script refuses to start without `OPENALEX_API_KEY`; every run prints
its budget up front and its measured credits per query at the end, and
`--check-budget` shows the remaining budget and reset time before you
commit to a pull.

**Metadata only. Do not download PDFs.** You need: title, abstract, authors, year, venue, DOI, arXiv ID, PubMed ID, citation count, concepts/keywords, source URL. That is a few kilobytes per paper.

**Corpus target:** 200,000 papers in your own domain (NLP, clinical NLP, machine learning, information retrieval). Minimum acceptable: 100,000 — below that the performance work has nothing to bite on.

**Hardware constraint, on the record:** the development machine is an M1 MacBook Air with 8 GB of RAM, and every benchmark runs on it. 200K is chosen so the hot working set — papers table, GIN index, HNSW graph — stays comfortably in memory alongside Postgres and the embedding process, which a 500K corpus would not. The README must name this hardware next to every number it reports; a measured claim without its machine is not a measurement.

**Storage math, so you do not get surprised:**
- Text and metadata: roughly 2 KB per paper, so 200K papers is about 400 MB.
- Embeddings at 384 dimensions as `float32`: 1,536 bytes per paper, so 200K is about 300 MB.
- Embeddings as `halfvec` (float16): 768 bytes per paper, so 200K is about 150 MB. Use `halfvec`. Measure the recall difference against `vector` and report it. That is a free bullet.
- HNSW index: budget roughly the same again as the vectors.

Free managed Postgres gives you 0.5 GB. Your full corpus will not fit. **This is fine and you should handle it explicitly:**

- **Full corpus runs locally** in Docker Postgres on your M1. No size limit. **All benchmark numbers come from here**, with the hardware documented in the README.
- **A 40,000 to 50,000 paper subset deploys** to Neon or Supabase free tier for the live public demo.
- The README says exactly this, in one sentence. Being explicit about your measurement environment reads as rigor. Hiding it reads as inexperience.

**Embedding time:** roughly 50 to 150 abstracts per second on M1 CPU with batching, so 200K papers takes roughly 25 to 70 minutes. Make it resumable so a crash does not restart from zero (this is one of the reasons you build the queue).

---

## PART 5: DATA MODEL

Sketch. The implementing agent should produce proper migrations (use `alembic` or plain numbered SQL files).

```sql
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS vector;

-- Raw records exactly as fetched, one row per source per paper.
-- Never mutated after insert. This is your audit trail.
CREATE TABLE source_records (
    id              BIGSERIAL PRIMARY KEY,
    source          TEXT NOT NULL,              -- 'openalex' | 'arxiv' | 'pubmed'
    source_id       TEXT NOT NULL,              -- native ID within that source
    raw             JSONB NOT NULL,             -- untouched API response
    fetched_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    paper_id        BIGINT REFERENCES papers(id),  -- set by the dedup pass
    UNIQUE (source, source_id)                  -- idempotency key
);

-- Canonical merged papers. One row per real-world paper.
CREATE TABLE papers (
    id              BIGSERIAL PRIMARY KEY,
    doi             TEXT UNIQUE,                -- normalized, lowercase, no prefix
    title           TEXT NOT NULL,
    title_norm      TEXT NOT NULL,              -- lowercased, punctuation stripped
    abstract        TEXT,
    year            SMALLINT,
    venue           TEXT,
    citation_count  INTEGER DEFAULT 0,
    arxiv_id        TEXT,
    pubmed_id       TEXT,
    fts             tsvector
        GENERATED ALWAYS AS (
            setweight(to_tsvector('english', coalesce(title, '')),    'A') ||
            setweight(to_tsvector('english', coalesce(abstract, '')), 'B')
        ) STORED,
    embedding       halfvec(384),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX papers_fts_idx        ON papers USING GIN (fts);
CREATE INDEX papers_title_trgm_idx ON papers USING GIN (title_norm gin_trgm_ops);
CREATE INDEX papers_embed_idx      ON papers USING hnsw (embedding halfvec_cosine_ops)
                                      WITH (m = 16, ef_construction = 64);
CREATE INDEX papers_year_idx       ON papers (year);

-- Audit trail of every merge decision. Lets you compute a false-merge rate.
CREATE TABLE merges (
    id            BIGSERIAL PRIMARY KEY,
    kept_paper_id BIGINT NOT NULL REFERENCES papers(id),
    merged_from   JSONB NOT NULL,   -- the source_record ids and their titles
    strategy      TEXT NOT NULL,    -- 'doi_exact' | 'arxiv_id' | 'title_trgm' | 'manual'
    similarity    REAL,             -- score that triggered the merge, if fuzzy
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE authors (
    id        BIGSERIAL PRIMARY KEY,
    name_norm TEXT NOT NULL UNIQUE,
    orcid     TEXT
);

CREATE TABLE paper_authors (
    paper_id  BIGINT REFERENCES papers(id),
    author_id BIGINT REFERENCES authors(id),
    position  SMALLINT,
    PRIMARY KEY (paper_id, author_id)
);

-- The job queue. This IS your queue. No Redis.
CREATE TABLE ingest_jobs (
    id            BIGSERIAL PRIMARY KEY,
    job_type      TEXT NOT NULL,        -- 'fetch_page' | 'embed_batch' | 'dedup_batch'
    payload       JSONB NOT NULL,
    dedupe_key    TEXT UNIQUE,          -- prevents enqueuing the same work twice
    status        TEXT NOT NULL DEFAULT 'pending',
                  -- 'pending' | 'running' | 'done' | 'failed' | 'dead'
    attempts      SMALLINT NOT NULL DEFAULT 0,
    max_attempts  SMALLINT NOT NULL DEFAULT 5,
    run_after     TIMESTAMPTZ NOT NULL DEFAULT now(),   -- for backoff
    locked_at     TIMESTAMPTZ,
    locked_by     TEXT,
    last_error    TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX ingest_jobs_claim_idx ON ingest_jobs (status, run_after)
    WHERE status = 'pending';

CREATE TABLE collections (
    id          BIGSERIAL PRIMARY KEY,
    name        TEXT NOT NULL,
    question    TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE screenings (
    collection_id BIGINT REFERENCES collections(id),
    paper_id      BIGINT REFERENCES papers(id),
    decision      TEXT NOT NULL,   -- 'include' | 'exclude' | 'maybe'
    note          TEXT,
    decided_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (collection_id, paper_id)
);

-- Every query, for the stats page and for building an eval set.
CREATE TABLE query_log (
    id          BIGSERIAL PRIMARY KEY,
    query       TEXT NOT NULL,
    mode        TEXT NOT NULL,        -- 'bm25' | 'vector' | 'hybrid'
    result_ids  BIGINT[],
    latency_ms  REAL,
    cache_hit   BOOLEAN,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

Note the `source_records` / `papers` / `merges` split. Raw records are immutable, canonical papers are derived, and every merge is logged. This is a standard data engineering pattern and it is what lets you report a dedup accuracy number instead of guessing.

---

## PART 6: THE FIVE HARD CORES

This is the actual content of the project. Each has an honest learning time estimate. Total learning: roughly 26 hours, spread over four weeks, decomposable.

### Core 1: Hybrid retrieval with rank fusion (learn: 6 hours)

**The problem.** Two retrieval methods with different failure modes.

- **Keyword search (BM25)** nails exact rare terms. Search "BioBART" and you get papers about BioBART. It completely misses a paper that says "clinical language simplification" when you searched "medical text simplification."
- **Dense vector search** catches paraphrase and synonymy. It also confidently returns semantically adjacent garbage, and it is bad at rare exact tokens, gene symbols, model names, and numbers, because those get smeared into the embedding.

You need both. The obstacle: **their scores are not comparable.** BM25 returns an unbounded positive score whose scale depends on the corpus and the query length. Cosine similarity returns a number in a fixed range with a completely different distribution. Adding them, or min-max normalizing and adding them, gives a fusion whose behavior changes per query.

**The solution: Reciprocal Rank Fusion.** Throw away the scores, keep only the ranks.

```
RRF_score(d) = sum over each ranking r of:  1 / (k + rank_r(d))
```

with `k` typically 60. A document at rank 1 in either list gets a large contribution. A document at rank 200 in both gets almost nothing. It is scale-free, requires no tuning per query, and it is what production hybrid search systems actually use.

**Implementation in one Postgres query.** Two CTEs, each returning `(paper_id, rank)` via `ROW_NUMBER()`, then a full outer join and a sum of reciprocals. Write this by hand. Read the `EXPLAIN ANALYZE`. This query is the heart of the project and you will be asked to explain it.

`DECISION-2`: RRF versus normalized score fusion versus a learned linear combination. Pick, justify, and note what would change your mind (a labeled training set of reasonable size would justify learning the weights).

**BM25 in Postgres:** native `ts_rank_cd` is not exactly BM25. Two options: use `ts_rank_cd` and be accurate about what it is, or compute BM25 yourself from term frequencies. Doing the second is more impressive and takes a few extra hours. Either is defensible. Do not call `ts_rank_cd` "BM25" in your README if you did not implement BM25.

**Learn:** the BM25 formula and specifically why the `k1` term saturates term frequency (a document with 50 occurrences of a word is not 50 times more relevant than one with 1). Postgres `tsvector`, `to_tsquery`, GIN indexes, `setweight`. Cosine similarity. The RRF paper (Cormack et al., 2009, four pages).

### Core 2: Deduplication and entity resolution (learn: 5 hours)

**The problem.** The same paper arrives from three APIs:

```
arXiv:     "Attention Is All You Need"                     arXiv:1706.03762   no DOI
OpenAlex:  "Attention is all you need"                     doi:10.5555/3295222
PubMed:    "Attention is all you need."                    PMID 12345678
```

Different IDs, different capitalization, a trailing period, sometimes a different subtitle, sometimes an author list formatted as "Vaswani A" versus "Ashish Vaswani." If you do not merge these, every search returns triplicates and the product is unusable.

**Solution: a cascade, cheap and certain first, expensive and fuzzy last.**

1. **Normalized DOI exact match.** Lowercase, strip `https://doi.org/` and `doi:`. This catches most of it and is certain.
2. **arXiv ID or PubMed ID exact match.**
3. **Normalized title exact match plus same year.** Lowercase, strip punctuation, collapse whitespace, strip a leading "the."
4. **Fuzzy title match** via `pg_trgm` similarity above a threshold, gated on same year and at least one shared author surname. Postgres `similarity(a, b) > 0.92` with a GIN trigram index.
5. Anything ambiguous stays unmerged. **Under-merging is much safer than over-merging.** Merging two different papers is a visible product bug; missing a merge just leaves a near-duplicate in the list.

**Every merge writes a `merges` row** recording which strategy fired and the similarity score.

**Then measure it.** Sample 200 candidate pairs stratified across similarity bands, label them by hand (one evening, roughly 90 minutes), and compute precision and recall of your merge decision. Now you can write "dedup precision 0.98 at recall 0.91 on a 200-pair hand-labeled sample" instead of "implemented deduplication." That sentence is worth more than the rest of the bullet.

`DECISION-4`: what similarity threshold, and why that one? Show the precision/recall curve across thresholds and explain why you chose the operating point you chose. Interviewers love this because it is a real engineering tradeoff with no textbook answer.

**Learn:** trigram similarity and how `pg_trgm` indexes it, string normalization, the precision/recall tradeoff, why you stratify a sample instead of taking 200 random pairs (almost all random pairs are trivially non-duplicates and teach you nothing).

### Core 3: Ingestion pipeline that survives reality (learn: 6 hours)

**The problem.** You need to pull 200,000 records from APIs that rate-limit you, return 500s, time out, and occasionally return malformed JSON. The job takes hours (arXiv's 1-request-per-3-seconds is the binding constraint). Your laptop will sleep. The process will crash. Restarting from zero is not acceptable, and neither is creating duplicate rows on retry.

**Solution.**

**Postgres as the queue.** The claim query:

```sql
UPDATE ingest_jobs SET
    status    = 'running',
    locked_at = now(),
    locked_by = %(worker_id)s,
    attempts  = attempts + 1
WHERE id IN (
    SELECT id FROM ingest_jobs
    WHERE status = 'pending' AND run_after <= now()
    ORDER BY id
    LIMIT %(batch)s
    FOR UPDATE SKIP LOCKED          -- the important part
)
RETURNING *;
```

`FOR UPDATE SKIP LOCKED` lets N workers claim disjoint batches concurrently without blocking each other and without a distributed lock. Understand precisely what it does and why the naive version (`SELECT` then `UPDATE`) either serializes your workers or hands the same job to two of them.

`DECISION-3`: Postgres SKIP LOCKED versus Redis versus SQS versus Celery. The honest answer at your scale is that a second datastore costs more in operational complexity than it buys, and you should have a measured throughput number to back it (jobs per second the Postgres queue sustains). This is one of the strongest interview answers in the whole project, because most candidates reach for Redis reflexively and cannot defend it.

**Idempotency.** Two layers:
- `ingest_jobs.dedupe_key UNIQUE` so enqueuing the same work twice is a no-op.
- `source_records (source, source_id) UNIQUE` with `INSERT ... ON CONFLICT DO UPDATE`, so processing the same job twice converges to the same state instead of duplicating rows.

Understand the distinction: you cannot prevent a job from running twice (the worker can die after doing the work but before marking it done), so you make running twice harmless. That is the whole idea, and it is a top-five backend interview question.

**Retries with exponential backoff and jitter.**

```python
delay = min(base * (2**attempts), cap)
delay = delay * (0.5 + random.random())  # full jitter
run_after = now() + delay
```

Know why the jitter is there: without it, every job that failed during the same outage retries at the same instant and re-creates the thundering herd that caused the outage.

**Dead letter handling.** After `max_attempts`, set `status = 'dead'` and keep `last_error`. Surface the dead count on the stats page. A pipeline with no visible failure state is a pipeline whose failures you never noticed.

**Rate limiting per source.** A token bucket per source, respecting the documented limit. arXiv at 1 request per 3 seconds is the binding constraint, so it needs its own bucket.

**Learn:** `FOR UPDATE SKIP LOCKED`, `ON CONFLICT`, at-least-once versus exactly-once, idempotency keys, exponential backoff with full jitter, token bucket, dead letter queues, `httpx` with connection pooling and timeouts.

### Core 4: Query latency engineering (learn: 5 hours)

**The problem.** Search is fast at 50,000 papers and slow at 200,000. You need to know why, not guess.

**Do it in this order. The order is the point.**

1. **Measure first.** Instrument search with a histogram. Report p50, p95, p99. Never report an average; one 4-second query hides behind 99 fast ones in a mean.
2. **Read the query plan.** `EXPLAIN (ANALYZE, BUFFERS)` on the hybrid query. Find where the time actually goes. It will surprise you at least once. Save the before-and-after plans in the repo.
3. **Fix what the plan tells you**, which will typically be some of:
   - **HNSW parameters.** `m` and `ef_construction` at build time, `hnsw.ef_search` at query time. `ef_search` is the direct recall-versus-latency dial. Plot the curve: measure recall against exact brute-force search at several `ef_search` values, and pick an operating point. That plot is a resume bullet by itself.
   - **`halfvec` instead of `vector`.** Half the memory, so more of the index stays in cache. Measure the recall cost. Usually near zero.
   - **Keyset pagination instead of `OFFSET`.** `OFFSET 10000` makes Postgres generate and discard 10,000 rows. Use `WHERE (score, id) < (:last_score, :last_id) ORDER BY score DESC, id DESC LIMIT 20`. Know why this is O(1) in page depth while `OFFSET` is O(n).
   - **Over-fetch then fuse.** Pull top 200 from each ranker, fuse, return top 20. Test whether 100 is enough; each candidate costs latency.
   - **Caching.** In-process LRU keyed on `(normalized_query, filters, mode)`. Measure the hit rate on your real query log. Only add Redis if you have a measurement showing you need it, and be ready to say what that measurement was.
   - **Trigram index on `title_norm`** so dedup lookups do not sequentially scan.
4. **Measure again.** The before-and-after table is the single most valuable artifact in the README.
5. **Load test.** k6 with a ramping arrival rate against your real query log. Find the RPS at which p99 crosses 200 ms. Report the concurrency ceiling and what breaks first (usually connection pool exhaustion, not CPU, which is itself a good finding).

**Learn:** `EXPLAIN ANALYZE` output, GIN versus HNSW index behavior, HNSW parameters, keyset versus offset pagination, percentiles versus averages, connection pooling, k6 basics.

### Core 5: Actually evaluating it (learn: 4 hours)

Almost no student project has an evaluation. This one will, and it is the piece that most cleanly separates you from a tutorial-follower. It is also your existing research skill applied where it is rare.

**Method.**

1. Write 5 to 8 realistic research questions in your domain. Use real ones, including questions from your own review paper work.
2. For each, run the union of BM25 top-30 and vector top-30. Pool and shuffle so you cannot tell which system produced which result.
3. Label each pooled result 0 (irrelevant), 1 (marginal), or 2 (relevant). Roughly 250 to 400 labels. Two to three hours total. Do it in one sitting for consistency.
4. Write the labels to a file in the repo.
5. Compute **nDCG@10** and **Recall@20** for three systems: BM25 only, vector only, hybrid RRF.
6. Put the table in the README.

**Report it honestly, including where hybrid loses.** It will lose on at least one query, almost certainly one dominated by a rare exact term where the dense ranker drags a good BM25 result down. Explaining that specific failure is a better interview answer than a clean win, and interviewers can tell the difference between someone who ran an evaluation and someone who wrote the number they wanted.

**Learn:** nDCG (what the discount is for and why you normalize by the ideal ranking), Recall@k, pooled relevance judgments, why graded relevance beats binary, and why you shuffle before labeling. Also learn what your evaluation does not support: 8 queries is a small sample and you should say so rather than have it pointed out to you.

---

## PART 7: PHASE PLAN

Four weeks. Each phase has acceptance criteria. Phase 2 is the resume checkpoint.

### Phase 1: Search over one source (Days 1 to 7)

Build:
- Docker Compose: Postgres 16 with `pg_trgm` and `vector`, plus the API container.
- Migrations for `source_records`, `papers`, `merges`, `ingest_jobs`.
- OpenAlex client with pagination, timeouts, and a per-source token bucket.
- A synchronous ingestion script (no queue yet) that pulls 50,000 papers in your domain.
- `POST /api/search` with `mode=bm25` only, using `tsvector` and `ts_rank_cd`.
- React frontend: a search box, a result list, year filter. Deliberately plain. Design later.
- pytest: ingestion idempotency (run the same fetch twice, row count unchanged), search returns results, filters apply.

Acceptance: `docker compose up` gives a working keyword search over 50,000 real papers. Reruns of ingestion create no duplicates. Tests pass in CI.

### Phase 2: Hybrid search (Days 8 to 14) <- RESUME CHECKPOINT

Build:
- Embedding pipeline: MiniLM via ONNX Runtime, batched, writing `halfvec(384)`. Resumable.
- HNSW index on `embedding`.
- `mode=vector` and `mode=hybrid`. The RRF query in raw SQL.
- Scale the corpus to 100,000, then 200,000 papers.
- Frontend: a mode toggle, and per-result score breakdown showing keyword rank, semantic rank, and fused score.
- Latency instrumentation from the start. Record p50/p95/p99 per mode.
- Tests: RRF ranking correctness on a fixture, mode switching, embedding idempotency.

Acceptance: all three modes work over 200,000 papers. You can demonstrate a query where BM25 wins, one where vector wins, and one where hybrid beats both. You have latency percentiles for each mode. **Update the resume and start applying.**

### Phase 3: Multi-source, dedup, and the queue (Days 15 to 21)

Build:
- arXiv and PubMed clients.
- The dedup cascade, writing `merges` rows.
- The 200-pair hand-labeled sample and the precision/recall measurement, including the threshold sweep.
- Convert ingestion to the queue: `ingest_jobs` with SKIP LOCKED claiming, backoff with jitter, dead lettering, N concurrent workers.
- Collections, screening decisions, BibTeX export.
- `GET /api/stats`: corpus size, per-source counts, merges by strategy, queue depth, dead jobs, cache hit rate, latency percentiles.
- Tests: each dedup strategy, concurrent workers claim disjoint jobs, a job that fails 5 times lands in `dead`, BibTeX output is valid.

Acceptance: three sources ingested and merged with a measured precision/recall number. Kill a worker mid-run and restart it; no duplicates, no lost jobs. Screening workflow usable end to end.

### Phase 4: Performance, evaluation, deploy (Days 22 to 28)

Build:
- The full latency engineering pass from Core 4, with before-and-after `EXPLAIN ANALYZE` plans committed to the repo.
- The `ef_search` recall-versus-latency curve, as a plot.
- Keyset pagination.
- Caching with a measured hit rate.
- k6 load test and the RPS ceiling at p99 200 ms.
- The evaluation from Core 5, table in the README.
- Deploy: Cloudflare Pages frontend, Render API, Neon/Supabase with the 40K subset.
- Prometheus `/metrics`, and a stats page in the UI.
- Now do the visual design pass on the frontend. It matters for the demo and it is the cheapest thing to improve last.
- README: what it does, architecture diagram, benchmarks table, evaluation table, dedup accuracy, the hardware you measured on, and what you would do differently.

Acceptance: live demo URL. Every number in the README traceable to a script. `docs/decisions.md` has 8 or more entries. You can pass the Part 10 cold-recall test.

### Phase 5 (optional, weeks 5 to 10, interview depth not resume space)

- Cross-encoder reranking of the top 50 on CPU (`ms-marco-MiniLM-L-6-v2`), with a measured nDCG gain and latency cost. The precision/latency tradeoff conversation is excellent.
- Citation graph features: rank boosting by citation count and co-citation.
- Query expansion or spelling correction using corpus statistics.
- Server-sent events so results stream in as each ranker finishes.
- Incremental daily ingestion on a GitHub Actions cron, so the corpus stays fresh.

---

## PART 8: WHAT TO MEASURE

Every one of these is a script in `bench/` that you can run in front of an interviewer.

| Metric | Target shape |
| --- | --- |
| Corpus size, per source, after merge | 200K papers, 3 sources |
| Ingestion throughput | papers/min, per source, limits respected |
| Merge rate | % of source records merged into an existing paper |
| Dedup precision and recall | on 200 hand-labeled pairs, with the threshold sweep |
| Embedding throughput | abstracts/sec on M1 CPU, total wall clock |
| Search latency p50/p95/p99 | per mode, before and after optimization |
| HNSW recall vs `ef_search` | curve, against exact brute force |
| `halfvec` vs `vector` | recall delta and memory delta |
| Cache hit rate | on the real query log |
| Load test ceiling | RPS at which p99 crosses 200 ms, plus what breaks first |
| nDCG@10 and Recall@20 | BM25 / vector / hybrid, on 5 to 8 labeled queries |
| Index sizes | GIN, HNSW, trigram, and the table itself |
| Queue throughput | jobs/sec with N workers, showing SKIP LOCKED scaling |

---

## PART 9: RESUME ENTRIES

Numbers below are shape examples. **Replace every one with a value you measured.** A fabricated number is the specific thing that collapses in an interview, because the follow-up is always "how did you measure that."

### Week 2 version (two bullets, apply with this)

> **Sieve: Hybrid Search over 200K Academic Papers** | Python, FastAPI, PostgreSQL, pgvector, React | github.com/...
> - Built a search system over 205K paper records ingested from OpenAlex, combining PostgreSQL full-text retrieval with pgvector HNSW dense retrieval over 384-dimensional CPU-generated embeddings, fused by Reciprocal Rank Fusion in a single SQL query.
> - Instrumented per-mode latency histograms and exposed a per-result score breakdown (keyword rank, semantic rank, fused score); p99 search latency 118 ms across 205K papers on an 8 GB M1 MacBook Air.

### Week 4 version (three bullets, final)

> **Sieve: Literature Search and Triage System** | Python, FastAPI, PostgreSQL, pgvector, React, Docker | github.com/...
> - Built a hybrid retrieval system over 205K papers from three sources, fusing PostgreSQL full-text and pgvector HNSW rankings via Reciprocal Rank Fusion; improved nDCG@10 by 7.2 points over keyword-only retrieval on 8 hand-labeled queries.
> - Designed an idempotent ingestion pipeline on a Postgres `SKIP LOCKED` queue with exponential backoff, jitter, per-source rate limiting, and dead-letter handling, sustaining 340 jobs/sec across 8 workers; deduplicated cross-source records via a DOI-to-trigram cascade at 0.98 precision and 0.91 recall on a 200-pair labeled sample.
> - Cut p99 search latency from 840 ms to 96 ms through HNSW `ef_search` tuning against a measured recall curve, `halfvec` quantization, keyset pagination, and query-level caching; load tested to 210 RPS before p99 exceeded 200 ms.

### Reading of that entry

Bullet 1 is the retrieval work plus an honest evaluation. Bullet 2 is backend systems: queues, idempotency, rate limiting, failure handling, entity resolution, all with numbers. Bullet 3 is performance engineering with a before and after. That is three distinct competencies, each measured, none of which requires you to have invented anything.

---

## PART 10: TOOLS AND TERMS YOU WILL LEGITIMATELY OWN

Directly answering the "which and what tools" question. Everything here is something you will actually have used, and most of it appears verbatim in backend and ML-infrastructure job descriptions.

**Languages and frameworks:** Python, FastAPI, TypeScript, React, SQL, Bash.

**Data:** PostgreSQL, `tsvector` full-text search, GIN indexes, pgvector, HNSW indexes, `pg_trgm`, JSONB, database migrations, connection pooling, query plan analysis, keyset pagination.

**Backend systems:** REST API design, async I/O, job queues, `FOR UPDATE SKIP LOCKED`, idempotency, at-least-once delivery, exponential backoff with jitter, rate limiting, token bucket, dead letter queues, caching, LRU eviction, pagination, worker concurrency.

**Search and ML:** information retrieval, BM25, term frequency saturation, dense retrieval, embeddings, sentence-transformers, ONNX Runtime, approximate nearest neighbor search, HNSW, vector quantization, rank fusion, Reciprocal Rank Fusion, cross-encoder reranking (Phase 5), nDCG, Recall@k, pooled relevance judgments.

**Data engineering:** ingestion pipelines, API integration, entity resolution, deduplication, precision/recall evaluation, immutable raw records with derived canonical tables, audit trails.

**Practice:** Docker, Docker Compose, GitHub Actions CI, pytest, ruff, mypy, load testing with k6, Prometheus metrics, `EXPLAIN ANALYZE` profiling, structured logging, design decision records.

That is a dense, honest keyword surface. Put the true subset in your Skills section and let the rest appear naturally inside the bullets.

---

## PART 11: COLD-RECALL TEST

Answer out loud, no notes, under two minutes each. Record yourself. Every question here is one an interviewer can actually ask about this resume entry. Failing one means you do not yet own that component; go read, then retry.

**Retrieval**
1. What is BM25 doing that TF-IDF is not? Why does term frequency saturation matter?
2. Give a query where keyword search beats dense retrieval, and explain the mechanism.
3. Give a query where dense beats keyword, and explain the mechanism.
4. Why can you not just add a BM25 score to a cosine similarity?
5. Why does RRF work without any score calibration? What is the `k` parameter doing?
6. Walk through your fusion SQL query. What does the plan look like?
7. Why do you fetch 200 candidates from each ranker instead of 20?

**Vector search**
8. What is HNSW actually doing at search time? Why is it approximate?
9. What do `m`, `ef_construction`, and `ef_search` each control?
10. How did you measure recall, and what did you measure it against?
11. What did `halfvec` cost you in recall, and why is it nearly free?

**Data**
12. Two records, same title, different DOI. Same paper or not? How do you decide?
13. Why is under-merging safer than over-merging here?
14. How did you pick your similarity threshold? Show me the curve.
15. Why did you stratify your 200-pair sample instead of sampling randomly?

**Backend systems**
16. What does `FOR UPDATE SKIP LOCKED` do? What happens without it?
17. Why Postgres as a queue instead of Redis or SQS? What number backs that?
18. Your worker dies after doing the work but before marking the job done. What happens next, and why is that safe?
19. Why is jitter in the backoff necessary? What goes wrong without it?
20. What is the difference between at-least-once delivery and exactly-once processing?

**Performance**
21. What was slow, and how did you find out? Not what you fixed. How you found it.
22. Why `OFFSET` pagination is a problem, and what you replaced it with.
23. Why p99 and not the mean?
24. What broke first under load, and why was it that and not CPU?

**Judgment**
25. Where does your evaluation fail to support your claim?
26. On which query does hybrid lose to BM25 alone, and why?
27. What would you do differently if you rebuilt this?
28. How would you scale this to 50 million papers? What breaks first?

---

## PART 12: ANTI-PATTERNS

Things that will make this read as a tutorial project. Avoid all of them.

- **An LLM chatbot on top.** Kills the project. The infrastructure is the point.
- **The words "AI-powered" anywhere.** In 2026 this signals nothing and reads as filler.
- **No evaluation.** Without the nDCG table this is just another search box.
- **One data source.** Kills the deduplication story, which is a third of the engineering.
- **Numbers with no benchmark script.** The first follow-up question destroys you.
- **A generated README.** Write it yourself. It is the first thing anyone reads and voice is obvious.
- **Three commits.** Commit daily, with messages explaining why.
- **Averages instead of percentiles.** Signals you have never operated anything.
- **A framework doing your search queries.** If an ORM generated your fusion query you cannot explain it.
- **Skipping `docs/decisions.md`.** It is the difference between having built something and having assembled something.

---

## PART 13: LEARNING RESOURCES

By topic, in the order you need them. Roughly 26 hours total, spread over four weeks. None of this requires prior background beyond SQL and Python.

**Postgres full-text search (3h).** The official Postgres chapter on text search. Read `tsvector`, `to_tsquery`, `setweight`, `ts_rank_cd`, and GIN indexes. Then run `EXPLAIN ANALYZE` on your own queries until the output stops being opaque.

**BM25 and IR basics (3h).** Manning, Raghavan, Schütze, *Introduction to Information Retrieval*, chapters 6 and 11, freely available online. Read only the ranked retrieval and probabilistic retrieval sections. Focus on why `k1` saturates term frequency and what `b` does for document length normalization.

**Embeddings and dense retrieval (3h).** The sentence-transformers documentation. Then the ONNX Runtime Python quickstart. You are not training anything, only running a forward pass, so skip everything about training objectives.

**pgvector and HNSW (3h).** The pgvector README, which is short and good. Then the HNSW paper (Malkov and Yashunin), abstract plus section 3 only, for the layered-graph intuition. Then run your own recall-versus-`ef_search` sweep, which teaches more than the paper.

**Rank fusion (30m).** Cormack, Clarke, Buettcher, "Reciprocal Rank Fusion Outperforms Condorcet and Individual Rank Learning Methods." Two pages of content.

**Postgres as a queue (2h).** Search for the `SKIP LOCKED` job queue pattern; there are several good writeups. Then read the Postgres documentation on `FOR UPDATE SKIP LOCKED` directly. Build a toy version with two workers and confirm they claim disjoint rows.

**Idempotency, retries, backoff (3h).** The AWS Builders' Library articles on timeouts, retries, and backoff with jitter, and on caching challenges. Short, free, and written by people who operate this at scale. Directly relevant to an Amazon interview.

**Trigram similarity (2h).** The Postgres `pg_trgm` documentation. Then implement your threshold sweep, which is where the actual understanding comes from.

**Pagination (1h).** Search for "keyset pagination" or "cursor pagination versus offset." One good article is enough. Then verify the difference yourself with `EXPLAIN ANALYZE` at `OFFSET 0` and `OFFSET 50000`.

**Load testing (3h).** The k6 getting-started documentation. Write one ramping-arrival-rate scenario against your query log.

**IR evaluation (3h).** *Introduction to Information Retrieval* chapter 8. Read nDCG, precision at k, and pooling. Then hand-label your own queries, which is where it becomes real.

Note the pattern: every topic is a few hours, independent of the others, and immediately applicable. That is the property the Raft project did not have and the reason this one fits into August.
