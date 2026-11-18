"""Buyer saved projection for the 08:00 / 20:00 summaries (person's decision 11/10 ~12:30; target saved 0.39 at the
round end). Read-only. From the board now and the board_full.jsonl snapshot WINDOW_H before:
  each buyer's saved on its new deals (deal-weighted: (saved1 x deals1 - saved0 x deals0) / new deals) and deals per
  hour; projected to limits.ROUND_END at those rates (posts at their rate, forum as now); ALL by board_speed.buyer_all.
    python tools/buyer_proj.py           (prints the summary lines)
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "tools"))

WINDOW_H = 12
TARGET = 0.39


def rates(b, p, h=WINDOW_H):
    """(saved on new deals, deals per hour, posts per hour) from the row now (b) and WINDOW_H before (p)."""
    if not p or p.get("deals") is None:
        return b["saved"], 0.0, 0.0
    dd = max(0, b["deals"] - p["deals"])
    m = (b["saved"] * b["deals"] - p["saved"] * p["deals"]) / dd if dd else b["saved"]
    return min(1.0, max(0.0, m)), dd / h, max(0, (b.get("posts") or 0) - (p.get("posts") or 0)) / h


def project(B, P, hours, h=WINDOW_H):
    """{name: row at the round end} for buyers B now and P (h before)."""
    out = {}
    for n, b in B.items():
        m, r, pr = rates(b, P.get(n), h)
        nd = r * hours
        d = b["deals"] + nd
        out[n] = dict(b, saved=(b["saved"] * b["deals"] + m * nd) / d if d else b["saved"], deals=d,
                      posts=(b.get("posts") or 0) + pr * hours)
    return out


def lines(B, P, now, end, me, h=WINDOW_H):
    import board_speed as bs
    top = sorted(B, key=lambda n: -B[n]["saved"])
    speed = lambda n: (B[n]["saved"] - P[n]["saved"]) / h if P.get(n) else None
    f = lambda v: "-" if v is None else f"{v:+.4f}"
    L = [f"우리 saved {B[me]['saved']:.4f} ({top.index(me) + 1}위); 1~4위 saved (시간당 변화, 최근 {h}시간): "
         + ", ".join(f"{i + 1}. {n} {B[n]['saved']:.4f} ({f(speed(n))})" for i, n in enumerate(top[:4]))]
    E = project(B, P, max(0.0, (end - now) / 3600), h)
    A = bs.buyer_all(E)
    rk = bs.ranks(A)
    es = sorted(E, key=lambda n: -E[n]["saved"])
    m, r, _ = rates(B[me], P.get(me), h)
    L.append(f"라운드 끝 예상 (지금 속도: 새 거래 saved {m:.3f}, 시간당 deals {r:.0f}): saved {E[me]['saved']:.4f} "
             f"({es.index(me) + 1}위, 목표 {TARGET}), ALL {A[me]:.3f} ({rk[me]}위/{len(E)}); 예상 saved 1~3위 "
             + ", ".join(f"{n} {E[n]['saved']:.4f}" for n in es[:3]))
    return L


def summary_lines(now=None):
    import board_speed as bs
    from limits import ROUND_END
    now = now or time.time()
    j = bs.board()
    B = {b["name"]: b for b in j["buyers"] if b.get("all") is not None}
    P = bs.hist_at(bs.FULL, "b", now - WINDOW_H * 3600) or {}
    return lines(B, P, now, ROUND_END, bs.ME)


if __name__ == "__main__":
    print("\n".join(summary_lines()))
