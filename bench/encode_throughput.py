"""Encode-throughput benchmark: N random real papers through the real
encoder, extrapolated to the full corpus. Every throughput number quoted
anywhere comes from this script, run INSIDE the compose environment —
podman's VM has its own CPU scheduling and memory cap, so host-venv
numbers do not transfer (Kishan, 2026-07-29).

Usage (from the repo root):
    docker compose build test
    docker compose run --rm -v ./bench:/app/bench -v <model-dir>:/models \
        -e EMBED_MODEL_DIR=/models test python bench/encode_throughput.py
"""

import argparse
import os
import resource
import sys
import time

import psycopg

from api.embed.onnx_encoder import OnnxEncoder
from api.embed.texts import document_text


def peak_rss_gb() -> float:
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # ru_maxrss is bytes on macOS, kilobytes on Linux.
    return rss / 1024**3 if sys.platform == "darwin" else rss / 1024**2


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sample", type=int, default=1000)
    parser.add_argument("--batch-size", type=int, default=32)
    args = parser.parse_args()

    encoder = OnnxEncoder(os.environ["EMBED_MODEL_DIR"], batch_size=args.batch_size)

    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        rows = conn.execute(
            "SELECT title, abstract FROM papers ORDER BY random() LIMIT %s",
            (args.sample,),
        ).fetchall()
        corpus_row = conn.execute("SELECT count(*) FROM papers").fetchone()
        assert corpus_row is not None
        corpus = corpus_row[0]

    texts = [document_text(t, a) for t, a in rows]
    encoder.encode(texts[:8])  # warmup: session initialization is not throughput

    start = time.perf_counter()
    encoder.encode(texts)
    elapsed = time.perf_counter() - start

    rate = len(texts) / elapsed
    print(f"sample:        {len(texts)} random papers, batch {args.batch_size}, fp32")
    print(f"throughput:    {rate:.1f} docs/s ({elapsed:.1f}s)")
    print(f"projected:     {corpus:,} papers in {corpus / rate / 60:.0f} min")
    print(f"peak RSS:      {peak_rss_gb():.2f} GB (this process)")


if __name__ == "__main__":
    main()
