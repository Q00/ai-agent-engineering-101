"""One-screen health check of the running shop and buyer. Read-only except for one table of its own
(board_snap in market.db, so the next run can compare board ranks with a day before).

    python tools/daily_check.py

Sources: market.db (cash, cases, settles, broken, my_broken, variant tables), .market/payments.jsonl,
.market/inventory.json, and https://market.ourolabs.ai/api/board.
"""
import json
import os
import re
import sqlite3
import sys
import time
from collections import Counter
from datetime import datetime

import httpx

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import strategy  # noqa: E402

ME = "choo-chu"
PLAYER = "Choo-chu"
START = 5_000_000
GOAL = 20
BOARD = "https://market.ourolabs.ai/api/board?round=3"


def t(ts):
    return datetime.fromtimestamp(ts).strftime("%m-%d %H:%M")


def won(x):
    return "-" if x is None else f"{int(x):,}"


def board():
    try:
        j = httpx.get(BOARD, timeout=30).json()
    except Exception as e:
        return None, str(e)

    def rank(rows, name, key="name", score="all"):
        s = sorted(rows, key=lambda x: -(x.get(score) or 0))
        return next(((i, x) for i, x in enumerate(s, 1) if x.get(key) == name), (None, {})), len(s)

    (sr, shop), ns = rank(j["shops"], ME)
    (br, buyer), nb = rank(j["buyers"], ME)
    (pr, player), npl = rank(j["players"], PLAYER, "player", "total")
    return {"shop_rank": sr, "shops": ns, "shop_all": shop.get("all"), "shop": shop,
            "buyer_rank": br, "buyers": nb, "buyer_all": buyer.get("all"), "buyer": buyer,
            "player_rank": pr, "players": npl, "total": player.get("total")}, None


def main():
    db = sqlite3.connect(os.path.join(HERE, "market.db"), timeout=30)
    db.execute("CREATE TABLE IF NOT EXISTS board_snap (ts REAL PRIMARY KEY, shop_rank INTEGER, shop_all REAL, "
               "buyer_rank INTEGER, buyer_all REAL, player_rank INTEGER, total REAL)")
    now = time.time()
    warn, out = [], []

    # 1. cash
    inv = json.load(open(os.path.join(HERE, ".market", "inventory.json"), encoding="utf-8"))
    cash = inv.get("balance", 0)
    stock = sum(u.get("cost", 0) for u in inv.get("units", []) if u.get("grade") != "expired")
    points = []
    for h in (24, 18, 12, 6, 0):
        row = db.execute("SELECT ts, balance FROM cash WHERE ts BETWEEN ? AND ? ORDER BY ABS(ts - ?) LIMIT 1",
                         (now - h * 3600 - 1800, now - h * 3600 + 1800, now - h * 3600)).fetchone()
        points.append(f"-{h}h {won(row[1]) if row else '-'}" if h else f"now {won(cash)}")
    out += ["1. cash (last 24 h, every 6 h): " + " | ".join(points),
            f"   stock at cost {won(stock)} ({len(inv.get('units', []))} units); net cash (balance - 5M) "
            f"{won(cash - START)}; book (net + stock) {won(cash - START + stock)}"]
    if cash < strategy.CASH_RESERVE:
        warn.append(f"cash {won(cash)} < {won(strategy.CASH_RESERVE)}")

    # 2. settles
    shop_n = db.execute("SELECT COUNT(*) FROM settles WHERE shop=?", (ME,)).fetchone()[0]
    buy_n = db.execute("SELECT COUNT(*) FROM settles WHERE buyer=?", (ME,)).fetchone()[0]
    shop_3h = db.execute("SELECT COUNT(*) FROM settles WHERE shop=? AND ts>=?", (ME, now - 3 * 3600)).fetchone()[0]
    buy_3h = db.execute("SELECT COUNT(*) FROM settles WHERE buyer=? AND ts>=?", (ME, now - 3 * 3600)).fetchone()[0]
    out.append(f"2. settles: shop {shop_n} ({'ok' if shop_n >= GOAL else 'below'} {GOAL}), last 3 h {shop_3h} | "
               f"buyer {buy_n} ({'ok' if buy_n >= GOAL else 'below'} {GOAL}), last 3 h {buy_3h}")
    if shop_3h == 0:
        warn.append("no shop settle in the last 3 h")
    if buy_3h == 0:
        warn.append("no buyer settle in the last 3 h")

    # 3. cases (last 24 h)
    rows = db.execute("SELECT ts, shop_case, buyer_case FROM cases ORDER BY ts").fetchall()
    dur, switches = Counter(), []
    for (ts, a, _), nxt in zip(rows, rows[1:] + [(now, None, None)]):
        lo, hi = max(ts, now - 86400), min(nxt[0], now)
        if hi > lo:
            dur[a] += (hi - lo) / 60
        if nxt[1] is not None and nxt[1] != a and nxt[0] >= now - 86400:
            switches.append(nxt[0])
    out.append(f"3. cases (24 h): {len(switches)} switches; minutes " +
               ", ".join(f"{k} {v:.0f}" for k, v in dur.items()) + (f"; now {rows[-1][1]}/{rows[-1][2]}" if rows else ""))
    for ts in switches:
        if sum(1 for x in switches if ts - 6 * 3600 < x <= ts) >= 4:
            warn.append("4 or more case switches within 6 h")
            break

    # 4. variants
    out.append("4. variants (weight, negotiations, per-negotiation result):")
    for key in strategy.VARIANTS:
        w, perf = strategy.weights(db, key), strategy.performance(db, key)
        out.append(f"   {key}: " + ", ".join(f"{v} {w[v]:.2f} n{perf[v][0]} {perf[v][1]}" for v in w))

    # 5. payment failures
    reasons = Counter()
    try:
        with open(os.path.join(HERE, ".market", "payments.jsonl"), encoding="utf-8") as f:
            pays = [json.loads(line) for line in f if line.strip()]
    except (OSError, ValueError):
        pays = []
    for p in pays:
        if not p.get("paid"):
            reasons[re.sub(r"[\d.]+", "N", p.get("reason") or "no reason")[:90]] += 1
    out.append(f"5. payment failures (payments.jsonl, {len(pays)} lines, {sum(reasons.values())} unpaid), top 5:")
    out += [f"   {n:4}  {r}" for r, n in reasons.most_common(5)]
    for r, n in reasons.items():
        if n >= 10:
            warn.append(f"payment failure x{n}: {r[:60]}")

    # 6. broken / refunds
    mine = db.execute("SELECT name, hours_after_buy, warranty_hours, in_warranty, refunded FROM my_broken").fetchall()
    sold = db.execute("SELECT b.seq, b.sku, b.hours_after_sale, b.warranty_hours, b.refunded FROM broken b "
                      "WHERE b.shop=?", (ME,)).fetchall()
    out.append(f"6. broken: bought by me {len(mine)}, sold by me {len(sold)}")
    out += [f"   bought: {n[:30]} broke {h:.1f} h after, warranty {w or 0} h, in warranty {bool(i)}, refunded {r}"
            for n, h, w, i, r in mine]
    out += [f"   sold: ledger #{s} {k} broke {h:.1f} h after, warranty {w or 0} h, refunded {bool(r)}"
            for s, k, h, w, r in sold]

    # 7. board
    b, err = board()
    if b is None:
        out.append(f"7. board: unavailable ({err})")
    else:
        out.append(f"7. board: shop ALL {b['shop_all']} (#{b['shop_rank']}/{b['shops']}, book {won(b['shop'].get('book'))}, "
                   f"stars {b['shop'].get('stars')}) | buyer ALL {b['buyer_all']} (#{b['buyer_rank']}/{b['buyers']}) | "
                   f"Players #{b['player_rank']}/{b['players']} total {b['total']}")
        prev = db.execute("SELECT ts, shop_rank, buyer_rank, player_rank FROM board_snap WHERE ts <= ? "
                          "ORDER BY ts DESC LIMIT 1", (now - 20 * 3600,)).fetchone()
        if prev:
            for name, old, new in (("shop", prev[1], b["shop_rank"]), ("buyer", prev[2], b["buyer_rank"]),
                                   ("Players", prev[3], b["player_rank"])):
                if old and new and new > old:
                    warn.append(f"{name} rank #{old} -> #{new} since {t(prev[0])}")
        else:
            out.append("   (no snapshot from a day ago yet: rank drops are checked from tomorrow)")
        db.execute("INSERT OR REPLACE INTO board_snap VALUES (?,?,?,?,?,?,?)",
                   (now, b["shop_rank"], b["shop_all"], b["buyer_rank"], b["buyer_all"], b["player_rank"], b["total"]))
        db.commit()

    print(f"== Choo daily check {t(now)} ==")
    print("\n".join(f"[WARN] {w}" for w in warn) or "[ok] no warning")
    print("\n".join(out))


if __name__ == "__main__":
    main()
