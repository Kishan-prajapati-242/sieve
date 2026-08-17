# Sieve — the whole animal, in one file

**What this file is.** A single document covering Sieve from the first commit to
today: what it is, how it flows, what was built in what order, what broke, what
was measured, what was decided, and what of that belongs on a resume.

**What this file is not.** It is not the README. Per the working agreement the
README prose is Kishan's; this is the source material behind it.

**Provenance rule.** Every number here traces to a script in `bench/`, a
migration, a test, or a dated entry in `docs/findings.md`. Where a number was
published and later withdrawn, it is shown ~~struck~~ with the reason, because
the withdrawal is a stronger interview answer than the number was.

**As of:** 2026-08-17 · 146 commits · 21 days elapsed (2026-07-28 → 2026-08-17)
· 214 files · 52,652 insertions.

---

## 0. The animal, at a glance

| | |
|---|---|
| **What** | Literature search and triage: hybrid keyword + semantic retrieval over academic papers, with per-result ranking provenance, screening decisions, and BibTeX/CSV export |
| **Corpus** | **183,167** deduplicated papers · 199,382 immutable source records · 2 live sources (OpenAlex, arXiv) · PubMed client built and smoke-tested, full pull staged |
| **Retrieval** | 3 modes: `bm25` (Postgres FTS), `vector` (pgvector HNSW, 384-dim halfvec), `hybrid` (Reciprocal Rank Fusion in one SQL statement) |
| **Embeddings** | BAAI/bge-small-en-v1.5 via ONNX Runtime, CPU, 512-token window, one vector per paper, asymmetric query prefix enforced inside `embed_query` |
| **Speed** | Paired retrieval speedup over exact scan: **7.7x** 95% CI [7.5, 8.0] at the shipped `ef_search=160` · bm25 SQL p50 **1.1 ms** · vector SQL p50 **2.4 ms** · hybrid e2e p50 **25.2 ms** |
| **Quality** | HNSW recall@200 **0.9856** vs exact-scan ground truth at `ef=600`; recall@20 **0.9782** at the shipped `ef=160` |
| **Dedup** | 6-strategy cascade · **13,445** merges with rollback snapshots · precision **0.959** [0.905, 1.000], recall **0.966** [0.952, 0.981] on 120 blind hand-labeled pairs · inter-annotator **κ = 0.905** |
| **Infra** | PostgreSQL 16 + pgvector 0.8.5 + pg_trgm · 16 forward-only SQL migrations · `SELECT … FOR UPDATE SKIP LOCKED` queue, no Redis, no Celery · Docker Compose, one command from clean clone |
| **Code** | 5,624 LOC API · 5,033 LOC web · 5,466 LOC bench · 4,589 LOC tests |
| **Tests** | 254 green at the last recorded full run (214 backend / 40 frontend, 2026-08-15); 220 backend test functions + 34 frontend cases in the tree today |
| **Rigor** | 17 decision records · **64** findings entries · 8 committed plan documents (`EXPLAIN ANALYZE` output, runbooks, design proposals) · a measurement harness that refuses to publish an unstable percentile |
| **Hardware** | Every number measured on a fanless MacBook Air M1, 8 GB, inside a podman VM at 4 vCPU / 4 GB. Nothing was rented to make it look faster. |

---

## 1. The single cell

Kishan hand-screened 200+ papers for a review paper published at **CML 2025**.
The pain was specific and reproducible: search a term, get 800 hits across three
databases, most irrelevant, many the *same paper listed three times* with
different IDs, keyword search misses papers that use different words for the same
concept, and nothing tracks which of the 800 you already rejected.

That is the origin story, and it is load-bearing. The project exists because the
problem was had, not because a stack was chosen.

The first commit was not code. It was the spec.

```
5b0c51a  Commit the spec and working agreement before any code
```

`docs/sieve-project-brief.md` (46 KB) defined five hard cores, a five-phase plan,
a measurement table, and an anti-pattern list, before a single line of Python
existed. Everything after it is downstream of that document — including the
several places where the document turned out to be wrong and was corrected in
writing rather than quietly abandoned.

**The one rule that shaped the whole project:** *never write a number that was
not measured.* Every figure in this file exists because a script in `bench/`
produced it. That rule is why §5 exists and why six published numbers in this
project have been withdrawn by their own author.

---

## 2. The flow

### 2.1 Query path

```
                        POST /api/search  { q, mode, k, year_from, year_to, ef_search }
                                        │
                        request-ID middleware, structured JSON log
                                        │
                    ┌───────────────────┴───────────────────┐
                    │                                       │
              mode = bm25                            mode = vector / hybrid
                    │                                       │
        websearch_to_tsquery(q)                  embed_query(q)
                    │                            "Represent this sentence for
        ts_rank_cd over the GENERATED             searching relevant passages: "
        fts tsvector column                       + ONNX bge-small, CPU, 512 tok
        GIN index papers_fts_idx (83 MB)          ── prefix applied INSIDE the
                    │                               function, bypass-proof
                    │                                       │
                    │                            halfvec(384) <=> query
                    │                            HNSW papers_embed_idx (204 MB)
                    │                            m=16, ef_construction=64
                    │                            hnsw.ef_search = 160 (vector)
                    │                                       │
                    └───────────────────┬───────────────────┘
                                        │
                            mode = hybrid: ONE SQL statement
                            ┌───────────────────────────┐
                            │  WITH kw  AS (bm25 top-200)│
                            │     , vec AS (hnsw top-200,│
                            │              ef_search 600)│
                            │  RRF: Σ 1/(k + rank_i)     │
                            │  FULL OUTER JOIN on paper  │
                            └───────────────────────────┘
                                        │
                    per-result breakdown: keyword #n · semantic #n · fused score
                    per-request timing:   embed_ms / retrieve_ms / serialize_ms / ef
                                        │
                                   React 18 + TanStack Query
                       segmented mode control · FLIP reorder on mode change
                       arm colors: amber = keyword, violet = semantic
```

Two details in that diagram are the interesting ones.

**The query prefix is enforced where it cannot be skipped.** bge-small is an
asymmetric model: queries need the instruction prefix, documents must never have
it, and getting it backwards degrades retrieval *silently* — no error, no
exception, just worse results. So the prefix lives inside `embed_query()` rather
than at call sites, and a stubbed-encoder test pins the contract end-to-end. The
contract was committed **before the encoder existed** (`ff59327`).

**`ef_search` is auto-raised to ≥ candidate depth in the hybrid path.** Asking
HNSW for the top-200 with a 40-wide candidate frontier asks for more than the
search breadth, which under-returns. That guard is why hybrid runs `ef=600` while
vector runs 160, and why the two defaults are different on purpose.

### 2.2 Ingestion path

```
  OpenAlex API              arXiv Atom API            PubMed E-utilities
  cursor pagination         1 req / 3 s               esearch → efetch, MEDLINE XML
  topic filters             version-stripped ids      2.5/s vs documented 3/s
  year-stratified budget                              (NCBI blocks, not throttles)
        │                          │                          │
        └──────────────────────────┴──────────────────────────┘
                                   │
                     api/ingest/http.py — the SINGLE outbound path
                     · per-source token bucket
                     · explicit timeout, always
                     · full-jitter retry on 429 / 5xx / transport
                     · Retry-After honored, 429 bodies kept
                     · immediate raise on other 4xx (no pointless retry)
                                   │
                     source_records  ← immutable raw JSON, trimmed by `select`
                                       (~2 KB/work instead of 10–20 KB)
                                   │
                     api/ingest/store.py — ONE shared write layer
                     · upsert into papers
                     · embedding = NULL wherever title/abstract changes
                       (DECISION-3a: the work queue IS the NULL column)
                                   │
                     ingest_jobs — SELECT … FOR UPDATE SKIP LOCKED
                     · one job per API PAGE; dedupe_key = (source, query, offset)
                     · each page enqueues its successor INSIDE its own txn,
                       so the queue holds the crawl's position, committed with
                       the data
                     · work + completion in ONE transaction
                     · failure recorded in a SEPARATE transaction
                     · full-jitter backoff via run_after, dead-letter at
                       max_attempts, reap_stale() for SIGKILLed workers
                                   │
                     dedup cascade (before embedding, before fusion)
                                   │
                     embed backfill — resumable, work queue = `embedding IS NULL`
                                   │
                     HNSW index build (after bulk load, never before)
```

Three of those lines are decisions someone will ask about.

**Failure recording runs in its own transaction.** A failure written inside the
aborted work transaction rolls back *with* the work, and the job spins forever.
This is the kind of bug that only shows up under real failure, so it was tested
under real failure.

**OpenAlex is deliberately NOT queue-converted.** Cursor pagination means page
N+1 is reachable only by holding page N's cursor, so a page is not addressable by
a dedupe key. Converting it would produce a queue whose jobs cannot be retried
independently — worse than the resumable loop it already has. The queue serves
arXiv and PubMed, where offset *is* addressable.

**Cross-process rate limiting is deferred with a reason.** N workers hold N
buckets. That is fine here because workers scale for *embedding* throughput, not
fetch throughput, and the fetch side is single-worker by design.

### 2.3 Data model

16 forward-only numbered SQL migrations, one transaction per run, guarded by
`pg_advisory_xact_lock` so it is safe under Neon/Supabase transaction pooling.
No Alembic, no autogeneration — the runner is hand-written (`eb18033`) because
the migrations have to be readable in an interview.

```
papers            canonical, derived, 1:1 with a real paper
                  · title, title_norm, abstract, abstract_md5, year, venue
                  · authors TEXT[]  (deliberately NOT in fts — stemming mangles names)
                  · doi UNIQUE, arxiv_id, pubmed_id
                  · citation_count, is_retracted (flagged, never hidden)
                  · fts tsvector GENERATED  → GIN
                  · embedding halfvec(384)  → HNSW
source_records    immutable raw payload per (source, external_id), audit trail
merges            every merge, with a rollback snapshot
dedup_review      groups the cascade refused to merge, awaiting a human
dedup_negative_pairs   pair-level "these are not the same paper" constraints
boilerplate_abstracts  933 hashes / 3,847 papers that embed title-only
ingest_jobs       the queue: state, attempts, run_after, dedupe_key, claimed_by
collections       a research question
screenings        (collection_id, paper_id) → include/exclude/maybe + note
users, sessions   argon2id, server-side sessions, HttpOnly + SameSite cookie
collaboration     migration 0016 — blind double screening (uncommitted, see §9)
```

**The one FK that is deliberately missing a cascade:** `screenings` cascades from
`collections` but **not** from `papers`. Dedup deletes paper rows. A human's
screening decision must not vanish because its paper was merged into another.
There is a test named `test_screening_survives_merge.py` whose entire job is to
hold that line.

---

## 3. The build, phase by phase

### Phase 0 — the scaffold that boots from a clean clone (2026-07-28, day 1)

Five commits, no features. FastAPI app with a `/healthz` that does a real DB
round trip; a lazy psycopg 3 connection pool that resets its global even when
startup *fails*; the hand-written migration runner; Docker Compose with a
health-gated startup order; and CI that gates every push on ruff, mypy,
migrations, and DB-backed pytest.

`0002_core_tables.sql` created `papers` **with no HNSW index**, and a comment
marking exactly where it goes. Building the graph before the bulk load means
every one of ~200,000 inserts pays graph maintenance. That comment sat in the
schema for three days until Phase 2 earned it.

### Phase 1 — one source, one mode, and the corpus argument (2026-07-29)

The OpenAlex client landed with a per-source token bucket, `select`-trimmed raw
records, cursor pagination, and polite-pool identification. Testing the bucket
caught a real spin: sleeping the *exact* token deficit can round below the
clock's float resolution, so `acquire()` adds a 1 µs margin. A rate limiter that
busy-waits is the sort of bug that only appears under load.

Then OpenAlex went usage-based mid-project — $0.01/day anonymous, $1/day keyed —
and the ingestion work turned into a **cost engineering** problem:

- The credit meter had a blind spot: entity search bills 10x a list page, and it
  was counting requests instead of credits (`findings.md`, entry one).
- Exhausted year slices were buying a page just to throw it away.
- Topic filters are list-class and **14x cheaper per work than phrase search**
  (182 vs 13 works/credit) — which is a large part of why DECISION-2 migrated the
  whole crawl to topics.

**DECISION-1b: stratify crawls by year.** Global citation sort biases toward old
famous work, which makes BM25 look better than it is and understates what dense
retrieval adds. Pure recency collapses the corpus into a two-year slice. Year
stratification lets papers compete on citations *only against their own year*.
That is a retrieval-evaluation argument wearing an ingestion costume.

**DECISION-1c: what never becomes a paper.** Six OpenAlex work types skipped at
ingest — paratext, editorial, erratum, supplementary-materials, peer-review,
retraction — after measuring that proceedings *volumes* were ranking beside their
own member papers with identical abstracts. But `is_retracted` papers **stay**,
flagged and surfaced in search. A screening tool user needs to see a retracted
paper in order to deliberately exclude it.

`POST /api/search` shipped `mode=bm25` — and its docstring says plainly that
`ts_rank_cd` is **not** real BM25. That honesty is in the code, not just the
docs.

**The Phase 3 baseline was captured here, on purpose, before dedup existed:**
query `clinical text simplification`, **3 of the top 20 results are redundant
copies**. You cannot claim a dedup improvement without measuring the mess first.

### Phase 2 — hybrid retrieval, and the resume checkpoint (2026-07-29 → 08-01)

**The corpus pull.** 196,893 papers / 199,285 source records, **1,435 credits
across 1,040 requests, ~24 minutes.** Composition measured from `/api/stats` by
first-fetch provenance: biomedical-clinical-text 31.9%, general-nlp 30.2%,
mental-health-nlp 11.1%, clinical-informatics 9.4%, text-simplification 9.2%.
**Specialty share 62.2%** against a >25% goal. Coverage reported honestly: 2.1%
null venue, 0.3% null authors, 28 null abstracts, 173 retracted and visible.
Fetches reconcile exactly: 170,656 new + 582 DOI-linked + 23,102 refreshed + 192
no-title + 1,499 junk-type = 196,031.

**DECISION-2f: decline the top-up. 196,893 stands.** A ~25-credit general-nlp
top-up would have crossed 200,000. It was refused, in Kishan's words: *"196,893
is more credible on a resume than 200,000, because a precise number reads as
counted and a round one reads as estimated. Adding papers so a rounder number
appears in a bullet is presentation-driven, which is the same instinct as a p99
from 20 samples."*

**DECISION-2b: bge-small-en-v1.5 supersedes the MiniLM pin.** Measured on the
real WordPiece tokenizer over the full corpus: title+abstract median 243 tokens,
p95 533 — **45.3% exceeds 256 tokens but only 5.6% exceeds 512.** MiniLM as
configured truncates at 256, and the decoded truncation tails are
Results/Conclusions: structured abstracts put the payoff last, so a 256 cap cuts
the retrieval-relevant text from nearly half the corpus. bge-small is natively
trained at 512, carries a ~10-point MTEB retrieval gap over MiniLM, and keeps 384
dims so the schema and planned HNSW DDL never move. Chunking at 256 was
considered and rejected: +115,322 vectors (+59%) plus per-paper MAX aggregation
in every vector query, to protect content a 512 window keeps anyway.

**The encode, and the resumability proof that caught a real bug.** SIGKILL at
2,304 committed rows → exactly 2,304 durable on a fresh connection; resume →
exactly 5,000 total, 0 gaps below the high-water mark, all 2,304 pre-kill vectors
**byte-identical by md5**. The proof found the bug first: psycopg's default
connection turned per-batch commits into *savepoints*, so nothing was actually
durable. Jobs now require autocommit connections and say so loudly.

**The HNSW build, and the shm lesson.** Before building, a live probe against
pgvector's `hnswbuild.c` established that parallel workers share **one**
`maintenance_work_mem`-sized area — so there is no per-worker OOM arithmetic to
do. That area is a single POSIX shm segment allocated up front, and the container
`/dev/shm` default of 64 MB **kills the build**. Postgres now runs with
`shm_size: 2g`. Build measured at **36–41 s** (against 5–15 min estimated), index
211 MB (estimate 200–260 MB).

**The exact-scan baseline was captured before the index existed**, because after
the index exists it is gone forever: p50 61.9 ms / p95 98.7 ms over 20 domain
queries, 196,893 rows scanned each, `EXPLAIN` archived, plus exact top-50 ground
truth with query vectors stored inline so the recall sweep never re-embeds.

**The recall sweep, and the ceiling nobody expected.** n=520 queries, tie-aware
recall: r@10 = 0.939 / 0.976 / 0.983 / 0.989 at `ef` = 40 / 160 / 320 / 640.
**The ceiling is 0.989@10, not 1.0** — that residual ~1% is the `m=16 /
ef_construction=64` *construction* ceiling and is unreachable by any search
depth. Knowing which knob cannot fix a problem is worth as much as knowing which
one can.

**DECISION-2e: hybrid defaults, depth 200 / `ef_search` 600.** The post-cascade
ladder over 493 distinct queries:

| ef | recall@200 | recall@20 | recall@10 | SQL p50 |
|---|---|---|---|---|
| 40 | 0.8804 | 0.9238 | 0.9308 | 2.3 ms |
| 160 | 0.9318 | 0.9782 | 0.9795 | 5.0 ms |
| 200 | 0.9401 | 0.9819 | 0.9824 | — |
| 400 | 0.9750 | 0.9900 | 0.9905 | — |
| **600** | **0.9856** | 0.9943 | 0.9941 | 11.7 ms |
| 800 | 0.9897 | 0.9954 | 0.9951 | — |

The phrasing in that decision is the tell: `ef=600`'s cost is *"below measurement
resolution on this hardware"* — its 13.6 ms p50 sits inside `ef=200`'s own
[12.5, 19.9] cross-run range. **"We cannot see the cost" is different from
"there is no cost,"** and the record says so rather than rounding to "free."

**Two of this project's own claims were withdrawn in the same session** that
produced them (`8d04b68`): the "N=50 dip proves the coupling" reading was
confounded because both `k` and `ef` moved between rows, and the
top-20-overlap justification for N=200 was **circular** — overlap against the
deepest tested ranking reaches 1.0 by construction. The honest column is the
identical-order rate, 0.781, because order is what nDCG scores.

**`mode=hybrid` — RRF in one SQL statement**, with the plan committed to
`docs/plans/hybrid-rrf-explain.md`, and a per-result breakdown on every row.

### Phase 3 — three sources, dedup, the queue, and a product (2026-08-01 → 08-17)

**arXiv client**: 1 req/3s, Atom parsing, version-stripped ids, idempotency
verified live by running 100 entries twice and getting identical counts.

**PubMed client**: esearch + efetch, MEDLINE XML via stdlib ElementTree,
structured abstracts keeping their section labels, and `is_retracted` from
`PublicationType` distinguishing *"Retracted Publication"* from *"Retraction of
Publication"* — two strings one character apart that mean opposite things. Bucket
at 2.5/s against NCBI's documented 3/s, because NCBI **blocks** rather than
throttles.

**The dedup cascade** — the hardest thing in the project.

Six candidate-generation strategies, cheapest and most certain first — five live
in `api/dedup/cascade.py`, the preprint variant in the planner: `doi_exact`
→ `id_exact` (arXiv/PubMed) → `abstract_hash` (MD5, boilerplate-excluded) →
`title_exact` (normalized title within year, length ≥ 20) → `title_trgm`
(similarity ≥ 0.92, gated on **same year AND a shared author surname** — the
surname is the guard against two 2024 papers both titled "Results") →
`preprint_trgm`. Union-find rebuilds components from pair edges, because
different strategies find different edges of the same real duplicate set.

Executed: **196,988 → 182,853 papers, 13,445 merges, every one with a rollback
snapshot.** The Phase 1 duplicate baseline went **3/20 → 0/20**, verified in both
bm25 and hybrid.

Then the interesting part. **The measurement changed the rules.** 120 pairs, hand
labeled *blind* — the cascade's verdict, rule, similarity and group size were
never shown during labeling — stratified across 12 strata, inverse-probability
weighted, stratified-bootstrap CIs. Per-stratum precision exposed one bad arm:

```
acc_abstract_hash        n=11   1.000
acc_title_trgm           n=3    1.000
acc_preprint_trgm        n=5    1.000
acc_title_exact_pair     n=7    0.857
acc_title_exact_group    n=19   0.684   ← the finding
```

So `MAX_GROUP_SIZE` dropped to **2 for `title_exact` only**, and the 122
already-executed groups above the new cap were **unwound via their rollback
snapshots — 314 papers restored, routed to `dedup_review`, 0 errors, 0 orphaned
records.** That is reversibility exercised on production data rather than
asserted in a unit test.

Two results that were kept deliberately separate from the headline:

1. **The three rules built under scrutiny have a zero miss rate.** The enumerator
   rule, the part-sibling rule and the size cap refused 1,617 pairs between them,
   and not one sampled refusal was a real duplicate. Rules added in response to
   hand-reading did not cost recall.
2. **`ref_below_threshold_preprint` misses 5 of 6 sampled pairs (0.833).** This is
   the "Ascle gap" as a *population fact* rather than one fixture — and it is
   exactly why tuning the preprint threshold to 0.90 to catch Ascle was
   **rejected**. Fitting a global parameter to a fixture would have hidden a
   population-level problem behind a green test.

**And the caveat travels with the number.** Kishan corrected **10 of his own 120
labels (8.3%)** on review, clustered on patterns whose taxonomy only emerged
partway through labeling — so early labels used a weaker rubric than late ones.
The precision figure rests on labels of which roughly 1 in 12 changed when
re-examined once. A second annotator gave **Cohen's κ = 0.905**. One pair (#67)
was withheld rather than corrected, on contradicting evidence.

**Then the number was rescored, because the rule had changed under it.** The 120
labels were collected while the global cap was 8; DECISION-3c capped
`title_exact` at 2 *because of* those labels, and nobody had recomputed the pair:

| | precision | recall | F1 |
|---|---|---|---|
| as sampled (global cap 8) | 0.9568 [0.9035, 0.9983] | 0.9728 [0.9589, 0.9880] | 0.9647 |
| **as shipped (title_exact cap 2)** | **0.9594** [0.9053, 1.0000] | **0.9662** [0.9524, 0.9813] | 0.9628 |

**Resume consequence, stated in the findings log: recall reads 0.966, not
0.973.** The rescoring is honest about its own limit too — the strata weights are
the old population's, so this answers "what would these labels have scored under
the shipped cap," which is the right question for a resume number and is *not* the
same as a fresh measurement of the shipped cascade.

**The queue.** `SELECT … FOR UPDATE SKIP LOCKED` claim, full-jitter backoff via
`run_after`, dead-lettering at `max_attempts`, `reap_stale()` for SIGKILLed
workers. Verified: **8 threads racing for 200 jobs claim each exactly once**, and
a worker killed mid-crawl loses no pages and creates no duplicates.

**Screening + export.** Collections; include/exclude/maybe as an upsert on
`(collection_id, paper_id)`; BibTeX export with TeX escaping, brace-protected
acronyms, and stable collision-suffixed keys so the same collection **exports
byte-identically**; `@misc` when there is no venue, because a preprint is not an
article with a blank journal.

**CSV export (2026-08-17)** answers a different question than BibTeX and
therefore has different defaults: BibTeX is "the citations that made the cut" and
defaults to *included*; CSV is "here is the screening" and defaults to **all**
papers, with `decision`, `note`, `decided_at` as the first three columns —
because a screening record with the exclusions stripped out is not a screening
record. Two things decide whether the file even opens correctly, and both are
tested: the **UTF-8 BOM** (Excel on Windows reads UTF-8 as Latin-1 without it and
turns every accented author name into mojibake) and **CSV formula injection** (a
cell starting `=`, `+`, `-` or `@` is *executed* by Excel and Sheets; real paper
titles start with `-` often enough that this is not hypothetical).

### Phase 3.5 — Sieve became a product (2026-08-15 → 08-17)

The brief changed mid-flight, and the record says why: *nine turns had gone into
one toggle's choreography while the app had no color system, no type scale, no
landing page, and no users — collections belonged to nobody.*

**Design sourced as a funnel, not a library adoption (DECISION-4a).** 3,587
unique URLs harvested from 11 awesome-lists → 465 after keyword filter → **37
screenshotted live** → 30 viewed as images → 18 put through motion capture → **8
effect categories catalogued**, each with sources, frames, and a lift cost. The
first pass produced a shortlist of twelve component *libraries* and asked which
to adopt; that was corrected mid-task, because adopting one registry produces an
app that looks like that registry's demo. **The unit is the effect; the
deliverable is assembly.**

The anchor effect is not decoration — it is the product's argument made visual.
On the real query `BERT for de-identification of clinical records`, paper 101226
is **#5 under bm25, outside vector's top 5, and #1 under hybrid.** Animating the
mode toggle *shows* fusion instead of narrating it. A runnable FLIP proof over
the real per-mode orderings was built and its frames verified before any of it
reached the app.

And the limit of the instrument was measured rather than assumed: a Playwright
screenshot round trip costs 20–375 ms, so bursts sample at 11–33 fps with jitter.
**Whether motion is smooth is not establishable that way** — which is why four
specific effects went to human eyes instead of being scored by the harness.

**Visual identity that is also the mechanism.** Amber keyword + violet semantic
were already the *arm* colors, so the brand is their fusion, and the ground is
near-black specifically so those two stay the only saturated things on screen.
The mono/sans split is a rule: if it is a measurement, it is monospace.

**Accounts (migration 0014–0015).** argon2id, server-side sessions, HttpOnly +
SameSite cookie, collections scoped by a `WHERE` predicate returning **404 rather
than 403** on a miss (403 confirms the row exists). 12 tests pin the security
properties, including one user being unable to read, list, screen, unscreen, or
export another's collection, and legacy ownerless rows being invisible.

**Email verification that actually gates (2026-08-16).** The first version was a
real vulnerability, and the commit says so: signup issued a real session and the
code screen was *advisory*, so anyone could register an address they did not own,
ignore the prompt, and have a working account. Now signup mints a **pending**
token under a separate cookie with a 30-minute life that authenticates `/verify`
and `/resend` and **nothing else** — no `/me`, no collections, no session cookie.
The real session is created only on a correct code, and the pending row is
destroyed at that moment so a captured pending cookie is dead rather than a second
way in. Four tests pin it. "Skip for now" is gone, because the server grants
nothing past the step.

Along the way the SMTP failure turned out not to be silent at all: Resend returns
an explicit **550 — "You can only send testing emails to your own email
address"** until a domain is verified. That rejection now reaches the *screen*,
hides the code boxes nobody can fill, and offers Google sign-in — which is the
right answer rather than a bypass, because OAuth proves control of the address the
same way a code does. Password signup is closed behind `PASSWORD_SIGNUP`, read at
**call time** so flipping it needs no code change, and enforced in the API with a
403 rather than merely hidden, because *a disabled path a curl can still walk is
not disabled.*

**Deploy sizing, measured before choosing a host** (`bench/deploy_sizing.py`):

| piece | size | needed to serve queries? |
|---|---|---|
| papers heap | 276 MB | yes |
| `papers_embed_idx` (HNSW) | 204 MB | yes — vector mode |
| `papers_fts_idx` (GIN) | 83 MB | yes — bm25 mode |
| `papers_title_year_idx` | 19 MB | yes — year filter |
| dedup-only indexes | 49 MB | **no** — cascade runs before deploy |
| `source_records` | 820 MB | **no** — ingestion provenance |
| **total database** | **2,067 MB** | |
| **serving footprint** | **586 MB** | **3,354 bytes per paper** |

586 MB does not fit a 0.5 GB free tier, so the deployment is a **subset —
128,078 papers, 69.9% of the corpus, ~410 MB** — and the subset keeps *whole
topic buckets* rather than a random sample, because the demo queries depend on
specific papers being present and a random 70% would quietly drop one arm's
unique hits and stop demonstrating fusion. A plain largest-first pass dropped
`text-simplification`, which is one of the three demo queries; hence an explicit
`--require` list. **And the deployed site reads its own `/api/stats`**, so a
128k deployment reports 128,078 rather than inheriting 183,167.

**The collaborative-screening design (2026-08-17; the committed artifact is the
design, and an unverified implementation is uncommitted in the tree — §9).** Four
designs explored and argued against. Shared-mutable ("Google Docs") is rejected
as *disqualifying* rather than merely cheap: seeing a colleague's call anchors
your own — the exact bias blind screening exists to prevent — last-write-wins
discards the disagreement that is the whole signal, and losing attribution makes
any agreement statistic impossible. **It is the smallest change and the only one
that makes the product worse at its job.** The recommendation is blind double
screening with explicit reconciliation, because its schema is a *superset*: a solo
collection is that design with one screener, so the machinery costs a lone user
nothing. Conflict is **derived, not stored**, because a stored conflict is a
second source of truth that will drift. And κ is surfaced only above 2 screeners
and 30 co-screened papers — the same refusal-to-publish-unstable-numbers rule the
bench harness already applies.

---

## 4. The five hard cores, and the number each one earned

The brief committed to five things being genuinely hard. Here is what each
actually earned.

### Core 1 — Hybrid retrieval with rank fusion

Two rankers with **incomparable score scales**: `ts_rank_cd` is a lexical
overlap score, cosine distance is a geometric one. RRF sidesteps the problem
entirely by throwing away magnitudes and fusing *ranks*: `Σ 1/(k + rank_i)`. One
SQL statement, `FULL OUTER JOIN` between a keyword CTE and a vector CTE, plan
committed.

**The demo set, measured** (`bench/demo_queries.py`, 15 candidates through all 3
modes):

| query | winner | mechanism |
|---|---|---|
| `reciprocal rank fusion` | **bm25** | exact multi-word technical phrase; vector drifts to *Rank-Biased Overlap* — a different measure retrieved for being semantically near "rank" and "fusion" |
| `why medical jargon confuses ordinary readers` | **vector** | pure paraphrase; **bm25 returns literally zero rows** (AND-semantics cliff); hybrid's top-10 takes 10 of 10 from the vector arm |
| `BERT for de-identification of clinical records` | **hybrid** | rare exact term + concept, each arm gets half the query; **hybrid's #1 was ranked first by neither arm**; hybrid's top-10 is 5 from each |

That middle row is the strongest possible version of the demo, and the third row
is the entire argument for fusion in one sentence.

**How "wins" was decided is stated so it can be discounted:** there are no
relevance labels yet, so the script measures which results each mode finds
*uniquely* and which of those hybrid adopts; the judgment that they are *better*
comes from reading titles. That is weaker than nDCG, and Phase 4 exists to
replace it.

### Core 2 — Deduplication and entity resolution

Covered in §3. Precision **0.959**, recall **0.966**, F1 **0.963**, κ **0.905**,
13,445 merges, all reversible, one rule changed by its own measurement, one
tempting threshold-fit refused.

### Core 3 — Ingestion that survives reality

Token buckets, explicit timeouts, full jitter, `Retry-After`, dead letters,
`SKIP LOCKED`, page-as-idempotent-unit, SIGKILL-proven at both the crawl and the
encode. Plus a cost model, because the API started charging mid-project.

### Core 4 — Query latency engineering

Measured on a fanless M1 Air in a 4 GB VM, 183,167 rows / 35,348 heap pages,
`pg_prewarm`, 520 distinct queries, 1 warmup + 3 measured runs:

| mode | window | p50 | p95 | p99 |
|---|---|---|---|---|
| bm25 | SQL | **1.1 ms** | 24.5 ms | *unstable* [52.1, 105.3] |
| vector | SQL | **2.4 ms** | *unstable* [3.1, 4.6] | *unstable* [3.6, 6.7] |
| vector | e2e + embed | **10.5 ms** | 30.2 ms | 59.3 ms |
| hybrid | SQL | *unstable* [14.3, 18.9] | *unstable* [35.9, 57.0] | *unstable* [112.0, 208.2] |
| hybrid | e2e + embed | **25.2 ms** | *unstable* [60.2, 86.2] | *unstable* [130.4, 212.7] |
| exact scan (baseline) | SQL | 64.7 ms | 95.3 ms | *unstable* [109.3, 241.1] |

*"Unstable" is not hedging — it is a gate. The harness publishes a point estimate
only when cross-run max/min ≤ 1.3; otherwise it publishes the observed range and
refuses the number.*

**The paired speedup** (`bench/paired_speedup.py`, DECISION-3d): exact scan vs
HNSW at the shipped `ef=160`, both arms timed back to back inside the same query
slot with rotated order, ratio computed per query, percentile bootstrap over 520
queries:

```
retrieval-only   7.7x   95% CI [7.5, 8.0]     ← the resume figure
end-to-end       4.2x   95% CI [4.1, 4.3]
```

**Where the wins actually came from** — three profiling results, all with a
before and after:

| fix | before | after | how it was found |
|---|---|---|---|
| Index the FK column Postgres does not create | 1,140 ms | **0.608 ms** | `EXPLAIN ANALYZE` on the merge path — 3rd instance of this pattern in the project, and it is logged as a pattern |
| Cosmetic result count on the search path | 78 ms paid to display an unchanged number; naive counts cost **47x** on the modes they labelled | derived from the fusion query already in the plan | instrumenting the endpoint's own timing breakdown |
| Table bloat | heap 44,059 pages, GIN 118 MB, trgm 70 MB | heap **35,348**, GIN **83 MB**, trgm **39 MB** | `VACUUM FULL` after dedup — the corpus carried ~20% bloat, well beyond what dedup deleted, and **every percentile steadied afterwards** |
| Cascade candidate generation | 46.7 M pairs evaluated to produce 1,616 | block-keyed generation; **cascade runs in 10 minutes** | per-step `EXPLAIN`, after a false 3h55m reading (see §5) |

### Core 5 — Actually evaluating it

Ground truth is exact brute-force top-200 over the real corpus, with query
vectors stored inline so the recall sweep never re-embeds. It has been **rebuilt
when it went stale** — 8.2% of the ids it referenced had been deleted by dedup,
and 99% of queries had a dead id in their top-200 — and the file now records the
corpus it describes, so that failure cannot recur silently.

The evaluation *protocol* for the next corpus change is settled **in advance**,
which is the part most projects skip: after the PubMed pull, rebuild ground truth
**twice** — once with the same 493 queries (isolates the *corpus* effect) and once
with `--refresh-queries` (represents a corpus a third of whose sources are new) —
because the corpus change and the query-set change are separate effects and must
not be confounded. Untangling exactly that confound already cost two sessions
once, when **one degenerate query weighted 28x moved a decision's apparent margin
by five points.**

---

## 5. The measurement discipline (this is the differentiator)

Most student projects have a benchmark. This one has an **epistemology**, and it
is 64 dated entries long. Four mechanisms, each of which caught something real.

### The stability gate

A percentile is published only if cross-run `max/min ≤ 1.3`. Otherwise the range
is published and the point estimate is **refused**. This came from
`findings.md 2026-07-31: "A percentile computed from 20 samples is not a
percentile"` — a p99 from 20 samples is just the maximum wearing a costume.

Consequence: **hybrid SQL p50 is unpublished.** `18.2 ms` was published, drifted
13.6 → 15.8 → 15.3 → 18.2, and a clean re-run gave per-run p50s of
[18.9, 14.7, 14.3]. Max/min = 1.32, so the gate refused a replacement. Two of
three runs sit *below* 18.2 and none reproduce it, so **~~18.2~~ was withdrawn
with no replacement figure** rather than re-rounded.

### Paired measurement (DECISION-3d)

Speedups were measured across separate runs and divided. That is broken, and the
decision record says why in Kishan's words: *"The unexplained regression inflates
my headline claim… The number improved 14% because the denominator got worse for
reasons nobody understands."* Now both arms are timed back to back inside one run
with alternating order, and the CI is on the **ratio itself**, so thermal state,
page cache, and VM scheduling affect both sides equally and cancel.

Then pairing was itself audited and found to have a *limit*: **pairing controls
for drift between runs, not for who your neighbour is inside one.** The same
measurement returns **7.7x with two arms and 12.2x with four**, because HNSW arms
preheat each other's index pages and rotation controls position but not neighbour.
**7.7x is the number used**, because production runs one vector query, not three
at different `ef` back to back — the conservative reading of two defensible
protocols.

### The gradient audit

The most sophisticated finding in the log. End-to-end speedup is
`(embed + baseline) / (embed + candidate)`. Both arms carry the same embed cost,
and a constant added to both arms of a ratio does not cancel — **it means making
the encoder slower raises the reported speedup.**

```
embed 8.0 ms → 7.1x        embed 6.1 ms → 8.7x
embed 2 ms   → 12.0x       embed 40 ms  → 2.6x
```

Nobody did this and nobody would mean to. But **an incentive that is not written
next to the number gets discovered by someone else later** — so it was written
next to the number, and the resume claim was moved to the retrieval-only figure,
which carries no encoder term and therefore has no such gradient.

### Instrument audits

Three times an instrument passed *by not looking*, and each time the instrument
was fixed and the conclusions it had produced were re-derived:

- **The motion that never fired.** Nine turns of choreography tuning were
  verified by instrumenting rather than reading: tag every `<li>`, toggle mode,
  read back — **0 of 20 nodes survived.** The list unmounted on mode change, so
  layout animation had nothing to move. *Every earlier motion decision had been
  made against frames the code could not have produced.* Fixed with
  `placeholderData`; the probe now reports 5 of 5 survivors, reordered.
- **The screenshot harness had a blind region.** `fullPage` was never set in
  **19 capture scripts**, so two passes had tuned a schedule against a cropped
  image, and "the missing survivors" were simply below the crop. Audited,
  defaults fixed, re-run scope named.
- **The contrast auditor was swallowing exceptions and reporting green.** It now
  **counts what it checked and prints what it skipped**: 208 hover/focus/active
  state samples checked, 2 skipped and named. The bug it had been hiding was real
  — `hover:bg-white` uses Tailwind's *literal* white, which does not flip with
  the theme, producing a **1.02:1** white-on-white button in light mode. Now a
  token: measured **16.83:1 light / 16.08:1 dark**.

### The four-hour cascade that was a sleeping laptop

The single best debugging story in the repo, and it runs six commits long.

Cascade candidate generation was measured at **3 h 55 m**. That number was
attributed to a `LIKE` scan, then to `dd_scored`, then to quadratic behaviour
inside the blocks; a per-step `EXPLAIN` was written to find it; an alarm was
raised that the next cascade would re-merge all 314 unwound papers.

**The cascade takes 10 minutes.** The host had gone to sleep. A suspended podman
VM freezes the guest clock while host wall-clock keeps running — a **23x
inflation**. The alarm was retracted (the executor would have merged nothing), the
`LIKE` attribution was retracted, and three project-wide conventions were written
into `CLAUDE.md` so it cannot recur:

1. Wrap every long run in `caffeinate -dimsu`.
2. Any host-side duration is cross-checked **in-VM** before publication —
   `SELECT now()` inside Postgres, or `perf_counter`, which is CLOCK_MONOTONIC
   and does not advance across a suspend. When the two disagree, **the in-VM
   number is the measurement.**
3. Capture the stdout of any run whose rate you will later quote. The full corpus
   encode printed a sleep-immune throughput figure and nobody saved it — so the
   project still has **no sustained encode rate for its own hardware**, and says
   so instead of quoting the number it wishes it had.

### The withdrawal ledger

Six published figures retracted by their own author, each with the mechanism
recorded:

| figure | status | why |
|---|---|---|
| ~~13.2 docs/s~~ encode throughput | retired | unseeded sample, on a corpus that no longer exists, exceeding every steady-state rate measured since |
| ~~18.2 ms~~ hybrid SQL p50 | withdrawn, unreplaced | drift, then a clean re-run failed the stability gate |
| ~~7.1x~~ e2e speedup | moved off resume | joint measurement of retrieval *and* the encoder, with a perverse gradient |
| ~~24.1x~~ retrieval speedup | superseded | describes `ef=40`, which **nothing runs** after DECISION-4b, and carries three-arm inflation |
| ~~5.5x~~ end-to-end ratio | corrected up to 6.3x | divided scan-only by end-to-end — a window mismatch that happened to run *conservative* |
| ~~"the next cascade re-merges 314 papers"~~ | retracted | the executor would have merged nothing |
| ~~"122 groups re-attributed between runs"~~ | withdrawn as unsupported | the planner never consulted attribution; the two runs differed in which *cap* applied |

The pattern in that table is the thing to notice: **the corrections run in both
directions.** One of them made the project's own number *better* and was still
corrected.

---

## 6. Decision index — 17 records, `docs/decisions.md`

Each written in Kishan's words at the moment it was made, with alternatives, the
rejection reasoning, and **what would change my mind**.

| # | Decision | The load-bearing reason |
|---|---|---|
| 1 | Python + FastAPI, single service | ONNX embeddings are Python-native; Node forces two services and a network hop for the benefit of familiarity |
| 1b | Year-stratified crawls | Citation sort biases old/famous, which flatters BM25 and understates dense retrieval |
| 1c | Skip 6 junk types; **keep** retracted, flagged | A screening tool user must see a retracted paper to deliberately exclude it |
| 1d | Whole stack in Docker, zero host deps | One `docker compose up` from a clean clone; accepted cost: unresolved imports in the editor |
| 2 | Migrate crawl to topic filters | Concepts deprecated; topics 14x cheaper per work; clinical-informatics **halved** to 10% because too many hard negatives would make hybrid look worse than BM25 and undercut the eval |
| 2b | bge-small-en-v1.5, 512-token window | 45.3% of documents exceed 256 tokens and structured abstracts put the payoff last |
| 2c | Keep `dataset` records | Measured 0 of 120 top-20 slots polluted; the problem is abstract *shape*, not the type field |
| 2d | fp32 encode; int8 deferred | Cosine parity ≠ ranking parity; the correct metric is Recall@10 against the fp32 index, which did not exist yet |
| 2e | Hybrid depth 200 / `ef` 600 | +4.5 recall points at a cost *below measurement resolution* — stated as "cannot see," not "free" |
| 2f | No top-up; 196,893 stands | A precise number reads as counted; a round one reads as estimated |
| 3a | Null the vector wherever text is written | A hash column is a second invariant every write path must maintain; forgetting it yields a stale vector the system *believes* is fresh |
| 3b | Published wins over preprint | All 524 JMIR pairs rewrote the abstract; `citation_count` takes **max, not sum**, because summing double-counts anyone who cited both |
| 3c | `title_exact` cap → 2; unwind 122 groups | Its own measurement said 0.684, so the rule changed and the bad merges were reversed on production data |
| 3d | Speedups are paired, not divided | Cross-run division let an unexplained regression inflate the headline claim |
| 4a | Assemble motion from effects, not adopt a library | Adopting one registry produces an app that looks like that registry's demo |
| 4b | Vector `ef_search` 40 → 160 | +5.4 recall@20 points for 2.6 ms behind a ~7 ms embed floor; also removes the low-`ef` regime where the degenerate-cluster failure lives — **and it costs the flattering 24.1x, which is the point** |
| — | Collaborative screening | Design proposal; shared-mutable rejected as *disqualifying* rather than merely cheap. Implementation is uncommitted in the tree — see §9 |

---

## 7. Resume material

### Header line

> **Sieve — Literature Search & Triage System** · Python, FastAPI, PostgreSQL,
> pgvector, ONNX Runtime, React, TypeScript, Docker · github.com/…

### Three-bullet version (recommended)

> - Built a **hybrid retrieval engine over 183,167 deduplicated papers** from
>   OpenAlex and arXiv, fusing PostgreSQL full-text and pgvector **HNSW** rankings
>   via **Reciprocal Rank Fusion in a single SQL statement**; measured HNSW
>   **recall@200 = 0.986** against an exact brute-force ground truth and a
>   **7.7x paired retrieval speedup (95% CI [7.5, 8.0])** over exact scan at the
>   shipped `ef_search`, with p50 SQL latency of 1.1 ms keyword / 2.4 ms vector.
> - Designed an **idempotent multi-source ingestion pipeline** on a PostgreSQL
>   `SELECT … FOR UPDATE SKIP LOCKED` queue — per-source token buckets,
>   full-jitter retries, `Retry-After` handling, dead-lettering, and stale-job
>   reaping — where the unit of work is one API page keyed `(source, query,
>   offset)`; **verified 8 concurrent workers claim 200 jobs exactly once** and a
>   SIGKILLed worker loses no pages and creates no duplicates.
> - Built a **6-strategy entity-resolution cascade** (DOI → external ID →
>   abstract hash → normalized title → trigram ≥ 0.92 gated on shared author
>   surname), merging **13,445 duplicate records with full rollback snapshots**
>   and cutting the corpus 196,988 → 183,167; measured **precision 0.959, recall
>   0.966** on 120 *blind* hand-labeled pairs across 12 strata
>   (inverse-probability weighted, stratified-bootstrap CIs, inter-annotator
>   **κ = 0.905**) — and **lowered a group-size cap after that measurement showed
>   0.684 precision on one arm**, reversing 122 already-executed merges.

### Four-bullet version — add the performance bullet

> - **Profiled and cut latency with `EXPLAIN ANALYZE`:** added the index Postgres
>   does not create for a foreign key (**1,140 ms → 0.608 ms**), replaced trigram
>   candidate generation with block-keyed generation (**46.7 M pairs → 1,616
>   evaluated**, cascade runtime 10 min), removed a cosmetic result count costing
>   **47x** on the modes it labelled, and reclaimed ~20% table bloat via
>   `VACUUM FULL` (**heap 44,059 → 35,348 pages, GIN 118 → 83 MB**) — on a fanless
>   M1 Air in a 4 GB VM, with the query plans committed to the repo.

### Two-bullet version (space-constrained)

> - Built a hybrid search engine over **183,167 deduplicated academic papers**,
>   fusing PostgreSQL full-text and pgvector HNSW rankings by Reciprocal Rank
>   Fusion in one SQL statement; **recall@200 = 0.986** vs exact search, **7.7x
>   paired retrieval speedup [7.5, 8.0]**, 1.1 ms p50 keyword / 2.4 ms p50 vector.
> - Built the ingestion and entity-resolution side: an idempotent
>   `SKIP LOCKED` job queue with token-bucket rate limiting, full-jitter backoff
>   and dead-lettering, and a 6-strategy dedup cascade merging 13,445 records at
>   **0.959 precision / 0.966 recall** on 120 blind hand-labeled pairs
>   (**κ = 0.905**), fully reversible via rollback snapshots.

### The optional fifth bullet — only if the role rewards rigor

Research, data science, ML-infra, and any team that has been burned by a bad
benchmark will read this as the strongest bullet on the page. A generic web-dev
screen may not parse it. Judgment call.

> - Maintained a **64-entry findings log** and a benchmark harness that **refuses
>   to publish a percentile** whose cross-run `max/min` exceeds 1.3; **withdrew six
>   of my own published figures** after auditing their protocol — including a
>   headline speedup that was an artifact of a suspended-VM clock (a 23x
>   inflation) and an end-to-end ratio whose arithmetic *rewarded a slower
>   encoder*.

### Skills surface — the true subset

**Languages:** Python 3.12, TypeScript, SQL, Bash
**Data:** PostgreSQL 16, `tsvector`/GIN full-text search, pgvector, HNSW, `halfvec`,
`pg_trgm`, JSONB, connection pooling, forward-only migrations, `EXPLAIN ANALYZE`,
`VACUUM`, advisory locks
**Backend systems:** FastAPI, REST design, job queues, `FOR UPDATE SKIP LOCKED`,
idempotency, at-least-once delivery, full-jitter backoff, token-bucket rate
limiting, dead-letter queues, structured JSON logging with request IDs, argon2id
sessions, OAuth 2.0
**Search & ML:** information retrieval, dense retrieval, sentence embeddings, ONNX
Runtime, approximate nearest neighbour search, HNSW parameter tuning, rank fusion
(RRF), recall@k, entity resolution, precision/recall with bootstrap CIs, Cohen's κ
**Frontend:** React 18, Vite, Tailwind, TanStack Query, FLIP layout animation,
WCAG contrast auditing, `prefers-reduced-motion`
**Practice:** Docker Compose, GitHub Actions CI, pytest, ruff, mypy, vitest,
design decision records, blind annotation protocols

### What to say when they ask "what was hardest"

Not the retrieval. **The measurement.** Writing the RRF query took an afternoon;
establishing that its speedup number was true took the rest of the project, cost
six retracted figures, and produced three conventions that now live in the repo's
working agreement.

---

## 8. Numbers you can defend — with provenance

Every row is answerable with "here is the script, here is the protocol, here is
the caveat."

| Number | Value | Source | The caveat you volunteer |
|---|---|---|---|
| Corpus | 183,167 papers | `/api/stats` | 196,988 before dedup; 2 live sources, PubMed staged not pulled |
| Source records | 199,382, 0 orphaned | `/api/stats` | immutable audit trail, not searchable content |
| Ingest run | 196,893 papers, 1,435 credits / 1,040 requests, ~24 min | `progress.md` 2026-07-29 | credits, not requests — entity search bills 10x a list page |
| Merges | 13,445, all reversible | `bench/dedup_execute.py` | 179 groups still in `dedup_review` awaiting human judgment |
| Dedup precision | 0.959 [0.905, 1.000] | `bench/results_dedup_precision.json` | rescored under the shipped cap; weights are the pre-cap population's, so it is a rescoring, not a fresh measurement |
| Dedup recall | 0.966 [0.952, 0.981] | same | recall **among candidates**, not global recall |
| Inter-annotator κ | 0.905 | `bench/dedup_agreement.py` | 10 of 120 first-pass labels were corrected on review (8.3%) |
| HNSW recall@200 | 0.9856 at `ef=600` | `bench/ef_at_fixed_depth.py` | 493 distinct queries; 500/520 are corpus titles — known-item bias |
| HNSW recall@20 | 0.9782 at shipped `ef=160` | same | the figure that belongs beside vector mode, which serves k=20 |
| HNSW ceiling | 0.989@10, **not 1.0** | `bench/hnsw_recall_sweep.py` | the residual is the `m=16/efc=64` construction ceiling; search depth cannot reach it |
| Paired speedup | **7.7x [7.5, 8.0]** | `bench/paired_speedup.py` | two-arm protocol; four arms give 12.2x through index-page preheating, and 7.7x is the conservative reading |
| bm25 SQL p50 | 1.1 ms | `bench/results_mode_latency.json` | p99 unstable [52.1, 105.3] — bm25's tail is the match-count driver |
| vector SQL p50 | 2.4 ms | same | measured at `ef=40`; DECISION-4b moved the default to 160, so this wants a re-run |
| hybrid e2e p50 | 25.2 ms | same | hybrid **SQL** p50 is unpublished — the stability gate refused it |
| Exact-scan baseline | p50 64.7 ms / p95 95.3 ms | `bench/exact_scan_baseline.py` | forced via `enable_indexscan=off`, Seq Scan verified by `EXPLAIN` |
| FK index fix | 1,140 ms → **0.608 ms** | `findings.md` 2026-08-01 | third instance of this pattern; logged as a pattern, not a one-off |
| Bloat reclaim | heap 44,059 → 35,348 pages | `findings.md` 2026-08-12 | ~20% bloat, more than dedup deleted; every percentile steadied after |
| Cascade runtime | ~10 min | `findings.md` 2026-08-13 | previously reported 3 h 55 m — the host was asleep, a 23x inflation |
| Queue correctness | 8 workers / 200 jobs, each claimed once | `tests/test_queue.py` | correctness, **not** a throughput figure — see §9 |
| Encode kill-proof | SIGKILL at 2,304 → 2,304 durable, md5-identical | `progress.md` 2026-07-30 | the proof found a real bug first: psycopg turned commits into savepoints |
| HNSW build | 36–41 s, `m=16`, `efc=64` | migration 0006 | needed `shm_size: 2g`; the 64 MB container default kills the build |
| Index sizes | HNSW 204 MB, GIN 83 MB, trgm 39 MB | `bench/deploy_sizing.py` | at 183,167 papers post-`VACUUM FULL` |
| Serving footprint | 586 MB = **3,354 B/paper** | same | total DB is 2,067 MB; `source_records` and dedup indexes do not serve queries |
| Deploy subset | 128,078 papers (69.9%) | `bench/export_deploy_subset.py` | whole topic buckets, not a random sample — a random 70% would break the fusion demo |
| Contrast audit | 208 state samples; 16.83:1 / 16.08:1 | `bench/theme_audit.py` | the auditor previously reported green by swallowing exceptions; it now counts and names skips |
| Tests | 254 green (214 backend / 40 frontend) | 2026-08-15 run | three commits of new work have landed since, with their own tests |

---

## 9. What is NOT built, and what has NOT been measured

Read this before any interview. Overclaiming one of these is the only way this
project loses an argument it would otherwise win.

**Not built:**

- The **full PubMed pull.** The client is built, tested (14 tests), and
  smoke-verified on 12 live articles; the pull is staged as a runbook and has not
  run. The corpus is 2 sources, not 3. *Say "two live sources, third client
  built and staged."*
- **Collaborative screening** — designed and argued in
  `docs/plans/collaboration-design.md`, and **a substantial implementation is
  sitting UNCOMMITTED in the working tree** as of 2026-08-17: migration
  `0016_collaboration.sql` (111 lines), `api/collections/{members,screening,
  agreement}.py` (542 lines), 30 new tests in `tests/test_collaboration.py` +
  `tests/test_agreement.py`, plus modifications to `collections/routes.py`,
  `dedup/merge.py` and `test_screening_survives_merge.py`. **Its test suite has
  not been run in this session, so it is "written, not verified."** Do not claim
  it either way until `make test` says so.
- **Pagination, `ts_headline` match highlighting, sort control, faceted sidebar,
  bulk add-to-collection, abstract preview** — the accepted functional list, not
  yet built.
- **A README.** The repo has none. The resume links to this repo, so this is the
  highest-leverage remaining hour of work in the project.
- **A live public URL.** Deploy is configured (`render.yaml`, `vercel.json`,
  Neon loader, subset exporter, split-origin cookies) and sized against
  measurement; it needs four accounts only Kishan can create.
- **Password signup** is deliberately closed behind a flag until a sending domain
  is verified. Google OAuth is the working path.

**Not measured — do not put these on a resume:**

| Claim | Status |
|---|---|
| **nDCG@10** for bm25 / vector / hybrid | **Not measured.** No relevance labels exist. The brief's "improved nDCG@10 by 7.2 points" is a *shape example* in the spec, not a result. Demo-query wins are argued from unique-result adoption plus reading titles, and the docs say so. |
| **Queue throughput (jobs/sec)** | **Not measured.** `bench/queue_throughput.py` does not exist. Correctness under concurrency is proven; scaling is not. |
| **Load-test RPS ceiling** | **Not measured.** No k6 script yet. |
| **Cache hit rate** | **Not measured.** No query cache is shipped. |
| **`halfvec` vs `vector` recall/memory delta** | **Not measured.** `halfvec` was used from the start; there is no fp32 comparison. |
| **int8 quantization recall** | **Deliberately deferred** (DECISION-2d). Cosine parity 0.9977 mean / 0.9906 min *is* measured, but cosine parity is not ranking parity. |
| **Sustained encode throughput** | **Does not exist for this hardware.** The band 8.8–12.7 docs/s is one session's within-session spread; the same instrument re-run gave 8.1 vs 13.2 docs/s, **1.63x apart**. Treat it as a quantity that varies 1.6x, not an estimate with an error bar. |
| **Hybrid SQL p50** | **Withdrawn, unreplaced.** The stability gate refuses it. |
| **Hybrid paired speedup** | **Open.** Pairing has not been extended to `search_hybrid()`, so no hybrid before/after ratio is publishable. |
| **Global dedup recall** | Recall is measured **among candidates**. Pairs the cascade never generated are outside the denominator. |

**Two questions parked with explicit triggers**, so they stop reappearing in
every status summary: whether PubMed's specialty mass helps or hurts under
DECISION-2's hard-negative knob (trigger: the post-pull `demo_queries.py`
re-run), and whether a group's cap should bind on its *strictest* contributing
strategy rather than its earliest (trigger: the post-PubMed labeling draw, whose
`acc_abstract_hash_x_title_exact` stratum, n=20, is designed to settle it).

---

## 10. Interview ammunition

The five stories, in the order they land hardest.

**1. "Tell me about a time you found a bug in your own measurement."**
The four-hour cascade. Six commits of attribution — a `LIKE` scan, then
`dd_scored`, then quadratic behaviour inside the blocks — an alarm raised that the
next run would silently re-merge 314 papers, and the real answer was that the
laptop had gone to sleep and frozen the guest clock while host wall-clock kept
running. 23x inflation. The cascade takes 10 minutes. Everything built on the
wrong number was retracted, and three conventions went into the repo so it cannot
recur. **The lesson is not "check your clock." It is that a plausible mechanism is
the most dangerous thing you can find while chasing a wrong number** — every
attribution along the way was *technically true about the code* and irrelevant to
the effect.

**2. "How did you know your hybrid search was actually better?"**
Start by conceding it: there are no relevance labels yet, so nDCG is not measured
and the honest claim is narrower than "better." What *is* measured is which
results each arm finds uniquely and which of those fusion adopts. Then the three
queries: bm25 wins on `reciprocal rank fusion` (exact phrase; vector drifts to
*Rank-Biased Overlap*, a different measure retrieved for being semantically near
"rank" and "fusion"), vector wins on `why medical jargon confuses ordinary
readers` (**bm25 returns zero rows** — Postgres `websearch_to_tsquery` AND-semantics
has a cliff, not a curve), and hybrid wins on `BERT for de-identification of
clinical records`, where **hybrid's #1 was ranked first by neither arm.**

**3. "Your dedup precision is 0.96. How do you know?"**
120 pairs, labeled blind — verdict, rule, similarity and group size all hidden —
stratified across 12 strata, inverse-probability weighted, stratified-bootstrap
CIs, second annotator at κ = 0.905. Then the three things most people leave out:
**the measurement changed the code** (one arm scored 0.684, so its group cap
dropped to 2 and 122 executed merges were unwound on production data);
**the caveat travels** (10 of my own 120 labels changed on review, so the number
rests on labels of which 1 in 12 moved); and **the strata turned out to be
artifacts** of the same attribution the cap depends on, which is written down as a
limit rather than argued away.

**4. "Where did the flattering number come from?"**
Six of them, and the mechanisms are all different: a sample that no longer
exists; a percentile from too few samples; a ratio across two different timing
windows; a ratio contaminated by a component that was not being compared; index
pages preheated by a neighbouring benchmark arm; and a sleeping VM. The one to
lead with is the gradient: **end-to-end speedup arithmetic rewards a slower
encoder** — at embed 8.0 ms the figure is 7.1x, at 6.1 ms it is 8.7x, at 2 ms it
would read 12.0x. Nobody did it and nobody would mean to, but an incentive that
is not written next to a number gets discovered by someone else later. The claim
moved to retrieval-only, which carries no encoder term.

**5. "Why Postgres for all of it? Why not Elasticsearch, Redis, a vector DB?"**
Because the constraint was a fanless 8 GB laptop and the goal was to understand
the mechanisms. `SKIP LOCKED` gives at-least-once delivery with the queue's
position committed *in the same transaction as the data* — Redis cannot do that
without a distributed-transaction story. pgvector puts the HNSW graph beside the
`tsvector`, so RRF is one query with one plan instead of two services and a fusion
layer. The whole serving footprint is 586 MB — **3,354 bytes per paper** — and
every number about it came out of `EXPLAIN ANALYZE` rather than a dashboard.
*And the deliberate exclusions are part of the project:* Redis, Elasticsearch,
Celery and every vector database are absent on purpose, and the reasoning is
written down.

---

## 11. Repo map, and how to reproduce any number here

```
docs/
  sieve-project-brief.md    the spec, 46 KB, written before any code
  progress.md               phase state, what shipped, what is half-done
  decisions.md              17 decision records, in Kishan's words
  findings.md               64 entries: symptom → how found → cause → fix → verified
  sieve-dossier.md          this file
  demo-queries.md           the three measured demo queries
  plans/                    committed EXPLAIN ANALYZE plans + runbooks (8 files)
api/
  main.py  logs.py  stats.py
  search/   bm25.py  vector.py  fusion.py  totals.py  routes.py
  ingest/   http.py  ratelimit.py  openalex.py  arxiv.py  pubmed.py  store.py
  dedup/    rules.py  cascade.py  merge.py  normalize.py
  queue/    claim.py  worker.py  backoff.py  handlers.py
  embed/    onnx_encoder.py  texts.py  backfill.py  runtime.py
  auth/     service.py  routes.py  codes.py  google.py  mailer.py
  collections/  routes.py  bibtex.py  spreadsheet.py
  db/       pool.py  migrate.py  migrations/  (16 forward-only SQL files)
bench/      every number in this file comes from here (~30 scripts + results JSON)
  labels/   hand-labeled relevance judgments and dup pairs
web/src/    React 18 + Vite + TS + Tailwind + TanStack Query
tests/      25 backend test modules
```

```bash
make test          # pytest, in Docker, no host Python needed
make test-web      # vitest, in Docker
make lint          # ruff + mypy, CI's exact commands
docker compose up  # postgres → migrate → api → web, from a clean clone

caffeinate -dimsu python -m bench.paired_speedup     # 7.7x
caffeinate -dimsu python -m bench.ef_at_fixed_depth  # the recall ladder
caffeinate -dimsu python -m bench.dedup_precision --as-shipped   # 0.959 / 0.966
python -m bench.demo_queries                          # the three demo queries
python -m bench.deploy_sizing                         # 586 MB serving footprint
python -m bench.theme_audit                           # 208 contrast samples
```

`make test` runs in Docker on purpose: **green counts no longer depend on
anyone's say-so**, including mine.

---

## 12. What one hour of work would improve this most

1. **Write the README.** The resume links here and there is nothing at the door.
   The architecture diagram (§2.1) and the benchmark tables (§4, §8) are drafted
   and ready to drop in; the prose is yours.
2. **Get the live URL up.** Four accounts, all free tier, everything else built
   and sized.
3. **Re-run `bench/latency.py` at the shipped `ef=160`**, so vector-mode
   percentiles describe the configuration that actually ships.
4. **Run the PubMed pull.** It makes "three sources" true, and the post-pull
   measurement procedure is already written down in advance — which is the part
   that makes it a measurement instead of a re-roll.
