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
