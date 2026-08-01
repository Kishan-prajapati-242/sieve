"""Hand-labeling CLI for dedup pairs. Kishan labels; this only presents.

BLIND BY CONSTRUCTION, and more strictly than the first version was.
Nothing the cascade concluded reaches the screen: not the verdict, not the
rule that surfaced the pair, not the similarity score, not the group size.
Only the two records.

The first version showed the rule name and similarity as "context, not an
answer". Kishan rejected that, correctly: the rule name tells you what the
system concluded and why, so after ~40 pairs a labeller has learned which
rules are trustworthy and starts labeling the RULE instead of the
evidence. The stratum is still recorded in the output, so
dedup_precision.py can weight and stratify — it is simply not shown.

If the rule name would change your answer, the pair is genuinely
ambiguous and the answer is 'u'.

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
    width = int(os.environ.get("COLUMNS", "100"))

    # Deliberately absent: rule, similarity, group size, verdict. See the
    # module docstring — every one of them is a cascade conclusion.
    print("\n" + "=" * width)
    print(f"pair {pair['pair_id']}   ({done}/{total} labeled)")
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
            # All recorded, none displayed: the estimator needs them, the
            # labeller must not see them.
            "stratum": pair["stratum"],
            "cascade_verdict": pair["_verdict"],
            "rule": pair["strategy"],
            "similarity": pair.get("similarity"),
            "group_size": pair.get("group_size"),
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
