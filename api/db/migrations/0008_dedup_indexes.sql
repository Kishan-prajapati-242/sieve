-- Indexes the dedup cascade needs. Added after the cascade's first draft
-- ran for 11 minutes without completing a single strategy: it self-joined
-- papers on unindexed expressions (md5(abstract), arxiv_id, pubmed_id,
-- title_norm+year), which is quadratic on 197K rows.
--
-- The strategies were then rewritten as GROUP BY + star fan-out, which is
-- what actually fixed the complexity (see docs/findings.md 2026-07-31).
-- These indexes still earn their place: the GROUP BY steps use them to
-- avoid full sorts, and the future per-record cascade — one new paper
-- against the corpus, which is how ingestion will call it — is an
-- equality probe per key and would be a seq scan without them.
--
-- Partial WHERE ... IS NOT NULL on the id indexes: 7.7% of papers have no
-- DOI and most have no arXiv or PubMed id, so the partial index is a
-- fraction of the size and NULLs are never dedup keys anyway.

CREATE INDEX papers_arxiv_id_idx ON papers (arxiv_id) WHERE arxiv_id IS NOT NULL;
CREATE INDEX papers_pubmed_id_idx ON papers (pubmed_id) WHERE pubmed_id IS NOT NULL;
CREATE INDEX papers_abstract_md5_idx ON papers (md5(abstract)) WHERE abstract IS NOT NULL;
CREATE INDEX papers_title_year_idx ON papers (title_norm, year);
