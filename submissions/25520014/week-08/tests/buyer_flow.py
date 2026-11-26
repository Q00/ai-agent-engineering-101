"""Runs market_buyer.main() against fake shops (no network, no model) to check the order flow.

    DATA_DIR=<scratch>/data MARKET_DIR=<scratch>/.market BUYER_LLM=0 python tests/buyer_flow.py <scratch>

Shops: rej1..rej3 turn us down at the first question, short says its food lasts 10 hours (we need 48), ok asks a
price and closes at our second offer. The buyer must ask the next three shops after the first three refuse, walk away
from the short one, and close with ok. The opening must state the hours. Then the avoid list must name rej1..rej3
once they have refused three times.
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

SKU = "Gtest0001"
cat = {SKU: {"name": "test milk", "category": "식품>우유", "list_price": 20000, "kind": "food", "shelf_hours": 200}}
(out / ".market" / "catalog.json").write_text(json.dumps(cat))
names = ["rej1", "rej2", "rej3", "short", "ok"]
shops = [{"name": n, "url": f"http://{n}", "offers": [{"sku": SKU, "qty": 1}]} for n in names]
(out / ".market" / "shops.json").write_text(json.dumps(shops))
order = {"order": "o-test", "item": "우유", "category": "식품>우유", "budget": 30000, "acceptable": [SKU],
         "use_within_hours": 48, "kind": "food"}
(out / ".market" / "order.json").write_text(json.dumps(order))

import buyer
import market_buyer as mb

sent = []


def task(state, text, tid, data=None):
    t = {"id": tid, "status": {"state": state, "message": {"parts": [{"text": text}]}}}
    if data:
        t["artifacts"] = [{"parts": [{"data": data}]}]
    return t


def send(url, text, task_id=None):
    shop = url.split("//")[1]
    sent.append((shop, text))
    if shop.startswith("rej"):
        return task("TASK_STATE_REJECTED", "Sorry, we do not sell to you.", shop)
    if shop == "short":
        return task("TASK_STATE_INPUT_REQUIRED", "This unit keeps 10 hours. The price is 15000.", shop)
    n = sum(1 for s, _ in sent if s == "ok")
    if n >= 3:
        return task("TASK_STATE_COMPLETED", "Deal at 14000.", shop, {"price": 14000, "item": SKU})
    return task("TASK_STATE_INPUT_REQUIRED", "The price is 16000.", shop)


buyer.find_endpoint = lambda base: (None, base)
buyer.send = send
buyer.walk_away = lambda url, tid, why: sent.append((url.split("//")[1], f"walk away: {why}"))
mb.QUOTES = 3
sys.argv = ["market_buyer.py", "--shops", str(out / ".market/shops.json"), "--order", str(out / ".market/order.json")]
for i in range(4):
    sent.clear()
    mb.DONE.clear()
    mb.STARTED = time.time()
    buf = io.StringIO()
    with redirect_stdout(buf):
        mb.main()
    result = json.loads(buf.getvalue().strip().splitlines()[-1])
    rec = mb.past_orders()[-1]
    print(f"run {i + 1}: end={rec['end']!r} rounds={rec.get('rounds_of_quotes')} avoided={rec.get('avoided')} "
          f"too_short={rec.get('too_short')} receipt={bool(result.get('receipt'))} accept={result.get('accept')}")
    print("   shops asked:", [s for s, t in sent if not t.startswith("walk")])
    print("   opening:", next(t for s, t in sent if s == "ok"))
    print("   avoid:", json.loads((out / "data" / "buyer_avoid.json").read_text())["refusals"])
