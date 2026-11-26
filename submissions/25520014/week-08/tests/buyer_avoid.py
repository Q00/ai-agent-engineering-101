"""Checks market_buyer.avoid_list() (11-13): a refusal about one SKU sends that (shop, SKU) last, and the whole shop
only when it was asked AVOID_MIN_ASKED times or more and refused AVOID_REFUSED of them. No network, no model.

    DATA_DIR=<scratch>/data MARKET_DIR=<scratch>/.market BUYER_LLM=0 python tests/buyer_avoid.py <scratch>
"""
import json
import sys
import time
from pathlib import Path

out = Path(sys.argv[1])
(out / "data").mkdir(parents=True, exist_ok=True)
(out / ".market").mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import market_buyer as mb

now = time.time()
NO = "TASK_STATE_REJECTED: Sorry, none of that in stock."
recs, paid, n = [], [], 0


def order(shop, sku, refused=False, pay=False, age_h=1.0):
    global n
    n += 1
    r = {"ts": now - age_h * 3600, "order": f"o-{n}", "quotes": {shop: None if refused else 10000},
         "quote_skus": {shop: sku}, "end": "ask 10000 above our cap 9000"}
    if refused:
        r["shop_said"] = {shop: NO}
    if pay:
        r.update(shop=shop, sku=sku, end="deal")
        paid.append({"order": r["order"], "paid": True})
    recs.append(r)


# big: asked 20 times, refused 5 times for 5 different SKUs, 1 paid (5%): the old rule sent it last
for i in range(20):
    order("big", f"G{i:03d}", refused=i < 5, pay=i == 19)
# stock: asked 10 times, refused 3: twice for GX, once for GY
for i in range(10):
    order("stock", "GX" if i < 2 else "GY" if i == 2 else f"S{i}", refused=i < 3)
# dead: asked 8 times, refused 6, nothing paid
for i in range(8):
    order("dead", "GD", refused=i < 6)
# small: asked 4 times, refused 3 (twice for GS): too few questions for the whole shop
for i in range(4):
    order("small", "GS" if i < 2 else f"T{i}", refused=i < 3)
# old: 3 refusals of one SKU, all older than the window
for i in range(3):
    order("old", "GO", refused=True, age_h=mb.AVOID_H + 1)

mb.LOG.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
(mb.MARKET_DIR / "payments.jsonl").write_text("\n".join(json.dumps(p) for p in paid) + "\n")

shops, pairs = mb.avoid_list(now)
good = True
for label, ok in (("only the shop that refuses most questions goes last", set(shops) == {"dead"}),
                  ("refused SKUs go last as pairs", pairs == {("stock", "GX"), ("small", "GS")}),
                  ("the file names both", json.loads((mb.policy.DATA / "buyer_avoid.json").read_text())["pairs"] == ["small GS", "stock GX"])):
    print("ok " if ok else "BAD", label, "| shops", shops, "pairs", sorted(pairs))
    good &= ok
mb.AVOID_MIN_ASKED, mb.AVOID_REFUSED, mb.SKU_REFUSALS = 0, 0, 10 ** 9      # the rule until 11-13
shops, pairs = mb.avoid_list(now)
ok = set(shops) == {"big", "stock", "dead", "small"} and not pairs
print("ok " if ok else "BAD", "the old rule by the constants | shops", shops, "pairs", sorted(pairs))
good &= ok
print("ALL OK" if good else "FAILED")
