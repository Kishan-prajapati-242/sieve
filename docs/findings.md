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

**Fix:** the Phase 3 cascade, executed 2026-08-01.

**Verified before/after:** this exact query, rerun post-cascade:
**3 of 20 redundant copies -> 0.** The asthma trio is one row; the Ascle
family is one row (closed by jmir_doi, the mechanical DOI identity, not by
similarity — see the Ascle recall-gap entry). Corpus 196,988 -> 182,853
papers, 14,135 removed, source_records unchanged at 199,382 and 0 orphaned.

---

## 2026-07-29: 7.7% of papers have no DOI — a Phase 3 input, not a bug

> **Superseded 2026-08-12 (counts only; the reasoning stands).** Post-dedup
> the corpus holds **12,036 no-DOI papers, 6.6% of 183,167**. The rate fell
> because merging a no-DOI record into a DOI-carrying survivor removes a
> no-DOI paper — the numerator dropped 3,222, not just the denominator. The
> figures below are left as measured on 2026-07-29 at 196,893 papers.

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

## 2026-08-01: Single-pass hand labeling has its own accuracy ceiling

**What happened.** Kishan labeled 120 pairs, then reviewed the 14 pairs
where the model disagreed and **corrected 10 of his own labels** — 8.3% of
the sample. Nine were applied; one (pair 67) was withheld because
verification contradicted the stated reason.

**Why the errors clustered where they did, in his words:** the
disagreements fell on sibling and parallel-variant patterns whose taxonomy
he only worked out partway through labeling. So the EARLY labels were made
with a weaker rubric than the LATE ones. This is not carelessness; it is
what single-pass labeling of an unfamiliar domain does. You learn the
taxonomy by labeling, and the labels you made before you learned it are
worse.

The corrected pairs are exactly that shape — supplementary file vs figure
(7, 18), PheKnowLator build variants (46, 53, 102), WikiPathways monthly
releases (115), a missing-DOI twin (50), preprint/proceedings (65), and a
Zenodo concept/version DOI (83). Every one belongs to a category the
project only named during this phase: parts of a shared parent, parallel
variants of one artifact, and versioned deposits.

**The caveat that must sit next to the precision number:** an 8.3%
self-correction rate on review is the measured floor on single-pass
labeling accuracy here. The reported precision of 0.957 rests on labels
of which roughly 1 in 12 changed when re-examined once. A second full pass
would likely move it again, by less. Quote the number with this attached,
or it reads as more certain than the evidence supports.

**What would raise the ceiling** (not done, and not free): label in two
passes with the taxonomy fixed in advance between them; or adjudicate every
disagreement between two annotators rather than only the ones one
annotator chose to revisit.

---

## 2026-08-01: Why the model's dedup labels cannot be ground truth

The labeling harness has two annotators: Kishan and the model. Only one
of them can serve as the reference, and it is not the model.

**The circularity.** The cascade IS the model's judgment — every rule in
api/dedup/rules.py, every threshold, the survivorship policy, the group
cap. Scoring those rules against labels produced by the same judgment
measures SELF-AGREEMENT, not correctness. A systematic blind spot in the
rules is reproduced exactly in the labels, and the metric reports it as
success. The model's labels are useful for one thing only: as a second
annotator whose DISAGREEMENTS with Kishan point at pairs worth arguing
about.

**The empirical case, which is stronger than the argument.** Five hand-
reads in this project, each on data the model had already analyzed and
signed off:

  1. junk types / paratext (2026-07-29) — proceedings volumes ranking
     beside their own member papers;
  2. the boilerplate blocklist (2026-07-31) — 12 of 45 "legitimate"
     abstracts were dedup landmines, found only by re-asking the question;
  3. the 15 preprint-pass pairs (2026-08-01) — 14 clean, 1 a
     parent/child pair that produced the part-sibling rule;
  4. the 15 title_exact pairs (2026-08-01) — 2-member groups clean,
     groups of 4+ full of generic titles and versioned releases,
     including a Zenodo ERROR MESSAGE merged across 5 records;
  5. the p95 / merge-order / FK-index diagnoses, each of which the model
     had accepted as "expected cost" until the numbers were read.

In every case the model's prior analysis had passed the data as fine.
Five for five. That is the base rate that decides whose labels are the
reference.

**So:** Kishan's labels are the ground truth. The model's are a second
opinion, agreement is reported with Cohen's kappa rather than raw
agreement (raw is inflated when one class dominates), and only the
disagreements are surfaced for adjudication.

---

## 2026-08-01: Merge ordering — 536 groups refused by their own constraints

**Symptom:** the merge run completed but **536 of 7,712 groups (7%) failed**
with two distinct database errors. Every failure rolled back cleanly (one
transaction per group, 0 orphaned records), so the corpus stayed consistent
— just incomplete.

**Two ordering bugs, both about doing things in the wrong sequence:**

1. `duplicate key value violates unique constraint "papers_doi_key"`.
   DECISION-3b lets a survivor with no DOI inherit one from a loser. The
   code updated the survivor and THEN deleted the loser, so for the length
   of one statement two live rows held the same DOI — which the unique
   constraint exists to forbid. The donor has to die first.

2. `violates foreign key constraint "merges_kept_paper_id_fkey"`. A paper
   that SURVIVES one merge can LOSE the next, and the earlier merges row
   still names it as kept_paper_id. The FK blocks the delete. The audit
   trail has to follow the survivor.

**Fix:** one explicit order in merge_group, with the reason in a comment:
repoint source_records -> repoint prior merges -> DELETE losers -> update
the survivor. Rollback runs the mirror image (restore the survivor's own
fields BEFORE reinserting the donor), for the same collision reason.
prior_merge_map joins the snapshot so rollback restores the audit chain.

**Verified before/after:** three regression tests — DOI inheritance from a
deleted donor, a paper losing a merge after surviving one, and rollback
round-trip after DOI inheritance. All three FAIL against the old order and
pass against the new one (checked by reverting the fix, not assumed).

**Worth noting about the failure mode:** these 536 were loud. The
constraints caught every one, each group rolled back atomically, and the
run reported a count instead of producing silent corruption. That is the
schema doing its job — the UNIQUE and FK constraints that made the merge
fail are the same ones that made failure safe.

---

## 2026-08-01: The index a foreign key does not create (pattern, 3rd instance)

**Symptom:** the merge executor ran 90 minutes to reach 45% (5,730 of
12,796 groups) and projected 3.3 hours. Kishan read pg_stat_activity: a
parallel seq scan, three processes, ~1.5 s per execution — arithmetic that
matches ~1 second per group exactly.

**Diagnosed with EXPLAIN ANALYZE, not by guessing.** The papers lookup was
fine — `Index Scan using papers_pkey`. The cost was entirely inside a
correlated subquery:

    Index Scan using papers_pkey on papers   (actual time=1135.8..1140.3)
      -> Parallel Seq Scan on source_records (actual time=2.2..1120.1)
    Execution Time: 1140.490 ms

**Root cause:** `source_records.paper_id` has REFERENCED `papers(id)`
since migration 0002, and **PostgreSQL does not index the referencing side
of a foreign key.** Every "which records belong to this paper" question
was a parallel seq scan of the largest table in the database (815 MB).

**Fix:** migration 0009, a partial index on `source_records(paper_id)`.
**Verified before/after with EXPLAIN, not asserted:** 1,140.490 ms ->
0.608 ms, a 1,875x speedup, and the plan changes from Parallel Seq Scan to
`Index Scan using source_records_paper_id_idx`. Three hot paths were
paying this, not only the merge: `owns_paper()` on EVERY record refresh
during ingestion, the /api/stats attribution query, and the merge remap.

**Third instance of one class**, after the quadratic self-join and the
pg_trgm GUC default: *a set operation written so the database re-scans
instead of looking up.*

  * quadratic self-join — asked for a cross product to answer a grouping;
  * pg_trgm threshold — asked for every pair above 0.3 to answer a
    question about 0.92;
  * this — asked for a table scan to answer a key lookup.

**The shared tell, and it is the useful part: a query taking SECONDS when
the code implies MILLISECONDS means the planner is not doing what the code
says.** Not "the data is big" and not "the database is slow" — those were
the tempting readings each time. The habit that catches all three is
running EXPLAIN before accepting any per-item cost above a millisecond.

---

## 2026-08-01: Shared parents, not bad strings — the sibling rule

**The generalization (Kishan).** Four separate bugs turned out to be one
structure. Records that are PARTS OF or VERSIONS OF a common parent
inherit the parent's abstract:

  * textbook chapters share the book description (23 chapters, one group);
  * versioned data releases share the series description (Gene Ontology
    x54, COVID Twitter chatter x141);
  * supplementary files share the parent paper's abstract ("Additional
    file 1/2/3 of X");
  * proceedings volumes share a member paper's abstract (the Phase 1
    ghost, found 2026-07-29).

That is a permanent property of scholarly metadata, not an enumerable list
of bad strings, so the boilerplate blocklist could only ever treat
symptoms. The rule that replaces it: **same abstract + same title =
duplicate; same abstract + DIFFERENT titles = siblings under a shared
parent.**

**Re-audit, with the right question.** The blocklist curation had asked "is
this a real description?" The correct question is "would this merge
distinct papers?" Of 45 shared abstracts I had kept OUT of the blocklist as
legitimate, **12 (90 papers) have distinct titles** — every one a dedup
landmine, including the cataract-LLM structured abstract and the textbook.
The two roles are now separated: the blocklist governs EMBEDDING policy (a
shared abstract makes siblings mutually indistinguishable in vector space),
the sibling rule governs MERGING. The textbook was added to the blocklist
for the embedding reason alone.

**Second rule, same family:** titles differing only in digits are
enumerated siblings ("Additional file 1" vs "Additional file 3", "Figure
S4" vs "Figure S7"). Trigram scores them ~0.98 because one character
differs — precisely the band a strict threshold trusts most.

**Rejected: distinct DOI as a non-duplicate signal.** It would be a cheap
guard against versioned releases, and it is wrong: the pediatric-asthma
trio is ONE paper with THREE DOIs (BMC 10.1186/s12911-026-03371-x, Figshare
c.8354879, Figshare c.8354879.v1). Pinned as a regression fixture in
tests/test_dedup_rules.py so no future rule can adopt it silently.

---

## 2026-08-01: Chaining is a real risk that barely materialized

**The concern (Kishan):** union-find over pairwise edges assumes
similarity is transitive. A~B and B~C does not give A~C, so a component
can be a CHAIN of locally-similar records that are globally dissimilar.

**Measured, on every component of 4+ members (304 of them):** edge density
(edges found / pairs possible) and mean pairwise title similarity across
the whole component, not just the matched edges. **3 components of 304
(1%) are chains** by the test "mean pairwise similarity below the merge
threshold", and the worst has mean 0.890 / min 0.802 against a 0.92 gate —
so even the chains are shallow, not runaway.

Why so few: the strategies are mostly EXACT (identical abstract, identical
title+year), and exact keys are transitive by construction. Only
title_trgm can chain, and it now contributes 1,144 of 16,680 pairs (6.9%).

**Correction to the reasoning (Kishan, 2026-08-01).** An earlier draft
said chaining is rare "because exact keys are transitive by construction".
That is now FALSE and the error mattered: the sibling rule added a
title-similarity condition to abstract_hash, so it is no longer an exact
key and CAN chain. Left uncorrected, "exact keys cannot chain" would later
have justified dropping the group-size cap.

The correct reasoning: chaining is rare here because the conditions are
STRONG (identical abstract plus similar title; identical title plus year),
not because they are exact. That is an empirical property of this corpus,
not a guarantee.

**Decision: no near-clique requirement, no chain-depth cap, no separate
transitive threshold** — all three would add permanent machinery to tighten
1% of components. **The group-size cap is therefore load-bearing, not
belt-and-braces:** any long chain produces a large component, and
components over 8 are refused. It must not be dropped. Revisit if the
fuzzy share of pairs grows (PubMed will test this).

---

## 2026-08-01: The Ascle family is a recall gap, not a trigram win

**Symptom:** Phase 1 recorded the Ascle preprint/published pair as "trigram
territory". Building it as a regression fixture showed it is not, at the
shipped threshold. Measured title similarities:

    arXiv 2023      <-> JMIR preprint 2024 : 0.914   (gate is 0.92)
    JMIR preprint   <-> JMIR published     : 0.694
    arXiv 2023      <-> JMIR published     : 0.725

**How it was found:** writing the regression fixture and asserting the
Phase 1 claim, which failed. The claim had never been measured — it was
inferred from the pair "evading every exact key".

**Root cause:** the published version appends ": Development and Evaluation
Study", which adds enough trigrams to drop similarity to 0.694. No title
threshold reaches that without merging unrelated papers.

**Fix:** none yet, deliberately. Two candidate closures recorded with the
fixture: drop the PREPRINT pass to 0.90 (its sweep curve is nearly flat, so
the cost is ~79 pairs) which catches the 0.914 edge; and add the mechanical
JMIR rule 10.2196/preprints.N -> 10.2196/N as an exact strategy, which is
deterministic and closes the 0.694 edge that similarity cannot.

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

> **AMENDED 2026-08-13. Throttling is real; the 2.4x magnitude is not
> supported, and the "10+ hours" is the highest-prior candidate for the
> sleep artifact this project measured two weeks later.**
>
> The entry's evidence is a host-side wall clock — "Kishan, comparing the
> completed overnight run" — and `Verified: n/a`. On 2026-08-13 the same
> instrument reported a cascade at 3 h 55 m that VM-side clocks measured at
> **10 m 11 s**, a 23x inflation caused entirely by the host sleeping. An
> unattended overnight run on a laptop is precisely the shape that produces
> it, and nothing here rules it out.
>
> `api/embed/backfill.py` computes its rate from `time.perf_counter()`,
> which is CLOCK_MONOTONIC and does NOT advance while the host is
> suspended. That number was printed and never captured — **no encode log
> exists in this repo**, so the sleep-immune measurement is gone.
>
> What can still be said: the 8.8-12.7 docs/s band in progress.md is
> in-process, but it comes from the 2026-07-30 pre-encode resumability
> runs — 5,000 rows, ~8 minutes each — so it is a SHORT-run band and
> cannot demonstrate sustained behaviour either. **This project has no
> sustained in-process throughput measurement for its own hardware.** The
> 2.4x compared an unlogged overnight wall clock to a 75-second benchmark;
> a 1.1-1.6x computed from the short-run band to the same benchmark is
> better sourced but still not sustained.
>
> **What survives unchanged:** a fanless M1 Air does throttle under
> sustained all-core load; a 75-second benchmark cannot see it; the
> projection was honest arithmetic on a measurement that could not see
> thermal behaviour; and resumability is what made an unattended multi-hour
> run safe. Only the FACTOR is unsupported — and with it the int8 argument,
> which is weaker at 1.1-1.6x than at 2.4x.
>
> **How it gets fixed:** `backfill.py` now logs windowed rates with BOTH
> clocks, flagging any window where wall time exceeds monotonic time by
> more than 5 s as a clock discontinuity. The PubMed encode is ~16,800
> papers over 21-33 minutes and will produce this project's first sustained
> rate, provided its stdout is captured.

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

---

## 2026-08-12: Ground truth referenced 5,528 papers that no longer existed

**Symptom:** every recall number in `bench/` described a 196,893-paper
corpus while the live corpus held 183,167. Nothing failed. The recall
scripts happily compared HNSW results against an exact top-200 whose
entries dedup had deleted.

**How it was found:** not by a test — by asking the question. The dedup
cascade and the DECISION-3c unwind changed the corpus; the ground-truth
file had no field saying which corpus it was built from, so nothing could
notice. Quantified before rebuilding:

| | |
|---|---|
| distinct ids referenced by the 520 queries | 67,769 |
| of those, deleted | **5,528 (8.2%)** |
| queries with ≥1 dead id in top-200 | **515 / 520 (99.0%)** |
| top-200 slots pointing at a dead id | 8,399 / 104,000 (8.1%) |
| queries with a dead id in the **top-10** | **247 (47.5%)** |

**Root cause:** a measurement that does not record what it measured. The
file was a bare JSON list of query entries — no corpus size, no
timestamp, no plan proof. Staleness was undetectable by construction.

**Fix:** ground truth is now `{"method": {...}, "queries": [...]}` with
corpus size, EXPLAIN proof of the forced seq scan, and a timestamp;
`harness.load_ground_truth()` reads both shapes and labels the old one
`"v1 file: no method block, corpus unknown"`. `method_record()` calls in
the latency scripts now carry `corpus_size`, and `harness.carry_superseded()`
supersedes a results file whenever that number changes — the old
hand-written v1/v2 chain in `exact_scan_baseline.py` could only recognize
the two format changes it was written for, and produced no block at all
when the corpus shrank.

**Verified:** rebuilt 520 queries against 183,167 papers in 38s with
`assert "Seq Scan on papers" in plan` and `assert "papers_embed_idx" not
in plan`. Vector recall@200 at the shipped defaults moved 0.9857 →
0.9848 (se 0.0014) — a 0.6-SE move, so the stale ground truth had **not**
been materially inflating the published recall figure. The staleness was
a real correctness hole in the instrument; its effect on this particular
number happened to be small.

---

## 2026-08-12: The exact-scan "regression" was the noise floor (corrected)

**Symptom as first reported:** dedup removed 13,821 net papers, so the
exact-scan baseline was expected to improve. Warm p50 went the other way:
54.6 ms → 61.7, then 64.0 on a re-run.

**My first explanation was wrong in a specific way.** I wrote that "no
improvement was structurally possible" because the heap was unchanged at
44,059 pages, so a seq scan reads the same pages. Kishan corrected it:
the cost of an exact vector scan is 183,167 distance computations over
384-dim halfvecs, not page reads. Dead tuples fail the visibility check
*before* expression evaluation, so ~13,800 distance computations stop
happening — 7% less CPU at identical I/O. The page argument is right
about I/O and does not license the conclusion. Corrected expectation:
~7% faster, or flat. Observed: ~15% slower. Gap to explain: ~22%.

**Disconfirming evidence for thermal, which I had in hand and did not
flag.** In the same re-measurement, bm25 sql p50 went **2.2 → 1.1 ms**,
per-run [2.3, 2.2, 1.9] → [1.3, 1.0, 1.1] — non-overlapping, a clean 2x
*improvement*. Sustained thermal load cannot halve one query path while
degrading another in the same session. The pattern instead tracks what
each path is sensitive to:

| path | sensitive to | 7% fewer rows should | observed |
|---|---|---|---|
| bm25 GIN | matched documents per query | help, and duplicates are exactly what matched twice | 2x faster |
| HNSW | graph traversal, ~log in node count | ~nothing | flat |
| exact scan | distance computations at fixed page count | ~7% | (see below) |

**The decisive test (Kishan's): `VACUUM FULL` and re-measure.** If page
count were binding, p50 should drop toward 54.

| | heap pages | heap | warm p50 | per-run |
|---|---|---|---|---|
| pre-dedup | 44,059 | 344 MB | 54.6 | 51.5 / 54.6 / 56.4 |
| post-dedup, run 1 | 44,059 | 344 MB | 61.7 | 62.4 / 54.6 / 61.7 |
| post-dedup, run 2 | 44,059 | 344 MB | 64.0 | 55.2 / 66.3 / 64.0 |
| post-dedup, control | 44,059 | 344 MB | **56.0** | 58.6 / 56.0 / 52.1 |
| after VACUUM FULL | **35,348** | **276 MB** | **60.9** | 60.3 / 60.9 / 62.4 |

**Root cause: there was no regression to explain.** Four measurements of
the identical database returned p50 between 56.0 and 64.0; individual run
p50s across the same state span 52.1–66.3. Removing 20% of the heap
pages moved p50 by nothing detectable. The measurement's session-to-
session spread (~15%) is larger than both the effect being attributed to
dedup (~7%) and the effect of a fifth of the heap disappearing. The
"~15% regression" was a draw from that spread, and my earlier framing —
an unexplained regression with thermal as the leading suspect — treated
noise as signal.

Two things do survive as real conclusions:

1. **The exact scan is CPU-bound on distance computation, not I/O-bound.**
   A 20% page reduction produced no measurable change. That confirms
   Kishan's correction and retires the page argument in both directions.
2. **This instrument cannot resolve anything smaller than ~15% across
   sessions.** Any before/after claim below that threshold is unpublishable
   as a cross-run difference — which is what forced the methodology change
   below.

**Fix:** `harness.paired_ratio()` and `bench/paired_speedup.py` — baseline
and candidate timed back to back on the same query inside one run, order
rotated, ratio computed per query. Shared conditions divide out. Also
`harness.db_state()`: results now record heap pages and per-index bytes,
not just row count, so a rewrite supersedes the old numbers automatically.

**Verified:** `VACUUM FULL` took the heap 44,059 → 35,348 pages and every
index with it (FTS GIN 118 → 83 MB, title trgm 70 → 39 MB, pkey 8.7 → 4.0
MB), at an unchanged row count — i.e. the corpus had ~20% bloat, well
beyond the 7% dedup deleted. Post-rewrite the whole instrument steadied:
per-run p50s of [1.0, 1.0, 1.0] bm25, [2.0, 2.0, 1.9] vector, [18.2,
18.3, 17.9] hybrid, where the same measurements had been gating to ranges
an hour earlier. Recall at the shipped defaults, on the rebuilt HNSW:
0.9848 → 0.9861 (se 0.0010).

---

## 2026-08-12: A number drifted flattering, 4th instance (pattern)

**Symptom:** the published end-to-end speedup rose from **6.3x to 7.2x**
between sessions. Nothing about the system improved. The denominator —
the exact-scan baseline — got ~15% slower for reasons nobody had
explained, and the ratio rose because of it.

**How it was found:** Kishan, checking the new headline against the old
one and asking what had actually changed.

**The pattern, now four instances, all caught by Kishan:**
1. p99 published from the favorable end of an observed 95.8–406.9 spread;
2. a ratio computed across two different timing windows (5.5x vs 6.3x);
3. the fusion convergence proxy measured against the deepest ranking
   tested, which reaches 1.0 by construction;
4. this ratio, inflated 14% by an unexplained drift in its own baseline.

The shared shape is **not** dishonesty in any single step — each number
was measured. It is that the *error is never randomly signed*. A drifting
baseline could have made the ratio look worse; it happened to make it
look better, and a number that looks better invites less scrutiny than
one that looks worse. The habit that catches it: when a headline number
improves, ask which side of the fraction moved, before celebrating.

**Fix:** `harness.speedup()` is retired for new work in favour of
`harness.paired_ratio()`, which cannot express this failure — both sides
come from the same run, so a drift that hits both cancels exactly.
`tests/test_harness.py::test_paired_ratio_is_immune_to_a_drifting_baseline`
pins it: a 20% slowdown applied to both sides leaves the paired ratio
bit-identical, while the cross-run form moves.

The retirement also exposed a second hole in the old guard. It compares
window *strings*, so the retired retrieval-only ratio passed one
hand-written window for a 50-row `(id, distance)` scan on one side and a
20-full-row search on the other — mismatched windows that satisfied the
mismatched-window check. `paired_speedup.py` runs `search_vector()` on
both sides, differing only by `enable_indexscan`, so the window is
identical by construction rather than by assertion.

---

## 2026-08-12: Pair 67 — the conservative label is the honest one

**Context:** during the dedup precision review Kishan corrected 10 of his
120 labels and asked me to verify three before applying them. Pair 67
(PGxCorpus: bioRxiv preprint vs Figshare item) was the one I refused to
apply, and he has now confirmed the refusal and withdrawn the correction.

**The evidence:** the Figshare item's abstract describes *contents* ("941
sentences from 911 PubMed abstracts") while the bioRxiv and Scientific
Data records describe a *study*. Scientific Data is a data-descriptor
journal, which is precisely why all three share a title — the paper's
subject is the dataset. Same relation as pair 35: a data artifact and its
descriptor paper, not two copies of one record.

**Why it is worth an entry:** leaving it scored as a false positive holds
measured precision at **0.957** instead of raising it. The label that
makes the system look worse is the one the evidence supports, and it
stays. That is the counterexample to the drift pattern above — the same
review that found a flattering ratio also found a chance to improve a
number honestly declined.

---

## 2026-08-12: Demanding an explanation for noise (pattern, 5th instance)

**Symptom:** the exact-scan p50 moved 54.6 → 61.7 between sessions and I
was asked to account for a "~22% unexplained gap" — 7% expected
improvement plus 15% observed regression. Hours went into candidate
mechanisms: scratch tables, page cache, thermal load.

**Why it could not work:** the gap was smaller than the instrument's own
spread. Four measurements of a byte-identical database returned p50
between 56.0 and 64.0, and a `VACUUM FULL` that removed 20% of the heap
pages moved it by nothing detectable. A difference below the noise floor
has no mechanism to find, so every hypothesis fits equally well and none
can be eliminated — the investigation had no possible outcome that would
have falsified any candidate.

**Attribution, since it matters to the lesson:** this one was Kishan's
framing, and the four before it were mine. The pattern is not a property
of who is careless. It is what happens when a number is trusted before
its resolution is known, and the correct first move is the same for
everyone: measure the instrument's spread on an unchanged system before
attributing any difference to a change.

**The pattern, now five instances** (see 2026-07-31 for 1–4):
1. the durability test that read through the writer's own connection;
2. p95 = p99 from 20 samples, where both are the max by construction;
3. the fusion convergence proxy that reaches 1.0 by construction;
4. the drift count of 0, where both hypotheses predict zero;
5. this gap, smaller than the spread of the instrument measuring it.

The shared shape: a number asked to support a conclusion its construction
cannot support. "What result would falsify this?" catches 1–4. For 5 the
question is narrower and worth adding to the harness discipline: **what
is the smallest difference this measurement can resolve?** If the answer
is not known, no before/after claim from it is publishable.

**Fix:** the repeated-measure control is now the first step of any
before/after — re-measure the unchanged system 3+ times and publish the
spread alongside the effect. `paired_ratio()` sidesteps the problem for
ratios; for absolute levels there is no substitute for knowing the floor.

---

## 2026-08-12: 24x and 3.8x are the same system at two settings

**Symptom:** the paired measurement returned retrieval-only speedup of
**24.1x at ef=40** and **3.8x at ef=600**. Every speedup figure quoted so
far — including in a resume draft — came from the ef=40 end. The hybrid
path, which is what ships as the flagship mode, runs at ef=600.

**How it was found:** Kishan, on reading the paired table: "quoting 24x
for a system whose flagship mode runs at 3.8x is the flattering-drift
error one layer up."

**Why it is the same error:** both numbers are true, both are measured,
and neither is a lie in isolation. The selection between them is where
the flattery enters — the same shape as publishing the favorable end of a
p99 spread, moved from picking a sample to picking a configuration.

**The honest framing is the exchange rate, not either endpoint.** ef is a
recall/latency dial, so a speedup quoted without its recall is a number
with its price removed:

| ef_search | paired retrieval speedup | recall@200 |
|---|---|---|
| 40 | 24.1x [23.3, 25.0] | (measured separately — see below) |
| 600 | 3.8x [3.6, 3.9] | 0.9861 (se 0.0010) |

**and Sieve ships the second.** The claim worth making is not "24x
faster"; it is that the ef dial buys recall with latency at a measured
rate, and that the shipped configuration deliberately spends most of the
available speedup on recall.

**Related, on the guard that was supposed to prevent this:**
`harness.speedup()` compares timing-window *strings*. Two genuinely
different windows — a 50-row `(id, distance)` scan and a 20-full-row
search — pass it as long as the caller hands both the same string. String
equality cannot verify that two code paths did the same work.
`paired_speedup.py` fixes this structurally rather than by assertion:
both sides call `search_vector()`, differing only in whether
`enable_indexscan` lets the planner reach the HNSW index, so there is one
code path and the window is identical by construction.

---

## 2026-08-12: The forced-exact baseline stopped being exact, and EXPLAIN could not tell

**Symptom:** the paired hybrid measurement returned a speedup of **1.4x**
[1.3, 1.6] for the HNSW index over an exact scan, with 10% of queries
showing the *exact scan winning* (ratio p10 = 0.4). The decomposition in
the same run put hybrid at 9.4 ms and its own vector CTE at 11.4 ms — a
whole greater than its part, which is not a thing that happens.

**How it was found:** the number was too good in the wrong direction. A
forced sequential scan over 183,167 halfvecs cannot cost 14 ms when the
standalone exact-scan baseline costs 60 ms. Direct timing confirmed it:

```
per-query hybrid_exact ms:  845, 573, 817, 423, 485, 7.6
```

The first five are a real seq scan. The sixth is not.

**Root cause:** the baseline was built by toggling `enable_indexscan =
off` between calls on one connection. psycopg prepares a statement after
5 executions (`prepare_threshold=5`), and PostgreSQL serves custom plans
for the first five executions of a prepared statement before switching to
a cached **generic plan**. That generic plan was built while the index was
still allowed. From roughly the tenth execution on, the server kept
executing the index plan no matter what the GUC said — so the baseline
became the candidate, and the ratio decayed toward 1.0.

**Why the existing guard could not catch it.** `verify_plans()` ran
`EXPLAIN` on the same SQL and asserted `Seq Scan on papers`. It passed —
every time, including after the collapse:

```
exec  0: uses papers_embed_idx = False  (GUC says off)
exec 13: uses papers_embed_idx = False  (GUC says off)
```

`EXPLAIN <sql>` plans the statement fresh and obeys the current GUC.
The PREPAREd statement executes the cached plan. They are different
objects, and only `EXPLAIN EXECUTE` sees the one that runs. **A plan
assertion written against EXPLAIN cannot verify what a prepared statement
did** — which makes this the fourth "check whose passing condition is
independent of the property under test" and the 6th instance of that
pattern overall.

**Fix:** `harness.pinned_connection()`. One connection per plan, GUC
applied before its first query, never toggled; the measurement alternates
between connections instead. Both sides keep prepared statements, which
is also what the API does in production, so neither side is handicapped.
Verified directly — with the GUC set at open, the exact plan holds for
all 16 executions:

```
toggled  (prepare_threshold=5):  839, 887, 478, 489, ..., 1933, 2837, ...
pinned   (GUC set before open):  617, 419, 450, 486, ..., 340, 374, 434
```

**Verified:** `tests/test_harness.py::test_a_toggled_planner_guc_stops_
taking_effect_once_the_plan_is_cached` reproduces the whole mechanism on
a 20,000-row scratch table in 0.6 s: it PREPAREs a statement, executes it
six times, sets `enable_indexscan = off`, then asserts that a freshly
planned statement obeys the GUC while `EXPLAIN EXECUTE` still shows the
index — and that a connection pinned before PREPARE does not. If a future
PostgreSQL invalidates cached plans on planner-GUC changes, that test
fails and says `pinned_connection` can be simplified.

**Blast radius:** `paired_speedup.py` had the identical construction and
was corrected the same way. Its published figures were re-measured rather
than assumed safe — its exact side had measured 70.4 ms p50, consistent
with a real scan, which is why the bug surfaced in the hybrid script
first: hybrid's higher execution count per query reached the generic-plan
threshold sooner.

---

## 2026-08-12: The measurement was not alone on the machine

**Symptom:** the corrected paired hybrid run reported `hybrid_p50_ms`
57.78 with a `fusion_overhead_p50_ms` of 35.88 — implying the RRF join and
final sort cost twice the vector CTE. An uncontended run of the same
statement, an hour earlier, read **18.2 ms** total. Most of the run's
percentiles also gated to ranges by the stability rule, where the same
measurements had been reproducing to a tenth of a millisecond.

**How it was found:** the decomposition disagreed with a number measured
the same day, and the stability gate fired on everything at once. Both are
signatures of the environment, not of the query.

**Root cause: I ran the test suite twice while the measurement was in
flight.** 192 tests, each creating and dropping its own scratch database,
on the same Postgres and the same 4-vCPU VM. The measurement had no way to
know and no way to say so.

**Why pairing did not save it.** Pairing cancels noise that hits both
sides *in proportion* — that is what the immunity test pins, and it is why
the ratios from the contaminated run (10.7x retrieval-only) are closer to
believable than its levels. But CPU contention does not hit a 450 ms
sequential scan and a 20 ms index probe proportionally, so even the ratio
is suspect. The clean re-run is the only way to know by how much.

**The deeper mistake** is not the concurrency, it is that nothing recorded
it. Every other property of a measurement in this project is written into
its method block — corpus size, heap pages, plan, timing window, warmup
count — and "was anything else using the machine" was the one condition
left to memory. It joins the same family as the ground truth that did not
record its corpus and the baseline that did not record its plan: a number
that cannot say what it measured.

**Fix:** `harness.server_activity()` snapshots per-database transaction
counters, and `harness.contention_report()` diffs them across the run.
Foreign databases with a non-zero delta name the intruder exactly — which
works precisely because the test suite creates a scratch database per
test, so its traffic cannot hide inside our own datname. Every paired
result now carries `method.contention` with a `clean` boolean.

**Verified:** the contaminated run is preserved for comparison against the
clean one; the guard is what decides which of the two gets published, and
`clean=false` means the levels do not get quoted at all.

---

## 2026-08-12: The reconciled corpus chain, closed against the tables

**Why it is here:** the paper and record counts were being re-derived by
hand from scattered docs, and three separate attempts produced three
different decompositions. Every link below is a query against the live
tables, so it does not have to be re-derived again.

**Papers — 196,988 peak to 183,167:**

| step | n | source |
|---|---|---|
| OpenAlex 200K pull | 196,893 | commit 54ea19c |
| arXiv new papers | +95 | 97 arXiv records, 97 distinct paper_ids, of which **2** are shared with an OpenAlex record — so 95 are new |
| **peak** | **196,988** | |
| deleted by cascade merges, net of the unwind | -13,821 | `sum(jsonb_array_length(merged_from->'deleted_papers'))` over the 13,445 surviving merges |
| **live** | **183,167** | `count(*) FROM papers` |

The gross figure was 14,135 removed (to 182,853); DECISION-3c's unwind of
122 title_exact groups restored 314, and the merges table now records
exactly 14,135 - 314 = **13,821**. Both routes land on 183,167.

**Records — the 16,215 surplus over papers:**

| component | n | what it is |
|---|---|---|
| records that never became a paper | 1,736 | `paper_id IS NULL`; **all 1,736 have an empty title** |
| ingest-time DOI links | 658 | `merges` rows with no `deleted_papers` — a second record joining an existing paper |
| papers removed by the cascade | 13,821 | their records were repointed to survivors |
| **total** | **16,215** | = 199,382 - 183,167 |

658 + 13,821 = 14,479, which matches
`sum(n-1)` over papers holding more than one record exactly. The
decomposition closes with no residual.

**Correction to the working model:** the surplus does NOT split as "2,394
ingest-time + 13,821 merged". Ingest-time skips are **1,736**, not 2,394,
and the ingest-time DOI links (658) belong with the merged group because
those records DID become part of a paper. The 2,394 figure has no
referent in the tables.

**On the skip categories:** they are not disjoint buckets. Every unlinked
record is a no-title record; the work types are the reason the title is
missing. Types: peer-review 396, paratext 380, editorial 369, erratum
171, supplementary-materials 165, absent 86, conference-paper 85, dataset
22, retraction 17, preprint 15, other 13, article 11, book-chapter 3,
review 2, conference-abstract 1. So "junk-type skips vs no-title skips"
is a distinction the data does not support: there is one skip rule (no
title) and a type distribution that explains it.

---

## 2026-08-12: The planner GUC is not a scalpel, so the hybrid speedup has no instrument

**Symptom:** the paired hybrid baseline measured 567.6 ms where an additive
model of what it was supposed to measure — an exact vector arm at ~61-73 ms
plus a shared remainder of ~8-16 ms — predicts about 90 ms. A candidate
being slowed cannot explain a slow baseline, so something was inflating the
numerator.

**How it was found:** Kishan, working backwards from the additive model,
then asking the decisive question: does the bm25 arm cost the same on both
sides?

**Root cause: `enable_bitmapscan = off` removes `papers_fts_idx` entirely.**
A GIN index is reachable ONLY through a bitmap scan, so disabling bitmap
scans disables full-text search along with the vector index. And
`enable_indexscan = off` removes `papers_pkey` from the fused statement's
final join. Measured on 120 queries at k=200:

| bm25 arm | plan | p50 |
|---|---|---|
| index allowed | `Bitmap Index Scan on papers_fts_idx` | **7.39 ms** |
| "forced exact" | `Parallel Seq Scan on papers` | **227.80 ms** |

So "hybrid without the HNSW index" was really "hybrid without the HNSW
index, without the FTS index, and without the primary key" — three
sequential scans of a 276 MB heap in one statement where the intended
baseline runs one. **The baseline was inflated, biasing the ratio HIGH.**

**Why this matters more than the number:** a second contamination pushes
the same ratio LOW. The variant order was a cyclic rotation, and a rotation
preserves every adjacency, so the HNSW candidate ran immediately after the
cache-destroying baseline in 3 of 4 slots (51.7 ms measured against 18.2 ms
standalone). One bias is BETWEEN executions and a seeded permutation fixes
it; the other is WITHIN one execution and no ordering scheme touches it.
**Re-running with a better order would have returned a still-contaminated
number wearing a repair** — which is the trap worth remembering.

**Fix:** the fused ratio is retired as not-measurable with this lever and
reported per ARM instead. The vector arm is clean, because `VECTOR_SQL` has
exactly one index option and the GUC *is* selective there: 3.8x at ef=600,
24.1x at ef=40. What `papers_fts_idx` is worth (30.8x) is stated on its own
rather than smuggled into a hybrid figure. `bench/paired_hybrid.py` now
shuffles variant order with a seeded RNG and emits `fused_ratio.
not_reportable`; both bias directions are written into
`results_paired_hybrid.json` rather than the file being deleted.

**Open:** isolating one index without a global GUC would need something
like a hypothetical-index extension or a physical drop-and-rebuild. Not
attempted; the per-arm decomposition answers the design question ("which
index earns its place") without it.

---

## 2026-08-12: One query string, 28 times, moved a decision's margin by 5 points

**Symptom:** the re-measured ef ladder appeared to show recall@200 at ef=200
collapsing 0.9431 to 0.8918, doubling DECISION-2e's margin from +4.3 to
+9.4 points. Two plausible mechanisms were on the table — a rebuilt HNSW
graph, or known-item queries whose target had been merged away.

**How it was found:** Kishan rejected the first explanation offered ("a
candidate list exactly equal to the depth") because it cannot reach the
recall@10 column, where ef is 4x the depth and the same drop shape appears.
The split he asked for — group the queries by whether their own source
paper survived dedup — falsified the second explanation too:

| ef | survived (n=462) | deleted (n=31) |
|---|---|---|
| 40 | r@200 0.8822 | 0.8539 |
| 200 | r@200 0.9414 | 0.9219 |
| 600 | r@200 0.9861 | 0.9782 |

The deleted group is slightly worse, as expected, but it is 6% of the query
set and nowhere near enough to move the mean 5 points.

**Root cause: the query set contains the string "Occurrence Download" 28
times.** All 28 entries carry the identical query vector — same string,
same encoder — and DIFFERENT ground-truth lists. Solving for its own recall
from the two runs' means:

| ef | its recall@200 | mean over 520 entries | mean over 493 distinct |
|---|---|---|---|
| 40 | **0.0003** | 0.8347 | 0.8804 |
| 200 | **0.0099** | 0.8918 | 0.9401 |
| 600 | 0.9952 | 0.9861 | 0.9856 |

Weighted 28 times, that drags the ef=40 and ef=200 means by roughly five
points while leaving ef=600 untouched — which is exactly the "drop shape"
both hypotheses were invented to explain.

**Correction to this entry's own first mechanism, same day.** It originally
said the corpus papers have "near-identical embeddings" and that the exact
scan "breaks their distance ties arbitrarily". Measured, that is wrong:
those papers share a TITLE, not an embedding — their abstracts differ, so
their vectors differ and there are no exact ties. The real mechanism is
denser and worse, and has its own entry below.

**Corrected conclusion:** the ef=600-over-ef=200 gap on 493 distinct
queries is **+4.5 points**. It CANNOT be compared against the +4.3
published pre-cascade, because that column is a 520-entry mean over a
corpus that no longer exists and `results_ef_at_fixed_depth.json` stored
only means — no per-query values survive to re-aggregate on a matched
basis. The pre-cascade gap on a 493-basis is therefore unrecoverable, and
its admissible range spans roughly [0.0, +4.5] points depending on what
that one query scored then. **No "stable" and no "stronger" claim is
supportable**; DECISION-2e stands on the post-cascade ladder alone.

**Why the artifact survived the ground-truth rebuild:** the rebuild
deliberately reused the stored query vectors verbatim, "so the new numbers
are comparable rather than merely newer". That was right for comparability
and it also preserved 28 copies of a degenerate query whose target cluster
dedup had just changed underneath it. Reusing an input is not the same as
validating it.

**Fix:** recall is reported over DISTINCT query strings. A query set that
weights one string 28x is measuring that string, not the system.

---

## 2026-08-12: 238 papers with abstracts were dropped for having no title

**Symptom:** every one of the 1,736 source_records that never became a
paper has an empty title, which made the skip look like a single rule. It
is two rules, and the second one is losing real content.

**How it was found:** Kishan, checking the type breakdown against
DECISION-1c's junk list and noticing the residual did not consist of junk
types.

**The split, confirmed against the tables:**

| bucket | n |
|---|---|
| DECISION-1c junk types (peer-review, paratext, editorial, erratum, supplementary-materials, retraction) | **1,498** |
| legitimate types with an empty title | **238** |

The 238: conference-paper 85, absent type 86, dataset 22, preprint 15,
other 13, article 11, book-chapter 3, review 2, conference-abstract 1.

**Why it is a defect and not a policy:** of those 238, **216 carry a DOI
and all 238 carry an abstract.** An OpenAlex work with a null title can
still be a real paper with retrievable text — the title is missing from the
metadata, not from the world. Under the current rule they are stored as
audit rows and never indexed, so they are invisible to bm25 (no title, no
FTS) and to vector search (never embedded). That is a silent coverage loss:
nothing errors, nothing is counted, and the corpus is 238 papers smaller
than the crawl paid for.

**Also corrected:** the recorded junk-type count of 1,499 is off by one
against the measured 1,498.

**Not fixed here** — changing the ingest rule changes the corpus and every
number measured against it, and it should ride with the PubMed pull rather
than land on its own. Options when it does: derive a title from the
abstract's first sentence (cheap, lossy); store with a NULL title and let
bm25 index the abstract alone (needs the FTS generated column to tolerate a
null title, which it already does via coalesce); or keep the skip and
report the 238 as a known, counted coverage gap. **Kishan's call.**

---

## 2026-08-12: Low-ef vector search fails inside a 11,044-paper title cluster

**Symptom:** one query's recall@200 reads 0.0003 at ef=40, 0.0099 at
ef=200, and 0.9952 at ef=600 — a 99-point jump between two rungs of a dial
that moves every other query by two or three points.

**How it was found:** as a benchmark artifact, while explaining away a
recall drop. Kishan's read is that it is not an artifact: it is a live
property of the shipped system, on content the product deliberately keeps.

**The cluster, measured:** 11,044 papers share the `title_norm`
"occurrence download" — **6% of the entire corpus** — each with a distinct
DOI, so the cascade correctly refuses to merge them. They are GBIF
occurrence-download records that arrived through the topic queries.

Their embeddings are NOT identical: the titles match, the abstracts differ,
so the vectors differ. Density around one member:

| radius (cosine distance) | papers within |
|---|---|
| 0.0001 | 1 (itself) |
| 0.001 | 1 |
| 0.01 | 1 |
| 0.05 | 131 |
| 0.10 | 1,343 |

**Mechanism.** The k=200 boundary for this query falls inside a shell where
hundreds of papers are near-equidistant — between 131 and 1,343 candidates
separated by less than a tenth of the distance scale. Which 200 you get is
decided by where the greedy traversal enters the shell, not by the metric.
At ef=40 the candidate list is smaller than the confusable population by an
order of magnitude and the returned set shares almost nothing with the
exact top-200. Recall climbs only when ef grows past the size of the
confusable shell, which is why the jump sits between 200 and 600 and not
somewhere else.

This is the failure mode a graph index has and an exact scan does not: HNSW
is a greedy walk over neighbours, and inside a region where every neighbour
is equidistant the walk has no gradient to follow.

**Why it is a product property and not a corpus curiosity.** The system
deliberately retains near-duplicate families: 179 groups sit unmerged in
`dedup_review`, "COVID Twitter" returns 141 times, and DECISION-1c's
versioned-release policy keeps release families intact on purpose. Every
one of those is a shell of the same kind. So **low-ef vector search has a
known, measured failure mode on exactly the content the dedup policy chose
to keep** — and vector mode currently ships at ef=40.

**What is NOT established:** whether the cascade sharpened this or merely
exposed it. Testing that needs pre-cascade per-query recall, and
`results_ef_at_fixed_depth.json` stored only means against a corpus that no
longer exists. Recorded as unresolved rather than guessed.

**Bearing on the open ef_search decision:** at ef=160 this query's
neighbourhood is still smaller than the shell, so 160 does not fix this
case. What 160 does buy, measured on 493 distinct queries, is recall@20
0.9238 -> 0.9782 for sql p50 2.3 ms -> 5.0 ms. The cluster pathology argues
for a larger ef than the recall table alone would; how much larger is
Kishan's call, and no default was changed here.

---

## 2026-08-12: The DECISION-3c unwind is not durable — the next cascade re-merges all 314 papers

> **HEADLINE RETRACTED 2026-08-13. The executor would have merged nothing.**
> Everything below was inferred from `results_dedup_plan.json` and asserted
> about `bench/dedup_execute.py`, which is a different code path. Run:
>
> ```
> plan: 1830 pairs, 179 groups, 0 mergeable, 179 flagged
> held by dedup_review: 0 groups / 0 papers (1325 ids under review)
> ```
>
> **0 mergeable.** `dedup_plan.py` filtered on the GLOBAL `MAX_GROUP_SIZE`
> (8) and reported 122 groups "merged"; `dedup_execute.py` filters on
> `max_group_size(strategy)`, where title_exact is 2, and flags all 179.
> The per-strategy cap was doing its job the whole time.
>
> Two further claims below are also unfounded. "179 = the exact row count
> of dedup_review" — dedup_review holds 179 rows and 1,325 papers, and the
> plan's 179 groups contain **none** of them. "314 = the papers the unwind
> restored" — the unwound groups hold 436 papers (122 survivors + 314
> restored), of which **44** appear anywhere in the current candidate set.
> Two coincident integers were read as identity without checking either.
>
> This is the fifth mechanism I have asserted from a number produced by a
> different instrument than the one the claim is about. The pattern is
> exact each time: read a figure, name a mechanism, skip the run that would
> have falsified it. Kishan caught all five.
>
> **What survives:** the ATTRIBUTION analysis (a group's cap binds on the
> earliest strategy in ORDER, and abstract_hash precedes title_exact) is
> real and independently verified, and it remains the reason DECISION-3c's
> cap is order-dependent. What does not survive is the claim that it is
> currently causing harm.
>
> **Fixed:** `dedup_plan.py` now applies the per-strategy cap, so the dry
> run models the decision the executor will make. A dry run that does not
> is worse than no dry run.
>
> **And the reasoning I built on this is withdrawn too.** I described the
> 122 groups as having "re-attributed" from title_exact to abstract_hash
> between runs, and Kishan corrected his own argument to me on that basis.
> There was no re-attribution: the planner read a global cap and never
> consulted attribution. The two runs differed in which CAP they applied.
> Order-invariance remains a desirable property of a cap rule; "attribution
> is a function of run history" is unsupported and marked as such in
> DECISION-3c.

**Symptom:** timing the cascade on the current corpus produced, as a side
effect, a plan that is DECISION-3c run backwards:

| plan output | value | what it equals |
|---|---|---|
| groups_total | 179 | the exact row count of `dedup_review` |
| groups_merged | **122** | the exact number of groups the unwind reversed |
| groups_flagged | 57 | the groups that were already flagged before it |
| rows_merged_away | **314** | the exact number of papers the unwind restored |
| merged size distribution | 3:89, 4:17, 5:3, 6:7, 7:4, 8:2 | all above the title_exact cap of 2 |

So `dedup_execute --execute` on the current corpus would silently re-merge
precisely the 314 papers that were restored on hand-labeled evidence.

**How it was found:** not by a test. By timing the cascade for a cost
estimate and reading the numbers it printed.

**Root cause 1 — the per-strategy cap binds to one strategy per group.**
Grouping is union-find across all strategies, then each group is attributed
to the EARLIEST strategy in `ORDER` that contributed any edge:

```
ORDER = [doi_exact, jmir_doi, id_exact, abstract_hash, title_exact, ...]
cap_for(root) = max_group_size(best[root].strategy)
```

`abstract_hash` precedes `title_exact`. So a single `abstract_hash` edge
anywhere in a group raises that group's cap from 2 to 8, no matter how many
`title_exact` edges are what made it large. The re-plan found 596
abstract_hash pairs and 974 title_exact pairs over these same 179 groups.
**DECISION-3c's cap of 2 binds only when title_exact happens to be the
group's attributed strategy**, which is not the case that motivated it —
the measured 0.684 precision came from groups of 3+, and those are exactly
the ones most likely to carry an edge from more than one strategy.

**Root cause 2 — `dedup_review` is write-only.** `dedup_execute` inserts
into it; nothing reads it. Not the planner, not the executor. A human
decision to hold a group back therefore survives exactly until the next
planning run, which re-derives candidates from the corpus with no memory
that the question was already asked and answered.

**Why this is blocking for the PubMed pull:** the post-PubMed cascade is a
full `--rebuild`, and its first act would be to undo DECISION-3c.

**Not fixed here** — both fixes change dedup behaviour, which is Kishan's
call and needs its own measurement. The options, with what each costs:

1. **Cap by the STRICTEST contributing strategy, not the earliest.**
   `cap_for` takes `min(max_group_size(s) for s in strategies_in_group)`.
   Smallest change, and it makes the cap mean what DECISION-3c says it
   means. Would re-flag the 122 and probably some groups beyond them —
   the number is unmeasured.
2. **Have the planner read `dedup_review`** and exclude member sets already
   recorded there. Makes human review durable, but pins decisions to a
   member set that a later corpus can invalidate.
3. **Both.** They are independent: (1) is about what the rule means, (2) is
   about whether a human decision persists.

**Verified:** the plan above is a dry run — `dedup_plan.py` never writes to
`papers` or `merges`, and nothing was executed. The corpus is unchanged at
183,167 papers.

---

## 2026-08-12: Cascade candidate generation costs 3 h 55 m, and it is one seq scan

**Measured:** a full `--rebuild` of the dedup plan on 183,167 papers took
**3 h 55 m 11 s** wall clock. This was the last unmeasured component of the
PubMed cost estimate, and it dominates every other component by an order of
magnitude — fetch is ~7 minutes for the entire available pool, embedding
22-51 minutes, the HNSW rebuild 36-41 seconds.

**Where it goes:** sampled mid-run, `pg_stat_activity` showed one statement
holding two parallel workers for **8m57s**:

```sql
CREATE TABLE dd_preprints AS
SELECT p.id FROM papers p
WHERE p.arxiv_id IS NOT NULL OR p.doi LIKE '%/preprint%'
```

A leading-wildcard `LIKE` cannot use an index, so this is a full scan — and
nine minutes for one filtered scan of a 276 MB heap is not explained by
corpus size. That is one step of seven.

**Why a rebuild is not optional post-PubMed:** the `dd_*` scratch tables
are materialized from the corpus, so new papers invalidate them. Every
future cascade run pays this.

**Recommendation, not applied:** the cascade needs its own optimization
pass before the pull, and it should be measured the way the search paths
were — `EXPLAIN ANALYZE` per step, committed to `docs/plans/`. The last
time this code was slow the cause was quadratic self-joins, found by
reading a plan rather than guessing; there is no reason to think this one
is different in kind. Paying four hours per cascade run in a loop is how
that one survived as long as it did.

---

## 2026-08-13: I attributed 537 seconds to the wrong statement, then to the wrong step

**The claim, retracted:** that the cascade's cost was a
`LIKE '%/preprint%'` leading-wildcard scan, evidenced by
`pg_stat_activity` showing that statement at 8m57s.

**How it was found:** Kishan, on arithmetic alone. The exact-scan baseline
reads the same 276 MiB heap AND does 183,167 x 384-dim halfvec distance
computations in 60.9 ms warm. 537 s is 8,818x that, implying ~2.9 ms per
row for a substring search over a ~30-character DOI, where `memmem` over 30
bytes is tens of nanoseconds. Five orders of magnitude off.

**Measured:** the statement runs in **11.5 s** cold and the whole
CREATE TABLE AS, write included, in **3.4 s** warm — 27,904 rows out. A
concurrent wait sampler caught only `IO/DataFileRead`. Within the 11.5 s
the `LIKE` is not even the cost: **7.3 s is the correlated `EXISTS` over
source_records**, whose `raw->>'type'` extracts JSONB from 199,382 rows,
and 1.0 s is JIT compilation.

**Two errors, and the second is the instructive one:**

1. Wrong mechanism inside the statement — the `LIKE` is one of eight OR'd
   predicates and the cheapest of them.
2. **Wrong statement entirely.** `bench/dedup_plan.py` sends its scratch
   build as ONE multi-statement string. For a multi-statement simple query,
   `pg_stat_activity.query` holds the WHOLE batch and `query_start` is the
   batch's start. My probe did `left(regexp_replace(query,...), 110)`,
   which returned the batch's first 110 characters — the beginning of
   `CREATE TABLE dd_preprints` — for any statement in the batch. So
   "8m57s" was the elapsed time of the entire scratch build to that point,
   attributed by my truncation to whichever statement happened to be first
   in the file.

**The lesson, which is not "sample more carefully":** `pg_stat_activity`
reports elapsed, not work, and a blocked query, a spilling sort, and a
serialized batch all look identical in it. The instrument had no power to
distinguish them, and I read a specific mechanism out of it anyway. The
project's own rule already covers this — the last time this code was slow
the cause was quadratic self-joins found by reading a PLAN.

**Verified:** `EXPLAIN (ANALYZE, BUFFERS)` per step, plans in
`docs/plans/`. The corpus was not modified; the probe wrote to a throwaway
table and dropped it.

---

## 2026-08-13: 46.7 million pairs to produce 1,616 — the cascade is still quadratic, inside the blocks

**Measured, on the current corpus:**

| | |
|---|---|
| rows in `dd_sn` (papers x authors, deduped) | 737,487 |
| (surname, year) blocks | 322,447 |
| largest block | **wang / 2025 — 1,966 rows** |
| **candidate pairs generated before any filter** | **46,730,069** |
| rows surviving into `dd_scored` | **1,616** |

Top blocks: wang/2025 1.93M pairs, wang/2024 1.59M, li/2025 1.42M,
zhang/2025 1.38M. The top eight blocks alone are ~11.4M pairs, 24% of the
total. At roughly 0.25 ms per trigram `similarity()` call, 46.7M calls is
about 3.2 hours — which accounts for the measured **3 h 55 m** wall clock
for a full `--rebuild`.

**So the 2026-07-31 entry "the dedup cascade was quadratic as first
written" is only half-retired.** Blocking on (surname, year) removed the
all-pairs join across the corpus and left an all-pairs join INSIDE each
block. Where the blocking key is uninformative — a common surname in a
recent year — the block is thousands of rows and the quadratic is intact.
The proportional length prefilter is inside the join condition, so it
prunes pairs but only after they are enumerated.

**Directions, none applied — this is a behaviour change and Kishan's:**
cap block size and route oversized blocks to review; extend the blocking
key so common surnames split (surname + year + first title character, say);
or prefilter on something cheaper than trigram similarity before scoring.
Each changes which duplicates are FOUND, so each needs its own precision
measurement rather than a wall-clock argument.

**On `--rebuild` being all-pairs (Kishan's question):** it is.
`dd_sn` is built from every paper and `dd_scored` self-joins all of it, so
adding PubMed regenerates every existing-vs-existing pair whose
relationship cannot have changed. An incremental `(new x all)` shape is
available without new invariants — build `dd_sn_new` for the arriving
papers, join it against the full `dd_sn` — and it is strictly less work.
But it is not the order-of-magnitude win it sounds like: if new papers are
~12% of a block, `n_new x n_all` is ~24% of `n_all^2 / 2`, so a 4x
reduction against a 4-hour baseline. **The block size is the problem; the
join shape is a multiplier on it.**

---

## 2026-08-13: The precision measurement's strata are defined by the same attribution the cap depends on

**The question:** DECISION-3c caps `title_exact` groups at 2 on a measured
precision of 0.684. The cap binds on a group's ATTRIBUTED strategy, which
is the earliest contributing strategy in `ORDER`, and `abstract_hash`
precedes `title_exact`. The proposed fix was to bind the cap on every
contributing strategy instead. Kishan asked whether the labeling harness
stratified the same way, because if it did, the fix contradicts the
measurement it claims to honour.

**It did.** `bench/dedup_sample.py` builds its accepted strata with

```sql
SELECT ... FROM merges
WHERE merged_from ? 'deleted_papers' AND strategy = %(strategy)s
```

and `merges.strategy` is written by `dedup_execute` from
`best[root]` — the same earliest-in-`ORDER` attribution. So:

* `acc_abstract_hash` (n=11, **precision 1.000**) contains groups whose
  earliest contributing strategy is abstract_hash. Because abstract_hash
  precedes title_exact, that stratum INCLUDES large groups that also carry
  title_exact edges.
* `acc_title_exact_group` (n=19, **precision 0.684**) contains only groups
  with title_exact edges and no abstract_hash, doi_exact, jmir_doi or
  id_exact edge.

The two strata are disjoint by attribution, not by content.

**Consequence: the cap rule is not choosable yet.** Binding the cap on all
contributing strategies would apply a bound derived from title_exact's
0.684 to groups that the labels scored at 1.000 under abstract_hash —
contradicting the measurement rather than honouring it. Kishan's framing of
the argument is also the correct one: the reason to prefer a max over
contributing strategies is **order-invariance**, not strictness. A cap that
binds on "earliest in ORDER" is a function of a list's ordering, and the
122-group re-attribution between two runs of the same corpus shows it is
also a function of run history.

**What would resolve it:** a second labeling pass that stratifies on
CONTENT rather than attribution — sample by which strategies contributed
edges, not by which one won the ORDER race — with the taxonomy fixed in
advance. That is DECISION-3c's own stated revisit condition, and it names
title_exact's 0.684 off n=19 as the number most worth re-measuring because
it drove a rule change. **That condition is now met by evidence**, and the
evidence is that the strata themselves are attribution artifacts.

---

## 2026-08-13: Pair-level negative constraints, costed against the group-keyed options

**The problem:** `dedup_review` is write-only, so a human decision to hold
a group back survives only until the next planning run. Both options
recorded on 2026-08-12 are GROUP-keyed, which is why the second carries a
corpus-invalidation caveat: a group is a set of ids that a later corpus can
dissolve.

**Kishan's third option, recorded here as the preferred shape:** store the
judgment at PAIR level — "A and B are not duplicates" as a negative
constraint. Durable for a structural reason rather than a policy one: if
both papers exist the judgment holds regardless of what else joined the
component, and if either is gone the constraint is moot rather than wrong.
There is no state in which a stored pair judgment becomes silently
incorrect.

**The union-find complication and its resolution.** A component containing
a negative pair cannot simply be merged, and splitting it is ambiguous —
which side each other member falls on depends on edge insertion order, so
two runs can split the same component differently. **Refuse the whole
component and route it to review.** Deterministic, order-independent, and
consistent with the project's standing preference that under-merging is
safer than over-merging (DECISION-1c, DECISION-3c).

**Cost, against the other two:**

| option | keyed on | durable across corpus change | new invariants | work |
|---|---|---|---|---|
| 1. cap on all contributing strategies | rule | n/a | none | small, but blocked on the stratification question above |
| 2. planner reads `dedup_review` | group (member set) | **no** — a dissolved group silently stops matching | "a review row's member set is still meaningful" | small |
| 3. pair-level negative constraints | pair | **yes** — moot, never wrong | none; refusal rule is total | table + refusal check in grouping; the labeling harness already emits pair-level labels |

**Option 3 needs no new invariant**, which is the property options 1 and 2
lack, and the shape already exists: `bench/labels/dedup_pairs.json` is 120
hand-labeled PAIRS. The 'n' labels in it are exactly this constraint,
already collected and currently used only for scoring.

**Not implemented.** All three change dedup behaviour.

---

## 2026-08-13: The four hours was probably the laptop asleep, and every number I built on it was wrong

**Retracted, in order:** (a) that a `LIKE` was the mechanism; (b) that
`dd_preprints` was the step; (c) that `similarity()` costs 0.25 ms;
(d) that `dd_scored` is 83% of the runtime; (e) that the cascade takes
3 h 55 m at all.

**How (c) fell:** Kishan noticed the 0.25 ms/call was obtained by dividing
`dd_scored`'s runtime by its pair count, so it could not then explain that
runtime. Measured directly, on real title pairs from the densest block:

| wang/2025 block | rows | time |
|---|---|---|
| with `similarity()` | 737,094 pairs | 2,253 ms |
| without it | 737,094 | 1,055 ms |
| **difference** | 737,094 calls | **1,198 ms = 1.63 us/call** |

**1.63 microseconds, not 250.** Off by 150x, in the direction that made my
story work.

*(Label note: 737,094 is the wang/2025 PAIR count after the title-inequality
and length-band filters — 1,966 rows yield 1,931,595 raw pairs, of which
38% survive. Its near-match to `dd_sn`'s 737,487 total ROWS is a 0.05%
coincidence and not the same quantity; verified against both tables so a
later re-derivation does not read one for the other.)*

**How (d) and (e) fell:** running the real `dd_scored` shape on bounded
subsets, with ANALYZE:

| subset | pairs | time | per pair |
|---|---|---|---|
| q-surnames (sparse blocks) | 82,983 | 1,030 ms | 12.4 us |
| **wang (the densest surname, all years)** | **8,629,321** | **64.3 s** | **7.45 us** |

Extrapolating the dense case to the full 46,730,069 pairs: **~348 s, under
six minutes.** Every other step is seconds. The whole scratch build should
be **15-20 minutes**.

**So what was the 3 h 55 m?** Almost certainly the host asleep. The
evidence:

* During that run the Docker daemon reported the container "Up 8 minutes"
  while its own `CreatedAt` was 3 h 53 m earlier — the VM clock had frozen.
* `pg_stat_activity` inside the VM reported the batch at 8m57s, which is
  consistent with the real work, not with four hours.
* Independently measured per-step costs sum to ~15-20 min. That 8m57s was
  a MID-RUN sample, so it is a lower bound rather than a total, and the
  15-20 min sums four measured steps of seven — `dd_sn_pp`, `dd_scored_pp`
  and the per-table index builds are estimated, not measured.
* The two clocks agree to within 1 second now.

`time` measured host wall clock, which includes sleep. **The VM was not
working for most of those four hours; it was suspended.**

**What this invalidates:** "dedup is 80% of the PubMed wall clock" — twice
over. Dedup is roughly 15-20 minutes against an embed cost of 22-51
minutes, so **embedding is the dominant term after all** and the slice size
matters again.

**The instrument lesson, third variation of the same one:** `time` on a
container measures host wall clock, and a laptop that sleeps makes that
meaningless. A duration measured outside the machine doing the work is not
a measurement of that work. In-VM clocks (`pg_stat_activity`,
`EXPLAIN ANALYZE`) were right the whole time and I overrode them with a
host-side number because it was larger.

**CONFIRMED 2026-08-13.** Re-run under `caffeinate -dimsu`, with VM-side
timestamps bracketing it:

| | |
|---|---|
| VM start | 18:46:29 |
| VM end | 18:56:40 |
| **VM elapsed** | **10 m 11 s** |
| host `time` | 10 m 09 s |

Host and VM agree to within 2 seconds when the host stays awake, against a
~3h45m divergence when it did not. **The full cascade `--rebuild` is 10
minutes, not 3 h 55 m — a 23x inflation, entirely host sleep.**

The per-step extrapolation predicted 15-20 min against an actual 10, so it
was conservative by ~1.5-2x — the right direction for an estimate, and the
gap is the four steps that were estimated rather than measured.

---

## 2026-08-13: The trigram index loses to the blocking it was supposed to replace

**The hypothesis** (Kishan): `papers_title_trgm_idx` is live — VACUUM FULL
shrank it 70 -> 39 MB — and `similarity(a,b) > t` is not indexable while
the `%` operator is, via `gin_trgm_ops`. That is one index probe per record,
183,167 probes instead of 46.7M comparisons, with the index doing the
blocking. If it lands, the framing changes from "the blocking key is
uninformative" to "blocking stands in for an index that already exists".

**The index is on the right column:**
`CREATE INDEX papers_title_trgm_idx ON papers USING gin (title_norm gin_trgm_ops)`
— `title_norm` is exactly what the cascade compares, no expression index
needed. And the rewrite IS a superset by construction: `%` at threshold
0.85 is definitionally `similarity >= 0.85`, and dropping the surname
requirement and the length band only adds pairs.

**Measured, at `pg_trgm.similarity_threshold = 0.85`:**

| | |
|---|---|
| per-probe cost, first 500 by id | 33.6 ms |
| per-probe cost, random 500 (TABLESAMPLE) | 24.7 ms |
| buffers per probe | ~988 |
| **extrapolated to 183,167 probes** | **1.3 - 1.7 hours** |

Against a blocked nested loop measured at **~6 minutes**. **The index is
10-17x SLOWER.**

**Why:** a GIN trigram probe is not O(1). A ~100-character title yields
~100 trigrams, and the probe scans and intersects a posting list per
trigram — ~988 buffer accesses, 25-34 ms. The blocked nested loop does
~1.9 buffer accesses per pair because (surname, year) is a b-tree equality
lookup into a 116 MB table that stays cached.

**So the hypothesis is falsified, and cleanly.** For near-duplicate
detection at a HIGH threshold over short strings, blocking on a cheap
equality key beats an inverted index on the expensive one. GIN trigram
search earns its keep for low-threshold fuzzy lookup of ONE string against
a corpus, which is a different query.

**Consequence for the blocking-key decision:** it does not need making.
Nothing needs to be traded against recall, because there is no performance
problem to buy off — the cascade is ~20 minutes, and the pair count that
looked alarming costs ~6 of them.

**Left as a cheap improvement, not applied:** `similarity()` has the
default `procost = 1`, so the planner prices it as cheaply as an integer
comparison and orders it AHEAD of the numeric length band that exists to
avoid calling it (visible in the Filter clause order). Raising its cost, or
restructuring so the band is evaluated first, would cut some of the 46.7M
calls. At 1.63 us each the whole saving is bounded by ~76 seconds.

---

## 2026-08-13: Pair-level negative constraints — the migration and the planner change

Design recorded before wiring, per Kishan. Migration written
(`0013_dedup_negative_pairs.sql`), planner change NOT applied.

**The table.** `(a, b, source, note, decided_at)`, PK `(a, b)`, `CHECK
(a < b)` so a pair has one representation, `ON DELETE CASCADE` from
`papers`.

The cascade direction differs from `screenings` deliberately. A screening
is a judgment ABOUT a paper and must outlive a merge — its FK is RESTRICT
so a merge that would orphan it fails loudly. A negative pair is a judgment
about a RELATIONSHIP: when one side stops existing the relationship is
moot, not lost. That asymmetry is the whole durability argument, so it is
enforced in the schema rather than in a comment.

**The planner change, exactly three additions to `dedup_execute`:**

1. Load the constraint set once, as a Python `set` of `(a, b)` tuples with
   `a < b`. It is small — 120 hand-labeled pairs today — so a set membership
   test costs nothing against the 1,616 candidate pairs.

2. After union-find builds components, mark any component containing a
   negative pair. The test is over pairs WITHIN the component, not over the
   candidate edges: a negative pair can be joined transitively by two
   positive edges without ever appearing as a candidate itself, and that is
   precisely the case worth catching.

   ```python
   def refused(members: list[int]) -> tuple[int, int] | None:
       for i, x in enumerate(members):
           for y in members[i + 1:]:
               if (min(x, y), max(x, y)) in negatives:
                   return (min(x, y), max(x, y))
       return None
   ```

   Quadratic in component size, which is bounded by MAX_GROUP_SIZE (8), so
   at most 28 lookups per component.

3. Route a marked component to `dedup_review` with the offending pair in
   the note, instead of merging it. **Refuse the whole component; do not
   split it.** Splitting asks which side each other member falls on, and
   the answer depends on edge insertion order, so two runs can split the
   same component differently. Refusal is deterministic and
   order-independent, and it matches the standing preference that
   under-merging is safer than over-merging (DECISION-1c, DECISION-3c).

**What it does NOT do:** it does not stop the 122 groups from being
re-proposed by the planner. `dedup_plan` still generates them as
candidates; the executor refuses them at merge time. That is the right
layer — the planner's job is to find candidates, and a candidate that a
human has ruled on is still a candidate, just a resolved one.

**Seeding it from data that already exists:** `bench/labels/dedup_pairs.json`
holds 120 hand-labeled pairs, and its `n` labels ARE this constraint. They
are currently used only for scoring precision. A loader would turn the
measurement into an artifact the system acts on, which is the first time in
this project that hand-labeling would feed back into behaviour rather than
only into a number.

**Open question for Kishan, not decided here:** whether the 122 unwound
groups should be seeded as negative pairs directly. They were unwound on an
AGGREGATE precision measurement (0.684 at n=19), not on a per-pair
judgment, so seeding them would record 122 group-level inferences as though
they were pair-level observations. The conservative reading is that they
stay in `dedup_review` awaiting the second labeling pass DECISION-3c calls
for, and only labeled pairs become constraints.

---

## 2026-08-13: The sustained encode rate does not exist in this repo

**The audit Kishan asked for, answered plainly: the data is not there.**

**Does the encode log in-process rates or host elapsed?** Both, but only
one survived. `api/embed/backfill.py` prints per-batch progress and a final
`"wrote N embeddings in Xs (Y docs/s)"` computed from `time.perf_counter()`
— CLOCK_MONOTONIC, which does not advance while the host sleeps, so it is
the correct instrument. **That output was never saved.** There is no
encode log anywhere in the repo. findings.md's "10+ hours" cites Kishan's
observation of an overnight run, and progress.md's "Kishan's overnight run,
10+ h" says the same. The sleep-immune number was printed to a terminal and
lost.

**Does 8.8 survive?** Unanswerable, and for a sharper reason than expected.
The 8.8-12.7 band is recorded in progress.md's **pre-encode prerequisites,
dated 2026-07-30** — it comes from the resumability runs, which totalled
5,000 rows at roughly 8 minutes each. So it is a SHORT-run band, measured
before the full encode, and it cannot show sustained thermal behaviour any
more than the 13.2 docs/s benchmark could. There is no discontinuity to
look for because there is no windowed log.

**So the honest state of the three numbers:**

| number | instrument | duration measured |
|---|---|---|
| 13.2 docs/s | in-process benchmark | 75 s |
| 8.8-12.7 docs/s | in-process, real runs | ~8 min each |
| "10+ h" | host wall clock, unlogged | overnight, includes any sleep |

**No sustained in-process rate for this hardware exists.** The withdrawn
2.4x compared row 3 against row 1; the 1.1-1.6x I offered instead compares
row 2 against row 1, and row 2 is not sustained either. Both factors are
built from short runs.

**Consequence for the PubMed estimate, which is where this matters:** the
encode there is ~16,800 papers, 22-32 minutes — a duration much closer to
the 8-minute runs the band came from than to a 10-hour one. The band is
defensible for THIS workload and would not be for a full-corpus re-encode.
Propagating the survival CI [69.5%, 76.6%] rather than the 73.2% point
gives **21-33 minutes**.

**Fix, one line of operator discipline:** the pull's encode must have its
stdout captured. `backfill.py` already prints the sleep-immune rate; the
only failure was not keeping it. That single saved line would give this
project its first sustained throughput measurement and retire both
factors.

**Also audited:** 15 bench/ and api/ scripts time with `perf_counter`
inside the container and are sleep-immune. The only host-side durations
this project has published are the cascade's 3 h 55 m (retracted,
re-measured at 10 m 11 s) and VACUUM FULL's 2 m 42 s (short, observed
live, not corrected).

---

## 2026-08-13: The shipped cascade's precision and recall, rescored

**What changed and what did not.** `api/dedup/rules.py` has been unchanged
since DECISION-3c (`1c6385a`) and the executor's `cap_for` with it. The
2026-08-13 edit touched `bench/dedup_plan.py`'s REPORTING only — it had
been filtering on the global `MAX_GROUP_SIZE` while the executor filtered
on `max_group_size(strategy)`. No cap was fixed; a dry run was made to
model the rule that actually runs. (My commit message called it a "cap
fix", which was wrong.)

**But the pair on the resume was measured under the OLD rule.** The 120
labels were collected against merges executed with the global cap of 8.
DECISION-3c then capped title_exact at 2 *because of* those labels, and
the pair was never recomputed.

Rescoring the same labels with `acc_title_exact_group` moved from merged
to refused, which is exactly what the cap change does:

| | precision | recall | F1 |
|---|---|---|---|
| as sampled (global cap 8) | 0.9568 [0.9035, 0.9983] | 0.9728 [0.9589, 0.9880] | 0.9647 |
| **as shipped (title_exact cap 2)** | **0.9594** [0.9053, 1.0000] | **0.9662** [0.9524, 0.9813] | 0.9628 |

Precision up, recall down, both inside the intervals — the stratum's
population is 122 against `acc_abstract_hash`'s 6,662, so reweighting it
moves the totals about a point.

**This is a rescoring under UNCHANGED STRATUM WEIGHTS, not a
re-measurement.** The weights come from the populations observed when the
sample was drawn, and under the shipped rule
`acc_title_exact_group`'s accepted population is **zero** — those groups
are flagged now, not merged. So the inverse-probability weights being
applied are the old population's, and a fresh sample drawn from the cap-2
cascade would not produce them. What the table shows is "what would these
120 labels have scored had the cap been 2 when they were drawn", which is
the right question for a resume number and is NOT the same as measuring
the shipped cascade on its own sample. `--as-shipped` makes it
reproducible; only a fresh draw makes it a measurement.

**Resume consequence:** recall should read **0.966**, not 0.973.

---

## 2026-08-13: The PubMed weights were never a decision, so there is no deviation

**Found by asking what the weights are FOR.** DECISION-2 states its
composition weights explicitly and ties them to retrieval evaluation. The
PubMed weights — 0.40 / 0.25 / 0.20 / 0.15 across clinical-nlp,
simplification, mental-health-nlp, biomedical-ner — appear in **no
decision record**. They live in `api/ingest/pubmed.py` with the comment
"DECISION-2 domains in PubMed's field syntax". An assistant chose them.

**Consequences, and they retire work:**

* **"Realized composition as a labeled deviation" is the wrong frame.** A
  deviation needs a target. There is none, only a mapping of DECISION-2's
  domains into PubMed syntax. The posture is: take the pool as a coverage
  decision under DECISION-2f's standard ("new specialty sources in Phase 3
  grow it for coverage, not for roundness"), and record the composition it
  produces as the composition.
* **The biomedical-ner "gap" does not exist.** It was defined as 1,455
  available against a 0.15 weight. Under a coverage standard, 1,455 is what
  that term yields. The substitute measurements taken to close it — no date
  bound +153, adding `"relation extraction"[tiab]` +493 — were answering a
  question nobody asked. They are kept as data, not as options.
* **The query-terms question shrinks** from "how do we hit the weights" to
  "are these four the right coverage", which is a DECISION-2-shaped
  question about what the corpus is for.

**And a distinction I had collapsed.** I wrote that the pull moves "the
engineered specialty share the right way". Two decisions use the word
"share" for different quantities: DECISION-2's knob is the
**clinical-informatics** share (10%), tuned for hard-negative difficulty;
DECISION-2f's protected quantity is the **overall specialty** share
(62.2%), defended against general-nlp dilution. The pull moves the second
up. Whether that is good under the first is open — DECISION-2 sorts
specialty mass into "on-topic mass" (welcomed) and "not random hard
negatives" (cut in half), and nothing has established which side PubMed's
terms fall on. Recorded in decisions.md beside DECISION-2.

**Interval on the round number.** At survival [69.5%, 76.6%] the post-pull
corpus is **199,152 to 200,785**, so the low end does NOT cross 200,000.
The DECISION-2f observation stands — the round number it declined to
manufacture may arrive as a side effect of coverage — but it is a
coin-flip, not a fact.

---

## 2026-08-13: The screenings FK is a silent contamination path into the precision number

**Measured** (`tests/test_screening_survives_merge.py`): merging a paper
that carries a screening decision raises `ForeignKeyViolation`.
`merge_group` repoints `source_records` and `merges` but not `screenings`,
and `screenings.paper_id` has no `ON DELETE`.

**I framed this as "safe for the judgment, blocking for dedup". That
understates it.** The full path, per Kishan:

1. the merge raises;
2. `dedup_execute` catches per-group exceptions and continues;
3. the group is skipped, so **the cascade silently under-merges**;
4. it under-merges on exactly the papers a human cared enough to screen;
5. post-PubMed precision/recall is then measured on that cascade, with
   **recall understated** and the cause invisible unless somebody reads the
   error counts in the results file.

So it is not a workflow annoyance. It is a contamination path into the
number Kishan's labeling session pays for, and it is the kind that leaves
no trace in the metric itself — the same shape as the stale ground truth
and the sleeping clock.

**Recommended collision rule (Kishan's call, not implemented):** when the
survivor and the loser carry decisions in the same collection —

* **same decision on both** -> collapse the rows silently. There is no
  conflict to resolve, and refusing would be pedantry.
* **different decisions** -> refuse the merge and route the group to
  `dedup_review`. A human disagreed with themselves about two records that
  turned out to be one paper; that is exactly what review is for.

**Rules rejected, with reasons:**

* *most-recent-wins* — makes the outcome depend on timestamp order, which
  is the order-dependence three exchanges were just spent removing from the
  cap rule.
* *survivor's-decision-wins* — borrows DECISION-3b's survivorship logic,
  but survivorship is chosen on METADATA QUALITY (which record has the DOI,
  the abstract, the citations) and says nothing about which of two human
  judgments was better considered.

**Tests to add once the rule is picked:** the pinned refusal test stays as
the pre-fix baseline, plus one per branch — same-decision collapses to one
row on the survivor, different-decisions refuses and lands in
`dedup_review`.
