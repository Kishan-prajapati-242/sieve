"""Precision, recall and F1 for the dedup cascade, from hand labels.

Stratified sampling means a raw count of labels is NOT an estimate of the
corpus. Every label is weighted by its stratum's inverse sampling
fraction, N_h / n_h, so a stratum of 3,631 pairs sampled 30 times counts
121x per label and one of 14 sampled 5 times counts 2.8x.

    precision = TP / (TP + FP)   over MERGED pairs
    recall    = TP / (TP + FN)   FN = true duplicates the cascade REFUSED
    F1        = harmonic mean

Confidence intervals come from a stratified bootstrap (resample labels
within each stratum, recompute the weighted ratio, take percentiles),
because the quantity is a ratio of weighted sums and a normal
approximation on a ratio is a poor fit at these sample sizes.

WHAT RECALL HERE DOES AND DOES NOT MEAN — read before quoting it:
    Recall is measured against pairs the cascade CONSIDERED and refused.
    It cannot see duplicates that no blocking key ever brought together:
    two records of one paper with different authors, different years, no
    shared abstract and dissimilar titles are invisible to this estimate
    and to the cascade alike. So this is *recall among candidates*, an
    upper bound on true corpus recall. Measuring the real denominator
    needs an exhaustive hand-labeled subset of the full pair space, which
    is O(n^2) and not what this harness does.
"""

import json
import random
from collections import defaultdict
from pathlib import Path
from typing import Any

LABELS = Path(__file__).parent / "labels" / "dedup_pairs.json"
SAMPLE = Path(__file__).parent / "labels" / "dedup_sample.json"
BOOTSTRAP = 2000
SEED = 20260801


def weighted_counts(
    labels: dict[str, Any], strata: dict[str, Any], subset: list[str] | None = None
) -> dict[str, float]:
    """Weighted TP/FP/FN/TN. subset restricts to given pair_ids (bootstrap)."""
    by_stratum: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for pid, rec in labels.items():
        if subset is not None and pid not in subset:
            continue
        if rec["same_paper"] is None:  # 'unsure' contributes to neither
            continue
        by_stratum[rec["stratum"]].append(rec)

    out = {"tp": 0.0, "fp": 0.0, "fn": 0.0, "tn": 0.0}
    for stratum, recs in by_stratum.items():
        meta = strata.get(stratum)
        if not meta or not recs:
            continue
        weight = meta["population"] / len(recs)
        merged = meta["verdict"] == "merged"
        for rec in recs:
            same = rec["same_paper"]
            if merged and same:
                out["tp"] += weight
            elif merged and not same:
                out["fp"] += weight
            elif not merged and same:
                out["fn"] += weight
            else:
                out["tn"] += weight
    return out


def metrics(counts: dict[str, float]) -> dict[str, float | None]:
    tp, fp, fn = counts["tp"], counts["fp"], counts["fn"]
    precision = tp / (tp + fp) if (tp + fp) else None
    recall = tp / (tp + fn) if (tp + fn) else None
    f1 = (
        2 * precision * recall / (precision + recall)
        if precision and recall and (precision + recall)
        else None
    )
    return {"precision": precision, "recall": recall, "f1": f1}


def bootstrap_ci(
    labels: dict[str, Any], strata: dict[str, Any], key: str, n: int = BOOTSTRAP
) -> tuple[float, float] | None:
    rng = random.Random(SEED)
    by_stratum: dict[str, list[str]] = defaultdict(list)
    for pid, rec in labels.items():
        if rec["same_paper"] is not None:
            by_stratum[rec["stratum"]].append(pid)
    values: list[float] = []
    for _ in range(n):
        resampled: list[str] = []
        for ids in by_stratum.values():
            resampled.extend(rng.choices(ids, k=len(ids)))
        value = metrics(weighted_counts(labels, strata, resampled))[key]
        if value is not None:
            values.append(value)
    if len(values) < n * 0.5:
        return None
    values.sort()
    return values[int(0.025 * len(values))], values[int(0.975 * len(values))]


def main() -> None:
    if not LABELS.exists():
        print("no labels yet — run: python -m bench.dedup_label")
        return
    store = json.loads(LABELS.read_text())
    frame = json.loads(SAMPLE.read_text())
    labels: dict[str, Any] = store["labels"]
    strata = frame["strata"]

    usable = {k: v for k, v in labels.items() if v["same_paper"] is not None}
    unsure = len(labels) - len(usable)
    counts = weighted_counts(usable, strata)
    point = metrics(counts)

    per_stratum: dict[str, dict[str, Any]] = {}
    for stratum, meta in strata.items():
        recs = [r for r in usable.values() if r["stratum"] == stratum]
        if not recs:
            continue
        same = sum(1 for r in recs if r["same_paper"])
        per_stratum[stratum] = {
            "labeled": len(recs),
            "population": meta["population"],
            "verdict": meta["verdict"],
            "fraction_same_paper": round(same / len(recs), 3),
            # For merged strata this is precision; for refused strata it is
            # the miss rate — the share of refusals that were real duplicates.
            "reads_as": "precision" if meta["verdict"] == "merged" else "miss rate",
        }

    report: dict[str, Any] = {
        "labels_used": len(usable),
        "labels_unsure_excluded": unsure,
        "labels_outstanding": len(frame["pairs"]) - len(labels),
        "weighted_counts": {k: round(v, 1) for k, v in counts.items()},
        "point_estimates": {k: (round(v, 4) if v is not None else None) for k, v in point.items()},
        "per_stratum": per_stratum,
        "recall_caveat": (
            "Recall is measured against pairs the cascade CONSIDERED and refused. "
            "Duplicates no blocking key ever paired are invisible to it, so this is "
            "recall among candidates — an upper bound on true corpus recall."
        ),
    }
    if len(usable) >= 30:
        for key in ("precision", "recall", "f1"):
            ci = bootstrap_ci(usable, strata, key)
            if ci:
                report.setdefault("ci95", {})[key] = [round(ci[0], 4), round(ci[1], 4)]
    else:
        report["ci95"] = "withheld: fewer than 30 usable labels"

    out = Path(__file__).parent / "results_dedup_precision.json"
    out.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    if report["labels_outstanding"]:
        print(
            f"\nNOTE: {report['labels_outstanding']} pairs still unlabeled; "
            "estimates will move as they land."
        )


if __name__ == "__main__":
    main()
