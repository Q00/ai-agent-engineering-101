"""soft_neg 6 h check: widen to 100% only if all conditions hold (11/09 ~17:00).
    python tests/test_soft_neg_mid.py"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import soft_neg_mid as m  # noqa: E402

B = {"saved": 0.42, "saved_rank": 1}
S = {"rate": 0.55, "save": 0.40, "kit": 23.0}
C = {"rate": 0.48, "save": 0.46, "kit": 34.0}


def test_decide():
    assert m.decide(S, C, B) == (True, [])
    assert m.decide(S, C, {"saved": 0.409, "saved_rank": 1})[0] is False
    assert m.decide(S, C, {"saved": 0.42, "saved_rank": 2})[0] is False
    k = {"kit": 23.0}
    assert m.decide(dict(k, rate=0.46, save=0.40), C, B)[0] is True        # 2%p under: allowed (11/09 ~17:10)
    assert m.decide(dict(k, rate=0.459, save=0.40), C, B)[0] is False      # more than 2%p under
    assert m.decide(dict(k, rate=0.55, save=0.349), C, B)[0] is False
    assert m.decide(dict(k, rate=0.55, save=None), C, B)[0] is False
    assert m.decide(dict(S, kit=34.0), C, B)[0] is False                     # order time not shorter
    assert m.decide(dict(S, kit=None), C, B)[0] is False
    assert m.decide(None, C, B) == (False, ["기록 부족"])
    assert m.decide(dict(k, rate=0.55, save=0.35), C, {"saved": 0.41, "saved_rank": 1})[0] is True   # bounds inclusive


def test_chunks():
    t = "\n".join(f"line {i} " + "x" * 50 for i in range(200))
    parts = m.chunks(t, 1000)
    assert all(len(p) <= 1000 for p in parts) and "\n".join(parts) == t
    assert m.chunks("y" * 2500, 1000) == ["y" * 1000, "y" * 1000, "y" * 500]
    assert m.chunks("short") == ["short"]


if __name__ == "__main__":
    test_decide()
    test_chunks()
    print("ok")
