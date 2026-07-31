"""Resumable embedding backfill: papers.embedding IS NULL is the work queue.

Resumability is structural, not bolted on. Each loop claims the lowest-id
un-embedded rows, encodes them, and commits the batch in one transaction.
A kill at any moment loses at most one uncommitted batch; the rerun's
NULL-driven SELECT picks up exactly the missing rows. There is nothing to
checkpoint and no state file to corrupt:
  - no duplicates: a row is selected only while embedding IS NULL, and the
    write is an UPDATE by primary key;
  - no gaps: rows leave the queue only by being written, in the same
    transaction as the batch that encoded them;
  - committed rows are never rewritten, so their bytes cannot change.

The alternative — an offset/cursor checkpoint file — was rejected: it can
drift from the database and turns a crash into silent gaps or double work.

The encoder is injected (anything with .encode(list[str]) -> ndarray), so
the loop's crash/resume semantics are tested against the real database
with a cheap deterministic stub; the ONNX encoder plugs in for real runs.
"""

import argparse
import os
import time
from typing import Protocol

import numpy as np
import psycopg

from api.embed.texts import document_text


class Encoder(Protocol):
    def encode(self, texts: list[str]) -> np.ndarray: ...


def vector_literal(vec: np.ndarray) -> str:
    """pgvector text input, e.g. '[0.1,-0.2,...]'. %.8g exceeds halfvec's
    fp16 precision, so the round-trip is deterministic."""
    return "[" + ",".join(f"{x:.8g}" for x in vec) + "]"


# Boilerplate abstracts resolve to NULL here, so document_text() takes its
# existing title-only branch — the blocklist is a JOIN, not a second code
# path (DECISION-2c: shape matters, not type).
CLAIM_SQL = """
SELECT p.id, p.title,
       CASE WHEN b.abstract_md5 IS NULL THEN p.abstract END AS abstract
FROM papers p
LEFT JOIN boilerplate_abstracts b ON b.abstract_md5 = md5(p.abstract)
WHERE p.embedding IS NULL
ORDER BY p.id
LIMIT %(n)s
"""

WRITE_SQL = "UPDATE papers SET embedding = %(vec)s::halfvec WHERE id = %(id)s"


def backfill(
    conn: psycopg.Connection,
    encoder: Encoder,
    *,
    limit: int | None = None,
    batch_rows: int = 256,
) -> int:
    """Embed up to `limit` un-embedded papers (all of them when None).
    Returns how many rows this call wrote. Safe to kill; safe to rerun."""
    if not conn.autocommit:
        # The savepoint trap, found by a real SIGKILL (docs/findings.md
        # 2026-07-30): on a default connection an open implicit transaction
        # makes conn.transaction() a SAVEPOINT, so every "committed" batch
        # actually rides one giant transaction that dies with the process —
        # the killed run lost all 2,304 "committed" rows. Autocommit mode
        # makes each conn.transaction() block a real, durable commit.
        raise ValueError("backfill requires an autocommit connection; batch commits are the point")
    written = 0
    while limit is None or written < limit:
        take = batch_rows if limit is None else min(batch_rows, limit - written)
        rows = conn.execute(CLAIM_SQL, {"n": take}).fetchall()
        if not rows:
            break
        vectors = encoder.encode([document_text(title, abstract) for _, title, abstract in rows])
        with conn.transaction():
            for (paper_id, _, _), vec in zip(rows, vectors, strict=True):
                conn.execute(WRITE_SQL, {"vec": vector_literal(vec), "id": paper_id})
        written += len(rows)
        print(f"embedded {written} (through paper id {rows[-1][0]})", flush=True)
    return written


def main() -> None:
    from api.embed.onnx_encoder import OnnxEncoder

    parser = argparse.ArgumentParser(description="Backfill paper embeddings (resumable).")
    parser.add_argument("--limit", type=int, default=None, help="stop after N rows (smoke runs)")
    parser.add_argument("--batch-rows", type=int, default=256)
    parser.add_argument(
        "--model-dir",
        default=os.environ.get("EMBED_MODEL_DIR"),
        help="dir with tokenizer.json and onnx/model.onnx (or set EMBED_MODEL_DIR)",
    )
    args = parser.parse_args()
    if not args.model_dir:
        raise SystemExit("--model-dir or EMBED_MODEL_DIR is required")

    encoder = OnnxEncoder(args.model_dir)
    start = time.perf_counter()
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
        written = backfill(conn, encoder, limit=args.limit, batch_rows=args.batch_rows)
        remaining = conn.execute("SELECT count(*) FROM papers WHERE embedding IS NULL").fetchone()
        assert remaining is not None
    elapsed = time.perf_counter() - start
    rate = written / elapsed if elapsed > 0 else 0.0
    print(
        f"done: wrote {written} embeddings in {elapsed:.0f}s ({rate:.1f} docs/s); "
        f"{remaining[0]} papers still un-embedded"
    )


if __name__ == "__main__":
    main()
