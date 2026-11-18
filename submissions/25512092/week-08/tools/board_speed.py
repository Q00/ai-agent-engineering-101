"""Board speed (person's request 11/09 ~21:00, analysis only): per hour over the last 3 / 6 / 24 h, and the rank at the
round end if those speeds hold.
  shops   Book, revenue, rank. ALL = 0.5 Book pct + 0.3 revenue pct + 0.2 stars pct (fits the board within 0.001)
  buyers  surplus, deals, saved, rank. ALL = 0.5 saved pct + 0.3 forum + 0.1 deals pct + 0.1 posts pct
Sources: revenue and deals of every shop and buyer from the market ledger (market.db settles); my Book / saved / ranks
from board_hist (mine only); other buyers' surplus / deals / posts from logs/board_buyers.jsonl (since 11/09 14:05);
other shops' Book and other buyers' saved have no history before logs/board_full.jsonl (this tool, --log, every
10 min from 11/09 ~21:15). Where a speed is missing the projection holds that value at today's level (said in the text).
    python tools/board_speed.py [--log]          (--log: snapshot loop; else print the report)
"""
import json
import os
import sqlite3
import sys
import time
from datetime import datetime

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.chdir(HERE)
ME = "choo-chu"
FULL = os.path.join(HERE, "logs", "board_full.jsonl")
WINDOWS = (3, 6, 24)
NEAR_SHOP, NEAR_BUYER = 3, 3


def board():
    import httpx
    return httpx.get("https://market.ourolabs.ai/api/board", params={"round": 3}, timeout=30).json()


def snap(j=None):
    j = j or board()
    rec = {"ts": round(time.time()),
           "s": {s["name"]: {k: s.get(k) for k in ("book", "revenue", "profit", "deals", "stars_rank", "all")}
                 for s in j["shops"] if s.get("all") is not None},
           "b": {b["name"]: {k: b.get(k) for k in ("saved", "surplus", "deals", "orders", "posts", "forum", "all")}
                 for b in j["buyers"] if b.get("all") is not None}}
    with open(FULL, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def pct(vals, v):
    lo = sum(1 for x in vals if x < v)
    eq = sum(1 for x in vals if x == v)
    return (lo + 0.5 * (eq - 1)) / (len(vals) - 1) if len(vals) > 1 else 0.5


def shop_all(S):
    """{name: ALL} for {name: {book, revenue, stars_rank}}"""
    cols = {k: [s[k] for s in S.values()] for k in ("book", "revenue", "stars_rank")}
    return {n: 0.5 * pct(cols["book"], s["book"]) + 0.3 * pct(cols["revenue"], s["revenue"])
            + 0.2 * pct(cols["stars_rank"], s["stars_rank"]) for n, s in S.items()}


def buyer_all(B):
    cols = {k: [b[k] for b in B.values()] for k in ("saved", "deals", "posts")}
    return {n: 0.5 * pct(cols["saved"], b["saved"]) + 0.3 * (b["forum"] or 0) + 0.1 * pct(cols["deals"], b["deals"])
            + 0.1 * pct(cols["posts"], b["posts"]) for n, b in B.items()}


def ranks(scores):
    order = sorted(scores, key=lambda n: -scores[n])
    return {n: i + 1 for i, n in enumerate(order)}


def ledger(now, h):
    """{shop: (revenue, deals)}, {buyer: (deals, surplus estimate)} over the last h hours (settles = all the market)."""
    from depts.substitute import catalog
    cat = catalog()
    db = sqlite3.connect(os.path.join(HERE, "market.db"), timeout=60)
    try:
        rows = db.execute("SELECT shop, buyer, sku, amount FROM settles WHERE ts>=? AND ts<?", (now - h * 3600, now)).fetchall()
    finally:
        db.close()
    S, B = {}, {}
    for shop, buyer, sku, amt in rows:
        r, d = S.get(shop, (0, 0))
        S[shop] = (r + (amt or 0), d + 1)
        lp = (cat.get(sku) or {}).get("list_price") or 0
        d, sp = B.get(buyer, (0, 0))
        B[buyer] = (d + 1, sp + max(0, lp - (amt or 0)))
    return S, B


def hist_at(path, key, t):
    """The snapshot line closest to t (within 20 min) from a jsonl of {"ts", key: {...}}; None if none."""
    best = None
    try:
        with open(path, encoding="utf-8") as f:
            for ln in f:
                if not ln.strip():
                    continue
                try:
                    e = json.loads(ln)
                except ValueError:                    # a line cut by a concurrent write
                    continue
                if abs(e["ts"] - t) <= 1200 and (best is None or abs(e["ts"] - t) < abs(best["ts"] - t)):
                    best = e
    except OSError:
        return None
    return best[key] if best else None


def mine_at(t):
    """My shop and buyer board rows at t via tools/board_read.py (11/11 ~17:00, person: one common reading): now = the
    live board, else the nearest snapshot; past = the nearest board_hist snapshot within 45 min; (None, None) failed."""
    import board_read
    return board_read.mine(t)


def report(j=None, now=None):
    from limits import ROUND_END
    j = j or board()
    now = now or time.time()
    left = (ROUND_END - now) / 3600
    S = {s["name"]: s for s in j["shops"] if s.get("all") is not None}
    B = {b["name"]: b for b in j["buyers"] if b.get("all") is not None}
    srank = ranks({n: s["all"] for n, s in S.items()})
    brank = ranks({n: b["all"] for n, b in B.items()})
    my_s, my_b = srank.get(ME), brank.get(ME)
    near_s = [n for n, r in srank.items() if abs(r - my_s) <= NEAR_SHOP]
    near_b = [n for n, r in brank.items() if abs(r - my_b) <= NEAR_BUYER]
    near_b += [n for n in sorted(B, key=lambda n: -B[n]["saved"])[:3] if n not in near_b]      # the saved top 3 too
    near_s.sort(key=lambda n: srank[n])
    near_b.sort(key=lambda n: brank[n])
    W = {h: ledger(now, h) for h in WINDOWS}
    L = [f"보드 속도 {datetime.fromtimestamp(now):%m-%d %H:%M} (라운드 끝까지 {left:.0f}시간), 시간당 증가 3h / 6h / 24h"]
    f0 = lambda x: "-" if x is None else f"{x:,.0f}"
    f4 = lambda x: "-" if x is None else f"{x:+.4f}"
    # shops
    L.append(f"[가게] 우리 {my_s}위 / {len(S)} (ALL {S[ME]['all']}), 주변 ±{NEAR_SHOP}")
    book_rate = {}
    for n in near_s:
        rev = [W[h][0].get(n, (0, 0))[0] / h for h in WINDOWS]
        bk = []
        for h in WINDOWS:
            past = mine_at(now - h * 3600)[0] if n == ME else (hist_at(FULL, "s", now - h * 3600) or {}).get(n)
            bk.append((S[n]["book"] - past["book"]) / h if past else None)
        rk = []
        if n == ME:
            for h in WINDOWS:
                p = mine_at(now - h * 3600)[0]
                rk.append(p.get("rank") if p else None)
        book_rate[n] = next((x for x in (bk[2], bk[1], bk[0]) if x is not None), None)
        L.append(f"  {srank[n]:>2}위 {n}{' (우리)' if n == ME else ''}: Book {f0(S[n]['book'])} ({' / '.join(f0(x) for x in bk)}), "
                 f"매출 {f0(S[n]['revenue'])} ({' / '.join(f0(x) for x in rev)}), 별점 {S[n]['stars_rank']}"
                 + (f", 순위 3/6/24시간 전 {' / '.join(str(x) for x in rk)}" if rk else ""))
    # buyers
    L.append(f"[buyer] 우리 ALL {my_b}위 / {len(B)} (ALL {B[ME]['all']}), saved 1위 기준 주변 ±{NEAR_BUYER} + saved 상위 3")
    for n in near_b:
        dl = [W[h][1].get(n, (0, 0))[0] / h for h in WINDOWS]
        sp = []
        for h in WINDOWS:
            past = (mine_at(now - h * 3600)[1] if n == ME else None) or \
                   (hist_at(os.path.join(HERE, "logs", "board_buyers.jsonl"), "b", now - h * 3600) or {}).get(n)
            sp.append((B[n]["surplus"] - past["surplus"]) / h if past else None)
        if sp[2] is None:
            est = W[24][1].get(n, (0, 0))[1] / 24
            sp[2] = est
            sp24 = f"{f0(est)}(원장 추정)"
        else:
            sp24 = f0(sp[2])
        sv = []
        for h in WINDOWS:
            past = mine_at(now - h * 3600)[1] if n == ME else (hist_at(FULL, "b", now - h * 3600) or {}).get(n)
            sv.append((B[n]["saved"] - past["saved"]) / h if past else None)
        rk = []
        if n == ME:
            for h in WINDOWS:
                p = mine_at(now - h * 3600)[1]
                rk.append(f"{p.get('rank')}(saved {p.get('saved_rank')})" if p else "-")
        L.append(f"  {brank[n]:>2}위 {n}{' (우리)' if n == ME else ''}: surplus {f0(B[n]['surplus'])} ({f0(sp[0])} / {f0(sp[1])} / "
                 f"{sp24}), deals {B[n]['deals']:,} ({' / '.join(f'{x:.1f}' for x in dl)}), saved {B[n]['saved']} "
                 f"({' / '.join(f4(x) for x in sv)}), 글 {B[n]['posts']}, forum {B[n]['forum']}"
                 + (f", 순위 3/6/24시간 전 {' / '.join(rk)}" if rk else ""))
    # projection to the round end
    L.append(f"[라운드 끝 예상] 지금 속도가 {left:.0f}시간 유지되면 (6h 속도 기준, 괄호는 24h 속도 기준)")
    for base in (6, 24):
        Sp = {}
        for n, s in S.items():
            rv = W[base][0].get(n, (0, 0))[0] / base
            bk = book_rate.get(n) if base == 24 else None
            if n == ME:
                p = mine_at(now - base * 3600)[0]
                bk = (s["book"] - p["book"]) / base if p else None
            elif bk is None:
                past = (hist_at(FULL, "s", now - base * 3600) or {}).get(n)
                bk = (s["book"] - past["book"]) / base if past else None
            Sp[n] = {"book": s["book"] + (bk or 0) * left, "revenue": s["revenue"] + rv * left, "stars_rank": s["stars_rank"]}
        Bp = {}
        posts_rate = {}
        for n, b in B.items():
            past = (hist_at(os.path.join(HERE, "logs", "board_buyers.jsonl"), "b", now - min(base, 6) * 3600) or {}).get(n)
            posts_rate[n] = (b["posts"] - past["posts"]) / min(base, 6) if past else 0
            dr = W[base][1].get(n, (0, 0))[0] / base
            Bp[n] = {"saved": b["saved"], "forum": b["forum"], "deals": b["deals"] + dr * left,
                     "posts": b["posts"] + posts_rate[n] * left}
        rs, rb = ranks(shop_all(Sp)), ranks(buyer_all(Bp))
        sv_rank = ranks({n: b["saved"] for n, b in Bp.items()})
        if base == 6:
            r6 = (rs, rb, sv_rank)
        else:
            r24 = (rs, rb, sv_rank)
    L.append(f"  가게: 우리 {r6[0][ME]}위 ({r24[0][ME]}위), 지금 {my_s}위; 주변 " + ", ".join(
        f"{n} {r6[0][n]}({r24[0][n]})" for n in near_s if n != ME))
    L.append(f"  buyer ALL: 우리 {r6[1][ME]}위 ({r24[1][ME]}위), 지금 {my_b}위, saved {r6[2][ME]}위; 주변 " + ", ".join(
        f"{n} {r6[1][n]}({r24[1][n]})" for n in near_b if n != ME))
    have_book = os.path.exists(FULL) and any(hist_at(FULL, "s", now - h * 3600) for h in WINDOWS)
    L.append("  가정: 다른 가게 Book 속도" + (" 스냅샷 기준" if have_book else " 기록 없음 → 지금 값 고정 (우리만 실제 속도) - "
             "가게 예상 순위는 우리 쪽으로 치우침. logs/board_full.jsonl 쌓이면 3시간 뒤부터 실제 속도")
             + "; buyer saved와 forum은 지금 값 고정(다른 buyer saved 이력 없음), deals는 원장, 글은 스냅샷 속도로 늘림; 별점 고정")
    return "\n".join(L)


def main():
    if "--log" in sys.argv:
        from limits import ROUND_END
        while time.time() < ROUND_END:
            try:
                snap()
            except Exception:
                pass
            time.sleep(600)
        return
    j = board()
    try:
        snap(j)
    except Exception:
        pass
    print(report(j))


if __name__ == "__main__":
    main()
