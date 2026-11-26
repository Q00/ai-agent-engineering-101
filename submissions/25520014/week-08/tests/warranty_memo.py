"""Checks warranty_memo (11-13): the template of a first message, the decision from paid and refused receipts, and
seller.option taking it (0 drops the warranty, hours give one with its refund in the floor). No network, no model.

    DATA_DIR=<scratch>/data MARKET_DIR=<scratch>/.market python tests/warranty_memo.py
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import seller
import warranty_memo as wm

good = True


def check(ok, label, got=None):
    global good
    print("ok " if ok else "BAD", label, "" if got is None else f"| {got}")
    good &= bool(ok)


t = wm.template("I want to buy [에이스침대] BMA 1139 (G8095ddd6), grade A or B, a 6 hours warranty if you offer one",
                "[에이스침대] BMA 1139")
check(t == "I want to buy <N> (<S>), grade A or B, a # hours warranty if you offer one", "template", t)

# synthetic memo: template X sent by buyer b1; b1 pays A without a warranty, refuses B without one but pays it with
# one, refuses C either way
m = wm.Memo()
now = time.time()
m.goods = {"G1"}
m.names = {}
m.task_tpl = {f"t{i}": (now - 3600, "X") for i in range(10)}
res = []
for i in range(10):
    res.append((now - 3600, f"oa{i}", "b1", "A", False, f"t{i}"))
    res.append((now - 3600, f"ob{i}", "b1", "B", False, None))
    res.append((now - 3600, f"obw{i}", "b1", "B", True, None))
    res.append((now - 3600, f"oc{i}", "b1", "C", False, None))
    res.append((now - 3600, f"ocw{i}", "b1", "C", True, None))
m.results.extend(res)
m.settled = {f"oa{i}": now for i in range(10)} | {f"obw{i}": now for i in range(9)}
m._read_ledger = lambda now: None
m._read_turns = lambda now: None
m.build(now)
got = (m.decide("X", "A"), m.decide("X", "B"), m.decide("X", "C"), m.decide("Y", "A"))
check(got == (0, 72, None, None), "decisions A 0, B 72, C unknown (refused both ways), unknown template None", got)

# seller.option with the memo
p = dict(seller.policy.load())
p.update(warranty_A=True, warranty_B=False, warranty_C=True)
base = {"kind": "goods", "sku": "G1", "name": "n", "cost": 500000, "list_price": 1000000, "unit": "u1"}
a = seller.option(dict(base, grade="A"), p, 0.0, False, False, None)
a0 = seller.option(dict(base, grade="A"), p, 0.0, False, False, 0)
check(a["warranty"] == 72 and a0["warranty"] == 0, "A: 72 h by the rules, none by the memo",
      (a["warranty"], a0["warranty"]))
b = seller.option(dict(base, grade="B"), p, 0.0, False, False, None)
b72 = seller.option(dict(base, grade="B"), p, 0.0, False, False, 72)
check(b["warranty"] == 0 and b72["warranty"] == 72 and not b72["unit"].get("_no_premium"),
      "B: none by the rules, 72 h by the memo with its refund in the floor", (b["floor"], b72["floor"]))
fl_nop = seller.floor_price(dict(base, grade="B", _no_premium=True), p, 72, 700000)
fl_p = seller.floor_price(dict(base, grade="B"), p, 72, 700000)
check(fl_p > fl_nop, "the premium raises the B floor", (fl_nop, fl_p))
c = seller.option(dict(base, grade="C"), p, 0.0, False, False, 72)
check(c["warranty"] == 72, "C: 72 h by the memo though the buyer did not ask", c["warranty"])
f = seller.option(dict(base, kind="food", grade="A"), p, 0.0, False, False, 72)
check(f["warranty"] == 0, "food never gets one", f["warranty"])
print("ALL OK" if good else "FAILED")
