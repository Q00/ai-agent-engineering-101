"""Checks that the buyer does not take a receipt the kit is sure to refuse (11-04: 49 of 56 shelf-life refusals came
with claim C or B), on fake shops (no network, no model).

    DATA_DIR=<scratch>/data MARKET_DIR=<scratch>/.market BUYER_LLM=0 python tests/buyer_claim.py <scratch>

Part 1, doomed() on its own: the food order needs 540.5 h of a 720 h shelf life (75%), so a C claim (at most 45%,
324 h) and a B claim (at most 80%, 576 h) differ: C is sure to fail, B may pass. Goods: a claim outside the grades.
Part 2, the flow: cheap signs at once with claim C, ok closes at 12500 with claim A. The buyer must pass on cheap's
receipt and close with ok. (11-14: ok closed at 16000, 0.80 of the list, over every cap since 11-12 (patch 3.6: A
0.745, food A 0.668 since 11-13 23:18), so the flow failed on the cap; the prices now sit under the cap of a unit that names no grade, B 0.638.)
"""
import io
import json
import sys
import time
from contextlib import redirect_stdout
from pathlib import Path

out = Path(sys.argv[1])
(out / "data").mkdir(parents=True, exist_ok=True)
(out / ".market").mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

SKU = "Gtest0002"
cat = {SKU: {"name": "test kimchi", "category": "식품>김치", "list_price": 20000, "kind": "food", "shelf_hours": 720}}
(out / ".market" / "catalog.json").write_text(json.dumps(cat))
shops = [{"name": n, "url": f"http://{n}", "offers": [{"sku": SKU, "qty": 1}]} for n in ("cheap", "ok")]
(out / ".market" / "shops.json").write_text(json.dumps(shops))
order = {"order": "o-claim", "item": "김치", "category": "식품>김치", "budget": 30000, "acceptable": [SKU],
         "use_within_hours": 540, "kind": "food"}
(out / ".market" / "order.json").write_text(json.dumps(order))

import buyer
import market_buyer as mb

food = cat[SKU]
acc = {"cert": True, "min_hours_left": 540.5}
goods = {"kind": "goods"}
cases = [
    ("food C", mb.doomed({"claim": "C"}, acc, food), True),
    ("food B", mb.doomed({"claim": "B"}, acc, food), False),
    ("food A", mb.doomed({"claim": "A"}, acc, food), False),
    ("food no claim", mb.doomed({}, acc, food), False),
    ("food cert hidden", mb.doomed({"claim": "A", "cert": False}, acc, food), True),
    ("food B, short order", mb.doomed({"claim": "B"}, {"cert": True, "min_hours_left": 300}, food), False),
    ("goods C, pay A/B", mb.doomed({"claim": "C"}, {"grades": ["A", "B"]}, goods), True),
    ("goods B, pay A/B", mb.doomed({"claim": "B"}, {"grades": ["A", "B"]}, goods), False),
    ("goods C, pay any", mb.doomed({"claim": "C"}, {}, goods), False),
]
bad = 0
for name, why, want in cases:
    ok = bool(why) == want
    bad += not ok
    print(f"{'ok ' if ok else 'BAD'} {name}: {why}")

sent = []


def task(state, text, tid, data=None):
    t = {"id": tid, "status": {"state": state, "message": {"parts": [{"text": text}]}}}
    if data:
        t["artifacts"] = [{"parts": [{"data": data}]}]
    return t


def send(url, text, task_id=None):
    shop = url.split("//")[1]
    sent.append((shop, text))
    if shop == "cheap":
        return task("TASK_STATE_COMPLETED", "Deal at 9000.", shop, {"price": 9000, "item": SKU, "claim": "C"})
    n = sum(1 for s, _ in sent if s == "ok")
    if n >= 3:
        return task("TASK_STATE_COMPLETED", "Deal at 12500.", shop, {"price": 12500, "item": SKU, "claim": "A"})
    return task("TASK_STATE_INPUT_REQUIRED", "The price is 13500.", shop)


buyer.find_endpoint = lambda base: (None, base)
buyer.send = send
buyer.walk_away = lambda url, tid, why: sent.append((url.split("//")[1], f"walk away: {why}"))
mb.QUOTES = 3
sys.argv = ["market_buyer.py", "--shops", str(out / ".market/shops.json"), "--order", str(out / ".market/order.json")]
mb.DONE.clear()
mb.STARTED = time.time()
buf = io.StringIO()
with redirect_stdout(buf):
    mb.main()
result = json.loads(buf.getvalue().strip().splitlines()[-1])
rec = mb.past_orders()[-1]
got = (result.get("receipt") or {})
flow_ok = got.get("claim") == "A" and rec.get("doomed") and rec["end"] == "deal"
bad += not flow_ok
print(f"{'ok ' if flow_ok else 'BAD'} flow: end={rec['end']!r} doomed={rec.get('doomed')} receipt={got}")
print("   shops asked:", [s for s, t in sent if not t.startswith("walk")])
sys.exit(1 if bad else 0)
