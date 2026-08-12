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

**Symptom as first reported:** dedup removed 13,726 net papers, so the
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
