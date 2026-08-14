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
    parser.add_argument("--windows", type=int, default=10)
    parser.add_argument("--seed", type=float, default=0.42)
    args = parser.parse_args()

    encoder = OnnxEncoder(os.environ["EMBED_MODEL_DIR"], batch_size=args.batch_size)

    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        # SEEDED. This was `ORDER BY random()` with no seed, so every run
        # encoded a DIFFERENT set of texts -- and encode cost is per-TOKEN,
        # not per-doc. Cross-run docs/s therefore confounded throughput with
        # whatever abstract lengths got drawn, which is part of why 13.2 and
        # 8.1 docs/s were both "measured on the same hardware"
        # (findings.md 2026-08-14).
        conn.execute("SELECT setseed(%s)", (args.seed,))
        rows = conn.execute(
            "SELECT title, abstract FROM papers ORDER BY random() LIMIT %s",
            (args.sample,),
        ).fetchall()
        corpus_row = conn.execute("SELECT count(*) FROM papers").fetchone()
        assert corpus_row is not None
        corpus = corpus_row[0]

    texts = [document_text(t, a) for t, a in rows]
    encoder.encode(texts[:8])  # warmup: session initialization is not throughput

    # Windowed, not just total. A single aggregate rate cannot distinguish
    # THERMAL throttling (rates decay across the run as the fanless M1 heats)
    # from MACHINE STATE (rates flat but low -- another process, battery
    # power, a different CPU allocation). Those have opposite remedies:
    # thermal is helped by caffeinate and mains power, machine state is not.
    # The 8.1 vs 13.2 docs/s spread on identical hardware made the
    # distinction load-bearing (findings.md 2026-08-14).
    window = max(1, len(texts) // args.windows)
    windows: list[tuple[int, float]] = []
    start = time.perf_counter()
    for i in range(0, len(texts), window):
        chunk = texts[i : i + window]
        w0 = time.perf_counter()
        encoder.encode(chunk)
        windows.append((len(chunk), time.perf_counter() - w0))
    elapsed = time.perf_counter() - start

    rate = len(texts) / elapsed
    print(f"sample:        {len(texts)} random papers, batch {args.batch_size}, fp32")
    print(f"throughput:    {rate:.1f} docs/s ({elapsed:.1f}s)")
    rates = [n / d for n, d in windows]
    print("windows:       " + " ".join(f"{r:.1f}" for r in rates))
    # First third vs last third: a decay ratio well above 1 is thermal.
    third = max(1, len(rates) // 3)
    head = sum(rates[:third]) / third
    tail = sum(rates[-third:]) / third
    print(
        f"decay:         first-third {head:.1f} -> last-third {tail:.1f} docs/s "
        f"({head / tail:.2f}x)"
    )
    print(f"projected:     {corpus:,} papers in {corpus / rate / 60:.0f} min")
    # Report the unit that is actually stable. docs/s depends on the sample's
    # text length; chars/s is closer to the work done. Tokens would be
    # closer still, but that needs the tokenizer and this is enough to show
    # whether two runs encoded comparable work.
    total_chars = sum(len(t) for t in texts)
    print(f"sample text:   {total_chars:,} chars, {total_chars / len(texts):.0f} mean")
    print(f"char rate:     {total_chars / elapsed / 1000:.1f} k chars/s")
    print(
        f"steady state:  {tail:.1f} docs/s (last third; use THIS for planning "
        f"runs long enough to amortize the ramp)"
    )
    print(f"peak RSS:      {peak_rss_gb():.2f} GB (this process)")


if __name__ == "__main__":
    main()
