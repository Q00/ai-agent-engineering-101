"""Market tracking report: richest shops, deepest in the red, sharpest buyers, players, and what our shop sells.

    python3 market_track.py            # prints the report; keeps the snapshot in .market/r3/track-last.json
                                       # so the next run shows changes since this one

Reads the public board (/api/board), our memory DB (settles, negotiations) and .market/inventory.json. Read-only
toward the market. Board "profit" is balance minus the 5M starting capital, so stock bought and not yet sold lowers it.
"""
import json
import time

from r3lib import config, market
from r3lib.memory import Memory

LAST = config.MARKET_DIR / "r3" / "track-last.json"
M = lambda x: f"{(x or 0) / 1e6:7.2f}M"
mine = lambda n: "★" if n in config.MY_NAMES else " "


def delta(now, before, key, fmt=lambda d: f"{d / 1e6:+.2f}M"):
    if before is None or before.get(key) is None or now.get(key) is None:
        return ""
    d = now[key] - before[key]
    return fmt(d) if d else "="


def rank_delta(now_rank, before_rank):
    if before_rank is None:
        return ""
    d = before_rank - now_rank
    return f"(▲{d})" if d > 0 else f"(▼{-d})" if d < 0 else "(=)"


def main():
    b = None
    for wait in (0, 5, 15):                    # 2026-11-05 16:18: one empty board answer blanked the report
        time.sleep(wait)                       #   and overwrote track-last.json with an empty snapshot
        b = market.get_json("/api/board")
        if isinstance(b, dict) and b.get("shops"):
            break
    if not (isinstance(b, dict) and b.get("shops")):
        raise SystemExit("board unavailable (/api/board gave no shops); track-last.json left as it was")
    last = json.loads(LAST.read_text()) if LAST.exists() else {}
    prev_shops, prev_buyers, prev_players = last.get("shops", {}), last.get("buyers", {}), last.get("players", {})
    snap = {"ts": time.time(), "shops": {}, "buyers": {}, "players": {}}
    t = time.strftime("%Y-%m-%d %H:%M KST", time.gmtime(time.time() + 9 * 3600))
    since = time.strftime("%H:%M", time.gmtime(last["ts"] + 9 * 3600)) if last else None
    print(f"# Market tracking {t}" + (f"  (changes since {since})" if since else ""))

    # Shop ALL takes Book (balance - start + unsold stock at cost, patch 3.3), not the board's cash "profit", so rank
    # and change are by book; profit stays as a column.
    book = lambda s: s.get("book") if s.get("book") is not None else s.get("profit")
    shops = sorted([s for s in b.get("shops") or [] if s.get("deals")], key=lambda s: -(book(s) or 0))
    for i, s in enumerate(shops, 1):
        snap["shops"][s["name"]] = {"rank": i, "book": book(s), "profit": s.get("profit"), "revenue": s.get("revenue"),
                                    "deals": s.get("deals")}
    print("\n## Richest shops (book = net profit + stock at cost; Shop ALL uses book)")
    print(f"{'#':>3}  {'shop':22}{'book':>9}{'change':>9}{'profit':>9}{'stock':>9}{'revenue':>10}{'deals':>7}{'stars':>6}{'ALL':>6}")
    rows = shops[:10] + [s for s in shops[10:] if s["name"] in config.MY_NAMES]
    for s in rows:
        i = snap["shops"][s["name"]]["rank"]
        p = prev_shops.get(s["name"])
        print(f"{i:>3}{mine(s['name'])} {s['name'][:22]:22}{M(book(s))}{delta(snap['shops'][s['name']], p, 'book'):>9}"
              f"{M(s.get('profit'))}{M(s.get('stock_cost'))}{M(s.get('revenue'))}{s.get('deals'):>7}{s.get('stars'):>6}"
              f"{s.get('all'):>6} {rank_delta(i, (p or {}).get('rank'))}")

    print("\n## Deepest in the red")
    for s in sorted(shops, key=lambda s: book(s) or 0)[:6]:
        print(f"     {s['name'][:22]:22}{M(book(s))}{M(s.get('revenue'))}{s.get('deals'):>7}{s.get('stars'):>6}"
              f"  bankruptcies {s.get('bankruptcies')}")

    buyers = sorted([x for x in b.get("buyers") or [] if x.get("deals")], key=lambda x: -(x.get("surplus") or 0))
    for i, x in enumerate(buyers, 1):
        snap["buyers"][x["name"]] = {"rank": i, "surplus": x.get("surplus"), "posts": x.get("posts")}
    saved_rank = {x["name"]: i for i, x in enumerate(sorted(buyers, key=lambda x: -(x.get("saved") or 0)), 1)}
    # Buyer ALL = 0.5 saved (percentile) + 0.3 forum rating as itself (patch 3.5) + 0.1 deals + 0.1 posts
    print("\n## Sharpest buyers (surplus; Buyer ALL scores saved, so its rank is shown too)")
    print(f"{'#':>3}  {'buyer':22}{'surplus':>9}{'change':>9}{'saved':>7}{'s#':>4}{'deals':>7}{'paid':>6}{'posts':>6}{'forum':>6}{'ALL':>6}")
    rows = buyers[:8] + [x for x in buyers[8:] if x["name"] in config.MY_NAMES]
    for x in rows:
        i = snap["buyers"][x["name"]]["rank"]
        p = prev_buyers.get(x["name"])
        print(f"{i:>3}{mine(x['name'])} {x['name'][:22]:22}{M(x.get('surplus'))}{delta(snap['buyers'][x['name']], p, 'surplus'):>9}"
              f"{x.get('saved') or 0:>7.3f}{saved_rank[x['name']]:>4}{x.get('deals'):>7}{x.get('paid'):>6}{x.get('posts'):>6}{x.get('forum'):>6}{x.get('all'):>6} "
              f"{rank_delta(i, (p or {}).get('rank'))}")

    players = b.get("players") or []
    print("\n## Players (shop ALL + buyer ALL)")
    for i, pl in enumerate(players, 1):
        snap["players"][pl["player"]] = {"rank": i, "total": pl.get("total")}
        ours = pl["player"] in config.MY_NAMES or (pl.get("shop") or {}).get("name") in config.MY_NAMES
        if i <= 5 or ours:
            p = prev_players.get(pl["player"])
            print(f"{i:>3}{'★' if ours else ' '} {pl['player'][:22]:22}{pl.get('total'):>7}  shop {(pl.get('shop') or {}).get('all')}"
                  f"  buyer {(pl.get('buyer') or {}).get('all')} {rank_delta(i, (p or {}).get('rank'))}")

    m, now = Memory(), time.time()
    urls = m.my_shop_urls()
    ph = ",".join("?" * len(urls))
    print("\n## What we sell (last 6 h)  vs the market")
    if urls:
        with m.db() as c:
            ours = {r["category"]: r for r in c.execute(
                f"SELECT category, COUNT(*) n, SUM(amount) rev, AVG(amount*1.0/list_price) pr FROM settles "
                f"WHERE ts>? AND shop IN ({ph}) GROUP BY category ORDER BY rev DESC LIMIT 10", (now - 6 * 3600, *urls))}
            print(f"     {'category':18}{'sold':>6}{'revenue':>10}{'price/list':>11}{'market p/l':>11}")
            for k, r in ours.items():
                mk = c.execute(f"SELECT AVG(amount*1.0/list_price) pr FROM settles WHERE ts>? AND category=? AND "
                               f"shop NOT IN ({ph})", (now - 6 * 3600, k, *urls)).fetchone()["pr"]
                print(f"     {market.leaf(k)[:18]:18}{r['n']:>6}{M(r['rev']):>10}{r['pr']:>11.3f}{(mk or 0):>11.3f}")
            prem = c.execute(f"SELECT COUNT(*) n, COALESCE(SUM(amount), 0) rev FROM settles WHERE ts>? AND kind='goods' "
                             f"AND list_price>=? AND shop IN ({ph})", (now - 24 * 3600, config.PREMIUM_MIN_LIST, *urls)).fetchone()
            neg = c.execute("SELECT COUNT(*) n, AVG(outcome='deal') cl, AVG(first_ask*1.0/list_price) fa, "
                            "AVG(CASE WHEN outcome='deal' THEN final_price*1.0/list_price END) fp FROM negotiations "
                            "WHERE role='shop' AND list_price>0 AND ts>?", (now - 2 * 3600,)).fetchone()
        print(f"\n     premium (800k+) sold in 24 h: {prem['n']} for {M(prem['rev'])}")
        print(f"     last 2 h negotiations: {neg['n']}, close rate {neg['cl'] or 0:.3f}, first ask {neg['fa'] or 0:.3f}, "
              f"final {neg['fp'] or 0:.3f} of list")

    inv = market.read_json("inventory.json", {}) or {}
    units = [u for u in inv.get("units") or [] if u.get("grade") != "expired"]
    by = {}
    for u in units:
        d = by.setdefault(market.leaf(u["category"]), [0, 0])
        d[0] += 1
        d[1] += u["cost"]
    print(f"\n## Stock: balance {M(inv.get('balance'))}, {len(units)} units costing {M(sum(u['cost'] for u in units))}")
    print("     " + ", ".join(f"{k} {n} ({M(c).strip()})" for k, (n, c) in sorted(by.items(), key=lambda x: -x[1][1])[:6]))
    prem_held = [u for u in units if u.get("kind") == "goods" and (u.get("list_price") or 0) >= config.PREMIUM_MIN_LIST]
    print(f"     premium held: {len(prem_held)} " + ", ".join(f"{(u.get('name') or u['sku'])[:24]} ({u.get('grade')})" for u in prem_held))

    LAST.parent.mkdir(parents=True, exist_ok=True)
    LAST.write_text(json.dumps(snap, ensure_ascii=False))


if __name__ == "__main__":
    main()
