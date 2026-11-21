"""Runs market_buyer.main() against fake goods shops (no network, no model) to check QUIT_OVER_CAP.

    DATA_DIR=<scratch>/data MARKET_DIR=<scratch>/.market BUYER_LLM=0 python tests/buyer_quit.py <scratch>

Case 1: all three shops quote over our cap. The order must end after the quotes with no offer sent, every shop left.
Case 2: `high1`, `high2` quote over the cap, `fair` under it and closes at our second offer. Only `fair` is haggled
with and the order ends in a deal.
Case 3: `fair` quotes under the cap but never closes, `high1` and `high2` over it. After `fair` fails the order ends:
the two shops over the cap are not opened again.
Case 4: QUIT_OVER_CAP off, case 1's shops: the buyer haggles as before (offers are sent).
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
LIST = 400000
cat = {SKU: {"name": "test sofa", "category": "가구>소파", "list_price": LIST, "kind": "goods"}}
(out / ".market" / "catalog.json").write_text(json.dumps(cat))
order = {"order": "o-test", "item": "소파", "category": "가구>소파", "budget": 600000, "acceptable": [SKU],
         "kind": "goods"}
(out / ".market" / "order.json").write_text(json.dumps(order))

import buyer
import market_buyer as mb

sent = []
ASK = {"high1": 330000, "high2": 350000, "high3": 340000, "fair": 250000}
CLOSES = True


def task(state, text, tid, data=None):
    t = {"id": tid, "status": {"state": state, "message": {"parts": [{"text": text}]}}}
    if data:
        t["artifacts"] = [{"parts": [{"data": data}]}]
    return t


def send(url, text, task_id=None):
    shop = url.split("//")[1]
    sent.append((shop, text))
    n = sum(1 for s, _ in sent if s == shop)
    if shop == "fair" and CLOSES and n >= 3:
        return task("TASK_STATE_COMPLETED", "Deal at 240000.", shop,
                    {"price": 240000, "item": SKU, "grade": "A", "warranty_hours": 6})
    return task("TASK_STATE_INPUT_REQUIRED", f"Grade A, 6 h warranty. The price is {ASK[shop]}.", shop)


buyer.find_endpoint = lambda base: (None, base)
buyer.send = send
buyer.walk_away = lambda url, tid, why: sent.append((url.split("//")[1], f"walk away: {why}"))
mb.QUOTES = 3
sys.argv = ["market_buyer.py", "--shops", str(out / ".market/shops.json"), "--order", str(out / ".market/order.json")]


def run(label, names, quit_on=True, closes=True):
    global CLOSES
    CLOSES = closes
    mb.QUIT_OVER_CAP = quit_on
    shops = [{"name": n, "url": f"http://{n}", "offers": [{"sku": SKU, "qty": 1}]} for n in names]
    (out / ".market" / "shops.json").write_text(json.dumps(shops))
    sent.clear()
    mb.DONE.clear()
    mb.STARTED = time.time()
    buf = io.StringIO()
    with redirect_stdout(buf):
        mb.main()
    result = json.loads(buf.getvalue().strip().splitlines()[-1])
    rec = mb.past_orders()[-1]
    msgs = {n: sum(1 for s, t in sent if s == n and not t.startswith("walk")) for n in names}
    left = sorted(s for s, t in sent if t.startswith("walk"))
    print(f"{label}: end={rec['end']!r} cap={rec.get('cap')} tried={rec.get('tried')} offers={rec['offers']} "
          f"receipt={bool(result.get('receipt'))} secs={rec['secs']}")
    print(f"   messages per shop {msgs}, walked away from {left}")
    print(f"   shop_ends {rec.get('shop_ends')}")
    return rec, result, msgs, left


rec, result, msgs, left = run("1 all over the cap", ["high1", "high2", "high3"])
assert rec.get("quit_over_cap") and not rec["offers"] and not result.get("receipt"), rec
assert set(msgs.values()) == {1} and left == ["high1", "high2", "high3"], (msgs, left)
assert rec["end"].startswith("ask ") and "above our cap" in rec["end"], rec["end"]

rec, result, msgs, left = run("2 one under the cap", ["high1", "high2", "fair"])
assert rec["end"] == "deal" and result.get("receipt") and msgs["high1"] == msgs["high2"] == 1, (rec["end"], msgs)

rec, result, msgs, left = run("3 the fair shop never closes", ["high1", "high2", "fair"], closes=False)
assert not result.get("receipt") and msgs["high1"] == msgs["high2"] == 1, (rec["end"], msgs)
assert sum("not haggled" in e for e in rec["shop_ends"]) == 2, rec["shop_ends"]

rec, result, msgs, left = run("4 switch off", ["high1", "high2", "high3"], quit_on=False)
assert rec["offers"] and not rec.get("quit_over_cap"), rec
print("ok")
