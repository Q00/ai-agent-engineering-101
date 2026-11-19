"""Runs market_buyer.main() against fake shops (no network, no model) to check the patch 3.6 rules: the cap per
grade (the price where the expected share of the shop's margin kept is still min_share), the haggle going where the
most margin is kept, no offer under the least the unit can have cost, and the grades the accept policy names.

    DATA_DIR=<scratch>/data MARKET_DIR=<scratch>/.market BUYER_LLM=0 python tests/buyer_share.py <scratch>

A fake shop names a grade and an ask, comes down by a step to its floor, and closes when our offer reaches the floor.
List price 20000, min_share 0.75 (11-13; 0.70 before). The SKU is food, so since 11-13 23:xx the caps come from the
food cost ranges (market_buyer.FOOD_COST): A 13362, B 12753, C 12145 (the goods ranges gave A 14894, B 12601, C 10327).
"""
import io
import json
import re
import sys
import time
from contextlib import redirect_stdout
from pathlib import Path

out = Path(sys.argv[1])
(out / "data").mkdir(parents=True, exist_ok=True)
(out / ".market").mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

SKU, LIST = "Gtest0001", 20000
cat = {SKU: {"name": "test milk", "category": "식품>우유", "list_price": LIST, "kind": "food", "shelf_hours": 200}}
(out / ".market" / "catalog.json").write_text(json.dumps(cat))
order = {"order": "o-test", "item": "우유", "category": "식품>우유", "budget": 30000, "acceptable": [SKU],
         "use_within_hours": 48, "kind": "food"}
(out / ".market" / "order.json").write_text(json.dumps(order))

import buyer
import market_buyer as mb

SHOPS, sent = {}, []


def task(state, text, tid, data=None):
    t = {"id": tid, "status": {"state": state, "message": {"parts": [{"text": text}]}}}
    if data:
        t["artifacts"] = [{"parts": [{"data": data}]}]
    return t


def send(url, text, task_id=None):
    name = url.split("//")[1]
    s = SHOPS[name]
    sent.append((name, text))
    nums = [int(n) for n in re.findall(r"\d{4,}", text)]
    if task_id is None:                                   # the opening
        s["now"] = s["ask"]
    elif nums and nums[-1] >= s["floor"]:
        return task("TASK_STATE_COMPLETED", f"Deal at {nums[-1]}.", name,
                    {"price": nums[-1], "item": SKU, "claim": s.get("claim", s["grade"]), "unit": "u-0123456789ab"})
    else:
        s["now"] = max(s["floor"], s["now"] - s["step"])
    grade = f"Grade {s['grade']} unit, 150 h of shelf life left. " if s["grade"] else "150 h of shelf life left. "
    return task("TASK_STATE_INPUT_REQUIRED", f"{grade}The price is {s['now']}.", name)


buyer.find_endpoint = lambda base: (None, base)
buyer.send = send
buyer.walk_away = lambda url, tid, why: sent.append((url.split("//")[1], f"walk away: {why}"))
sys.argv = ["market_buyer.py", "--shops", str(out / ".market/shops.json"), "--order", str(out / ".market/order.json")]


def run(shops):
    SHOPS.clear()
    SHOPS.update(shops)
    (out / ".market" / "shops.json").write_text(json.dumps(
        [{"name": n, "url": f"http://{n}", "offers": [{"sku": SKU, "qty": 1}]} for n in shops]))
    sent.clear()
    mb.DONE.clear()
    mb.STARTED = time.time()
    buf = io.StringIO()
    with redirect_stdout(buf):
        mb.main()
    return mb.past_orders()[-1], json.loads(buf.getvalue().strip().splitlines()[-1])


def check(label, ok, rec, result):
    print(("ok " if ok else "BAD"), label, "| end=%r shop=%s price=%s cap=%s offers=%s accept=%s" % (
        rec.get("end"), rec.get("shop"), rec.get("price"), rec.get("cap"), rec.get("offers"), result.get("accept")))
    return ok


good = True
# 1. food A (11-13): the goods range said an A at 0.74 of the list kept 0.75 of the margin; the food cost (0.55 of the
# list on average) says 0.58. At 0.74 it is now refused; an A that comes down to 0.665 closes and the accept names A alone
rec, res = run({"a": {"grade": "A", "ask": 16000, "floor": 14800, "step": 400}})
good &= check("food A at 0.74 is refused", rec["end"].startswith("ask ") and not res.get("receipt") and rec["cap"] == 13362, rec, res)
rec, res = run({"a": {"grade": "A", "ask": 14500, "floor": 13300, "step": 400}})
good &= check("food A at 0.665 closes", rec["end"] == "deal" and rec["price"] <= 13362 and res["accept"].get("grades") == ["A"]
              and min(rec["offers"]) >= 9000, rec, res)
# 2. food C: at 0.60 of the list it keeps 0.75 at the food cost (0.47 on average), so it closes; at 0.63 it is over the cap
rec, res = run({"c": {"grade": "C", "ask": 13200, "floor": 12000, "step": 400}})
good &= check("food C at 0.60 closes", rec["end"] == "deal" and rec["price"] <= 12145, rec, res)
rec, res = run({"c": {"grade": "C", "ask": 13200, "floor": 12600, "step": 400}})
good &= check("food C at 0.63 is refused", rec["end"].startswith("ask ") and not res.get("receipt") and rec["cap"] == 12145, rec, res)
# 3. a C asking 0.56 and an A asking 0.65: at the food cost the C keeps more of the margin (0.83 against 0.78), so the
# haggle starts there (the goods ranges ranked the A first: 1.00 against 0.61)
rec, res = run({"c": {"grade": "C", "ask": 11200, "floor": 11000, "step": 100},
                "a": {"grade": "A", "ask": 13000, "floor": 12800, "step": 200}})
good &= check("food C before a dearer A", rec["end"] == "deal" and rec["shop"] == "c" and rec["tried"] == 1, rec, res)
# goods keep the goods ranges
good &= check("goods caps unchanged", round(mb.ratio_cap("A", 0.75), 4) == 0.7447 and round(mb.ratio_cap("C", 0.75), 4) == 0.5164
              and mb.share_kept("A", 0.70) == mb.share_kept("A", 0.70, food=False), {}, {})
# 4. a B asking under the least a food B can cost (0.41 of the list): taken at once, no counter-offer
rec, res = run({"b": {"grade": "B", "ask": 7900, "floor": 7000, "step": 300}})
good &= check("B under its least cost is taken at once", rec["end"] == "deal" and rec["price"] == 7900 and rec["offers"] == [7900], rec, res)
# 5. the shop says B, the receipt claims C at a price only A and B are worth
rec, res = run({"liar": {"grade": "B", "claim": "C", "ask": 13000, "floor": 12500, "step": 200}})
good &= check("receipt claiming C at a B price is refused", not res.get("receipt") and "claims C" in str(rec.get("doomed")), rec, res)
# 6. no grade in the message: B's cap, and the accept leaves C out at that price (the kit checks the certificate)
rec, res = run({"mute": {"grade": None, "claim": None, "ask": 13400, "floor": 12400, "step": 400}})
good &= check("no grade named: B's cap, accept A or B", rec["end"] == "deal" and rec["cap"] == 12753 and res["accept"].get("grades") == ["A", "B"], rec, res)
print("ALL OK" if good else "FAILED")
