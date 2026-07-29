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

## Template

```text
## DECISION-N: <short title>

**Date:** YYYY-MM-DD

**Decision:** <what was chosen>

**Alternatives considered:** <what else was on the table>

**Why rejected:** <the reasoning, in Kishan's words>

**What would change my mind:** <the conditions under which this gets revisited>
```
