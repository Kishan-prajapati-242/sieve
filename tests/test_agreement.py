"""Agreement statistics, checked against hand-computable cases.

Every expected value here is derived by hand in the test, not copied from a
library — the point of implementing these is being able to defend the numbers,
and a test that asserts whatever the code produced defends nothing.
"""

from api.collections.agreement import (
    MIN_ALPHA_ITEMS,
    MIN_PAIR_ITEMS,
    agreement_report,
    cohens_kappa,
    krippendorff_alpha,
)


class TestCohensKappa:
    def test_perfect_agreement_with_real_variation_is_one(self) -> None:
        pairs = [("include", "include")] * 5 + [("exclude", "exclude")] * 5
        # observed 1.0; expected = .5*.5 + .5*.5 = .5; (1-.5)/(1-.5) = 1
        assert cohens_kappa(pairs)["kappa"] == 1.0

    def test_hand_computed_case(self) -> None:
        # 10 items: agree on 8, disagree on 2.
        #   A: 6 include, 4 exclude    B: 6 include, 4 exclude
        #   observed = 0.8
        #   expected = .6*.6 + .4*.4 = .36 + .16 = .52
        #   kappa = (.8 - .52) / (1 - .52) = .28/.48 = 0.5833
        pairs = (
            [("include", "include")] * 5
            + [("exclude", "exclude")] * 3
            + [("include", "exclude")]
            + [("exclude", "include")]
        )
        got = cohens_kappa(pairs)
        assert got["n"] == 10
        assert got["observed_agreement"] == 0.8
        assert got["expected_agreement"] == 0.52
        assert got["kappa"] == 0.5833

    def test_total_agreement_on_one_category_is_undefined_not_one(self) -> None:
        """The trap. Two raters who called everything 'include' agree
        perfectly, and chance predicts exactly that — kappa is 0/0. Reporting
        1.0 would claim perfect reliability from raters who never
        discriminated, which is the opposite of what happened."""
        got = cohens_kappa([("include", "include")] * 40)
        assert got["kappa"] is None
        assert "no category variation" in got["undefined"]
        assert got["observed_agreement"] == 1.0

    def test_chance_level_agreement_is_about_zero(self) -> None:
        # Independent raters splitting 50/50 land near 0, not near 0.5.
        pairs = [("include", "include"), ("include", "exclude"),
                 ("exclude", "include"), ("exclude", "exclude")] * 10
        assert abs(cohens_kappa(pairs)["kappa"]) < 0.001

    def test_empty_is_undefined(self) -> None:
        assert cohens_kappa([])["kappa"] is None


class TestKrippendorffAlpha:
    def test_perfect_agreement_is_one(self) -> None:
        items = [["include", "include"]] * 10 + [["exclude", "exclude"]] * 10
        assert krippendorff_alpha(items)["alpha"] == 1.0

    def test_variable_rater_counts_are_accepted(self) -> None:
        """The whole reason alpha was chosen over Fleiss: some papers have two
        screeners and some have three, and neither is discarded."""
        items = [["include", "include", "include"], ["exclude", "exclude"],
                 ["include", "include"], ["exclude", "exclude", "exclude"]]
        got = krippendorff_alpha(items)
        assert got["alpha"] == 1.0
        assert got["n_items"] == 4  # nothing thrown away

    def test_single_rater_items_carry_no_information_and_are_dropped(self) -> None:
        items = [["include"], ["exclude"], ["include", "include"]]
        assert krippendorff_alpha(items)["n_items"] == 1

    def test_no_variation_is_undefined(self) -> None:
        got = krippendorff_alpha([["include", "include"]] * 20)
        assert got["alpha"] is None
        assert "no category variation" in got["undefined"]

    def test_disagreement_lowers_alpha_below_one(self) -> None:
        agree = [["include", "include"]] * 10 + [["exclude", "exclude"]] * 10
        mixed = agree + [["include", "exclude"]] * 5
        a1 = krippendorff_alpha(agree)["alpha"]
        a2 = krippendorff_alpha(mixed)["alpha"]
        assert a1 is not None and a2 is not None and a2 < a1


class TestReportGuards:
    def _rows(self, n: int, raters: int = 2) -> list[tuple[int, int, str]]:
        rows = []
        for paper in range(n):
            for u in range(raters):
                rows.append((paper, u, "include" if paper % 2 == 0 else "exclude"))
        return rows

    def test_small_samples_are_suppressed_not_shown(self) -> None:
        """A kappa on twelve papers moves 0.1 when one call changes, and the
        reader cannot tell that from the number. Same refusal the bench
        harness applies to unstable percentiles."""
        report = agreement_report(self._rows(12))
        assert report["pairwise_cohen"][0]["kappa"] is None
        assert str(MIN_PAIR_ITEMS) in report["pairwise_cohen"][0]["undefined"]
        assert report["alpha"]["alpha"] is None
        assert str(MIN_ALPHA_ITEMS) in report["alpha"]["undefined"]

    def test_above_the_thresholds_both_statistics_appear(self) -> None:
        report = agreement_report(self._rows(60))
        assert report["pairwise_cohen"][0]["kappa"] == 1.0
        assert report["alpha"]["alpha"] == 1.0
        assert report["multiply_screened"] == 60

    def test_three_raters_produce_three_pairs_and_one_alpha(self) -> None:
        """N screeners is the case Cohen's kappa alone cannot express: there
        is no single pairwise number, so the report carries a matrix plus one
        alpha over everything."""
        report = agreement_report(self._rows(60, raters=3))
        assert report["raters"] == 3
        assert len(report["pairwise_cohen"]) == 3  # (0,1) (0,2) (1,2)
        assert report["alpha"]["alpha"] is not None

    def test_solo_collection_has_no_agreement_to_report(self) -> None:
        report = agreement_report(self._rows(60, raters=1))
        assert report["pairwise_cohen"] == []
        assert report["multiply_screened"] == 0
        assert report["alpha"]["alpha"] is None
