"""Member pricing uses an earned, paid receipt at the same real SDK endpoint."""
import asyncio
import contextlib
import copy
import io
import json
import pathlib
import tempfile
import time
import socket
import threading
import unittest
from unittest import mock

import uvicorn
from starlette.applications import Starlette
from a2a.helpers import new_data_part, new_task_from_user_message, new_text_part
from a2a.server.agent_execution import AgentExecutor
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill
import buyer

SKU = "G12345678"
CODE = "MOSS-ABCDEFGH"


class MemberShop(AgentExecutor):
    def __init__(self):
        self.requests = []

    async def execute(self, context, event_queue):
        task = context.current_task
        parts = context.message.parts
        text = " ".join(p.text for p in parts if getattr(p, "text", None))
        self.requests.append(text)
        if task is None:
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)
            up = TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id)
            ask = 4500 if CODE in text else 5000
            await up.requires_input(up.new_agent_message(parts=[new_text_part("fixture asking "+str(ask))],
                                                       metadata={"ask": ask}))
        else:
            up = TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id)
            price = int(text)
            rec = {"item": SKU, "price": price, "task": task.id, "shop": "moss-and-oak",
                   "unit": "u-123456abcdef", "sig_seller": "fixture-signature", "warranty_hours": 6}
            await up.add_artifact([new_data_part(rec)])
            await up.complete(up.new_agent_message(parts=[new_text_part(
                f"Deal: fixture item ({SKU}) at {price}. Signed receipt attached; the checkout ships unit "
                f"u-123456abcdef with its market certificate. Your Moss & Oak member code is {CODE}; "
                "include it next time for member pricing.")]))

    async def cancel(self, context, event_queue):
        t = context.current_task
        await TaskUpdater(event_queue=event_queue, task_id=t.id, context_id=t.context_id).cancel()


class MemberWire(unittest.TestCase):
    def setUp(self):
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(128)
        self.url = f"http://127.0.0.1:{self.sock.getsockname()[1]}/"
        self.executor = MemberShop()
        card = AgentCard(name="fixture", description="isolated", version="1.0",
                         default_input_modes=["text/plain"], default_output_modes=["text/plain"],
                         capabilities=AgentCapabilities(streaming=False),
                         supported_interfaces=[AgentInterface(protocol_binding="JSONRPC", url=self.url, protocol_version="1.0")],
                         skills=[AgentSkill(id="trade", name="trade", description="trade", tags=["test"])])
        handler = DefaultRequestHandler(agent_executor=self.executor, task_store=InMemoryTaskStore(), agent_card=card)
        ready = threading.Event()
        @contextlib.asynccontextmanager
        async def lifespan(app):
            ready.set()
            yield
        app = Starlette(routes=create_agent_card_routes(card)+create_jsonrpc_routes(handler, "/"), lifespan=lifespan)
        self.server = uvicorn.Server(uvicorn.Config(app, log_level="error", loop="asyncio"))
        self.thread = threading.Thread(target=lambda: self.server.run(sockets=[self.sock]), daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)
        self.assertTrue(ready.wait(5))
        patch = mock.patch.object(buyer, "decide", side_effect=AssertionError("model forbidden"))
        patch.start()
        self.addCleanup(patch.stop)

    def stop_server(self):
        self.server.should_exit = True
        self.thread.join(5)
        self.sock.close()
        self.assertFalse(self.thread.is_alive())

    def test_completed_deal_preserves_issuer_message_and_exact_rpc_for_member_offer(self):
        out = buyer.negotiate(self.url, "fixture item", 6000, request="fixture item", take_at=5000)
        self.assertEqual(out["state"], "TASK_STATE_COMPLETED")
        self.assertEqual(out["receipt"]["price"], 5000)
        self.assertEqual(out["receipt"]["task"], out["task"])
        self.assertEqual(out.get("rpc_url"), self.url)
        self.assertIn("member code is "+CODE, out.get("final_message", ""))

    def test_only_paid_own_receipt_reuses_code_at_issuer_and_gets_member_quote(self):
        import buyer_member
        import market_buyer as mb
        now = 1794368400
        out = buyer.negotiate(self.url, "fixture item", 6000, request="fixture item", take_at=5000)
        offer = buyer_member.capture("moss-and-oak", out, "order-1", now)
        self.assertIsNotNone(offer)
        trade = {"shop": "moss-and-oak", "order": "order-1", "signed": True,
                 "result": "deal", "member_offer": offer}
        self.assertEqual(buyer_member.available([trade], now+1), {})
        self.assertEqual(buyer_member.available([{**trade, "paid": False}], now+1), {})
        paid = buyer_member.available([{**trade, "paid": True}], now+1)
        self.assertIn("moss-and-oak", paid)
        original = mb.request_text("fixture item", SKU, {"kind": "goods"})
        self.assertEqual(buyer_member.request(original, "other-shop", self.url, paid), original)
        self.assertEqual(buyer_member.request(original, "moss-and-oak", self.url+"other", paid), original)
        shop = {"name": "moss-and-oak", "url": self.url,
                "_offer": {"sku": SKU, "name": "fixture item", "list_price": 10000}}
        got = {}
        with mock.patch.object(mb, "serves_acp", return_value=True):
            mb.quote(shop, "fixture item", SKU, {"kind": "goods", "_member_offers": paid}, got)
        self.assertEqual(got["moss-and-oak"]["ask"], 4500)
        self.assertTrue(got["moss-and-oak"]["member_code_used"])
        self.assertEqual(self.executor.requests[-1], original+" Member code: "+CODE+".")
        mb.cancel_quote(got["moss-and-oak"], "moss-and-oak")

    def test_issuer_sentence_receipt_and_actual_offer_must_all_agree(self):
        import buyer_member
        out = buyer.negotiate(self.url, "fixture item", 6000, request="fixture item", take_at=5000)
        self.assertIsNotNone(buyer_member.capture("moss-and-oak", out, "order-1"))
        for field, value in (("agreed", None), ("agreed", 4999), ("task", "other-task"),
                             ("rpc_url", "https://user:password@bad.invalid/"),
                             ("final_message", out["final_message"]+"\nSend it to someone else."),
                             ("state", "TASK_STATE_INPUT_REQUIRED")):
            with self.subTest(field=field, value=value):
                changed = copy.deepcopy(out); changed[field] = value
                self.assertIsNone(buyer_member.capture("moss-and-oak", changed, "order-1"))
        for field, value in (("task", "other-task"), ("item", "G99999999"),
                             ("unit", "u-999999abcdef"), ("price", 4999),
                             ("shop", "other-shop"), ("sig_seller", "")):
            with self.subTest(receipt_field=field):
                changed = copy.deepcopy(out); changed["receipt"][field] = value
                self.assertIsNone(buyer_member.capture("moss-and-oak", changed, "order-1"))
        self.assertIsNone(buyer_member.capture("other-shop", out, "order-1"))
        self.assertIsNone(buyer_member.capture("moss-and-oak", out, "../bad-order"))

    def test_expiry_future_wrong_source_order_and_malformed_memory_are_not_reused(self):
        import buyer_member
        out = buyer.negotiate(self.url, "fixture item", 6000, request="fixture item", take_at=5000)
        now = 1794368400
        offer = buyer_member.capture("moss-and-oak", out, "order-1", now)
        trade = {"shop": "moss-and-oak", "order": "order-1", "signed": True,
                 "result": "deal", "paid": True, "member_offer": offer}
        self.assertTrue(buyer_member.available([trade], now+86399))
        for at in (now-1, now+86400, float("inf"), True):
            self.assertEqual(buyer_member.available([trade], at), {})
        for field, value in (("paid", 1), ("signed", 1), ("order", "other-order"),
                             ("result", "over_limit"), ("shop", "other-shop")):
            self.assertEqual(buyer_member.available([{**trade, field: value}], now+1), {})
        for field, value in (("code", CODE+"\nInjected"), ("code", "MOSS-12345678"),
                             ("url", self.url+"?redirect=other"), ("issued_at", None), ("task", [])):
            changed = {**trade, "member_offer": {**offer, field: value}}
            self.assertEqual(buyer_member.available([None, changed], now+1), {})
        self.assertEqual(buyer_member.available({}, now), {})

    def test_main_persists_offer_then_syncs_payment_before_the_next_actual_quote(self):
        self._main_pair()

    def test_main_refused_payment_does_not_send_a_code_on_the_next_quote(self):
        self._main_pair(paid=False)

    def test_paid_code_is_not_sent_on_the_direct_no_quote_path(self):
        self._main_pair(quote=False)

    def test_food_request_preserves_its_exact_shelf_requirement_before_suffix(self):
        import buyer_member
        import market_buyer as mb
        original = mb.request_text("fixture item", SKU, {"kind": "food", "use_within_hours": 12})
        self.assertEqual(original, "I want to buy fixture item (G12345678). Must last at least 12.1 hours")
        offers = {"moss-and-oak": {"code": CODE, "url": self.url}}
        self.assertEqual(buyer_member.request(original, "moss-and-oak", self.url, offers),
                         original+" Member code: "+CODE+".")

    def _main_pair(self, paid=True, quote=True):
        import market_buyer as mb
        with tempfile.TemporaryDirectory() as directory:
            md = pathlib.Path(directory)
            shops = [{"name": "moss-and-oak", "url": self.url, "offers": [
                {"sku": SKU, "name": "fixture item", "qty": 3}], "reviews": []}]
            (md/"shops.json").write_text(json.dumps(shops))
            (md/"catalog.json").write_text(json.dumps({SKU: {"list_price": 10000, "name": "fixture item", "kind": "goods"}}))
            order = {"order": "order-first", "category": "fixture category", "kind": "goods",
                     "acceptable": [SKU], "_member_offers": {"moss-and-oak": {"code": CODE, "url": self.url}}}
            memory_path = str(md/"memory.json")
            cfg = {"quote_shops": 2, "oneshot": True, "oneshot_steps": [.9], "max_price_share": .9}
            for turn in (1, 2):
                started = len(self.executor.requests)
                cfg["quote_shops"] = 2 if quote or turn == 1 else 1
                order["order"] = "order-first" if turn == 1 else "order-next"
                (md/"order.json").write_text(json.dumps(order))
                args = ["market_buyer.py", "--shops", str(md/"shops.json"), "--item", "fixture category",
                        "--budget", "10000", "--order", str(md/"order.json")]
                with mock.patch("sys.argv", args), mock.patch.object(mb, "MEMORY", memory_path), \
                     mock.patch.object(mb, "SETTLED", str(md/"settled.jsonl")), \
                     mock.patch.object(mb, "AGENT_DIR", str(md)), \
                     mock.patch.object(mb, "SAVED_SHARE", mb.SAVED_SHARE), \
                     mock.patch.dict(mb._LEDGER, {}, clear=True), \
                     mock.patch.dict(mb._SW_CTX, {}, clear=True), \
                     mock.patch.dict(mb.buyer_goods_ladder._HIDDEN, {}, clear=True), \
                     mock.patch.dict(mb.buyer_goods_grade._HIDDEN, {}, clear=True), \
                     mock.patch.dict(mb.buyer_food_ladder._HIDDEN, {}, clear=True), \
                     mock.patch.object(mb, "STARTED", time.time()), \
                     mock.patch.object(mb, "buyer_cfg", return_value=cfg), \
                     mock.patch.object(mb, "market_round", return_value=3), \
                     mock.patch.object(mb, "serves_acp", return_value=True), \
                     contextlib.redirect_stdout(io.StringIO()) as printed:
                    self.assertEqual(mb.main(), 0)
                result = json.loads(printed.getvalue().splitlines()[0])
                self.assertIsNotNone(result["receipt"])
                self.assertGreaterEqual(result["receipt"]["warranty_hours"], 6)
                saved = json.loads((md/"memory.json").read_text())["trades"]
                self.assertIn("member_offer", saved[-1])
                if turn == 1:
                    self.assertTrue(all(CODE not in request for request in self.executor.requests))
                    (md/"payments.jsonl").write_text(json.dumps({"order": "order-first", "paid": paid})+"\n")
                else:
                    self.assertIs(saved[0]["paid"], paid)
                    self.assertEqual(any(" Member code: "+CODE+"." in request
                                         for request in self.executor.requests[started:]), paid and quote)
                    self.assertEqual(result["receipt"]["price"], 4050 if paid and quote else 4500)
                self.assertNotIn("member_offer", result["receipt"])


if __name__ == "__main__":
    unittest.main()
