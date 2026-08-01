"""Hand-labeling CLI for dedup pairs. Kishan labels; this only presents.

BLIND BY CONSTRUCTION. The cascade's verdict (merged / refused) is never
shown before a label is recorded, and the sample is shuffled so verdict
cannot be inferred from order. What IS shown is the rule that brought the
pair into consideration and its title similarity, because "why am I
looking at this?" is context, not an answer — a labeller who knows the
cascade merged it is measuring their agreement with the cascade rather
than the truth.

Resumable: every label is written to disk immediately, so quitting and
returning loses nothing. Labels already recorded are skipped.

    make label            # or the docker compose run line in the Makefile
    y / n / u / s / q     verdict, unsure, skip, quit
"""

import json
import os
import sys
from pathlib import Path
from typing import Any

LABELS = Path(__file__).parent / "labels" / "dedup_pairs.json"
SAMPLE = Path(__file__).parent / "labels" / "dedup_sample.json"

PROMPT = """
  [y] same paper      [n] different papers      [u] unsure
  [s] skip for now    [q] save and quit
> """


def load(path: Path) -> Any:
    return json.loads(path.read_text()) if path.exists() else None


def show(pair: dict[str, Any], done: int, total: int) -> None:
    a, b = pair["a"], pair["b"]
    sim = pair.get("similarity")
    sim_text = f"{sim:.3f}" if isinstance(sim, float) else "n/a"
    width = int(os.environ.get("COLUMNS", "100"))
    rule = pair["strategy"]

    print("\n" + "=" * width)
    print(
        f"pair {pair['pair_id']}   ({done}/{total} labeled)   "
        f"candidate via: {rule}   title similarity: {sim_text}"
    )
    if pair.get("group_size", 2) > 2:
        print(f"   part of a {pair['group_size']}-member group")
    print("=" * width)
    for tag, side in (("A", a), ("B", b)):
        print(f"\n[{tag}] {side.get('title') or '(no title)'}")
        meta = [
            str(side.get("year") or "no year"),
            side.get("venue") or "no venue",
            side.get("doi") or "no doi",
        ]
        if side.get("arxiv_id"):
            meta.append(f"arXiv:{side['arxiv_id']}")
        meta.append(f"{side.get('citation_count', 0)} citations")
        print("    " + "  |  ".join(meta))
        abstract = (side.get("abstract") or "(no abstract)").replace("\n", " ")
        print(f"    {abstract[:340]}{'...' if len(abstract) > 340 else ''}")


def main() -> None:
    frame = load(SAMPLE)
    if frame is None:
        print("no sample found — run: python -m bench.dedup_sample", file=sys.stderr)
        raise SystemExit(2)

    store = load(LABELS) or {"labels": {}, "sample_seed": frame["seed"]}
    labels: dict[str, Any] = store["labels"]
    pairs = frame["pairs"]
    todo = [p for p in pairs if str(p["pair_id"]) not in labels]

    print(f"{len(labels)} labeled, {len(todo)} remaining of {len(pairs)}")
    if not todo:
        print("all pairs labeled — run: python -m bench.dedup_precision")
        return

    for pair in todo:
        show(pair, len(labels), len(pairs))
        while True:
            try:
                answer = input(PROMPT).strip().lower()
            except (EOFError, KeyboardInterrupt):
                answer = "q"
            if answer in ("y", "n", "u", "s", "q"):
                break
            print("  please answer y, n, u, s or q")

        if answer == "q":
            break
        if answer == "s":
            continue
        labels[str(pair["pair_id"])] = {
            "same_paper": {"y": True, "n": False, "u": None}[answer],
            "stratum": pair["stratum"],
            # Recorded AFTER the label, never shown before it.
            "cascade_verdict": pair["_verdict"],
        }
        LABELS.write_text(json.dumps({**store, "labels": labels}, indent=1))

    print(f"\nsaved {len(labels)} labels to {LABELS}")
    remaining = len(pairs) - len(labels)
    if remaining:
        print(f"{remaining} left — rerun to continue")
    else:
        print("complete — run: python -m bench.dedup_precision")


if __name__ == "__main__":
    main()
