"""Run a worker: `python -m api.queue`.

    python -m api.queue                      # consume until stopped
    python -m api.queue --seed-pubmed        # enqueue the pubmed crawl, exit
    python -m api.queue --drain              # consume until the queue is empty
    python -m api.queue --reap               # return crashed workers' jobs

Scale with `docker compose up --scale worker=4`; the claim is what makes
that safe, and nothing here needs to know how many peers exist.
"""

import argparse
import logging
import os
import sys

import psycopg

from api.logs import setup_logging
from api.queue.claim import queue_depth, reap_stale
from api.queue.handlers import HANDLERS, enqueue_pubmed_crawl
from api.queue.worker import Worker

logger = logging.getLogger("sieve.queue")


def main() -> None:
    parser = argparse.ArgumentParser(description="Sieve queue worker.")
    parser.add_argument("--seed-pubmed", action="store_true", help="enqueue the crawl and exit")
    parser.add_argument("--drain", action="store_true", help="stop when the queue is empty")
    parser.add_argument("--reap", action="store_true", help="requeue crashed workers' jobs, exit")
    parser.add_argument("--name", default=None, help="worker identity for locked_by")
    args = parser.parse_args()

    setup_logging()
    conninfo = os.environ.get("DATABASE_URL")
    if not conninfo:
        print("DATABASE_URL is required", file=sys.stderr)
        raise SystemExit(2)

    if args.seed_pubmed:
        from api.ingest.pubmed import QUERIES

        with psycopg.connect(conninfo, autocommit=True) as conn:
            ids = enqueue_pubmed_crawl(conn, QUERIES)
            print(f"enqueued {len(ids)} seed jobs; depth={queue_depth(conn)}")
        return

    if args.reap:
        with psycopg.connect(conninfo, autocommit=True) as conn:
            reaped = reap_stale(conn)
            print(f"reaped {len(reaped)}: {reaped[:10]}")
        return

    worker = Worker(conninfo, HANDLERS, name=args.name)
    worker.install_signal_handlers()
    logger.info("worker starting", extra={"worker": worker.name, "handlers": list(HANDLERS)})
    worker.run(max_idle_polls=1 if args.drain else None)
    logger.info(
        "worker stopped",
        extra={"worker": worker.name, "processed": worker.processed, "failed": worker.failed},
    )


if __name__ == "__main__":
    main()
