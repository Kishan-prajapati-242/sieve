"""Inter-annotator agreement between Kishan's labels and the model's.

Reports raw agreement, Cohen's kappa, and — the only part worth acting on
— the DISAGREEMENTS, one at a time, so they can be adjudicated.

Kappa rather than raw agreement alone because raw agreement is inflated
whenever one class dominates: labeling everything "different" would score
~50% here for free. Kappa subtracts the agreement expected from each
annotator's own marginal rates.

Pairs where EITHER annotator said "unsure" are excluded from kappa (it is
defined over a shared category set and "unsure" is an abstention, not a
third opinion) and reported separately.

    python -m bench.dedup_agreement
"""

import json
from collections import Counter
from pathlib import Path
from typing import Any

HUMAN = Path(__file__).parent / "labels" / "dedup_pairs.json"
MODEL = Path(__file__).parent / "labels" / "dedup_pairs_model.json"
SAMPLE = Path(__file__).parent / "labels" / "dedup_sample.json"


def cohens_kappa(pairs: list[tuple[bool, bool]]) -> float | None:
    n = len(pairs)
    if n == 0:
        return None
    observed = sum(1 for a, b in pairs if a == b) / n
    a_rate = Counter(a for a, _ in pairs)
    b_rate = Counter(b for _, b in pairs)
    expected = sum((a_rate[c] / n) * (b_rate[c] / n) for c in (True, False))
    if expected == 1.0:
        return None  # both annotators used exactly one category
    return (observed - expected) / (1 - expected)


def main() -> None:
    if not HUMAN.exists():
        print("no human labels yet — run: make label")
        return
    human = json.loads(HUMAN.read_text())["labels"]
    model = json.loads(MODEL.read_text())["labels"]
    frame = {p["pair_id"]: p for p in json.loads(SAMPLE.read_text())["pairs"]}

    shared = sorted(set(human) & set(model), key=int)
    both_decided = [
        (pid, human[pid]["same_paper"], model[pid]["same_paper"])
        for pid in shared
        if human[pid]["same_paper"] is not None and model[pid]["same_paper"] is not None
    ]
    abstained = [
        pid
        for pid in shared
        if human[pid]["same_paper"] is None or model[pid]["same_paper"] is None
    ]

    verdicts = [(h, m) for _, h, m in both_decided]
    agree = sum(1 for h, m in verdicts if h == m)
    kappa = cohens_kappa(verdicts)

    disagreements = [(pid, h, m) for pid, h, m in both_decided if h != m]
    by_stratum: Counter[str] = Counter(human[pid]["stratum"] for pid, _, _ in disagreements)

    print(f"labels compared:      {len(shared)} of {len(frame)}")
    print(f"both decided:         {len(both_decided)}  (abstentions: {len(abstained)})")
    if both_decided:
        rate = agree / len(both_decided)
        print(f"raw agreement:        {agree}/{len(both_decided)} = {rate:.1%}")
    print(f"Cohen's kappa:        {kappa:.3f}" if kappa is not None else "Cohen's kappa: n/a")
    print(f"disagreements:        {len(disagreements)}")
    if by_stratum:
        print("  by stratum: " + ", ".join(f"{k}={v}" for k, v in by_stratum.most_common()))

    if not disagreements:
        return
    print("\n" + "=" * 78)
    print("DISAGREEMENTS — your label vs the model's")
    print("=" * 78)
    for pid, h, m in disagreements:
        pair: dict[str, Any] = frame[int(pid)]
        say = {True: "same", False: "different"}
        print(
            f"\npair {pid}   you: {say[h]:9}  model: {say[m]:9}"
            f"   [{human[pid]['stratum']}, cascade {human[pid]['cascade_verdict']}]"
        )
        for tag in ("a", "b"):
            side = pair[tag]
            print(f"  [{tag.upper()}] {(side.get('title') or '')[:88]}")
            print(
                f"      {side.get('year')} | {(side.get('venue') or '-')[:34]}"
                f" | {(side.get('doi') or '-')[:44]}"
            )


if __name__ == "__main__":
    main()
