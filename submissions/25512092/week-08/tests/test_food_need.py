"""Food shelf life the buyer needs (person 11/12 ~16:40), through seller_r3.Seller.execute (the A2A seller's live path)
with real a2a message / task objects, a temp market.db and inventory, the code haggle arm (no LLM call) and live
logs patched out:
  - "I need at least 20 hours of shelf life left": the unit with 30 h is offered, not the one with 10 h (the default
    expiry-first pick); only short units -> "none of that in stock"; no hours stated -> expiry-first as before
  - right before the receipt the unit is checked again: short -> another unit of the same SKU and grade, else no
    receipt (rejected)
  - food_need.json off -> unchanged (the 10 h unit)
    python tests/test_food_need.py"""
import asyncio
import contextlib
import importlib.util
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest import mock

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _isolate import policy, tmp_json  # noqa: E402

policy()
import market_db  # noqa: E402
import dbutil  # noqa: E402
from depts import food_need, open_expiry  # noqa: E402
from a2a.server.events import EventQueue  # noqa: E402
from a2a.types import Message, Part, Role  # noqa: E402

_spec = importlib.util.spec_from_file_location("seller_r3", os.path.join(HERE, "seller_r3.py"))
seller = importlib.util.module_from_spec(_spec)
_argv, sys.argv = sys.argv, ["seller_r3.py"]
_spec.loader.exec_module(seller)
sys.argv = _argv

NOW = time.time()
SKU = "Gf00d0001"


def unit(uid, left_h, grade="A"):
    return {"unit": uid, "sku": SKU, "name": "테스트 김치 10kg", "category": "식품>김치>김치", "list_price": 30000,
            "kind": "food", "cost": 12000, "supplied_at": NOW - 3600, "expires_at": NOW + left_h * 3600, "grade": grade,
            "cert": None}


class Ctx:
    def __init__(self, text, task=None):
        self.message = Message(message_id=f"m{time.time_ns()}", role=Role.ROLE_USER, parts=[Part(text=text)])
        self.current_task = task


async def send(s, text, task=None):
    q = EventQueue()
    ctx = Ctx(text, task)
    await s.execute(ctx, q)
    events = []
    while not q.queue.empty():
        events.append(await q.dequeue_event())
    t = task or next((e for e in events if type(e).__name__ == "Task"), None)
    return t, events


def texts(events):
    out = []
    for e in events:
        st = getattr(e, "status", None)
        msg = getattr(st, "message", None) if st is not None else None
        for p in getattr(msg, "parts", []) if msg is not None else []:
            if getattr(p, "text", None):
                out.append(p.text)
    return " | ".join(out)


@contextlib.contextmanager
def env(units, on=True):
    d = tempfile.mkdtemp()
    with open(os.path.join(d, "inventory.json"), "w", encoding="utf-8") as f:
        json.dump({"round": 3, "units": units}, f)
    db = os.path.join(d, "market.db")
    noop = lambda *a, **k: None
    P = mock.patch.object
    with contextlib.ExitStack() as es:
        for obj, name, val in ((seller, "MARKET_DIR", Path(d)), (market_db, "DB_PATH", db),
                               (food_need, "RULES", tmp_json({"enabled": True} if on else {}, "food_need")),
                               (seller, "haggle_pick", lambda unit=None: "code"),
                               (seller, "price_of", lambda sku: (20000, 50)), (seller.decisions, "record", noop),
                               (open_expiry, "write_state", noop), (seller, "load_stats", lambda: {}),
                               (seller.strategy, "current", lambda *a: ("normal", "normal")),
                               (seller.strategy, "pick", lambda key: ("1.00", {})),
                               (seller.strategy, "warranty_hours", lambda *a: 0)):
            es.enter_context(P(obj, name, val))
        es.enter_context(mock.patch("depts.receipt_exp.claim_arm", lambda u: None))
        es.enter_context(mock.patch("depts.receipt_exp.category_item", lambda *a: None))
        es.enter_context(mock.patch("depts.substitute.on", lambda: False))
        es.enter_context(mock.patch("depts.b_warranty.arm", lambda *a: None))
        with mock.patch.object(dbutil, "RULES", tmp_json({}, "dbs")):
            market_db.connect().close()
        yield d


ASK = f"I want to buy 테스트 김치 10kg ({SKU}). I need at least 20 hours of shelf life left. What is your price?"


def test_choose_and_refuse():
    with env([unit("short", 10), unit("long", 30)]):
        s = seller.Seller()
        t, ev = asyncio.run(send(s, ASK))
        assert s.open[t.id]["unit"]["unit"] == "long" and s.open[t.id]["need_h"] == 20, s.open
    with env([unit("short", 10), unit("short2", 15)]):
        s = seller.Seller()
        t, ev = asyncio.run(send(s, ASK))
        assert t.id not in s.open and "none of that in stock" in texts(ev), texts(ev)
    with env([unit("short", 10), unit("long", 30)]):                       # no hours asked: expiry-first as before
        s = seller.Seller()
        t, ev = asyncio.run(send(s, f"I want to buy 테스트 김치 10kg ({SKU}). What is your price?"))
        assert s.open[t.id]["unit"]["unit"] == "short"
    with env([unit("short", 10), unit("long", 30)], on=False):             # rule off: unchanged
        s = seller.Seller()
        t, ev = asyncio.run(send(s, ASK))
        assert s.open[t.id]["unit"]["unit"] == "short"


def test_receipt_recheck():
    for others, want in (([unit("long2", 40)], "swap"), ([unit("longB", 40, grade="B")], "refuse")):
        with env([unit("long", 30)] + others):
            s = seller.Seller()
            t, ev = asyncio.run(send(s, ASK))
            deal = s.open[t.id]
            ask = deal["ask"]
            deal["unit"] = dict(deal["unit"], expires_at=time.time() + 5 * 3600)   # it aged while haggling
            t, ev = asyncio.run(send(s, f"{ask}", t))
            names = [type(e).__name__ for e in ev]
            if want == "swap":                                            # same SKU and grade with 40 h: receipt
                arts = [e for e in ev if type(e).__name__ == "TaskArtifactUpdateEvent"]
                assert arts, (names, texts(ev))
                assert "long2" in str(arts[0]), arts[0]
            else:                                                         # only a B unit left: no receipt
                assert not any(type(e).__name__ == "TaskArtifactUpdateEvent" for e in ev), names
                assert "less than the 20 h you need" in texts(ev), texts(ev)
                assert t.id not in s.open


if __name__ == "__main__":
    test_choose_and_refuse()
    test_receipt_recheck()
    print("ok")
