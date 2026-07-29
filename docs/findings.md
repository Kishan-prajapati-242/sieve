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
