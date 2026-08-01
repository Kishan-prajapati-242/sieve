"""Build the stratified sample for hand-labeling dedup precision AND recall.

Core 2 of the brief. Two design points that decide whether the numbers
mean anything:

  1. REFUSED pairs are sampled too. Precision alone says how often the
     cascade was right when it merged; it says nothing about what it
     missed. Half the frame is pairs the cascade rejected — below
     threshold, enumerator-refused, part-prefix-refused, size-capped.

  2. Strata carry their POPULATION size, so bench/dedup_precision.py can
     weight each label by N_h/n_h. Without that, sampling 30 pairs from a
     3,631-pair stratum and 30 from a 14-pair stratum would silently claim
     the small stratum is half the corpus.

Accepted pairs come from merges snapshots (the papers no longer exist, but
the snapshot preserved both sides in full). Refused pairs come from the
dd_* scratch tables and dedup_review against the live corpus.

    docker compose run --rm --no-deps -v ./bench:/app/bench -v ./api:/app/api \\
      -e DATABASE_URL=... test python -m bench.dedup_sample
"""

import json
import os
import random
from pathlib import Path
from typing import Any

import psycopg

from api.dedup.rules import (
    ABSTRACT_TITLE_SIM,
    TRGM_THRESHOLD,
    enum_siblings_sql,
    part_siblings_sql,
)

TARGET = 120
SEED = 20260801

# Allocation is EXPLICIT, not formula-derived, because the three groups of
# strata are being bought for different reasons (Kishan, 2026-08-01):
#
#   acc_title_exact_group  20 — decides the MAX_GROUP_SIZE question, and 6
#                               labels cannot tell 20% bad from 60% bad.
#   every ref_* stratum    unchanged — they are the ONLY recall signal, so
#                               thinning them buys nothing measurable.
#   the large acc_* strata thinned proportionally to fit 120 total.
#
# Consequence, stated rather than buried: acc_title_trgm and acc_jmir_doi
# land at 3 labels each. Their PER-STRATUM precision is uninformative at
# that size; they still contribute correctly to the weighted overall
# estimate, which is what the inverse-probability weights are for.
ALLOC = {
    "acc_abstract_hash": 12,
    "acc_title_exact_pair": 8,
    "acc_title_exact_group": 20,
    "acc_title_trgm": 3,
    "acc_preprint_trgm": 5,
    "acc_jmir_doi": 3,
    "ref_below_threshold_sameyear": 12,
    "ref_below_threshold_preprint": 6,
    "ref_enumerated_sibling": 15,
    "ref_part_sibling": 5,
    "ref_abstract_low_title": 15,
    "ref_size_capped": 16,
}

ENUM = enum_siblings_sql("pa.title_norm", "pb.title_norm")
PART = part_siblings_sql("pa.title_norm", "pb.title_norm")

# Each stratum: a SQL query returning (a_id, b_id, sim). Accepted strata read
# the snapshots; refused strata read live rows.
ACCEPTED_SQL = """
SELECT id AS merge_id,
       (merged_from->'survivor_before') AS sa,
       (merged_from->'deleted_papers'->0) AS sb,
       similarity, strategy,
       jsonb_array_length(merged_from->'deleted_papers') + 1 AS grp_size
FROM merges
WHERE merged_from ? 'deleted_papers' AND strategy = %(strategy)s
  AND jsonb_array_length(merged_from->'deleted_papers') + 1 {size_clause}
"""

REFUSED_SQL: dict[str, str] = {
    # Scored below the merge gate, but above the sweep floor.
    "ref_below_threshold_sameyear": f"""
        SELECT s.a, s.b, s.sim FROM dd_scored s
        JOIN papers pa ON pa.id=s.a JOIN papers pb ON pb.id=s.b
        WHERE s.sim >= 0.85 AND s.sim < {TRGM_THRESHOLD} AND NOT ({ENUM}) AND NOT ({PART})
    """,
    "ref_below_threshold_preprint": f"""
        SELECT s.a, s.b, s.sim FROM dd_scored_pp s
        JOIN papers pa ON pa.id=s.a JOIN papers pb ON pb.id=s.b
        WHERE s.sim >= 0.85 AND s.sim < {TRGM_THRESHOLD} AND NOT ({ENUM}) AND NOT ({PART})
    """,
    # Refused by a rule despite scoring above the gate.
    "ref_enumerated_sibling": f"""
        SELECT s.a, s.b, s.sim FROM dd_scored s
        JOIN papers pa ON pa.id=s.a JOIN papers pb ON pb.id=s.b
        WHERE s.sim >= {TRGM_THRESHOLD} AND ({ENUM})
    """,
    "ref_part_sibling": f"""
        SELECT s.a, s.b, s.sim FROM dd_scored s
        JOIN papers pa ON pa.id=s.a JOIN papers pb ON pb.id=s.b
        WHERE s.sim >= {TRGM_THRESHOLD} AND NOT ({ENUM}) AND ({PART})
    """,
    # Same abstract, but titles too dissimilar for the sibling rule.
    "ref_abstract_low_title": f"""
        SELECT s.a, s.b, s.sim FROM dd_abs s
        JOIN papers pa ON pa.id=s.a JOIN papers pb ON pb.id=s.b
        WHERE s.sim < {ABSTRACT_TITLE_SIM}
    """,
    # Inside a group that exceeded MAX_GROUP_SIZE and was never merged.
    "ref_size_capped": """
        SELECT r.member_ids[1] AS a, r.member_ids[i] AS b, NULL::float8 AS sim
        FROM dedup_review r, generate_subscripts(r.member_ids, 1) i
        WHERE i > 1
    """,
}

ACCEPTED_STRATA = [
    ("acc_abstract_hash", "abstract_hash", "> 0"),
    ("acc_title_exact_pair", "title_exact", "= 2"),
    ("acc_title_exact_group", "title_exact", ">= 3"),
    ("acc_title_trgm", "title_trgm", "> 0"),
    ("acc_preprint_trgm", "preprint_trgm", "> 0"),
    ("acc_jmir_doi", "jmir_doi", "> 0"),
]

PAPER_FIELDS = "id, doi, title, abstract, year, venue, citation_count, arxiv_id"


def snapshot_side(obj: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": obj.get("id"),
        "doi": obj.get("doi"),
        "title": obj.get("title"),
        "abstract": obj.get("abstract"),
        "year": obj.get("year"),
        "venue": obj.get("venue"),
        "citation_count": obj.get("citation_count"),
        "arxiv_id": obj.get("arxiv_id"),
    }


def main() -> None:
    rng = random.Random(SEED)
    out_dir = Path(__file__).parent / "labels"
    out_dir.mkdir(exist_ok=True)
    strata: dict[str, dict[str, Any]] = {}

    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
        conn.execute("SET statement_timeout='20min'")

        for name, strategy, size_clause in ACCEPTED_STRATA:
            rows = conn.execute(
                ACCEPTED_SQL.format(size_clause=size_clause), {"strategy": strategy}
            ).fetchall()
            strata[name] = {
                "population": len(rows),
                "verdict": "merged",
                "rows": [
                    {
                        "a": snapshot_side(r[1]),
                        "b": snapshot_side(r[2]),
                        "similarity": float(r[3]) if r[3] is not None else None,
                        "strategy": r[4],
                        "group_size": r[5],
                    }
                    for r in rows
                ],
            }

        for name, sql in REFUSED_SQL.items():
            pairs = conn.execute(sql).fetchall()
            strata[name] = {"population": len(pairs), "verdict": "refused", "rows": []}
            # Materialise only what we sample, to avoid fetching whole tables.
            strata[name]["_pairs"] = pairs

    names = list(strata)
    alloc = {n: min(ALLOC.get(n, 5), strata[n]["population"]) for n in names}

    sample: list[dict[str, Any]] = []
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
        for name in names:
            info = strata[name]
            take = alloc[name]
            if info["verdict"] == "merged":
                chosen = (
                    rng.sample(info["rows"], take) if take < len(info["rows"]) else info["rows"]
                )
                for c in chosen:
                    sample.append({"stratum": name, **c, "_verdict": "merged"})
            else:
                pairs = info["_pairs"]
                chosen_pairs = rng.sample(pairs, take) if take < len(pairs) else pairs
                for a, b, sim in chosen_pairs:
                    rows = conn.execute(
                        f"SELECT {PAPER_FIELDS} FROM papers WHERE id = ANY(%s) ORDER BY id",  # noqa: S608
                        ([a, b],),
                    ).fetchall()
                    if len(rows) != 2:
                        continue
                    keys = PAPER_FIELDS.replace(" ", "").split(",")
                    sample.append(
                        {
                            "stratum": name,
                            "a": dict(zip(keys, rows[0], strict=True)),
                            "b": dict(zip(keys, rows[1], strict=True)),
                            "similarity": float(sim) if sim is not None else None,
                            "strategy": name.replace("ref_", ""),
                            "group_size": 2,
                            "_verdict": "refused",
                        }
                    )

    rng.shuffle(sample)  # so the labeller cannot infer verdict from order
    for i, item in enumerate(sample):
        item["pair_id"] = i

    frame = {
        "seed": SEED,
        "target": TARGET,
        "strata": {
            n: {
                "population": strata[n]["population"],
                "sampled": alloc[n],
                "verdict": strata[n]["verdict"],
            }
            for n in names
        },
        "pairs": sample,
    }
    (out_dir / "dedup_sample.json").write_text(json.dumps(frame, indent=1))
    print(f"sampled {len(sample)} pairs across {len(names)} strata")
    for n in names:
        print(f"  {n:32} population {strata[n]['population']:>6}  sampled {alloc[n]:>3}")
    print("\nwritten: bench/labels/dedup_sample.json")


if __name__ == "__main__":
    main()
