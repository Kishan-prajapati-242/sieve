# Decision Records

Each record is written in Kishan's words, at the moment the decision was made.
Format follows the template at the bottom of this file.

---

## DECISION-1: Backend language and framework

**Date:** 2026-07-28

**Decision:** Python + FastAPI for the entire backend.

**Alternatives considered:** Node/Express API with a Python sidecar for embeddings.

**Why rejected:** the embedding model (MiniLM via ONNX) is Python-native, so Node
forces two services, a network hop between them, two deploy targets, and two
dependency sets. The operational cost is real and the benefit is only that I
already know Express. Secondary reason: I have MERN experience already and want
backend range beyond it.

**What would change my mind:** if the frontend and backend needed to share
substantial validation or type logic, or if embeddings moved to a hosted API and
Python stopped being required in the request path.

---

## DECISION-1b: Corpus sort order (how a capped crawl picks its works)

**Date:** 2026-07-29

> Note (2026-07-29): the QUERY TABLE this decision stratified (concept
> filter + four phrase queries) is superseded by DECISION-2 — the crawl
> now runs on topic filters. The sort-order decision itself stands
> unchanged: every query, topic or phrase, still stratifies by year.

**Decision:** Stratify by year: split each query's budget evenly across the
last ~10 publication years plus one pre-2016 "classics" slice, and sort by
citations within each year slice.

**Alternatives considered:** keep the global `cited_by_count:desc` sort;
sort by `publication_date:desc`.

**Why rejected:** citation sort biases toward older famous work, since
citations accrue with age — my real queries target recent LLM-era work, and
a fame-biased corpus makes BM25 look better than it is and understates what
dense retrieval adds in Phase 4. Pure recency has the opposite problem: NLP
publishes at such volume now that the corpus would be a one-to-two-year
slice — no classic papers, no meaningful year filter, and no citation
quality floor. Year stratification keeps recent coverage my queries can
actually hit while papers only compete on citations against their own year.

**What would change my mind:** if the Phase 4 eval queries end up mostly
targeting classic-era topics, the coverage argument against citation sort
weakens and its simplicity starts to win; if I wanted the corpus to mirror
real publication volume instead of even temporal coverage, that is
approximately the recency sort.

---

## DECISION-1c: What never becomes a paper, and what stays flagged

**Date:** 2026-07-29

**Decision:** Exclude six OpenAlex work types at ingest — paratext,
editorial, erratum, supplementary-materials, peer-review, retraction
(measured: 144 rows, 0.5% of a 26.4K corpus, including proceedings volumes
that ranked beside their own member papers with identical abstracts). Raw
source records are kept for the audit trail; skip counts are reported per
type in run stats. Existing junk-type papers rows deleted the same way.

Separately: do NOT exclude is_retracted=true papers (16 in corpus, only 1
of which the type filter would catch). Keep them, add an is_retracted
boolean on papers, surfaced in search results so the UI can show a
retraction warning.

**Alternatives considered:** also excluding is_retracted=true papers, as
originally proposed.

**Why rejected:** this is a screening tool: someone doing a systematic
review needs to see a retracted paper to exclude it deliberately and check
what cites it. Silently dropping it is worse.

**What would change my mind:** if sieve stopped being a screening tool —
in a general-audience search product, surfacing retracted work without the
screening context could mislead more than it protects.

---

## DECISION-1d: Whole stack in Docker, zero host dependencies

**Date:** 2026-07-29

**Decision:** The entire stack runs in Docker Compose — the frontend
included, as a node:20-alpine service running the Vite dev server with hot
reload via a bind mount, exposed on 5173. Zero host dependencies beyond
Docker itself. Chosen for reproducibility and to avoid host toolchain
drift.

**Alternatives considered:** installing Node on the host and running Vite
there, like most React setups.

**Why rejected:** I don't have Node installed locally and don't want it. A
host Node means nvm, a version that drifts from what CI and teammates run,
and a setup step the README has to explain. One `docker compose up` from a
clean clone should produce the whole working system.

**What would change my mind:** if editor tooling becomes the bottleneck —
TypeScript IntelliSense wants a local node_modules, which only a host npm
install can provide. If frontend work gets heavy enough in Phase 4 that
red-underlined imports slow me down more than toolchain drift would, a
host Node install is back on the table.

---

## DECISION-2: Corpus composition v2 — migrate to topic filters

**Date:** 2026-07-29

**Context (measured, 2026-07-29):** the 26.2K corpus was ~75% general NLP,
and the specialty phrase queries were result-capped, not budget-capped
(clinical-nlp maxes at ~919 works total). Topping up to 50K under the old
table would have made it ~87% general. OpenAlex has deprecated concepts
("/concepts → Use /topics", developers.openalex.org); topic filters are
list-class, 14x cheaper per work than phrase search (182 vs 13
works/credit). Measured topic pools (works with abstracts): T10181 NLP
Techniques 376K; T11710 Biomedical Text Mining 224K; T10350|T13702
EHR/ML-in-healthcare 170K; T13629 Text Readability and Simplification
49K; T12488 Mental Health via Writing 54K. There is no clinical-NLP
topic, and topic intersections cannot substitute (works carry max 3
topics: NLP∩EHR matches 15 works).

**Decision:** migrate the crawl to topics, composition for 200K:
general-nlp T10181 60K (30%), biomedical-clinical-text T11710 70K (35%),
clinical-informatics T10350|T13702 20K (10%), text-simplification T13629
25K (12.5%), mental-health-nlp T12488 25K (12.5%) — plus the four
existing phrase queries, kept as a small high-precision core. Keep the
existing 26.2K papers; upserts converge.

**Alternatives considered:** the proposed 40K (20%) clinical-informatics
share, reallocated to T11710; staying on concept + phrase queries.

**Why rejected:** T10350 and T13702 are largely not NLP, so at 20% of the
corpus they're not random hard negatives, they're clinical papers lacking
simplification vocabulary. BM25 rejects them on exact terms while dense
retrieval pulls them in on clinical semantics, which could make hybrid
look worse than BM25 in Phase 4 and undercut the exact comparison the
evaluation exists to make. Some difficulty is good, a fifth of the corpus
is not. T11710 is actual text mining, so it's on-topic mass. Staying on
phrase queries fails on both counts: result-capped below the specialty
targets, and 10x the credits per page.

**What would change my mind:** if Phase 4 shows hybrid winning too easily
— hard negatives that BM25 and dense both have to work for are what make
the eval mean something, and clinical-informatics share is the knob.

---

## DECISION-2b: What goes into the embedding, and which model encodes it

**Date:** 2026-07-29

**Decision:** (a) title + abstract concatenated, degrading to title-only
for the 28 abstract-less papers; (b) a 512-token window; (c)
bge-small-en-v1.5 (33M params, 384 dims, CLS pooling, query-side
instruction prefix), single vector per paper, no chunking. This supersedes
the stack table's all-MiniLM-L6-v2 pin.

**Measured (2026-07-29, full 196,893-paper corpus, real WordPiece
tokenizer):** title+abstract median 243 tokens, p95 533; **45.3% exceeds
256 tokens but only 5.6% exceeds 512**. MiniLM as configured truncates at
256 (`sentence_bert_config.json`), was trained at 128, and its decoded
truncation tails are Results/Conclusions — structured abstracts put the
payoff last, so a 256 cap cuts the retrieval-relevant text from nearly
half the corpus. The >512 tail is junk-skewed (109 papers over 4,096
tokens are mostly data payloads, not prose).

**Alternatives considered:** MiniLM at 512 (config-legal, but past both
its configured cap and far past its trained length); title-repeated input
(grows every length for no measured benefit); chunking at 256 (+115,322
vectors, +59%, plus per-paper MAX aggregation in every vector query — to
protect content a 512 window keeps anyway); larger models
(nomic-embed-text, BGE-M3 — break the halfvec(384) schema and the 8 GB
budget).

**Why bge-small:** ~10-point MTEB retrieval gap over MiniLM (BEIR avg ≈52
vs ≈42), natively trained at 512 tokens, same 384 dims so the schema and
planned HNSW DDL are untouched. Cost: 12 layers vs 6 — roughly 4x compute
per document at 512 vs 256 — and a query-side instruction prefix
("Represent this sentence for searching relevant passages: ") that MUST
be applied to queries and NEVER to documents; getting it wrong degrades
retrieval silently, so the contract is pinned by a test before any encode.

**What would change my mind:** if the 1,000-paper throughput benchmark
projects the full encode beyond hours on the fanless 8 GB M1 Air, the
fallback is MiniLM at 512 or bge-small at 256 — remeasure, don't assume.

---

## DECISION-2c: Dataset-type records stay in the corpus

**Date:** 2026-07-29

**Decision:** do NOT add `dataset` to the excluded types (15,517 papers,
7.9% of corpus). Measured first, per the rule "if they don't pollute
results, we keep them and move on": across six eval-domain queries
(Kishan's real query first), **0 of 120 top-20 slots** were dataset-type.
Token lengths confirm the noise theory: dataset abstracts are typically
SHORTER than articles (median 129 vs 286 tokens); the data-payload tail
(>1,024 tokens) is 1.9% of datasets (~293 records) vs 0.8% of articles.

**Alternatives considered:** excluding type=dataset outright, like the
DECISION-1c junk types.

**Why rejected:** the problem isn't the type, it's that some records have
an "abstract" that is a data payload rather than prose. Excluding
type=dataset throws away legitimate research artifacts (Med-EASi and
Cochrane are datasets, and a screening tool should surface them) to
remove noise that is detectable independently by abstract shape.

**What would change my mind:** if payload-shaped abstracts start ranking
in real results — the fix would target abstract shape (token length /
prose-ness), not the type field.

---

## Template

```text
## DECISION-N: <short title>

**Date:** YYYY-MM-DD

**Decision:** <what was chosen>

**Alternatives considered:** <what else was on the table>

**Why rejected:** <the reasoning, in Kishan's words>

**What would change my mind:** <the conditions under which this gets revisited>
```
