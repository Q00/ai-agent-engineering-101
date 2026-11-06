"""market.benchmark (design 16-2) and the hourly market watch, from the public ledger (observed, by code).

    py -m harness.market            # the top shops by revenue share over the last 6 h, and ours
    py -m harness.market watch      # write this hour's knowledge items (the runner does it every hour)

User 11/6 10:5x: find why our share of the market fell (15% on 11/5 morning, 3-4% since 11/5 18h) and benchmark
the shops with the highest share. Shops are told apart by the ledger's shop field (a URL); names come from what
our buyer met (reflex url_names) and the kit's shops.json; ours is found by the ledger lines our seller settled
(seller.settled), since our tunnel URL changes when the kit restarts. outer/benchmark.py ranked by the old cash
column and matched us by name, so it showed our shop with 0 deals.
"""
import collections
import json
import statistics as st
import sys
import time

from core import atomic, paths
from harness import knowledge, trajectory

GOODS_HIGH = 100000


def _catalog():
    c = atomic.read_json(paths.root() / ".market" / "catalog.json", default=None) or {}
    return c.get("items", c) if isinstance(c, dict) else {}


def _names():
    """{shop url: name} from what our buyer met and the kit's last shops.json."""
    from core import reflex
    out = dict(reflex.load().get("url_names") or {})
    shops = atomic.read_json(paths.root() / ".market" / "shops.json", default=None) or []
    for s in shops if isinstance(shops, list) else shops.get("shops", []):
        if s.get("url") and s.get("name"):
            out[s["url"]] = s["name"]
    return out


def _days(since, until):
    return sorted({time.strftime("%Y%m%d", time.localtime(t)) for t in (since, until)})


def settles(since, until, kinds=("settle",)):
    rows = []
    for d in _days(since, until):
        p = paths.memory() / "market" / f"ledger-{d}.jsonl"
        if not p.exists():
            continue
        with open(p, encoding="utf-8") as f:
            for line in f:
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if e.get("kind") in kinds and since <= e["ts"] < until:
                    rows.append(e)
    return rows


def our_orders(since, until):
    """The orders our shop sold: seller.settled names the ledger's paid result line, which names the order; the
    settle line of the same order is the sale (11/6: the two lines have different seqs)."""
    seqs = set()
    for d in _days(since - 3600, until):
        for e in trajectory.read_day(d):
            if e["kind"] == "seller.settled" and e["keys"].get("ledger_seq"):
                seqs.add(e["keys"]["ledger_seq"])
    return {e["data"].get("order") for e in settles(since - 3600, until + 3600, kinds=("result",))
            if e.get("seq") in seqs and e["data"].get("order")}


def benchmark(hours=6, top=5, now=None):
    """The top shops by revenue over the window, and ours: revenue, share, deals, median share of list paid,
    goods share of revenue, mean list price, top SKUs, and which of their top SKUs we sold none of."""
    now = now or time.time()
    since = now - hours * 3600
    cat, names, mine = _catalog(), _names(), our_orders(since, now)
    by = collections.defaultdict(lambda: {"revenue": 0, "deals": 0, "shares": [], "goods_rev": 0, "lists": [],
                                          "skus": collections.Counter()})
    total = 0
    for e in settles(since, now):
        d = e["data"]
        key = "ours" if d.get("order") in mine else d.get("shop") or "?"
        amt, sku = d.get("amount") or 0, d.get("sku")
        c = cat.get(sku) or {}
        s = by[key]
        s["revenue"] += amt
        s["deals"] += 1
        s["skus"][sku] += 1
        total += amt
        if c.get("list_price"):
            s["shares"].append(amt / c["list_price"])
            s["lists"].append(c["list_price"])
        if c.get("kind") == "goods":
            s["goods_rev"] += amt

    def row(key, s):
        return {"shop": "ours" if key == "ours" else names.get(key, key[-40:]), "revenue": s["revenue"],
                "share_of_market": round(s["revenue"] / total, 4) if total else None, "deals": s["deals"],
                "deals_per_h": round(s["deals"] / hours, 1),
                "median_share_of_list": round(st.median(s["shares"]), 3) if s["shares"] else None,
                "goods_share_of_revenue": round(s["goods_rev"] / s["revenue"], 3) if s["revenue"] else None,
                "mean_list_price": round(st.mean(s["lists"])) if s["lists"] else None,
                "top_skus": [[k, n, (cat.get(k) or {}).get("name", "")[:30]] for k, n in s["skus"].most_common(8)]}
    ranked = sorted(by.items(), key=lambda kv: -kv[1]["revenue"])
    rank = {k: i + 1 for i, (k, _) in enumerate(ranked)}
    ours_skus = set(by["ours"]["skus"]) if "ours" in by else set()
    tops = []
    for k, s in ranked[:top]:
        r = row(k, s)
        r["rank"] = rank[k]
        r["their_top_skus_we_sold_none_of"] = [x for x, _ in s["skus"].most_common(8) if x not in ours_skus]
        tops.append(r)
    ours = dict(row("ours", by["ours"]), rank=rank.get("ours"), of=len(ranked)) if "ours" in by else None
    return {"hours": hours, "market_revenue": total, "shops": len(ranked), "top": tops, "ours": ours}


def watch(now=None):
    """Hourly knowledge (observed): the high-list goods price level and our share of the market."""
    now = now or time.time()
    since = now - 3600
    cat = _catalog()
    shares = [e["data"]["amount"] / cat[e["data"]["sku"]]["list_price"] for e in settles(since, now)
              if (cat.get(e["data"].get("sku")) or {}).get("kind") == "goods"
              and (cat[e["data"]["sku"]].get("list_price") or 0) >= GOODS_HIGH and e["data"].get("amount")]
    b = benchmark(hours=1, top=3, now=now)
    stamp = time.strftime("%Y%m%d-%H", time.localtime(now))
    items = []
    if shares:
        items.append({"id": f"market:price:highlist:{stamp}", "about": "market:price:highlist",
                      "claim": f"high-list goods settled at a median {st.median(shares):.3f} of list in the hour to "
                               f"{time.strftime('%m-%d %H:%M', time.localtime(now))}",
                      "data": {"median_share": round(st.median(shares), 4), "n": len(shares)},
                      "source": "data:ledger", "method": "median settle / list, goods list >= 100k, 1 h",
                      "sample": {"since": since, "until": now, "n": len(shares)}, "expires_in": 7 * 86400})
    if b["ours"]:
        items.append({"id": f"shop:share_of_market:{stamp}", "about": "shop:share_of_market",
                      "claim": f"our shop took {b['ours']['share_of_market']:.3f} of market revenue, rank "
                               f"{b['ours']['rank']} of {b['ours']['of']} shops, in the hour",
                      "data": {k: b["ours"][k] for k in ("share_of_market", "rank", "of", "deals",
                                                         "median_share_of_list")},
                      "source": "data:ledger", "method": "harness.market.benchmark, 1 h",
                      "sample": {"since": since, "until": now, "n": b["ours"]["deals"]}, "expires_in": 7 * 86400})
    return knowledge.write_many(items, now)


if __name__ == "__main__":
    if sys.argv[1:2] == ["watch"]:
        print(json.dumps(watch(), ensure_ascii=False, indent=1)[:2000])
    else:
        print(json.dumps(benchmark(), ensure_ascii=False, indent=1))
