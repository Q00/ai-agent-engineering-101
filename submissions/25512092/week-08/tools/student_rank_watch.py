"""Student ranking every 3 hours (person 11/12 ~16:10): the midterm's Round 3 rank is the board Players table's
student_pct (shop ALL + buyer ALL recomputed among the students). One Telegram at every HOURS-th hour (00, 03, 06 ...):
our student_pct and student rank, our student_total, the gap to the 9th and 10th student totals, and our saved vs
the best other student buyer's saved (the 2nd among students). Read-only: GET /api/board only.
    python tools/student_rank_watch.py [--once | --dry]
"""
import os
import sys
import time
from datetime import datetime

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
ME = "Choo-chu"
HOURS = 3
RIVAL = "11im"                                             # 11/12 ~23:20 (person): saved gap to 11im
END = datetime(2026, 11, 17, 23, 59).timestamp()


def fetch():
    import httpx
    from market_db import MARKET
    return httpx.get(f"{MARKET}/api/board", params={"round": 3}, timeout=30).json()


def numbers(j, me=ME):
    st = [p for p in j.get("players") or [] if p.get("student")]
    st.sort(key=lambda p: -(p.get("student_total") or 0))
    mine = next(p for p in st if p["player"] == me)
    rank = 1 + sum(1 for p in st if (p.get("student_total") or 0) > mine["student_total"])
    tot = [p["student_total"] for p in st]
    stud_buyers = {(p.get("buyer") or {}).get("name") for p in st}
    my_b = (mine.get("buyer") or {}).get("name")
    saved = {b["name"]: b.get("saved") for b in j.get("buyers") or [] if b["name"] in stud_buyers and b.get("saved") is not None}
    others = sorted(((v, n) for n, v in saved.items() if n != my_b), reverse=True)
    sv = sorted(saved.values(), reverse=True)              # 11/12 ~18:30: student saved 1st / 3rd
    return {"pct": mine.get("student_pct"), "rank": rank, "n": len(st), "total": mine["student_total"],
            "saved_1": sv[0] if sv else None, "saved_3": sv[2] if len(sv) > 2 else None,
            "ninth": tot[8] if len(tot) > 8 else None, "tenth": tot[9] if len(tot) > 9 else None,
            "rival": saved.get(RIVAL), "saved": saved.get(my_b), "second": others[0] if others else None,
            "saved_rank": 1 + sum(1 for v, _ in others if v > (saved.get(my_b) or 0))}


def text(x, now=None):
    now = now or time.time()
    g = lambda v: "-" if v is None else f"{x['total'] - v:+.3f}"
    sec = x["second"]
    s = (f"saved {x['saved']} 학생 {x['saved_rank']}위, 다음 학생 {sec[1]} {sec[0]} (차이 {x['saved'] - sec[0]:+.4f})"
         if sec and x["saved"] is not None else f"saved {x['saved']}")
    out = (f"학생 순위 {datetime.fromtimestamp(now):%m-%d %H:%M}: {x['rank']}위/{x['n']} (student_pct {x['pct']}), "
           f"합계 {x['total']:.3f}; 9위 {x['ninth']} ({g(x['ninth'])}), 10위 {x['tenth']} ({g(x['tenth'])}) | " + s)
    if x.get("saved_1") is not None and x.get("saved") is not None:
        out += (f" | 학생 saved 1위 {x['saved_1']} ({x['saved'] - x['saved_1']:+.4f})"
                + (f", 3위 {x['saved_3']} ({x['saved'] - x['saved_3']:+.4f})" if x.get("saved_3") is not None else ""))
    if x.get("rival") is not None and x.get("saved") is not None:
        out += f" | {RIVAL} saved {x['rival']} (차이 {x['saved'] - x['rival']:+.4f})"
    if x.get("prev_grades") is not None and x.get("grades") is not None:
        c, pc = x["grades"][0].get("C"), x["prev_grades"][0].get("C")
        f = lambda v: "0건" if not v else f"{v[0]}건 {v[1]:.3f}"
        out += (f" | C등급(첫 제안 0.55, 11/12 23:20~): 최근 {HOURS}시간 {f(c)} vs 그 전 {HOURS}시간 {f(pc)}; "
                f"시간당 deals {x['grades'][1]:.1f} vs {x['prev_grades'][1]:.1f}")
    if x.get("rate") is not None:
        r = lambda v: f"{v[0]}/{v[1]} ({v[0] / v[1]:.0%})" if v[1] else "0/0"
        out += f" | 성사율 최근 {HOURS}시간 {r(x['rate'])} vs 그 전 {HOURS}시간 {r(x['prev_rate'])}"
    if x.get("grades") is not None:
        gs, dph = x["grades"]
        out += (f" | 최근 {HOURS}시간 등급별 거래(새 saved 추정 점수): "
                + (", ".join(f"{k} {n}건 {v:.3f}" for k, (n, v) in gs.items()) or "없음") + f"; 시간당 deals {dph:.1f}")
    return out


def grade_stats(now=None, hours=HOURS, db_path=None):
    """11/12 ~18:30: my buys of the last `hours` by the grade settled: (n, estimated new-saved score per deal) - the
    score with the unit cost at grade_rule's cost share of list (A 65%, B 50%, C 35%), capped at 1; and deals per hour."""
    import sqlite3
    from depts import grade_rule
    now = now or time.time()
    cs = grade_rule.rules()["cost_share"]
    d = sqlite3.connect(f"file:{db_path or os.path.join(HERE, 'market.db')}?mode=ro", uri=True, timeout=10)
    try:
        lp = dict(d.execute("SELECT sku, MAX(list_price) FROM buyer_deals WHERE ts>=? GROUP BY sku", (now - 3 * 86400,)))
        rows = d.execute("SELECT sku, grade, amount FROM settles WHERE buyer='choo-chu' AND ts>=? AND ts<?",
                         (now - hours * 3600, now)).fetchall()
    finally:
        d.close()
    out = {}
    for sku, g, amt in rows:
        L = lp.get(sku)
        if g in cs and L:
            out.setdefault(g, []).append(min(1.0, (L - amt) / (L - cs[g] * L)))
    return {g: (len(v), sum(v) / len(v)) for g, v in sorted(out.items())}, len(rows) / hours


def deal_rate(t0, t1, db_path=None):
    """11/12 ~23:45 (person): (deals, orders) of my buyer in [t0, t1) from buyer_deals."""
    import sqlite3
    d = sqlite3.connect(f"file:{db_path or os.path.join(HERE, 'market.db')}?mode=ro", uri=True, timeout=10)
    try:
        o, n = d.execute("SELECT COUNT(*), COALESCE(SUM(outcome='deal'), 0) FROM buyer_deals WHERE ts>=? AND ts<?",
                         (t0, t1)).fetchone()
    finally:
        d.close()
    return int(n), int(o)


def due(now, last):
    """The latest HOURS-th hour mark at or before now, if it was not reported yet."""
    t = datetime.fromtimestamp(now)
    mark = t.replace(minute=0, second=0, microsecond=0, hour=t.hour - t.hour % HOURS).timestamp()
    return mark if mark > (last or 0) else None


def main():
    if "--dry" in sys.argv:
        x = numbers(fetch())
        x["grades"] = grade_stats()
        x["prev_grades"] = grade_stats(time.time() - HOURS * 3600)
        t = time.time()
        x["rate"], x["prev_rate"] = deal_rate(t - HOURS * 3600, t), deal_rate(t - 2 * HOURS * 3600, t - HOURS * 3600)
        print(text(x))
        return
    import alerts
    last = None
    while time.time() < END + 3600:
        now = time.time()
        mark = due(now, last)
        if mark and now - mark < 1800:
            try:
                x = numbers(fetch())
                try:
                    x["grades"] = grade_stats(now)
                    x["prev_grades"] = grade_stats(now - HOURS * 3600)
                    x["rate"] = deal_rate(now - HOURS * 3600, now)
                    x["prev_rate"] = deal_rate(now - 2 * HOURS * 3600, now - HOURS * 3600)
                except Exception as e:
                    print(f"grade stats failed: {type(e).__name__}: {e}", flush=True)
                alerts.send("daily", f"student_rank:{int(mark)}", text(x, now))
                last = mark
            except Exception as e:
                print(f"{datetime.now():%H:%M:%S} failed: {type(e).__name__}: {e}", flush=True)
        elif mark:
            last = mark                                # started late in a slot: wait for the next mark
        if "--once" in sys.argv:
            return
        time.sleep(60)


if __name__ == "__main__":
    main()
