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

## DECISION-2d: fp32 for the corpus encode; int8 deferred, not rejected

**Date:** 2026-07-29

**Decision:** the full encode runs fp32 with length-sorted batching
(length-sorting stays — it's free and measured ~2x). int8 quantization is
deferred to Phase 4 as a measured optimization, not rejected.

**Alternatives considered:** int8 dynamic quantization now (measured
~114 min projected vs ~230 min fp32, cosine vs fp32 mean 0.9977 / min
0.9906 on 1,000 real papers).

**Why rejected (for now):** cosine parity between int8 and fp32 vectors of
the same document does not establish that ranking is preserved, since
ranking depends on relative distances across the corpus and small
perturbations reorder near-ties. The correct metric is Recall@10 of an
int8 index against the fp32 index as ground truth, which I don't have
yet. More decisive: this job runs overnight, so 2 hours versus 4 hours
buys nothing I'm using.

**What would change my mind:** the Phase 4 measurement itself — int8
becomes a measured optimization with a real recall number, and that
measurement requires the fp32 index to exist as the baseline.

---

## DECISION-2e: Hybrid defaults — candidate depth 200, ef_search 600

**Date:** 2026-07-31

**Decision:** mode=hybrid defaults to depth N=200 with ef_search 600. An
explicit ef_search is honored but still auto-raised to >= depth (the
truncation guard); vector mode keeps its own default of 40.

**Measured basis:** vector recall@200 = .9857±.0011 at ef=600 vs
.9431±.0028 at ef=200 (bench/results_ef_at_fixed_depth.json). ef=600's
cost is **below measurement resolution on this hardware** — its 13.6 ms
p50 sits inside ef=200's own [12.5, 19.9] cross-run range, so the two
are indistinguishable. That is the correct framing, not "free" and not
"inside noise": we cannot see the cost, which is different from there
being none.

**Alternatives considered:** ef=200 (leaves 4.3 recall points
unclaimed); ef=800 (+0.4 points for +4 ms — past the elbow); N=500
(18.4 ms SQL p50, and the known-item caveat means the value of deeper N
is unevidenced until labels).

**Prediction to revisit (test in Phase 4):** ef is cheap because bm25's
match-count variance swamps the vector CTE. Once Phase 4 fixes the bm25
tail, ef=600's real cost becomes visible. Test it then, alongside the
N/rrf_k re-tune under nDCG.

---

## DECISION-2f: No corpus top-up — 196,893 stands

**Date:** 2026-07-31

**Decision:** decline the ~25-credit general-nlp top-up that would cross
200,000 papers. The corpus is 196,893 and stays there for Phase 2.

**Alternatives considered:** topping up ~3.5K general-nlp papers to
cross the round number.

**Why rejected:** 196,893 is more credible on a resume than 200,000,
because a precise number reads as counted and a round one reads as
estimated. A general-nlp top-up dilutes the 62.2% specialty share I
deliberately engineered, and specialty pools are already exhausted so
more papers can only be general. 200K was a sizing estimate in the
brief, never a requirement, and 197K clears the threshold that makes
performance work meaningful by 2x. Adding papers so a rounder number
appears in a bullet is presentation-driven, which is the same instinct
as a p99 from 20 samples.

**What would change my mind:** a real retrieval reason to grow the
corpus — new specialty sources in Phase 3 (arXiv, PubMed) grow it for
coverage, not for roundness.

---

## DECISION-3a: Embedding freshness — null the vector where text is written

**Date:** 2026-07-31

**Decision:** unconditional freshness (option B), implemented as
null-on-text-change rather than a hash column. Every stored vector must
be the embedding of that paper's current document_text(). Enforce it at
the point text is written: `ON CONFLICT DO UPDATE` and the merge path
both set `embedding = NULL` when title or abstract differs from the
stored value, and the existing `embedding IS NULL` work queue re-embeds.
Also approved: post-merge HNSW rebuild (36 s measured), and
dedup-before-embedding as the default ordering for Phase 3 ingestion.

**Alternatives considered:** keep the survivor's vector (stale); keep
both vectors (reintroduces the duplicate RRF double-pays); conditional
re-embedding via a stored text hash (option D, ~63 s cheaper).

**Why rejected:** the hash column creates a second invariant every
text-mutating path must maintain, and forgetting it yields a stale vector
the system believes is fresh. Nulling at the write site has no such
failure mode. The 63-second saving from conditional re-embedding does not
buy permanent complexity.

**Measured basis:** twin pairs whose abstracts differ shift their vector
by median 0.0027 (p90 0.0915), while adjacent ranks inside a top-10 are
separated by a median of only 0.00213 — so a stale vector is 1.3x to 43x
the rank spacing, i.e. large enough to reorder results. 47% of exact-
title twin pairs have differing abstracts; all 524 JMIR preprint/
published pairs do.

**What would change my mind:** nothing about the invariant; the
implementation could change if a text-write path appears where nulling is
impractical (e.g. bulk SQL maintenance), in which case that path needs
its own re-embed sweep, not a hash.

---

## DECISION-3b: Merge survivorship — published wins over preprint

**Date:** 2026-07-31

**Decision:** when a merge group contains a published version and a
preprint, the published side wins **title, abstract, and venue**.
`arxiv_id` and `pubmed_id` are kept from whichever side has them, because
a screening tool should cite the published version and still link the
free PDF. `citation_count` takes the **max, not the sum** — summing
double-counts anyone who cited both. Where neither side is clearly
published, fall back to **lowest id** for determinism.

This replaces the owner-predicate placeholder in api/ingest/store.py
(lowest-id linked record writes the text), which existed only to stop two
DOI-linked records overwriting each other on every crawl.

**Alternatives considered:** preprint wins (earlier, more "original");
newest-updated wins; summing citation counts.

**Why rejected:** the measurement settles it — all 524 JMIR
preprint/published pairs rewrote the abstract, and the rewritten one is
canonical: it is the version that passed review and the version a
reviewer will read. Summing citations is simply wrong arithmetic on
overlapping sets.

**What would change my mind:** a source where the "published" version is
a paywalled abridgement and the preprint carries the full text — then
abstract and title would want different survivorship rules, which the
per-field structure here already allows.

---

## DECISION-3c: The dedup measurement, and what it changed

**Date:** 2026-08-01

**Measured** (120 hand-labeled pairs, stratified across 12 strata, blind —
the cascade's verdict, rule, similarity and group size were never shown
during labeling; inverse-probability weighted; stratified-bootstrap CIs):

    precision              0.957   95% CI [0.904, 0.998]
    recall (candidates)    0.973   95% CI [0.959, 0.988]
    F1                     0.965   95% CI [0.938, 0.990]

**Per-stratum precision, merged strata:**

    acc_abstract_hash        n=11  1.000
    acc_title_trgm           n=3   1.000
    acc_preprint_trgm        n=5   1.000
    acc_jmir_doi             n=2   1.000
    acc_title_exact_pair     n=7   0.857
    acc_title_exact_group    n=19  0.684   <- the finding

**Per-stratum miss rate, refused strata** (share of refusals that were
real duplicates):

    ref_enumerated_sibling   n=14  0.000
    ref_part_sibling         n=5   0.000
    ref_size_capped          n=16  0.000
    ref_below_threshold_sameyear  n=11  0.182
    ref_abstract_low_title   n=8   0.250
    ref_below_threshold_preprint  n=6   0.833   <- the Ascle gap, as a population

**Decision:** MAX_GROUP_SIZE drops to 2 for title_exact only, on the
measured 0.684. Every other strategy keeps 8. The 122 already-executed
title_exact groups above the new cap were UNWOUND via the rollback
snapshots — 314 papers restored, routed to dedup_review, 0 errors, 0
orphaned records — which is also the first exercise of reversibility on
production data rather than in a unit test.

**Two results worth keeping separate from the headline:**

1. **The three rules built under scrutiny have a ZERO miss rate.** The
   enumerator rule, the part-sibling rule and the size cap refused 1,617
   pairs between them and not one sampled refusal was a real duplicate.
   Rules added in response to hand-reading did not cost recall.

2. **ref_below_threshold_preprint misses 5 of 6 sampled pairs (0.833).**
   This is the Ascle gap as a population fact rather than one fixture, and
   it is exactly why tuning the preprint threshold to 0.90 to catch Ascle
   was REJECTED: the gap is real and systemic, so it deserves a real fix
   (the mechanical publisher DOI rules, of which jmir_doi is the first),
   not a threshold moved until one known case passes. Fitting a global
   parameter to a fixture would have hidden a population-level problem
   behind a green test.

**The caveat that travels with the precision number:** Kishan corrected 10
of his own 120 labels (8.3%) on review, clustered on sibling and
parallel-variant patterns whose taxonomy only emerged partway through
labeling — so early labels used a weaker rubric than late ones. 0.957
rests on labels of which roughly 1 in 12 changed when re-examined once.
Quote it with the caveat attached (docs/findings.md).

**Alternatives considered:** dropping the global cap to 2 (would refuse
correct merges in strategies measuring 1.000 precision); leaving the cap
at 8 (accepts ~32% error on 122 groups); deleting the bad merges outright
rather than unwinding (loses the audit trail and the ability to revisit).

**What would change my mind:** a second labeling pass with the taxonomy
fixed in advance. It would move precision, probably by less than the first
review did, and title_exact's 0.684 is the number most worth re-measuring
since it drove a rule change off n=19.

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
