"""v1.27 (2026-11-05): a switchback experiment whose mean B - A is zero or below at an interim look ends as keep_a
with a futility reason; adoption and rejection boundaries, the final look and retests are unchanged."""

from icrl import experiment as X


def test_a_non_positive_interim_mean_is_futile():
    v, st = X.switchback_verdict([1.0, -1.0] * 3)
    assert v == "keep_a" and st["futility"] == "futility: mean 0 <= 0 at 6 of 12 pairs"
    v, st = X.switchback_verdict([0.5, -0.7] * 5)
    assert v == "keep_a" and st["futility"].endswith(" at 10 of 12 pairs") and st["mean"] < 0


def test_a_positive_interim_mean_continues():
    assert X.switchback_verdict([1.0, -0.9] * 3) == (None, X.switchback_verdict([1.0, -0.9] * 3)[1])
    assert X.switchback_verdict([1.0, -0.9] * 3)[0] is None


def test_boundaries_win_over_futility():
    assert X.switchback_verdict([-1.0, -1.1] * 3)[0] == "reject"  # clearly worse: a reject, not a futility stop
    assert X.switchback_verdict([1.0, 1.1] * 3)[0] == "adopt"


def test_the_final_look_and_retests_have_no_futility_key():
    v, st = X.switchback_verdict([1.0, -1.0] * 6)
    assert v == "keep_a" and "futility" not in st
    v, st = X.switchback_verdict([1.0, -1.0] * 3, retest=True)  # a retest has one look: its final one
    assert v == "keep_a" and "futility" not in st


def test_non_look_pair_counts_continue():
    assert X.switchback_verdict([-1.0, 0.5] * 2 + [-1.0]) == (None, {"pairs": 5})
