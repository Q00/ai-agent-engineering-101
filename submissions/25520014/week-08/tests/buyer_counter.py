"""Runs market_buyer.main() against fake goods shops (no network, no model) to check the counter at the cap
(COUNTER_SHOPS, 11-14).

    DATA_DIR=<scratch>/data MARKET_DIR=<scratch>/.market BUYER_LLM=0 python tests/buyer_counter.py <scratch>

Every shop asks over our cap. Case 0 finds the cap (no counter shop: the order quits as before).
1 lee-sungsoo asks 1.04 x cap and takes our offer at the cap: a deal at the cap, one offer, the others left.
2 lee-sungsoo refuses the offer (task rejected): no deal, the refusal is in rec["counter"] and not in shop_said, so
  turned_down() does not count it.
3 lee-sungsoo answers with a price still over the cap: no deal, the talk is left after one offer.
4 lee-sungsoo asks 1.20 x cap (over COUNTER_X): quit with no offer.
5 COUNTER_SHOPS = (): quit with no offer.
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

SKU = "Gtest0003"
LIST = 400000
cat = {SKU: {"name": "test sofa", "category": "가구>소파", "list_price": LIST, "kind": "goods"}}
(out / ".market" / "catalog.json").write_text(json.dumps(cat))
order = {"order": "o-test", "item": "소파", "category": "가구>소파", "budget": 600000, "acceptable": [SKU],
         "kind": "goods"}
(out / ".market" / "order.json").write_text(json.dumps(order))

import buyer
import market_buyer as mb

sent = []
ASK = {"high1": 390000, "high2": 380000, "lee-sungsoo": 385000}
MODE = "take"           # lee-sungsoo's answer to our offer: take / reject / hold


def task(state, text, tid, data=None):
    t = {"id": tid, "status": {"state": state, "message": {"parts": [{"text": text}]}}}
    if data:
        t["artifacts"] = [{"parts": [{"data": data}]}]
    return t


def send(url, text, task_id=None):
    shop = url.split("//")[1]
    sent.append((shop, text))
    n = sum(1 for s, _ in sent if s == shop)
    if shop == "lee-sungsoo" and n >= 2:
        offer = int(buyer.last_number(text))
        if MODE == "take":
            return task("TASK_STATE_COMPLETED", f"Deal at {offer}.", shop,
                        {"price": offer, "item": SKU, "grade": "A", "warranty_hours": 6})
        if MODE == "reject":
            return task("TASK_STATE_REJECTED", "No deal.", shop)
        return task("TASK_STATE_INPUT_REQUIRED", f"Grade A, 6 h warranty. Best I can do is {ASK[shop] - 1000}.", shop)
    return task("TASK_STATE_INPUT_REQUIRED", f"Grade A, 6 h warranty. The price is {ASK[shop]}.", shop)


buyer.find_endpoint = lambda base: (None, base)
buyer.send = send
buyer.walk_away = lambda url, tid, why: sent.append((url.split("//")[1], f"walk away: {why}"))
mb.QUOTES = 3
mb.QUIT_OVER_CAP = True
sys.argv = ["market_buyer.py", "--shops", str(out / ".market/shops.json"), "--order", str(out / ".market/order.json")]


def run(label, names):
    shops = [{"name": n, "url": f"http://{n}", "offers": [{"sku": SKU, "qty": 1}]} for n in names]
    (out / ".market" / "shops.json").write_text(json.dumps(shops))
    sent.clear()
    mb.DONE.clear()
    mb.STARTED = time.time()
    buf = io.StringIO()
    with redirect_stdout(buf):
        mb.main()
    time.sleep(0.2)                              # walk_away threads
    result = json.loads(buf.getvalue().strip().splitlines()[-1])
    rec = mb.past_orders()[-1]
    msgs = {n: sum(1 for s, t in sent if s == n and not t.startswith("walk")) for n in names}
    left = sorted(s for s, t in sent if t.startswith("walk"))
    print(f"{label}: end={rec['end']!r} cap={rec.get('cap')} offers={rec['offers']} counter={rec.get('counter')} "
          f"receipt={bool(result.get('receipt'))}")
    print(f"   messages per shop {msgs}, walked away from {left}")
    return rec, result, msgs, left


rec, *_ = run("0 find the cap", ["high1", "high2"])
cap = rec["cap"]
assert rec.get("quit_over_cap") and not rec["offers"], rec
NAMES = ["high1", "high2", "lee-sungsoo"]
ASK.update(high1=int(cap * 1.10), high2=int(cap * 1.12), **{"lee-sungsoo": int(cap * 1.04)})

MODE = "take"
rec, result, msgs, left = run("1 takes the counter", NAMES)
assert rec["end"] == "deal" and result.get("receipt") and rec["offers"] == [cap], rec
assert msgs == {"high1": 1, "high2": 1, "lee-sungsoo": 2} and left == ["high1", "high2"], (msgs, left)

MODE = "reject"
rec, result, msgs, left = run("2 refuses the counter", NAMES)
assert not result.get("receipt") and rec["offers"] == [cap] and rec["counter"].get("said") == "No deal.", rec
assert "lee-sungsoo" not in (rec.get("shop_said") or {}) and not mb.turned_down(rec, "lee-sungsoo"), rec

MODE = "hold"
rec, result, msgs, left = run("3 holds over the cap", NAMES)
assert not result.get("receipt") and rec["offers"] == [cap] and rec["end"].startswith("counter"), rec
assert msgs["lee-sungsoo"] == 2 and "lee-sungsoo" in left, (msgs, left)

ASK["lee-sungsoo"] = int(cap * 1.20)
rec, result, msgs, left = run("4 too far over the cap", NAMES)
assert rec.get("quit_over_cap") and not rec["offers"] and not rec.get("counter"), rec

ASK["lee-sungsoo"] = int(cap * 1.04)
mb.COUNTER_SHOPS = ()
rec, result, msgs, left = run("5 switch off", NAMES)
assert rec.get("quit_over_cap") and not rec["offers"] and not rec.get("counter"), rec
print("ok")
