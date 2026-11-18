"""Hidden demand, estimated from what reaches us (Round 3).

The market hides what sells: Round 3 `order` entries on the ledger carry no item or budget, and demand.json has no
weights. The instructor said (11/3) that the orders still follow a popularity ranking the market keeps hashed. Two
signals reach our own agents and nobody else's, and both are samples of that hidden demand:
  - every order our buyer gets (order.json): its category, and its `acceptable` SKUs, which are the category's SKUs
    priced 0.5-1.5x a hidden reference price. The middle of that band, (cheapest + dearest) / 2, estimates the
    reference, and the SKU nearest it is the product the order was drawn around. Counting those over two weeks
    ranks categories and products by how often orders come for them, settled or not;
  - every request to our shop (the buyer names a SKU or a category): what buyers came to us for, including what we
    did not have (a missed sale is demand we could not see in any settle).
The ledger's settles, the third signal, stay in restock_r3.py. Each event is one line in logs/demand_r3.jsonl,
so the estimate survives restarts and the report can show it.

    python demand_r3.py      # prints the top categories, products and reference prices so far
"""
import json
import math
import os
import re
import time
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOG = Path(os.environ.get("DEMAND_LOG") or HERE / "logs" / "demand_r3.jsonl")
SKU = re.compile(r"\bG[0-9a-f]{8}\b")
HALF_LIFE_H = 48      # demand drifts slowly (favourite categories, a fixed popularity ranking): a long memory


def record(**event):
    try:
        LOG.parent.mkdir(exist_ok=True)
        with LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(dict(event, t=round(time.time(), 1)), ensure_ascii=False) + "\n")
    except OSError:
        pass


def record_order(order, catalog, shops=None):
    """One order our buyer received: category, the estimated reference price and the SKU nearest it, and how many
    units of the category the listed shops hold (how crowded the category is)."""
    acc = [s for s in order.get("acceptable") or [] if s in catalog]
    prices = sorted(catalog[s]["list_price"] for s in acc)
    ref = (prices[0] + prices[-1]) / 2 if prices else None
    near = min(acc, key=lambda s: abs(catalog[s]["list_price"] - ref)) if acc else None
    held = sum(int(o.get("qty") or 0) for sh in shops or [] for o in sh.get("offers") or [])
    record(event="order", order=order.get("order"), category=order.get("category"), kind=order.get("kind"),
           ref=round(ref) if ref else None, near=near, n_acceptable=len(acc), shops=len(shops or []), held=held)


def record_ask(text, catalog, have, warranty=False):
    """One request to our shop: the SKU it named (if any), whether we had a unit for it, and whether the buyer asked
    for a warranty (the meta-harness weighs that against the refund risk)."""
    named = [s for s in SKU.findall(text or "") if s in catalog][:1]
    cat = catalog[named[0]]["category"] if named else None
    kind = catalog[named[0]].get("kind") if named else None
    record(event="ask", sku=named[0] if named else None, category=cat, kind=kind, have=bool(have), warranty=bool(warranty))


def warranty_demand(hours=6, now=None):
    """Of the requests for goods to our shop in the last `hours`, how many asked for a warranty."""
    now = now or time.time()
    n = w = 0
    try:
        lines = LOG.read_text(encoding="utf-8").splitlines()[-20000:]
    except OSError:
        return {}
    for line in lines:
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if e.get("event") == "ask" and e.get("kind") == "goods" and now - e.get("t", 0) < hours * 3600:
            n += 1
            w += bool(e.get("warranty"))
    return {"goods_asks": n, "asked_warranty": w, "share": round(w / n, 2) if n else None}


def signals(now=None, half_life_h=HALF_LIFE_H):
    """(category weight, SKU weight, reference prices per category) from the log, recent events weighing more."""
    now = now or time.time()
    cat_w, sku_w, refs = Counter(), Counter(), defaultdict(list)
    try:
        lines = LOG.read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    for line in lines:
        try:
            e = json.loads(line)
        except ValueError:
            continue
        w = 0.5 ** (max(0.0, now - e.get("t", now)) / 3600 / half_life_h)
        if e.get("event") == "order":
            if e.get("category"):
                cat_w[e["category"]] += w
                if e.get("ref"):
                    refs[e["category"]].append(e["ref"])
            if e.get("near"):
                sku_w[e["near"]] += w
        elif e.get("event") == "ask":
            if e.get("category"):
                cat_w[e["category"]] += w * (2 if not e.get("have") else 1)    # a miss is demand we lost
            if e.get("sku"):
                sku_w[e["sku"]] += w * (2 if not e.get("have") else 1)
    ref = {c: math.exp(sum(math.log(r) for r in v) / len(v)) for c, v in refs.items() if v}
    return cat_w, sku_w, ref


if __name__ == "__main__":
    cat_w, sku_w, ref = signals()
    print("categories:", [(c.split(">")[-1], round(w, 1)) for c, w in cat_w.most_common(10)])
    print("products:", [(s, round(w, 1)) for s, w in sku_w.most_common(10)])
    print("reference prices:", {c.split(">")[-1]: round(r) for c, r in list(ref.items())[:10]})
