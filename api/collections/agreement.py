"""Inter-rater agreement at variable N.

Kishan's constraint broke the original proposal and the break was real, not
pedantic: Cohen's kappa is defined for EXACTLY two raters. With N screeners —
and with one paper screened by three people while another was screened by two —
there is no single Cohen's kappa to compute, and averaging the pairwise ones
is not a statistic, it is a number that resembles one.

Three candidates were considered.

  Fleiss' kappa       generalises to N raters, but requires the SAME number of
                      raters on every item. Meeting that means discarding every
                      paper that does not have exactly k screeners, and the
                      retained subset is not random — papers get extra screeners
                      precisely when they are contentious. REJECTED: throwing
                      away data to fit a formula, and biasing what remains.

  Krippendorff alpha  handles variable raters per item and missing data by
                      construction, which is exactly this situation. It is the
                      defensible single number here. ACCEPTED as the headline.

  pairwise Cohen      one kappa per pair of screeners, over the papers both
                      judged. Not a single number, but it is the one a human
                      can act on: "you and Grace agree at 0.84, you and Sam at
                      0.41" names where the problem is. ACCEPTED as the detail.

So both ship, doing different jobs — alpha answers "is this screening
reproducible", the pairwise matrix answers "who needs to talk to whom". Neither
is presented without the item count it rests on.

GUARDS, applied the same way the bench harness refuses unstable percentiles:

  * a pair with fewer than MIN_PAIR_ITEMS co-screened papers gets no kappa
  * alpha needs MIN_ALPHA_ITEMS multiply-screened papers
  * kappa and alpha are BOTH degenerate when there is no disagreement to be
    had — if every rater used one category, chance agreement is 1.0, the
    denominator is 0, and the "correct" answer is undefined rather than 0 or 1.
    That case is detected and reported as undefined, never printed as a number.
"""

from __future__ import annotations

from collections import defaultdict
from itertools import combinations
from typing import Any

CATEGORIES = ("include", "exclude", "maybe")

# Below ~30 paired items a kappa swings wildly on one changed call; below ~50
# items alpha is similarly unstable. Both thresholds are conventional rather
# than derived, and are stated in the output so a reader can judge them.
MIN_PAIR_ITEMS = 30
MIN_ALPHA_ITEMS = 50


def cohens_kappa(pairs: list[tuple[str, str]]) -> dict[str, Any]:
    """Cohen's kappa for one pair of raters over the items both judged.

    Returns the value with the count it rests on, or an explicit reason it is
    undefined. Never returns a bare float, because a kappa without its n is a
    number nobody can check.
    """
    n = len(pairs)
    if n == 0:
        return {"kappa": None, "n": 0, "undefined": "no co-screened papers"}

    observed = sum(1 for a, b in pairs if a == b) / n

    # Expected agreement by chance, from each rater's own marginal use of the
    # categories — this is what separates kappa from raw percent agreement.
    ma: dict[str, int] = defaultdict(int)
    mb: dict[str, int] = defaultdict(int)
    for a, b in pairs:
        ma[a] += 1
        mb[b] += 1
    expected = sum((ma[c] / n) * (mb[c] / n) for c in CATEGORIES)

    if abs(1.0 - expected) < 1e-12:
        # Both raters used a single identical category. They agree completely
        # and chance predicts exactly that, so kappa is 0/0. Reporting 1.0
        # would claim perfect reliability from a rater who never discriminated.
        return {
            "kappa": None,
            "n": n,
            "observed_agreement": round(observed, 4),
            "undefined": "no category variation — chance agreement is 1.0",
        }

    return {
        "kappa": round((observed - expected) / (1.0 - expected), 4),
        "n": n,
        "observed_agreement": round(observed, 4),
        "expected_agreement": round(expected, 4),
        "undefined": None,
    }


def krippendorff_alpha(items: list[list[str]]) -> dict[str, Any]:
    """Krippendorff's alpha for nominal data, variable raters per item.

    `items` is one list of decisions per paper — length 2 for a paper two
    people screened, 3 for one that three did. Items with a single rater carry
    no agreement information and are dropped, which is the definition rather
    than a convenience.

    Uses the coincidence-matrix form, which is what makes variable rater counts
    work: each item contributes pairs weighted by 1/(m-1), so a paper with
    three raters does not outweigh one with two simply by having more pairs.
    """
    usable = [i for i in items if len(i) >= 2]
    if len(usable) < 1:
        return {"alpha": None, "n_items": 0, "undefined": "no multiply-screened papers"}

    # Coincidence matrix: how often each ordered category pair co-occurs,
    # normalised per item by (m - 1).
    coincidence: dict[tuple[str, str], float] = defaultdict(float)
    for decisions in usable:
        m = len(decisions)
        for a, b in combinations(decisions, 2):
            # Each unordered pair contributes to both cells, per the definition.
            coincidence[(a, b)] += 1.0 / (m - 1)
            coincidence[(b, a)] += 1.0 / (m - 1)

    totals: dict[str, float] = defaultdict(float)
    for (a, _b), v in coincidence.items():
        totals[a] += v
    n_total = sum(totals.values())
    if n_total <= 0:
        return {"alpha": None, "n_items": len(usable), "undefined": "no pairable decisions"}

    observed_disagree = sum(v for (a, b), v in coincidence.items() if a != b) / n_total
    # Expected disagreement from the marginal distribution.
    expected_disagree = sum(
        (totals[a] / n_total) * (totals[b] / (n_total - 1))
        for a in CATEGORIES
        for b in CATEGORIES
        if a != b and n_total > 1
    )

    if expected_disagree <= 1e-12:
        return {
            "alpha": None,
            "n_items": len(usable),
            "undefined": "no category variation — expected disagreement is 0",
        }

    return {
        "alpha": round(1.0 - (observed_disagree / expected_disagree), 4),
        "n_items": len(usable),
        "undefined": None,
    }


def agreement_report(rows: list[tuple[int, int, str]]) -> dict[str, Any]:
    """Everything reportable for a collection.

    `rows` is (paper_id, user_id, decision) for every screening in the
    collection. Both statistics are computed from the same input so they cannot
    disagree about which papers were counted.
    """
    by_paper: dict[int, dict[int, str]] = defaultdict(dict)
    for paper_id, user_id, decision in rows:
        by_paper[paper_id][user_id] = decision

    # ---- pairwise Cohen -------------------------------------------------
    raters = sorted({u for p in by_paper.values() for u in p})
    pairwise: list[dict[str, Any]] = []
    for a, b in combinations(raters, 2):
        both = [
            (p[a], p[b]) for p in by_paper.values() if a in p and b in p
        ]
        result = cohens_kappa(both)
        if result["n"] < MIN_PAIR_ITEMS:
            result = {
                "kappa": None,
                "n": result["n"],
                # Suppressed rather than shown small: a kappa on twelve papers
                # moves by 0.1 when one call changes, and a reader has no way
                # to know that from the number alone.
                "undefined": f"fewer than {MIN_PAIR_ITEMS} co-screened papers",
            }
        pairwise.append({"user_a": a, "user_b": b, **result})

    # ---- Krippendorff alpha ---------------------------------------------
    multi = [list(p.values()) for p in by_paper.values() if len(p) >= 2]
    if len(multi) < MIN_ALPHA_ITEMS:
        alpha = {
            "alpha": None,
            "n_items": len(multi),
            "undefined": f"fewer than {MIN_ALPHA_ITEMS} multiply-screened papers",
        }
    else:
        alpha = krippendorff_alpha(multi)

    return {
        "screened_papers": len(by_paper),
        "multiply_screened": len(multi),
        "raters": len(raters),
        "alpha": alpha,
        "pairwise_cohen": pairwise,
        "method": {
            "alpha": (
                "Krippendorff's alpha, nominal, coincidence-matrix form — chosen "
                "because it admits a variable number of raters per item, which "
                "Fleiss' kappa does not without discarding papers"
            ),
            "pairwise": "Cohen's kappa per screener pair, over papers both judged",
            "min_pair_items": MIN_PAIR_ITEMS,
            "min_alpha_items": MIN_ALPHA_ITEMS,
        },
    }
