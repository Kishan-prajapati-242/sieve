# Hybrid RRF query plan — 196,893 papers, depth=100, k=20, ef_search=100

Captured 2026-07-31 (warm, second run; the cold first run spent 743 ms
reading vector-index pages — shared read=535 — which the warm path hits
in cache). Query: "clinical text simplification", year filter absent.

Node-time decomposition (the per-component timing a single fused
statement cannot expose at request time):

- vector CTE (Index Scan papers_embed_idx -> WindowAgg -> Limit): ~1.8 ms, 100 rows
- bm25 CTE (GIN Bitmap Heap Scan -> Sort -> WindowAgg -> Limit): ~3.6 ms, 75 rows
  (only 75 papers match this tsquery corpus-wide; bm25 depth unfilled)
- Hash Full Join (fusion union): 170 candidates, 5 overlap
- Nested Loop join back to papers + top-N heapsort on RRF score: ~0.8 ms
- total execution: 6.2 ms

```
Limit  (cost=2651.70..2651.75 rows=20 width=522) (actual time=6.080..6.085 rows=20 loops=1)
  Buffers: shared hit=3066
  ->  Sort  (cost=2651.70..2651.95 rows=100 width=522) (actual time=6.078..6.082 rows=20 loops=1)
        Sort Key: (((COALESCE((1.0 / (('60'::smallint + b.rank))::numeric), '0'::numeric) + COALESCE((1.0 / (('60'::smallint + (row_number() OVER (?))))::numeric), '0'::numeric)))::double precision) DESC, p.id
        Sort Method: top-N heapsort  Memory: 39kB
        Buffers: shared hit=3066
        ->  Nested Loop  (cost=1654.28..2649.04 rows=100 width=522) (actual time=5.136..5.976 rows=170 loops=1)
              Buffers: shared hit=3066
              ->  Hash Full Join  (cost=1653.86..1803.29 rows=100 width=32) (actual time=5.119..5.458 rows=170 loops=1)
                    Hash Cond: (papers.id = b.id)
                    Buffers: shared hit=2386
                    ->  Limit  (cost=803.33..951.04 rows=100 width=24) (actual time=1.530..1.839 rows=100 loops=1)
                          Buffers: shared hit=1776
                          ->  WindowAgg  (cost=803.33..291635.49 rows=196893 width=24) (actual time=1.529..1.828 rows=100 loops=1)
                                Buffers: shared hit=1776
                                ->  Index Scan using papers_embed_idx on papers  (cost=803.33..288189.86 rows=196893 width=16) (actual time=1.525..1.785 rows=100 loops=1)
                                      Order By: (embedding <=> '[<384-dim query vector>]'::halfvec)
                                      Buffers: shared hit=1776
                    ->  Hash  (cost=850.09..850.09 rows=35 width=16) (actual time=3.579..3.580 rows=75 loops=1)
                          Buckets: 1024  Batches: 1  Memory Usage: 12kB
                          Buffers: shared hit=610
                          ->  Subquery Scan on b  (cost=848.96..850.09 rows=35 width=16) (actual time=3.526..3.564 rows=75 loops=1)
                                Buffers: shared hit=610
                                ->  Limit  (cost=848.96..849.74 rows=35 width=20) (actual time=3.525..3.555 rows=75 loops=1)
                                      Buffers: shared hit=610
                                      ->  WindowAgg  (cost=848.96..849.74 rows=35 width=20) (actual time=3.525..3.547 rows=75 loops=1)
                                            Buffers: shared hit=610
                                            ->  Sort  (cost=848.96..849.04 rows=35 width=12) (actual time=3.517..3.522 rows=75 loops=1)
                                                  Sort Key: (ts_rank_cd(papers_1.fts, '''clinic'' & ''text'' & ''simplif'''::tsquery)) DESC, papers_1.id
                                                  Sort Method: quicksort  Memory: 27kB
                                                  Buffers: shared hit=610
                                                  ->  Bitmap Heap Scan on papers papers_1  (cost=710.49..848.06 rows=35 width=12) (actual time=2.046..3.479 rows=75 loops=1)
                                                        Recheck Cond: (fts @@ '''clinic'' & ''text'' & ''simplif'''::tsquery)
                                                        Heap Blocks: exact=69
                                                        Buffers: shared hit=610
                                                        ->  Bitmap Index Scan on papers_fts_idx  (cost=0.00..710.48 rows=35 width=0) (actual time=1.845..1.846 rows=75 loops=1)
                                                              Index Cond: (fts @@ '''clinic'' & ''text'' & ''simplif'''::tsquery)
                                                              Buffers: shared hit=238
              ->  Index Scan using papers_pkey on papers p  (cost=0.42..8.44 rows=1 width=498) (actual time=0.002..0.002 rows=1 loops=170)
                    Index Cond: (id = COALESCE(b.id, papers.id))
                    Buffers: shared hit=680
Planning:
  Buffers: shared hit=2
Planning Time: 0.425 ms
Execution Time: 6.160 ms```
