"""Find queries where each mode wins, for the Phase 2 demo.

There are no relevance labels for these queries, so "wins" is not scored
here — it is SHOWN. For each candidate the script reports what each mode
puts in its top-10 and, crucially, what hybrid pulled from each arm that
the other arm missed. A hybrid "win" worth demoing is one where its top-10
contains results that ONLY bm25 found and results that ONLY vector found:
that is RRF doing the thing the mode exists to do, visible on screen.

Judgment about whether those results are actually relevant stays with the
human reading the titles. This narrows 20 candidates to 3.
"""

import json
import os
from pathlib import Path
from typing import Any

import psycopg

from api.embed.onnx_encoder import OnnxEncoder
from api.embed.texts import query_text
from api.search.bm25 import search_bm25
from api.search.fusion import HYBRID_DEFAULT_EF_SEARCH, search_hybrid
from api.search.vector import DEFAULT_EF_SEARCH, search_vector

K = 10

CANDIDATES = [
    # lexically exact, rare terms — bm25 territory
    "ts_rank_cd BM25 ranking",
    "reciprocal rank fusion",
    "MIMIC-III clinical notes",
    "SNOMED CT concept normalization",
    "readability formulas for health texts",
    # paraphrases with little lexical overlap — vector territory
    "making hospital paperwork easier for patients to read",
    "spotting people at risk of self-harm from what they write online",
    "teaching computers to find drug names in doctors notes",
    "why medical jargon confuses ordinary readers",
    "shortening long research papers automatically",
    # mixed: a rare term plus a concept
    "BERT for de-identification of clinical records",
    "transformer models for patient education materials",
    "zero-shot classification of biomedical abstracts",
    "explainable AI for clinical decision support",
    "low-resource languages in medical NLP",
]


def main() -> None:
    encoder = OnnxEncoder(os.environ["EMBED_MODEL_DIR"])
    out: list[dict[str, Any]] = []
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
        conn.execute("CREATE EXTENSION IF NOT EXISTS pg_prewarm")
        conn.execute("SELECT pg_prewarm('papers_embed_idx','read'), pg_prewarm('papers','read')")
        for q in CANDIDATES:
            vec = [float(x) for x in encoder.encode([query_text(q)])[0]]
            bm = search_bm25(conn, query=q, k=K)
            ve = search_vector(conn, query_vec=vec, k=K, ef_search=DEFAULT_EF_SEARCH)
            hy = search_hybrid(
                conn,
                query=q,
                query_vec=vec,
                k=K,
                depth=200,
                ef_search=HYBRID_DEFAULT_EF_SEARCH,
            )
            b_ids = {r["id"] for r in bm}
            v_ids = {r["id"] for r in ve}
            h_ids = {r["id"] for r in hy}
            only_b = b_ids - v_ids
            only_v = v_ids - b_ids
            out.append(
                {
                    "query": q,
                    "bm25_n": len(bm),
                    "bm25_only": len(only_b),
                    "vector_only": len(only_v),
                    "overlap_bm25_vector": len(b_ids & v_ids),
                    # the demo-worthy signal: hybrid surfacing both arms' finds
                    "hybrid_from_bm25_only": len(h_ids & only_b),
                    "hybrid_from_vector_only": len(h_ids & only_v),
                    "hybrid_titles": [r["title"][:70] for r in hy[:5]],
                    "bm25_titles": [r["title"][:70] for r in bm[:3]],
                    "vector_titles": [r["title"][:70] for r in ve[:3]],
                }
            )
    Path(__file__).parent.joinpath("results_demo_queries.json").write_text(
        json.dumps(out, indent=2)
    )
    print(f"{'query':52s} {'bm25':>5} {'b-only':>7} {'v-only':>7} {'h<-b':>5} {'h<-v':>5}")
    for r in out:
        print(
            f"{r['query'][:52]:52s} {r['bm25_n']:>5} {r['bm25_only']:>7} "
            f"{r['vector_only']:>7} {r['hybrid_from_bm25_only']:>5} "
            f"{r['hybrid_from_vector_only']:>5}"
        )


if __name__ == "__main__":
    main()
