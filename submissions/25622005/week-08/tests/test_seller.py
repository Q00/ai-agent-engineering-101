"""seller.py in process (A2A over httpx.ASGITransport, no network, no model): fact line, receipt, promises."""
import asyncio
import base64
import collections
import contextlib
import io
import json
import logging
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path

os.environ.setdefault("R3_HOME", tempfile.mkdtemp(prefix="r3-seller-"))
W = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(W))

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

import policy
import memory.decisions
import memory.store
import seller

H = 3600
# ASGITransport plus one asyncio.run per conversation makes the SDK warn about its dispatcher; under uvicorn it
# does not (test_real_server_prints_nothing).
logging.getLogger("a2a.server.events.event_queue_v2").setLevel(logging.ERROR)


def verify(pub_b64, receipt):
    """The kit's verify_seller (kit L165), replicated so the test does not depend on the kit copy being importable."""
    try:
        import market_kit
        return market_kit.verify_seller(pub_b64, receipt)
    except Exception:
        pass
    body = {k: receipt.get(k) for k in ("shop", "item", "price", "task")}
    data = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    try:
        Ed25519PublicKey.from_public_bytes(base64.b64decode(pub_b64)).verify(base64.b64decode(receipt["sig_seller"]), data)
        return True
    except (InvalidSignature, KeyError, ValueError):
        return False


class SellerA2A(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="r3-seller-"))
        self.addCleanup(shutil.rmtree, self.home, True)
        (self.home / "strategy").mkdir()
        shutil.copy(W / "strategy" / "SKILL.md", self.home / "strategy" / "SKILL.md")
        logs = self.home / "logs"
        patches = [(policy, "SKILL", self.home / "strategy" / "SKILL.md"),
                   (policy, "LASTGOOD", self.home / "memory" / "policy.lastgood.json"),
                   (memory.store, "LOGS", logs), (memory.decisions, "LOGS", logs), (seller, "LOGS", logs),
                   (seller, "_query", lambda: None)]
        for mod, name, value in patches:
            old = getattr(mod, name)
            setattr(mod, name, value)
            self.addCleanup(setattr, mod, name, old)
        policy._cache.clear()
        self.addCleanup(policy._cache.clear)

        key = Ed25519PrivateKey.generate()
        self.key_path = self.home / "shop_key.pem"
        self.key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                    serialization.NoEncryption()))
        self.pub = base64.b64encode(key.public_key().public_bytes(serialization.Encoding.Raw,
                                                                  serialization.PublicFormat.Raw)).decode()
        self.md = self.home / ".market"
        self.md.mkdir()
        self.now = 1794000000.0                       # about 257 h before the end of Round 3
        self.write_inventory([self.goods("u-1")])
        (self.md / "catalog.json").write_text(json.dumps({
            "G000f6cda": {"name": "Curtain", "category": "a>b>커튼", "list_price": 100000, "kind": "goods"},
            "G0011e806": {"name": "Milk", "category": "식품>우유/두유", "list_price": 20000, "kind": "food",
                          "shelf_hours": 96}}), encoding="utf-8")
        self.app = seller.build(9301, self.md, self.key_path, clock=lambda: self.now)

    def goods(self, unit, grade="A", cost=50000):
        return {"unit": unit, "sku": "G000f6cda", "name": "Curtain", "category": "a>b>커튼", "list_price": 100000,
                "kind": "goods", "cost": cost, "grade": grade, "supplied_at": self.now - H, "expires_at": None,
                "cert": {"cert": {"unit": unit, "sku": "G000f6cda", "grade": grade}, "sig": "market-sig"}}

    def milk(self, unit, hours):
        return {"unit": unit, "sku": "G0011e806", "name": "Milk", "category": "식품>우유/두유", "list_price": 20000,
                "kind": "food", "cost": 9000, "grade": "B", "supplied_at": self.now - H,
                "expires_at": self.now + hours * H, "cert": {"cert": {"unit": unit, "sku": "G0011e806"}, "sig": "s"}}

    def write_inventory(self, units, rnd=3):
        (self.md / "inventory.json").write_text(json.dumps({"round": rnd, "balance": 5000000, "units": units}),
                                                encoding="utf-8")

    def talk(self, *steps):
        """steps: (text, task key). Returns the raw JSON-RPC results in order."""
        async def run():
            out, tasks = [], {}
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://shop") as c:
                for text, key in steps:
                    msg = {"role": "ROLE_USER", "messageId": os.urandom(6).hex(), "parts": [{"text": text}]}
                    if key in tasks:
                        msg["taskId"] = tasks[key]
                    t0 = time.monotonic()
                    r = await c.post("/", json={"jsonrpc": "2.0", "id": "1", "method": "SendMessage",
                                                "params": {"message": msg}}, headers={"A2A-Version": "1.0"})
                    res = r.json()["result"]["task"]
                    tasks[key] = res["id"]
                    out.append((res, time.monotonic() - t0, r.content))
            return out
        return asyncio.run(run())

    @staticmethod
    def said(task):
        return " ".join(p["text"] for p in task["status"]["message"]["parts"] if "text" in p)

    @staticmethod
    def data(task):
        return next(p["data"] for p in task["status"]["message"]["parts"] if "data" in p)

    @staticmethod
    def receipt(task):
        r = dict(task["artifacts"][0]["parts"][0]["data"])
        if isinstance(r.get("price"), float) and r["price"].is_integer():
            r["price"] = int(r["price"])              # what the kit does (A2A data parts carry doubles)
        return r

    def rows(self):
        p = self.home / "logs" / "decisions" / "seller.jsonl"
        return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []

    # ---

    def test_sku_inquiry_gets_fact_line_and_data_part_within_a_second(self):
        (task, elapsed, _), = self.talk(("I want to buy Curtain (G000f6cda). Please include the unit.", "a"))
        self.assertEqual(task["status"]["state"], "TASK_STATE_INPUT_REQUIRED")
        self.assertLess(elapsed, 1.0)
        text = self.said(task)
        for part in ("SKU G000f6cda", "unit u-1", "list 100000", "grade A now", "no warranty"):
            self.assertIn(part, text)
        self.assertTrue(text.endswith("ask 85000"))
        d = self.data(task)
        self.assertEqual((d["sku"], d["unit"], int(d["ask"]), int(d["list_price"]), d["grade_now"]),
                         ("G000f6cda", "u-1", 85000, 100000, "A"))
        self.assertEqual(d["cert"]["sig"], "market-sig")
        self.assertEqual(int(task["status"]["message"]["metadata"]["ask"]), 85000)
        row = self.rows()[-1]
        self.assertEqual((row["role"], row["kind"], row["choice"]["price"]), ("seller", "quote", 85000))
        self.assertTrue(row["policy_ver"].startswith("1/"))

    def test_accepted_offer_yields_a_verifiable_receipt_with_the_unit(self):
        steps = self.talk(("I want to buy Curtain (G000f6cda)", "a"), ("85000", "a"))
        task, _, raw = steps[-1]
        self.assertEqual(task["status"]["state"], "TASK_STATE_COMPLETED")
        r = self.receipt(task)
        self.assertEqual((r["shop"], r["item"], r["price"], r["task"], r["unit"], r["claim"]),
                         ("psyoung82", "G000f6cda", 85000, task["id"], "u-1", "A"))
        self.assertNotIn("warranty_hours", r)
        self.assertTrue(verify(self.pub, r))
        self.assertFalse(verify(self.pub, dict(r, price=1)))
        self.assertEqual(self.rows()[-1]["kind"], "sign")
        try:                                          # the kit's proxy must pick up the seller's own terms
            import market_kit
        except Exception:
            self.skipTest("market_kit.py not importable")
        fake = types.SimpleNamespace(seen=[], acp=types.SimpleNamespace(terms=collections.OrderedDict()))
        market_kit.Proxy.observe(fake, raw)
        self.assertEqual(fake.acp.terms[task["id"]]["unit"], "u-1")

    def test_counter_once_then_accept_above_floor(self):
        steps = self.talk(("I want to buy G000f6cda", "a"), ("I offer 65,000", "a"), ("61000", "a"))
        self.assertIn("Counteroffer: 72500", self.said(steps[1][0]))
        self.assertEqual(int(steps[1][0]["status"]["message"]["metadata"]["ask"]), 72500)
        self.assertEqual(steps[2][0]["status"]["state"], "TASK_STATE_COMPLETED")
        self.assertEqual(self.receipt(steps[2][0])["price"], 61000)

    def test_below_floor_gets_final_then_rejection(self):
        steps = self.talk(("I want to buy G000f6cda", "a"), ("50000", "a"), ("50000", "a"))
        self.assertIn("Final price: 60000", self.said(steps[1][0]))
        self.assertEqual(steps[2][0]["status"]["state"], "TASK_STATE_REJECTED")
        self.assertEqual([r["kind"] for r in self.rows()], ["quote", "quote", "reject"])

    def test_warranty_on_request_is_priced_and_signed(self):
        steps = self.talk(("I want to buy Curtain (G000f6cda) with a 6 h warranty", "a"), ("I accept", "a"))
        self.assertIn("warranty 6 h included", self.said(steps[0][0]))
        self.assertTrue(self.said(steps[0][0]).endswith("ask 97750"))
        r = self.receipt(steps[1][0])
        self.assertEqual((r["price"], int(r["warranty_hours"])), (97750, 6))

    def test_no_warranty_for_grade_c(self):
        self.write_inventory([self.goods("u-c", grade="C", cost=30000)])
        steps = self.talk(("I want to buy G000f6cda, warranty please", "a"), ("I offer 65000, with warranty", "a"))
        self.assertIn("no warranty", self.said(steps[0][0]))
        self.assertIn("Counteroffer: 72500", self.said(steps[1][0]))     # a repeated request does not reset the deal

    def test_warranty_dropped_mid_negotiation(self):
        steps = self.talk(("I want to buy G000f6cda with warranty", "a"), ("no warranty then", "a"), ("85000", "a"))
        self.assertTrue(self.said(steps[1][0]).endswith("ask 85000"))
        self.assertNotIn("warranty_hours", self.receipt(steps[2][0]))

    def test_promised_unit_is_not_sold_twice_until_the_promise_expires(self):
        first, second = self.talk(("I want to buy G000f6cda", "a"), ("I want to buy G000f6cda", "b"))
        self.assertEqual(first[0]["status"]["state"], "TASK_STATE_INPUT_REQUIRED")
        self.assertEqual(second[0]["status"]["state"], "TASK_STATE_REJECTED")
        self.now += 181                                # promise_ttl_s 180
        (third, _, _), = self.talk(("I want to buy G000f6cda", "c"))
        self.assertEqual(self.data(third)["unit"], "u-1")

    def test_food_honours_use_within_hours(self):
        self.write_inventory([self.milk("u-short", 10), self.milk("u-long", 50), self.milk("u-mid", 30)])
        (plain, _, _), = self.talk(("I want to buy Milk (G0011e806)", "a"))
        self.assertEqual(self.data(plain)["unit"], "u-short")           # expires first
        (need, _, _), = self.talk(("I want to buy Milk (G0011e806). It must last at least 24 hours.", "b"))
        self.assertEqual(self.data(need)["unit"], "u-mid")              # first to expire among >= 25 h
        self.assertIn("h shelf life left", self.said(need))
        (none, _, _), = self.talk(("I want to buy Milk (G0011e806). use_within_hours 60", "c"))
        self.assertEqual(none["status"]["state"], "TASK_STATE_REJECTED")

    def test_memory_of_use_within_hours_lowers_the_food_floor(self):
        stub = types.SimpleNamespace(load_summary=lambda: {"x": 1},
                                     category_stats=lambda s, c: {"uwh": {"n": 12, "median": 24}},
                                     summary_ref=lambda kind, key: f"{kind}:{key}")
        seller._query = lambda: stub                   # restored by setUp's cleanup
        self.write_inventory([self.milk("u-old", 40)])  # 40 h left < 2 x 24 h median: D4 below cost
        steps = self.talk(("I want to buy Milk (G0011e806)", "a"), ("9000", "a"))
        self.assertEqual(steps[1][0]["status"]["state"], "TASK_STATE_COMPLETED")
        sign = self.rows()[-1]
        self.assertEqual((sign["kind"], sign["choice"]["price"], sign["inputs"]["floor"]), ("sign", 9000, 1))
        self.assertIn("D4-food", sign["rules"])
        self.assertEqual(sign["memory_refs"], ["cat:우유/두유"])

    def test_by_name_and_by_category(self):
        (by_name, _, _), = self.talk(("I want to buy a Curtain", "a"))
        self.assertEqual(self.data(by_name)["sku"], "G000f6cda")
        self.now += 181
        (by_cat, _, _), = self.talk(("anything in 커튼?", "b"))
        self.assertEqual(self.data(by_cat)["unit"], "u-1")

    def test_no_stock_before_round_3(self):
        self.write_inventory([self.goods("u-1")], rnd=2)
        (task, _, _), = self.talk(("I want to buy G000f6cda", "a"))
        self.assertEqual(task["status"]["state"], "TASK_STATE_REJECTED")

    def test_card_name_and_no_stdout(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            async def card():
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://shop") as c:
                    return (await c.get("/.well-known/agent-card.json")).json()
            c = asyncio.run(card())
            self.talk(("I want to buy G000f6cda", "a"), ("70000", "a"), ("nonsense", "a"))
        self.assertIn("psyoung82", c["name"])
        self.assertEqual(out.getvalue(), "")

    def test_policy_reloaded_per_request(self):
        skill = self.home / "strategy" / "SKILL.md"
        skill.write_text(skill.read_text(encoding="utf-8").replace("first_ask_list: 0.85", "first_ask_list: 0.90"),
                         encoding="utf-8")
        os.utime(skill, ns=(time.time_ns(), time.time_ns() + 10**9))
        (task, _, _), = self.talk(("I want to buy G000f6cda", "a"))
        self.assertTrue(self.said(task).endswith("ask 90000"))


class RealServer(unittest.TestCase):
    def test_real_server_prints_nothing(self):
        """The kit pumps the seller's stdout and stderr into market-kit.log: 20 requests under uvicorn, 0 lines."""
        home = Path(tempfile.mkdtemp(prefix="r3-seller-"))
        self.addCleanup(shutil.rmtree, home, True)
        (home / "strategy").mkdir()
        shutil.copy(W / "strategy" / "SKILL.md", home / "strategy" / "SKILL.md")
        key = Ed25519PrivateKey.generate()
        (home / "shop_key.pem").write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                              serialization.NoEncryption()))
        md = home / ".market"
        md.mkdir()
        now = time.time()
        units = [{"unit": f"u-{i}", "sku": "G000f6cda", "name": "Curtain", "category": "a>b>커튼", "list_price": 100000,
                  "kind": "goods", "cost": 50000, "grade": "A", "supplied_at": now, "expires_at": None, "cert": {}}
                 for i in range(10)]
        (md / "inventory.json").write_text(json.dumps({"round": 3, "units": units}), encoding="utf-8")
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        env = dict(os.environ, R3_HOME=str(home), MARKET_DIR=str(md))
        proc = subprocess.Popen([sys.executable, "-B", str(W / "seller.py"), "--port", str(port)], cwd=home, env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        base = f"http://127.0.0.1:{port}"
        try:
            with httpx.Client(timeout=5) as c:
                for _ in range(100):
                    try:
                        c.get(base + "/.well-known/agent-card.json")
                        break
                    except httpx.TransportError:
                        time.sleep(0.05)

                def send(text, task=None):
                    m = {"role": "ROLE_USER", "messageId": os.urandom(6).hex(), "parts": [{"text": text}]}
                    if task:
                        m["taskId"] = task
                    return c.post(base + "/", json={"jsonrpc": "2.0", "id": "1", "method": "SendMessage",
                                                    "params": {"message": m}},
                                  headers={"A2A-Version": "1.0"}).json()["result"]["task"]
                for i in range(10):
                    t = send("I want to buy G000f6cda")
                    last = send("85000" if i % 2 else "I offer 65000", t["id"])
            self.assertEqual(last["status"]["state"], "TASK_STATE_COMPLETED")
        finally:
            proc.terminate()
            out = proc.communicate(timeout=10)[0]
        self.assertEqual(out.decode(errors="replace"), "")


class ParseBuyerText(unittest.TestCase):
    def test_offers(self):
        p = seller.parse_offer
        self.assertEqual(p("I offer 65,000"), 65000)
        self.assertEqual(p("I offer 65000, final"), 65000)
        self.assertEqual(p("52000"), 52000)
        self.assertEqual(p("I accept", ask=85000), 85000)
        self.assertEqual(p("I accept 80000", ask=85000), 80000)
        self.assertIsNone(p("I want to buy 소화가잘되는우유 190ML 24팩 (G0011e806)", explicit_only=True,
                            strip=("소화가잘되는우유 190ML 24팩",)))
        self.assertEqual(p("I want to buy Milk (G0011e806) for 15000", explicit_only=True, strip=("Milk",)), 15000)
        self.assertIsNone(p("It must last at least 24 hours"))
        self.assertIsNone(p("My budget is 50000"))
        self.assertIsNone(p("no, I do not accept", ask=85000))

    def test_need_and_warranty(self):
        self.assertEqual(seller.parse_need_hours("It must last at least 24 hours", {}), 24.0)
        self.assertEqual(seller.parse_need_hours("x", {"use_within_hours": 12}), 12.0)
        self.assertIsNone(seller.parse_need_hours("I want to buy Milk", {}))
        self.assertIs(seller.parse_warranty("Do you offer a warranty?", {}), True)
        self.assertEqual(seller.parse_warranty("with a 6 h warranty", {}), 6.0)
        self.assertIs(seller.parse_warranty("no warranty needed", {}), False)
        self.assertIsNone(seller.parse_warranty("I want to buy it", {}))


if __name__ == "__main__":
    unittest.main()
