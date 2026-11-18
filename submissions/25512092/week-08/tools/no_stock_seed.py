"""Seed logs/no_stock.jsonl from the kit log of the last hour (person's decision 11/09 ~21:30, depts/shop_order.py):
every shop attempt that ended with a no-stock reply -> (shop, SKU). Run once at the switch-on.
    python tools/no_stock_seed.py [hours]"""
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "tools"))
os.chdir(HERE)
import order_speed2 as o  # noqa: E402
import noreply_soft as nr  # noqa: E402
from depts import shop_order  # noqa: E402

PRICE = re.compile(r"^\[price\] (G[0-9a-f]+) ")


def events(t0, t1):
    names = nr.url_names()
    out = []
    for s in o.segments(t0, t1):
        shop, sku, last_seller = None, None, ""
        for v in s["ev"]:
            t, x = v[0], str(v[-1])
            m = nr.CHOOSE.match(x) or nr.NEXT.match(x) or nr.SKIPPED.match(x)
            if m:
                shop = m[2].strip() if m.re is nr.SKIPPED else (m[2] if m.re is nr.CHOOSE else names.get(m[1].rstrip("/"), m[1]))
                continue
            m = PRICE.match(x)
            if m:
                sku = m[1]
            elif x.startswith("seller ["):
                last_seller = x
            elif x.startswith("ENDED without deal") and o.STOCK.search(last_seller) and shop and sku:
                out.append((t, shop, sku))
    return out


if __name__ == "__main__":
    h = float(sys.argv[1]) if len(sys.argv) > 1 else 1.0
    now = time.time()
    ev = events(now - h * 3600, now)
    for t, shop, sku in ev:
        shop_order.note_no_stock(shop, sku, now=t)
    print(f"{len(ev)} no-stock replies in the last {h} h written to {shop_order.LOG}")
