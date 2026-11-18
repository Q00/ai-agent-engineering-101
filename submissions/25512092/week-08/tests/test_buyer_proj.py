"""11/10 ~12:30 (person): summary line 13, the buyer saved projection (tools/buyer_proj.py). No files.
    python tests/test_buyer_proj.py"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import buyer_proj as BP  # noqa: E402


def row(saved, deals, posts=10, forum=0.5):
    return {"saved": saved, "deals": deals, "posts": posts, "forum": forum}


def test_rates_and_project():
    m, r, pr = BP.rates(row(0.40, 200, 20), row(0.42, 100, 8), h=10)
    assert abs(m - 0.38) < 1e-9 and r == 10 and pr == 1.2
    E = BP.project({"a": row(0.40, 200)}, {"a": row(0.42, 100)}, hours=20, h=10)
    assert E["a"]["deals"] == 400 and abs(E["a"]["saved"] - 0.39) < 1e-9          # (0.40*200 + 0.38*200) / 400


def test_lines():
    B = {"me": row(0.41, 1000), "x": row(0.39, 100), "y": row(0.37, 500), "z": row(0.36, 500)}
    P = {"me": row(0.41, 900), "x": row(0.37, 50), "y": row(0.37, 450), "z": row(0.36, 450)}
    L = BP.lines(B, P, 0, 12 * 3600, "me", h=12)
    assert L[0].startswith("우리 saved 0.4100 (1위)") and "2. x 0.3900 (+0.0017)" in L[0], L[0]
    assert "라운드 끝 예상" in L[1] and "목표 0.39" in L[1] and "x 0.3967" in L[1], L[1]   # x: (0.39 x 100 + 0.41 x 50) / 150


if __name__ == "__main__":
    test_rates_and_project()
    test_lines()
    print("ok")
