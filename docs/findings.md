# Findings

Diagnosed bugs, one entry each, newest last: symptom, how it was found,
root cause, fix, verified before/after. "How it was found" is the
load-bearing line — it records which instrument caught what the tests
missed, which is usually the real lesson.

---

## 2026-07-29: Exhausted slices bought a page just to throw it away

**Symptom:** a `--limit 5` smoke run fetched 5 works for 64 credits.
Expected ~32: two 1-credit concept pages plus three 10-credit search pages.

**How it was found:** credit instrumentation, not a test. The first live
run of the per-query credit report (added that same day to explain a
~790-credit reconciliation gap) showed every query costing exactly double
its predicted spend — nlp-concept 4 credits for 2 works, each specialty
20 credits for 1 work. The doubling pattern pointed straight at a
paid-request leak; all 55 tests were green throughout.

**Root cause:** the slice-budget check sat at the top of the work loop,
which means it ran when the page generator *resumed*. With
`per_page == slice_budget` the generator had already drained its page, so
resuming it fetched — and was billed for — the next page, whose works were
then discarded by the break. Every budget-exhausted slice paid for exactly
one unread page; at 10 credits per search page across 55 slices, the run
cost double.

**Fix:** check the budget after storing each work, so the loop breaks
before the generator can resume (commit `73391c3`). Regression test:
`test_exhausted_budget_never_fetches_the_next_page` counts requests at the
transport and asserts a one-work budget makes exactly one request.

**Verified before/after:** identical `--limit 5` runs: 64 credits before,
32 after — both reconciling exactly with the server's `credits_remaining`
delta (9055→8991, then 8991→8959).

---

## 2026-07-29: Ghost records — paratext volumes rank beside their papers

**Symptom:** in the first real search output, result 8 was the proceedings
volume for result 1 (DOI `...2025.sciprodllm-1` vs `...sciprodllm-1.1`),
carrying a character-identical abstract to a member paper. Trigram dedup
can never catch it: the titles are completely different.

**How it was found:** human eyeballs on real output. No test or instrument
flags a result that is technically valid and semantically wrong.

**Measured (full-corpus scan, 26,483 records, 265 credits):** candidate
junk types: paratext 84, editorial 45, erratum 7, supplementary-materials
4, peer-review 3, retraction 1 — 144 papers (0.5% of corpus). Separately,
`is_retracted` flags 16 papers (retracted articles keep their original
type — the type filter alone misses 15 of them). Exact-duplicate
abstracts: 560 groups covering 1,273 papers, of which only 18 are
candidate junk types — the rest are overwhelmingly preprint/article twins
of the same paper (391 preprints in dup groups), which is the Phase 3
dedup cascade's job, not a type filter's.

**Fix (DECISION-1c, Kishan):** ingest-time skip of the six junk types,
counted per type in run stats; raw records kept for the audit trail.
is_retracted papers deliberately NOT excluded — kept and flagged
(`papers.is_retracted`, surfaced in search results), because a screening
tool must show retractions for deliberate exclusion. The dup-abstract
twins stay for the Phase 3 cascade (exact-abstract-hash is a candidate
cascade step, noted in progress.md).

**Verified before/after:** papers 26,378 → 26,237 (141 junk-type papers
deleted: 81 paratext, 45 editorial, 7 erratum, 4 supplementary-materials,
3 peer-review, 1 retraction; the other 3 of the 144 had never derived a
paper). 0 mixed-parent papers, 0 merges affected, 172 raw records now
deliberately unlinked, 16 papers flagged is_retracted. The original ghost
(the sciprodllm proceedings volume) is gone from results; its member paper
remains at rank 1.

---

## 2026-07-29: Venue was null for a third of the corpus (92% of ACL)

**Symptom:** `venue` null for essentially every ACL Anthology paper (DOI
prefix 10.18653) — measured 8,295 of 26,378 papers null overall (31%),
3,154 of 3,439 ACL papers (92%).

**How it was found:** user report from real search output; quantified with
two SQL counts, diagnosed by refetching full work records for two ACL
papers and walking every field for venue-bearing strings.

**Root cause:** extraction read only
`primary_location.source.display_name`. ACL Anthology works (and many
others) have no linked Source entity anywhere — `source` is null in
`primary_location` and in every `locations[]` entry — but the venue string
sits in `raw_source_name` (publisher-deposited free text), which we never
read; `locations` wasn't even in SELECT_FIELDS.

**Fix:** `extract_venue()` fallback chain — canonical `source.display_name`
(primary, then locations[]) first, `raw_source_name` (same order) second —
used by ingestion for new records; existing null-venue papers backfilled
from the same 265-request scan via the same function.

**Verified before/after:** null venues 8,295 → 471 (94% recovered);
ACL-specific 3,154 → 24 (99.2%).

---

## 2026-07-29: One paper, three results — the Phase 3 dedup baseline

Not a bug to fix now (Kishan: phase gate first) — the observed motivation
for the Phase 3 dedup cascade, with a measured baseline to beat.

**Symptom:** Kishan's first real query on the functionally complete Phase 1
stack, "clinical text simplification": results 13/14/15 are the same paper
three times — "Evaluation of large Language models on pediatric asthma…"
(2026), once in BMC Medical Informatics and Decision Making (3 citations)
and twice on Figshare (0 each), author lists differing only by
capitalization ("jie wu" vs "Jie Wu"). Results 19/20 are the Ascle preprint
and its published JMIR version, differing in title suffix ("(Preprint)" vs
": Development and Evaluation Study") and citation count (1 vs 14).

**How it was found:** human eyeballs on real output, verified by rerunning
the query and running exact-key SQL over the 20 returned ids.

**Root cause:** Phase 1 dedup is DOI-exact only, and every row above has a
distinct DOI — Figshare mints versioned DOIs (`…figshare.c.8354879` vs
`…c.8354879.v1`), and JMIR gives preprints their own
(`10.2196/preprints.60601` vs `10.2196/60601`).

**Measured baseline (top 20, this query):** 2 duplicate groups; 5 of 20
results sit in a duplicate group (25%); 3 of 20 are redundant copies (15%);
17 unique papers. Which future cascade step catches what, checked against
the actual rows: the asthma triple shares an identical `title_norm`
(exact-title catches all 3), but only the two Figshare rows share an exact
abstract — the BMC copy's abstract text differs, so abstract-hash alone
merges 2 of 3. The Ascle pair evades every exact key (DOI, title_norm,
abstract) and is trigram territory — or a source-specific DOI rule, since
`10.2196/preprints.N` → `10.2196/N` is mechanical.

**Two things this measures that the brief did not anticipate (Kishan):**
duplicates occur within a single source — all three asthma rows are
distinct OpenAlex works (W7128481684, W7138342378, W7138370075) — not only
across sources; and the preprint/published pair needs a version-preference
rule, not just a merge, since the published version has the real citation
count.

**Fix:** deferred to Phase 3 by design. The "after" for this entry is this
same query rerun post-cascade; the baseline to beat is 3/20 redundant.

---

## 2026-07-29: 7.7% of papers have no DOI — a Phase 3 input, not a bug

**The number:** 196,893 papers vs 181,635 distinct DOIs (doi is UNIQUE, so
that equals papers-with-DOI): **15,258 papers (7.7%) have no DOI** —
verified directly with `WHERE doi IS NULL`, same count.

**How it was found:** Kishan, comparing the two totals in the post-pull
composition report.

**Why it matters (Phase 3 input):** DOI-exact is the dedup cascade's first
and cheapest step, and it cannot reach these papers at all. They fall
through to source-ID matching and trigram fuzzy — so the cascade's
measured precision must be reported for the no-DOI stratum separately,
not just overall, or the easy DOI wins will mask fuzzy-step quality.
Recorded before Phase 3 design so the bench (bench/dedup_precision.py)
stratifies by DOI presence from the start.

---

## 2026-07-29: The credit meter had a blind spot for entity search

**Symptom:** during the topics investigation, 8 requests to
`/topics?search=…` cost a server-verified 80 credits while the RequestMeter
predicted 8 — off by 10x, the exact class of error the meter was built to
prevent.

**How it was found:** the bracketing habit, not a test: every probe run
reads the free `/rate-limit` endpoint before and after, and the server
delta (2007 → 2087) disagreed with the meter's own total on the spot.

**Root cause:** `billing_class()` only recognized search-class requests by
`.search:` inside a works `filter` param. A bare `search=` query param —
entity search on /topics, /concepts, etc., and full-text `search=` on
/works — also bills as search class, and the classifier fell through to
list (1 credit).

**Fix:** any request carrying a `search` param is search-class, in addition
to the `.search:` filter rule. Regression test prices `/topics?search=`
and `/works?search=` at 10 and a plain topics.id filter at 1.

**Verified before/after:** the discovery run's 8 entity searches: meter
said 8 credits before the fix; repriced under the fixed classifier they
are 80, matching the server's measured delta exactly. Ingest crawls never
used entity search, so no earlier run report was affected.

---

## 2026-07-31: The dedup cascade was quadratic as first written

**Symptom:** the cascade's first draft ran for **11 minutes without
completing a single strategy** on 197K papers, and had to be killed.

**How it was found:** running it. `pg_stat_activity` showed three parallel
workers 650 seconds into the same `count(*)` over one strategy.

**Root cause, two independent ones:**
1. Every exact-key strategy was written as a self-join —
   `FROM papers a JOIN papers b ON md5(a.abstract) = md5(b.abstract)` —
   on expressions with no index. That is O(n^2): ~3.9e10 candidate row
   comparisons for a step whose answer is a grouping.
2. The trigram step's `%` operator uses `pg_trgm.similarity_threshold`,
   which **defaults to 0.3**. So it generated the candidate set for
   similarity >= 0.3 and only then filtered to 0.92 in the WHERE clause —
   paying for orders of magnitude more candidates than the query wanted.

**Fix:** exact strategies became `GROUP BY key HAVING count(*) > 1`,
fanning pairs out from each group's first member (a star, not a clique).
Union-find rebuilds the identical connected component from the star, so
the result is unchanged while the work drops from quadratic to a single
grouped pass — **seconds instead of never finishing**. The trigram step
now sets `pg_trgm.similarity_threshold` to the sweep value so the GIN
index prefilters at the real threshold. Migration 0008 adds the four
missing indexes (arxiv_id, pubmed_id, md5(abstract), title_norm+year),
which matter for the per-record cascade ingestion will call.

**The general lesson:** "find all pairs matching on a key" is a GROUPING,
not a join. Writing it as a join asks the database for the cross product
of the answer.

---

## 2026-07-31: NULL embeddings are now routine, and invisible

**The risk:** DECISION-3a (null the vector wherever text moves) plus
dedup-before-embedding made NULL embeddings a NORMAL state rather than an
anomaly. Right now 95 arXiv papers sit un-embedded by design.

**Why it needs surfacing:** a NULL-embedding paper is invisible to vector
search and contributes nothing to fusion, while still appearing via bm25.
So the failure mode is not an error — it is *slightly worse results*,
which nothing alerts on and no test catches. A refresh storm or a stalled
backfill would degrade retrieval quietly for as long as nobody looked.

**Fix:** GET /api/stats reports embedding coverage — total, embedded,
NULL, and the NULL count split by cause where the cause is knowable
(never embedded vs. invalidated by a text change vs. awaiting dedup).
Making it a number on an endpoint means the next stats-driven README
table shows it too.

---

## 2026-07-31: The drift verification was vacuous (pattern, 4th instance)

**Symptom:** I reported "title drift 0, citation drift 0, missed
retraction flags 0 across all 196,893 papers" as evidence about the
refresh path. It is evidence of nothing.

**Why it cannot work:** a drift count of 0 cannot distinguish "refresh
propagates correctly" from "refresh is a no-op and no upstream value
changed." All 23,102 refreshes happened inside ONE 24-minute pull, where
no OpenAlex title or citation count would have moved anyway. Both
hypotheses predict exactly the same observation, so the measurement had
no power to separate them. (The code reading that accompanied it — that
store_work returns "refreshed" before touching papers — is what actually
established the conclusion. The SQL added false confidence, not
evidence.)

**The pattern, now four instances** (Kishan spotted every one):
1. the durability test that read through the writer's own connection, so
   it could not fail on uncommitted work (2026-07-30);
2. p95 = p99 from 20 samples, where both are the max by construction;
3. the fusion convergence proxy measured against the deepest ranking
   tested, which reaches 1.0 by construction;
4. this drift count, where both hypotheses predict zero.
The shared shape: a check whose passing condition is guaranteed by its
own construction, independent of the property under test. The habit that
catches it is asking "what result would falsify this?" BEFORE running it
— if no observable outcome would, it is not a test.

**The real test (tests/test_refresh.py):** mutate a stored
source_records.raw — change the title and bump cited_by_count — then run
ingest again and assert on what papers does. That test fails against the
old no-op refresh and passes against the propagating one, which is the
distinction the drift count could not make.

---

## 2026-07-31: Refresh staleness — confirmed, but not where expected

**The claim under test (Kishan):** the 200K pull refreshed 23,102
papers, so abstracts do get rewritten on re-crawl; any future ingestion
that refreshes a paper leaves its vector stale and silently wrong, at a
magnitude that reorders results.

**Confirmed as a latent bug, with one correction to the mechanism.**
Checked the code as it stands: `store_work()` returns "refreshed" as soon
as the source record already has a paper_id — it updates
`source_records.raw` and **never touches the papers row at all**. So
today the derived paper's title/abstract/citations are frozen at first
derivation, and the vector matches the frozen text. Verified across all
196,893 papers: title drift 0, citation drift 0, missed retraction flags
0 — the corpus is internally consistent right now, and NOT because the
pull finished before the encode (the encode reads papers, which refresh
never rewrites).

**Why it is still the right thing to fix:** the invariant holding is an
accident of an unrelated limitation. The refresh path is knowingly
incomplete — citation counts and retraction flags go stale by design
today — and the moment refresh starts propagating text (which Phase 3's
multi-source merge REQUIRES, since a merge picks one side's title and
abstract), every refreshed paper's vector becomes stale with no signal.
The staleness magnitude is measured: median 0.0027 shift vs a median
adjacent-rank gap of 0.00213, i.e. 1.3x-43x the spacing that decides
rank order.

**Fix:** DECISION-3a — null the embedding at every text-write site, let
the `embedding IS NULL` queue re-embed. Not yet implemented; the
correction above means it is preventive rather than remedial, and no
back-fill sweep of existing rows is needed.

---

## 2026-07-31: The bm25 AND-semantics cliff

**Symptom:** "reducing the reading difficulty of health leaflets for
people with low literacy" returns **zero rows** in bm25 mode — the
vector mode's top-4 for the same query are precisely on-topic.

**How it was found:** the Phase 2 gate's demonstration queries; this was
the "vector wins" example, and the zero made it starker than expected.

**Root cause:** websearch_to_tsquery ANDs every term — a document must
contain all of them to match at all. It's a cliff, not a gradient: one
missing term takes a query from ranked results to nothing.

**Two consequences, recorded for Phase 4:** (1) bm25 mode alone has a
failure CLASS on exactly the long natural-language queries users
actually type; (2) hybrid silently degenerates to vector-only for those
queries, since RRF receives one input — no error, no signal, just a
missing ranker.

**Fix:** none now, by instruction. Phase 4 target: term-drop relaxation,
OR-fallback on empty, or query preprocessing — evaluated under nDCG.

---

## 2026-07-31: The p95 tail is partly a benchmark artifact

**Amends the fusion-tail entry below.** The widest query in the tail
diagnosis is the corpus title "Results" — 81,489 tsquery matches, 935 ms
— which no user would ever type. The bm25 match-count tail driver is
real (Pearson 0.663 across 520 queries), but its measured MAGNITUDE is
inflated by degenerate title-derived queries in the benchmark set: the
published p95 numbers are conservative for realistic query traffic.
(Same known-item caveat as the recall numbers, cutting the other way —
titles inflate recall but also inflate the bm25 tail.)

---

## 2026-07-31: The fusion tail is the bm25 CTE ranking every match

**Symptom:** hybrid p95 behaves as a floor plus a slope: across the depth
sweep, p95 went 27.3→39.9 ms (1.46x) while p50 went 5.5→18.4 (3.3x) —
roughly 5% of queries cost ~27 ms regardless of candidate depth.

**How it was found:** Kishan, from the p95/p50 ratio falling from 5.0x at
N=20 to 2.2x at N=500 — a depth-independent cost component. Hypothesis:
the bm25 CTE computes ts_rank_cd and sorts over EVERY tsquery match
before LIMIT N, so its cost tracks matched-document count, not N.

**Confirmed (bench/ef_at_fixed_depth.py, N=200, per-query mean SQL
latency vs bm25 match count):** Pearson r = 0.663; the extremes are the
story — the widest query (a real corpus title, "Results") matches
**81,489 documents and costs 935.4 ms mean**, the narrowest (0 matches)
costs 12.8 ms. Spearman is only 0.293 because the median query matches
just 1 document (websearch AND-semantics on long titles): the
correlation is entirely tail-driven, which is exactly what a p95-floor
looks like.

**Fix:** none now, by instruction. Logged as the fusion tail driver and a
Phase 4 target. Candidate levers for then: query-dependent depth for the
bm25 CTE, a cheaper pre-rank proxy before ts_rank_cd, or accepting the
tail and documenting it — measured against nDCG once labels exist.

---

## 2026-07-31: A ratio across two different windows is not a speedup

**Symptom:** "end-to-end only ~5.5x" — computed as 55 ms (exact scan,
SQL-ONLY window) over 9.9 ms (vector, END-TO-END window including the
7.6 ms embed). An exact-scan implementation of the same endpoint would
also embed the query, so its end-to-end p50 is 62.6 ms and the honest
ratio is **6.3x**.

**How it was found:** Kishan, from the window definitions the previous
fix had just made explicit.

**Root cause:** the timing_window discipline covered measurements but not
DERIVED numbers — a ratio silently mixed windows one message after the
windows were formalized.

**Fix:** bench/harness.py speedup() takes both windows and raises on
mismatch; the corrected figures state their window (retrieval-only ~24x,
end-to-end 6.3x).

**Worth noting:** unlike the fabricated-mechanism footnote and the
favorable p99, this error ran CONSERVATIVE — it understated the win. The
direction was luck; the class of error is the same, and the guard doesn't
care which way it points.

---

## 2026-07-31: Publishing the favorable end of a 4x spread

**Symptom:** the corrected baseline published warm p99 = 95.8 ms as the
official number — the LOW end of a 95.8-406.9 range observed across
same-day runs. Same species as the 20-sample p99: a number more
favorable than the evidence supports.

**How it was found:** Kishan, comparing the published point against the
reported spread in the same message.

**Root cause:** single-run percentiles. Each run is one draw from the
VM's environmental noise; the last draw happened to be a good one and
last-writer-wins made it official.

**Fix (range over pooling, deliberately):** pooling samples across runs
would assume they draw from one distribution, but the tail events are
environmental and differ between sessions — pooling would repeat the
cold/warm blend error at the run level. Instead bench/harness.py gained
across_runs(): every percentile gets its point estimate (median across
>= 3 runs) only when max/min across runs <= 1.3; otherwise None plus the
observed per-run range. The rule is uniform, not p99-special-cased. The
method record now carries p99_stability, answering the Phase 4 question
directly: before/after latency claims on this hardware target p50/p95;
any p99 claim must be a multi-run range.

**Verified:** the gate fired on real data — one session gated p99 to
range [83.8, 147.6] while passing p50 (53.2, spread 1.10x) and p95
(68.5, 1.25x). Caveat recorded: back-to-back runs share environment, so
a within-session gate can legitimately pass (a later session produced
p99 76.2 across three runs) while cross-session spread stays 4x — which
is exactly why the method record forbids point-estimate p99 claims
regardless of the gate. Prior published numbers preserved as
superseded_v1/superseded_v2 in the results file.

---

## 2026-07-31: The footnote that invented a mechanism

**Symptom:** the corrected-baseline report attributed a warm p50 shift
(61.9 → 57.0 ms) to "title-derived queries embed shorter texts."
Impossible on its face: a halfvec(384) distance costs the same regardless
of source text length, so the explanation could only hold if embedding
time were inside the timing window. It is not — every query vector is
precomputed before the timed loop; the window is the psycopg
execute()+fetchall() round trip only.

**How it was found:** Kishan, from the physics: the claimed mechanism
requires a window boundary the code doesn't have.

**Root cause:** a causal claim in a report that was never checked against
the measurement's window. The number was fine; the story attached to it
was fabricated.

**Fix, measured (six 600-sample runs, forced seq scan, one container
session):** identical-input rerun noise is ~2.7 ms at p50 (61.2 vs 58.5);
sequential eval-only vs titles-only runs differed 6.5 ms BUT in the
opposite direction of the footnote's claim — and when the two groups are
interleaved within one run so thermal/order drift hits both equally, the
gap collapses to **0.1 ms** (63.8 vs 63.7). Verdict: query composition
has no effect on exact-scan latency; the 61.9 → 57.0 shift was
run-to-run/environmental noise and is now attributed to nothing else.
Also observed and worth stating: p50 is stable across runs (57-64 ms) but
the tail is not (p99 ranged 95.8-406.9 ms across today's runs at n=600)
— on this VM, tail claims need multi-run aggregation.

**Structural fixes:** method_record() now REQUIRES a timing_window field
— a number without its window is not a measurement; and the API's took_ms
decomposes into embed_ms / retrieve_ms / serialize_ms (embed is null for
bm25; in vector mode it is the fixed floor no index can reduce, and it
gets named). Framework JSON encoding runs after the handler and is
documented as outside took_ms.

---

## 2026-07-31: A percentile computed from 20 samples is not a percentile

**Symptom:** the first exact-scan baseline reported p95 = p99 = 98.7 ms.
With n=20, nearest-rank p95 is the 19th value and p99 rounds to the 20th
— both are just the max wearing a percentile's name. Separately, p50 61.9
vs max 98.7 was a 1.6x spread on identical work (every query scans all
196,893 rows), pointing at blended cold/warm cache states in one sample.

**How it was found:** Kishan, reading the results file — the two
"different" percentiles printing the same number is the tell.

**Root cause:** the script computed percentiles with no minimum-sample
guard and measured whatever cache state it happened to start in. Nothing
enforced the difference between "a number" and "a measurement."

**Fix:** bench/harness.py, now shared by every future latency script: a
percentile is reportable only when >= 5 samples lie beyond it (p99 needs
n >= 500, else it reports None and says why); repetitions interleave;
warmup iterations are discarded; cold and warm cache are measured
separately (cold = postgres restart + VM page-cache drop per single-shot
sample, host-orchestrated since it cannot be forced from inside the
container); every result carries a method record. The baseline was
re-measured with the seq scan FORCED (enable_indexscan/bitmapscan off,
EXPLAIN-verified) since the HNSW index now exists; v1 numbers stay in the
results file marked superseded.

**Verified before/after (corrected baseline, 600 warm samples = 120
distinct queries x 5 interleaved reps):** warm p50 57.0 / p95 83.3 /
p99 136.7 ms — the REAL p99 is 38% worse than the fake one, which was
hiding the tail, exactly the failure mode the guard exists for. Cold
cache (6 single-shot cycles): 1,039-1,456 ms, median 1,126 — a 20x
cold/warm gap that the blended v1 sample averaged into invisibility.

---

## 2026-07-31: Short benchmarks don't capture thermal throttling

**Symptom:** the full 196,893-paper encode took **10+ hours** against a
248-minute projection from the 1,000-document container benchmark —
roughly **2.4x** the projected wall clock.

**How it was found:** Kishan, comparing the completed overnight run
against the benchmark projection.

**Root cause:** a 75-second benchmark runs at burst clocks; the fanless
M1 Air throttles under sustained all-core load, and a multi-hour ONNX
encode is exactly that. The projection was honest arithmetic on a
measurement that could not see thermal behavior. (Directionally known —
the projection was already labeled "assumes no throttling" — but the
magnitude, 2.4x, was not.)

**Fix:** none to code — resumability (proven 2026-07-30) is what made a
10-hour unattended run safe. Two consequences recorded instead: (1) any
future projection from a short benchmark on this hardware carries the
measured ~2.4x thermal factor until a sustained measurement replaces it;
(2) DECISION-2d's deferred int8 became materially more interesting — at
2x throughput, int8 would have meant ~5 hours, not 10. It still waits for
its Recall@10 measurement against the now-existing fp32 index (the
baseline DECISION-2d required).

**Verified before/after:** n/a — this entry IS the measurement: 248 min
projected, 10+ h sustained, factor ~2.4x.

---

## 2026-07-30: The resumability test that couldn't fail

**Symptom:** the live kill-proof (Kishan's requirement before any full
encode): SIGKILL to the embedding backfill after its log reported
"embedded 2304". Expected 2,304 durable rows; the database had **0**.
Meanwhile the unit test for exactly this scenario was green.

**How it was found:** a real `docker kill`, demanded precisely because "an
unresumable multi-hour job that dies at hour 5 costs me a night." No test
caught it; the first proof attempt (where the kill failed to fire and the
run finished cleanly) even showed 5,000 rows — false reassurance, because
clean exits commit.

**Root cause, two layers.** Code: psycopg connections default to implicit
transactions, so with a default connection an already-open transaction
makes `with conn.transaction():` a SAVEPOINT — every per-batch "commit"
rode one giant transaction that died with the process. Test: the kill
simulation verified through the writer's own connection, and a connection
always sees its own uncommitted work — the test could not distinguish
durable from transaction-local, so it passed against broken code. The
ingest loop had the same latent defect: the 200K pull's
"crash loses at most one work" promise was false the whole time, masked
because every run ended cleanly (QuotaExhausted is caught, clean exit
commits at connection close).

**Fix:** job entrypoints connect with `autocommit=True`, making each
`conn.transaction()` block a real durable commit; `backfill()` and
`ingest()` now REFUSE default-mode connections with a loud ValueError.
Tests verify durability from a second connection, and the dying writer's
connection is closed uncommitted, as SIGKILL leaves it.

**Verified before/after:** against the pre-fix code, the new guard and
cross-connection tests fail (verified by stashing the fix); after it, the
whole suite passes and the repeated LIVE kill-proof holds: killed at
2,304 rows → 2,304 durable rows survive on a fresh connection, resume
completes to 5,000, pre-kill vectors byte-identical (see progress.md).

---

## 2026-07-29: Score ties — invariant existed, proof did not

**Symptom:** results 7 and 8 carried identical ts_rank_cd scores, raising
the Phase 4 concern: unstable tie order breaks keyset pagination at page
boundaries.

**How it was found:** reading real search output. Code inspection showed
`ORDER BY score DESC, id` had shipped with the endpoint (api/search/
bm25.py), so the ordering was already deterministic — but no test created
an exact tie, so nothing protected the invariant from a future edit.

**Root cause:** none in code; a coverage gap. An untested invariant is one
refactor away from being a bug with a delayed fuse.

**Fix:** regression test seeding two textually identical papers (identical
fts vectors, identical scores) asserting equal scores order by ascending
id, stable across repeated queries.

**Verified:** the tie test passes and fails if the id tiebreaker is
removed from the ORDER BY.
