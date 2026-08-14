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
import pathlib
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

# Rows per rate window. 2,048 is ~3 minutes at the observed 8.8-12.7
# [2026-08-14: that band is retired — see findings.md. The window size is
# unchanged because what it has to resolve is the SHAPE of the curve, and
# ~3 min windows over a ~30 min run give ten points, which is what
# distinguished a cold-VM ramp from thermal decay in the benchmark.]
# docs/s — short enough to localize a throttling knee, long enough that
# batch noise averages out.
RATE_WINDOW_ROWS = 2048


def host_conditions() -> str:
    """What the VM can see about the machine it is sharing.

    The one known cause of encode-rate variance is host CPU availability
    (findings.md 2026-08-14: identical input, 1.28x apart, one run dipping
    to 4.7 docs/s mid-run and recovering), and it was the one variable the
    log did not record. This captures the VM side; the HOST side — power
    source, what else was open — cannot be seen from in here and is captured
    by the runbook's wrapper, which is why both halves exist.
    """
    try:
        load = pathlib.Path("/proc/loadavg").read_text().split()[:3]
    except OSError:
        load = ["?", "?", "?"]
    return f"vm_loadavg={'/'.join(load)} vm_cpus={os.cpu_count()}"


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
    # Windowed rate logging, with BOTH clocks on every line.
    #
    # perf_counter is CLOCK_MONOTONIC and does not advance while the host
    # sleeps; wall clock does. Printing both makes a suspend VISIBLE — the
    # two deltas diverge — instead of leaving it to be inferred later from
    # a total that looks 23x too large (findings.md 2026-08-13: a 10-minute
    # cascade was reported as 3 h 55 m). The full 196,893-paper encode
    # printed only a final rate and that output was never captured, so this
    # project still has no sustained throughput measurement for its own
    # hardware. This is how the next long run produces one.
    written = 0
    run_start = time.perf_counter()
    print(f"[host] {host_conditions()}", flush=True)
    window_start = run_start
    window_wall = time.time()
    window_rows = 0
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
        window_rows += len(rows)
        if window_rows >= RATE_WINDOW_ROWS:
            now_mono, now_wall = time.perf_counter(), time.time()
            mono_s = now_mono - window_start
            wall_s = now_wall - window_wall
            print(
                f"embedded {written} (through paper id {rows[-1][0]}) "
                f"window={window_rows} rows in {mono_s:.1f}s monotonic / "
                f"{wall_s:.1f}s wall -> {window_rows / mono_s:.1f} docs/s "
                f"[{host_conditions()}]"
                + (
                    f"  [CLOCK DISCONTINUITY: wall exceeds monotonic by "
                    f"{wall_s - mono_s:.0f}s — the host slept]"
                    if wall_s - mono_s > 5.0
                    else ""
                ),
                flush=True,
            )
            window_start, window_wall, window_rows = now_mono, now_wall, 0
        else:
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
