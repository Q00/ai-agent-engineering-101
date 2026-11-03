"""REPORT (3): what happened to the inextro shop and buyer in Rounds 0, 1 and 2, from the public ledger.

    python analysis/rounds.py

Per round: the shop's visits, deals, verified deals, share of all verified deals and rank, stars and
price over the reserve; the buyer's orders, deals and spend against the budget. Round 2 is also
split into 6-hour blocks (UTC) for the shop.
"""
import collections

from ledger import ME, RESERVE, load, shop_names, shop_of, utc

entries = load()
names = shop_names(entries)
orders = {e["data"]["order"]: e["data"] for e in entries if e["kind"] == "order"}
results = [e for e in entries if e["kind"] == "result"]
print(f"ledger seq 1..{entries[-1]['seq']}, up to {utc(entries[-1]['ts'])} UTC\n")


def shop_stats(res):
    verified = [e for e in res if e["data"].get("verified")]
    per_shop = collections.Counter(shop_of(e, names) for e in verified)
    rank = sorted(per_shop.values(), reverse=True).index(per_shop[ME]) + 1 if per_shop[ME] else None
    mine = [e for e in res if shop_of(e, names) == ME]
    deals = [e for e in mine if e["data"]["receipt"]]
    ok = [e for e in mine if e["data"].get("verified")]
    stars = [e["data"]["stars"] for e in ok if e["data"]["stars"] is not None]
    over = [round(float(e["data"]["receipt"]["price"])) - RESERVE[e["data"]["receipt"]["item"]] for e in ok
            if e["data"]["receipt"].get("item") in RESERVE]
    star = f"{sum(stars) / len(stars):.2f}" if stars else "-"
    price = (f"{sum(over) / len(over):+.1f} ({sum(o > 0 for o in over)}/{len(over)} above)" if over else "-")
    return (f"visits {len(mine)}, deals {len(deals)}, verified {len(ok)} of {len(verified)} in the market "
            f"({len(ok) / len(verified) * 100 if verified else 0:.1f}%, rank {rank} of {len(per_shop)}), "
            f"stars {star}, price over reserve {price}")


for r in sorted({e["round"] for e in results}):
    res = [e for e in results if e["round"] == r]
    print(f"== Round {r}: {utc(res[0]['ts'])} .. {utc(res[-1]['ts'])} UTC, {len(res)} results")
    print("shop :", shop_stats(res))
    mine = [e for e in res if e["data"]["buyer"] == ME]
    deals = [e for e in mine if e["data"]["receipt"]]
    ok = [e for e in deals if e["data"].get("verified")]
    paid = [(round(float(e["data"]["receipt"]["price"])), orders[e["data"]["order"]]["budget"]) for e in ok
            if e["data"]["order"] in orders]
    spend = f", paid {sum(p for p, b in paid)} of budgets {sum(b for p, b in paid)}" if paid else ""
    top = collections.Counter(shop_of(e, names) for e in mine).most_common(5)
    print(f"buyer: orders {len(mine)}, deals {len(deals)}, verified {len(ok)}{spend}; shops {top}\n")

print("== Round 2 shop by 6-hour block (UTC)")
res2 = [e for e in results if e["round"] == 2]
blocks = collections.defaultdict(list)
for e in res2:
    blocks[(e["ts"] // 21600) * 21600].append(e)
for start, res in sorted(blocks.items()):
    print(f"{utc(start)}  {shop_stats(res)}")
