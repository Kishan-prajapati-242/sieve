# Three demo queries, measured 2026-08-13

For the Phase 2 acceptance line: "a query where BM25 wins, one where vector
wins, one where hybrid beats both." Found by `bench/demo_queries.py`, which
runs 15 candidates through all three modes and reports which arm's results
hybrid actually adopted. **Re-run it after the PubMed pull — these are
corpus-dependent.**

**How "wins" was decided, so it can be discounted honestly:** there are no
relevance labels for these queries. The script measures which results each
mode finds *uniquely* and which of those hybrid's top-10 adopts; the
judgment that those results are *better* comes from reading the titles
below. That is weaker than an nDCG number and it is what Phase 4 exists to
replace.

---

## 1. BM25 wins — `reciprocal rank fusion`

An exact multi-word technical phrase. BM25 matches it literally; the dense
model drifts to adjacent-but-different IR concepts.

| mode | top 3 |
|---|---|
| **bm25** | DS4DH at #SMM4H 2023: Zero-Shot Adverse Drug Events Normalization…; **MMMORRF: Multimodal Multilingual MOdularized Reciprocal Rank Fusion**; Rag-Fusion: A New Take on Retrieval Augmented Generation |
| vector | Subset selection based fusion for biomedical IR; Hybrid Retrieval for COVID-19 Literature: Comparing Rank Fusion…; **The Treatment of Ties in Rank-Biased Overlap** |

The tell is vector's third result: rank-biased overlap is a *different*
measure, retrieved because it is semantically near "rank" and "fusion".

**Alternative with a more vivid failure:** `MIMIC-III clinical notes` —
BM25 returns the exact dataset, vector returns **MIMIC-IV** and MIMICause.
Wrong version of the right dataset is a very legible dense-retrieval
failure. Not chosen as primary because BM25's own third result is a paper
titled `2166`.

## 2. Vector wins — `why medical jargon confuses ordinary readers`

Pure paraphrase. No corpus title shares its vocabulary, so **BM25 returns
literally nothing** — the strongest possible version of this demo.

| mode | top 3 |
|---|---|
| bm25 | *(0 results)* |
| **vector** | Laypeople's (Mis)Understanding of Common Medical Acronyms; Evaluation of lexical clarification by patients reading their clinical notes; Terminology in medical reports |

Hybrid's top-10 takes 10 of 10 from the vector arm.

## 3. Hybrid beats both — `BERT for de-identification of clinical records`

A rare exact term (`BERT`) plus a concept (`de-identification`). Each arm
gets half the query.

| mode | top 3 |
|---|---|
| bm25 (only 5 results total) | Utility Preservation of Clinical Text After De-Identification; Automated redaction of names in adverse event reports…; Preserving the Privacy of Language Models |
| vector | De-identifying Clinical Texts using Biomed-Clinical BERTs; Publicly Available Clinical; Publicly Available Clinical BERT Embeddings |
| **hybrid** | **Applying and Sharing pre-trained BERT-models for Named Entity Recognition** …then results from both arms |

BM25 finds de-identification papers that are not BERT-specific; vector
finds BERT papers that are not de-identification-specific. **Hybrid's
top-10 is 5 from each arm**, and its #1 was ranked first by neither.

That is the mode toggle's whole argument, on one screen, with the per-result
`keyword #n / semantic #n` breakdown showing where each result came from.
