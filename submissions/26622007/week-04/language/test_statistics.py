from fractions import Fraction
from itertools import combinations, product
import unittest

from scipy.stats import fisher_exact
from statsmodels.stats.contingency_tables import mcnemar
import statistics_exact as stats


def rows(successes, n=3, scenario="1"):
    return [{"scenario": scenario, "correct": str(int(i < successes))} for i in range(n)]


class StatisticsTests(unittest.TestCase):
    def test_single_balanced_stratum_matches_fisher_all_cases(self):
        for x in range(4):
            for y in range(4):
                actual = stats.stratified_test(rows(x), rows(y))["p_raw"]
                expected = fisher_exact([[x, 3 - x], [y, 3 - y]]).pvalue
                self.assertAlmostEqual(actual, expected)

    def test_multiple_strata_match_exhaustive_label_permutations(self):
        a, b = rows(3, scenario="1") + rows(2, scenario="2"), rows(0, scenario="1") + rows(1, scenario="2")
        # 20 ways to assign three of six observations in each scenario, 400 total.
        assignments = list(combinations(range(6), 3))
        pooled = ([1, 1, 1, 0, 0, 0], [1, 1, 0, 1, 0, 0])
        totals = [sum(pooled[0][i] for i in left) + sum(pooled[1][j] for j in right)
                  for left, right in product(assignments, repeat=2)]
        brute = Fraction(sum(abs(2 * k - 6) >= abs(2 * 5 - 6) for k in totals), len(totals))
        self.assertEqual(stats.stratified_test(a, b)["p_fraction"], str(brute))
        self.assertEqual(stats.stratified_test(a, b)["p_raw"], stats.stratified_test(b, a)["p_raw"])

    def test_zero_information_and_holm(self):
        self.assertEqual(stats.stratified_test(rows(3), rows(3))["p_raw"], 1)
        tests = [{"p_raw": p} for p in (0.01, 0.04, 0.03)]
        stats.adjust(tests)
        self.assertEqual([t["significant_holm"] for t in tests], [True, False, False])
        for t, p in zip(tests, (0.03, 0.06, 0.06)): self.assertAlmostEqual(t["p_holm"], p)

    def test_paired_test_matches_exact_mcnemar(self):
        a = [{"run": "a", "scenario": str(i), "correct": str(i % 2)} for i in range(12)]
        b = [{**r, "correct": "1"} for r in a]
        result = stats.paired_test(a, b)
        self.assertEqual(result["p_raw"], mcnemar([[0, 6], [0, 6]], exact=True).pvalue)
        self.assertEqual((result["gain"], result["loss"]), (6, 0))
        self.assertEqual(stats.score({"correct": 1}), 1)
        self.assertEqual(stats.score({"correct": ""}), 0)


if __name__ == "__main__": unittest.main()
