"""Reviews one buyer left on settled deals in Round 3, from the ledger dump (tools/ledger_dump.py).

    python tools/review_buyer.py totato2203-r3

Per shop: settled deals reviewed, star distribution. Per review wording (numbers and SKUs replaced by #):
count and stars. The same wording from other buyers, for comparison. My shop's average stars with and without
this buyer. Writes reports/review_<buyer>_YYYYMMDD.md. Facts only: no reading of intent.
"""
import json
import os
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(HERE, "reports", "cache", "ledger_r3.jsonl")
ME, ME_DISPLAY = "choo-chu", "Choo Shop"
PHRASE = "no unobserved quality or failure claims"


def wording(text):
    t = re.sub(r"\bG[0-9a-f]{8}\b", "<sku>", text or "")
    t = re.sub(r"u-[0-9a-f]{12}", "<unit>", t)
    return re.sub(r"\d[\d,.]*", "#", t)[:110]


def stars_str(c):
    return ", ".join(f"{s}* {c[s]}" for s in sorted(c, reverse=True))


def avg(xs):
    xs = [x for x in xs if x is not None]
    return f"{sum(xs) / len(xs):.2f} (n {len(xs)})" if xs else "-"


def main(buyer):
    db = sqlite3.connect(os.path.join(HERE, "market.db"))
    url_name = dict(db.execute("SELECT shop_url, shop FROM settles WHERE shop_url IS NOT NULL AND shop IS NOT NULL"))
    settles, results = {}, []
    with open(CACHE, encoding="utf-8") as f:
        for line in f:
            e = json.loads(line)
            if e["kind"] == "settle":
                settles[e["data"]["order"]] = e
            elif e["kind"] == "result":
                results.append(e)
    name = lambda r: url_name.get(r["data"].get("shop")) or (r["data"].get("receipt") or {}).get("shop") \
        or r["data"].get("shop")
    settled = [r for r in results if r["data"].get("order") in settles]
    mine = [r for r in settled if r["data"]["buyer"] == buyer]

    by_shop = defaultdict(Counter)
    for r in mine:
        by_shop[name(r)][r["data"]["stars"]] += 1
    words = defaultdict(Counter)
    for r in mine:
        words[wording(r["data"]["review"])][r["data"]["stars"]] += 1
    phrase = [r for r in mine if PHRASE in (r["data"].get("review") or "")]
    phrase_shops = Counter(name(r) for r in phrase)
    phrase_stars = Counter(r["data"]["stars"] for r in phrase)
    others = [r for r in settled if r["data"]["buyer"] != buyer and PHRASE in (r["data"].get("review") or "")]
    other_buyers = Counter(r["data"]["buyer"] for r in others)
    other_stars = Counter(r["data"]["stars"] for r in others)

    my_urls = {u for u, n in url_name.items() if n == ME}
    is_me = lambda r: r["data"].get("shop") in my_urls or (r["data"].get("receipt") or {}).get("shop") == ME_DISPLAY
    my_all = [r for r in results if is_me(r)]
    my_settled = [r for r in my_all if r["data"].get("order") in settles]
    ones = sorted((r for r in phrase if r["data"]["stars"] == 1), key=lambda r: r["seq"])

    span = f"{datetime.fromtimestamp(results[0]['ts']):%m-%d %H:%M} - {datetime.fromtimestamp(results[-1]['ts']):%m-%d %H:%M}"
    lines = [f"# Reviews by buyer {buyer} on settled deals, Round 3", "",
             f"Source: ledger result entries {span} ({len(results):,} results, {len(settles):,} settles), "
             "a result counts as settled when a settle entry has the same order. Wording groups replace numbers, "
             "SKUs and unit ids with #, <sku>, <unit>.", "",
             f"## Settled deals reviewed by {buyer}: {len(mine)} at {len(by_shop)} shops", "",
             f"- stars: {stars_str(Counter(r['data']['stars'] for r in mine))}", "",
             "| shop | reviews | stars |", "|---|---|---|"]
    lines += [f"| {s} | {sum(c.values())} | {stars_str(c)} |"
              for s, c in sorted(by_shop.items(), key=lambda kv: -sum(kv[1].values()))]
    lines += ["", "## Review wording", "", "| wording | reviews | stars |", "|---|---|---|"]
    lines += [f"| {w} | {sum(c.values())} | {stars_str(c)} |"
              for w, c in sorted(words.items(), key=lambda kv: -sum(kv[1].values()))[:15]]
    lines += ["", f"## Reviews containing '{PHRASE}'", "",
              f"- {buyer}: {len(phrase)} reviews at {len(phrase_shops)} shops, stars {stars_str(phrase_stars)}",
              f"- shops: " + ", ".join(f"{s} {n}" for s, n in phrase_shops.most_common()),
              f"- other buyers with the same wording on settled deals: {len(others)} reviews from {len(other_buyers)} "
              f"buyers, stars {stars_str(other_stars) or '-'}"
              + (f" ({', '.join(f'{b} {n}' for b, n in other_buyers.most_common(5))})" if other_buyers else ""),
              "- 1-star examples (ledger #): " + ", ".join(f"#{r['seq']} {name(r)}" for r in ones[:10]), "",
              f"## {ME}: average stars", "",
              f"- all results naming my shop: with {buyer} {avg([r['data']['stars'] for r in my_all])}, "
              f"without {avg([r['data']['stars'] for r in my_all if r['data']['buyer'] != buyer])}",
              f"- settled deals only: with {avg([r['data']['stars'] for r in my_settled])}, "
              f"without {avg([r['data']['stars'] for r in my_settled if r['data']['buyer'] != buyer])}",
              f"- {buyer} on my shop: {stars_str(Counter(r['data']['stars'] for r in my_all if r['data']['buyer'] == buyer)) or '-'}",
              ""]
    slug = re.sub(r"\d.*", "", buyer)                   # totato2203-r3 -> totato
    path = os.path.join(HERE, "reports", f"review_{slug}_{datetime.now():%Y%m%d}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("\n".join(lines))
    print(f"-> {path}")
    json.dump({"phrase": len(phrase), "ones": [r["seq"] for r in ones], "shops": len(phrase_shops),
               "ones_shops": len({name(r) for r in ones})},
              open(os.path.join(HERE, "reports", "cache", "review_summary.json"), "w"))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "totato2203-r3")
