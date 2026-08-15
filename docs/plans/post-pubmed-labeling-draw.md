# The post-PubMed labeling draw, designed before the pull

One stratified draw, serving three questions that would otherwise each
demand their own labeling session:

1. **Precision and recall for the post-PubMed cascade.** Required by Phase
   3's acceptance ("three sources ingested and merged with a measured
   precision/recall number") and currently unmeasurable — the shipped pair
   was collected on a corpus with no PubMed in it.
2. **DECISION-3c's revisit condition.** Met on evidence 2026-08-13: its
   strata were built on `merges.strategy`, the earliest contributing
   strategy in `ORDER`, so `acc_abstract_hash` at 1.000 contains
   title_exact-edged groups and `acc_title_exact_group` at 0.684 contains
   only groups with no abstract_hash edge. The cap rule cannot be chosen
   until a draw stratifies on **contributing-strategy composition**.
3. **The no-DOI stratum**, never measured, flagged as an open input since
   the 2026-07-29 finding that 7.7% (now 6.6%) of papers carry no DOI.

Doing the pull first makes these collide productively. Labeling twice is
the outcome worth avoiding, and it is the outcome of pulling later.

## The change that unblocks the cap rule

Strata are keyed on **which strategies contributed edges to the component**,
not on which one won the `ORDER` race. That splits the population the old
sample conflated:

| old stratum | what it actually contained | new strata |
|---|---|---|
| `acc_abstract_hash` (n=11, 1.000) | groups with an abstract_hash edge, INCLUDING large ones that also carry title_exact edges | `acc_abstract_hash_only` + `acc_abstract_hash_x_title_exact` |
| `acc_title_exact_group` (n=19, 0.684) | only groups with NO abstract_hash edge | `acc_title_exact_only_3plus` (now refused — see below) |

`acc_abstract_hash_x_title_exact` is the decisive stratum. If it scores
near 1.000, binding the cap on all contributing strategies would refuse
correct merges and the current earliest-in-ORDER rule is defensible. If it
scores near 0.684, the cap should bind on the strictest contributor and the
order-invariance argument gets its evidence.

## Allocation, 123 pairs

| stratum | n | why this size |
|---|---|---|
| `acc_abstract_hash_x_title_exact` (3+) | **20** | decides the cap rule; 6 could not separate 20% from 60% last time |
| `acc_id_exact_only` | **12** ⚠ | has never fired; 44,517 papers already carry PMIDs so PubMed exercises it immediately — **but see the caveat below, this stratum can come back EMPTY** |
| `no_doi_pairs` (neither side has a DOI) | **15** | never measured, cross-cuts every strategy |
| `acc_abstract_hash_only` | 10 | re-measures the clean 1.000 claim on the split population |
| `acc_title_exact_pair` (size 2) | 8 | the shipped title_exact rule |
| `acc_title_trgm_only` | 5 | |
| `acc_preprint_trgm_only` | 5 | |
| `acc_doi_exact` | 3 | |
| `acc_jmir_doi` | 3 | mechanical identity; 3 is a spot check |
| `ref_size_capped_title_exact` | 12 | the 3+ groups the cap now flags — recall cost of DECISION-3c |

> ### ⚠ `acc_id_exact_only` may draw zero — check BEFORE the session
>
> **Verified 2026-08-14: the arm is not broken, but it has nothing to find
> today.** The current corpus has **0 colliding groups** across 44,517 PMIDs
> and 95 arXiv ids. Unlike `doi_exact` — whose zero has a mechanism, since
> `papers_doi_key` is a UNIQUE index and collisions cannot survive insert —
> `papers_pubmed_id_idx` is a plain partial index, so collisions here are
> structurally possible and simply do not exist.
>
> The arm itself is now PROVEN able to fire
> (`test_id_exact_actually_fires_on_a_shared_pmid`): given two rows sharing a
> PMID and two sharing an arXiv id it proposes exactly those pairs and leaves
> the controls alone. That test did not exist before; the arm had zero
> coverage and zero real firings, which is a universal negative from an
> instrument never shown able to see (findings.md 2026-08-14).
>
> **So the stratum's viability rests entirely on the pull creating
> cross-source PMID collisions, which is plausible but unmeasured.** Run this
> immediately after step 1 of the pull runbook, before allocating the draw:
>
>     SELECT count(*) FROM (
>       SELECT pubmed_id FROM papers WHERE pubmed_id IS NOT NULL
>       GROUP BY pubmed_id HAVING count(*) > 1) t;
>
> **If it returns 0, this stratum does not exist.** Reallocate its 12 pairs
> rather than letting Kishan sit down to a session with an empty bucket —
> `acc_title_year` and `ref_trgm_borderline` are the natural recipients, and
> the reallocation gets recorded because it changes what the precision number
> is measured over.
| `ref_below_threshold_sameyear` | 10 | measured 0.182 miss rate |
| `ref_abstract_low_title` | 8 | measured 0.250 |
| `ref_below_threshold_preprint` | 6 | measured 0.833 — the Ascle gap, confirmed as population fact |
| `ref_enumerated_sibling` | 3 | measured 0.000; a spot check, not a re-measurement |
| `ref_part_sibling` | 3 | measured 0.000; same |
| **total** | **123** | |

Refused strata stay large enough to be the recall signal; the two that
measured a 0.000 miss rate are thinned to spot checks, which is where the
budget for the three new strata comes from.

## What it costs Kishan

**Unknown in minutes, and that is a gap in the harness rather than an
estimate I should invent.** What is on record: the previous draw was cut
from 202 to 120 before labeling, and 120 was completed. So **120 is the
proven one-session size** and 123 is within it.

`bench/dedup_label.py` should record per-pair elapsed time this round, so
the next design has a real pace instead of a precedent.

## Second-annotator pass

Unchanged from last time: the model labels all 123 independently, hidden
during Kishan's pass, and the disagreements are reported with Cohen's
kappa. Last round that was 95.2% raw agreement, kappa 0.905, and the review
of 14 disagreements changed 10 of Kishan's labels — which is also why the
label-drift caveat sits beside the precision number.
