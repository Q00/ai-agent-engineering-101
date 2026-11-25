"""Real A2A SDK/HTTP cleanup boundary; no market, model, or persistent state."""
import asyncio
import contextlib
import copy
from types import SimpleNamespace
import socket
import threading
import unittest
from unittest import mock

import httpx
import uvicorn
from starlette.applications import Starlette
from starlette.routing import Mount

from a2a.helpers import get_message_text, new_data_part, new_task_from_user_message, new_text_part
from a2a.server.agent_execution import AgentExecutor
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill

import buyer
import market_buyer as mb


SKU = "G12345678"
TERMINAL = ("TASK_STATE_COMPLETED", "TASK_STATE_CANCELED", "TASK_STATE_FAILED", "TASK_STATE_REJECTED")


class Executor(AgentExecutor):
    def __init__(self):
        self.initial = "TASK_STATE_INPUT_REQUIRED"
        self.release = threading.Event()
        self.release.set()
        self.entered = threading.Event()

    async def execute(self, context, event_queue):
        task = context.current_task
        if task is None:
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)
            self.entered.set()
            await asyncio.to_thread(self.release.wait, 5)
        up = TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id)
        say = lambda text, ask=None: up.new_agent_message(
            parts=[new_text_part(text)], metadata={"ask": ask} if ask is not None else None)
        if context.current_task is not None or self.initial == "TASK_STATE_COMPLETED":
            await up.add_artifact([new_data_part({"item": SKU, "price": 5000, "task": task.id})])
            await up.complete(say("Deal at 5000."))
        elif self.initial == "TASK_STATE_REJECTED":
            await up.reject(say("No stock."))
        elif self.initial == "TASK_STATE_FAILED":
            await up.failed(say("Failed."))
        elif self.initial == "TASK_STATE_CANCELED":
            await up.cancel(say("Canceled."))
        else:
            await up.requires_input(say(f"fixture food ({SKU}), asking 5000.", 5000))

    async def cancel(self, context, event_queue):
        task = context.current_task
        await TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id).cancel()


class Handler(DefaultRequestHandler):
    def __init__(self, executor, card):
        super().__init__(agent_executor=executor, task_store=InMemoryTaskStore(), agent_card=card)
        self.cancel_requests = []
        self.get_requests = []

    async def on_get_task(self, params, context):
        self.get_requests.append(params.id)
        return await super().on_get_task(params, context)

    async def on_cancel_task(self, params, context):
        self.cancel_requests.append(params.id)
        return await super().on_cancel_task(params, context)


class CleanupWire(unittest.TestCase):
    def setUp(self):
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(128)
        self.base = f"http://127.0.0.1:{self.sock.getsockname()[1]}"
        self.executors, self.handlers = {}, {}
        routes = []
        for name in ("active", "other", "terminal", "late"):
            url = self.base + "/" + name + "/"
            card = AgentCard(name=name, description="isolated fixture", version="1.0",
                default_input_modes=["text/plain"], default_output_modes=["text/plain"],
                capabilities=AgentCapabilities(streaming=False),
                supported_interfaces=[AgentInterface(protocol_binding="JSONRPC", url=url, protocol_version="1.0")],
                skills=[AgentSkill(id="quote", name="quote", description="quote", tags=["test"])])
            executor = self.executors[name] = Executor()
            handler = self.handlers[name] = Handler(executor, card)
            routes.append(Mount("/"+name, app=Starlette(
                routes=create_agent_card_routes(card)+create_jsonrpc_routes(handler, "/"))))
        ready = threading.Event()
        @contextlib.asynccontextmanager
        async def lifespan(app):
            ready.set()
            yield
        app = Starlette(routes=routes, lifespan=lifespan)
        self.server = uvicorn.Server(uvicorn.Config(app, log_level="error", loop="asyncio"))
        self.thread = threading.Thread(target=lambda: self.server.run(sockets=[self.sock]), daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)
        self.assertTrue(ready.wait(5), "SDK loopback server did not start")
        self.assertEqual(httpx.get(self.base+"/active/.well-known/agent-card.json", timeout=5).status_code, 200)
        self.workers = []
        owner = self
        class OwnedThread(threading.Thread):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                owner.workers.append(self)
        for patch in (mock.patch.object(mb, "serves_acp", return_value=True),
                      mock.patch.object(mb, "log"),
                      mock.patch.object(buyer, "log"),
                      mock.patch.object(buyer, "decide", side_effect=AssertionError("model forbidden")),
                      mock.patch.object(mb, "threading", SimpleNamespace(
                          Thread=OwnedThread, Lock=threading.Lock, Event=threading.Event))):
            patch.start()
            self.addCleanup(patch.stop)
        self.addCleanup(self.finish_workers)

    def finish_workers(self):
        for executor in self.executors.values():
            executor.release.set()
        # Real owned threads, including cancellation threads appended by workers.
        for child in self.workers:
            child.join(5)
            self.assertFalse(child.is_alive(), "owned quote/cancel worker leaked")

    def stop_server(self):
        for executor in self.executors.values():
            executor.release.set()
        self.server.should_exit = True
        self.thread.join(5)
        self.sock.close()
        self.assertFalse(self.thread.is_alive(), "owned SDK server leaked")

    def quote(self, name, out=None, ctl=None):
        out = {} if out is None else out
        shop = {"name": name, "url": self.base+"/"+name,
                "_offer": {"sku": SKU, "name": "fixture food", "list_price": 10000}}
        mb.quote(shop, "fixture food", SKU, {"kind": "food", "use_within_hours": 24}, out, ctl)
        return out[name]

    def test_known_terminal_completed_quote_is_locally_closed_without_cancel_wire(self):
        self.executors["terminal"].initial = "TASK_STATE_COMPLETED"
        q = self.quote("terminal")
        self.assertEqual(q["task"]["status"]["state"], "TASK_STATE_COMPLETED")
        self.assertTrue(q["task"].get("artifacts"), "unused unsolicited receipt fixture must be real")
        with mock.patch.object(mb, "log"), mock.patch.object(buyer, "log"):
            mb.cancel_quote(q, "terminal")
        self.assertTrue(q["closed"])
        self.assertEqual(self.handlers["terminal"].cancel_requests, [])

    def test_all_sdk_terminal_states_skip_cancel_but_keep_local_closed(self):
        for state in TERMINAL:
            with self.subTest(state=state):
                self.executors["terminal"].initial = state
                q = self.quote("terminal")
                mb.cancel_quote(q, "terminal")
                self.assertTrue(q["closed"])
                self.assertFalse(mb.resumable(q))
        self.assertEqual(self.handlers["terminal"].cancel_requests, [])

    def test_active_or_unknown_cached_status_keeps_the_cancel_wire(self):
        for status in ({"state": "TASK_STATE_INPUT_REQUIRED"}, {"state": "TASK_STATE_WORKING"},
                       {"state": "TASK_STATE_AUTH_REQUIRED"}, {"state": "TASK_STATE_SUBMITTED"},
                       {"state": "TASK_STATE_UNSPECIFIED"}, {}, None, {"state": "unknown"},
                       {"state": []}, {"state": {}}, "badstatus", [],
                       {"state": "completed"}, {"state": 3},
                       {"state": "TASK_STATE_COMPLETED "}):
            with self.subTest(status=status):
                q = self.quote("other")
                q["task"]["status"] = status
                before = len(self.handlers["other"].cancel_requests)
                mb.cancel_quote(q, "other")
                self.assertTrue(q["closed"])
                self.assertEqual(self.handlers["other"].cancel_requests[before:], [q["task"]["id"]])

    def test_selected_quote_resumes_and_unused_receipt_is_not_the_deal(self):
        shops = [{"name": name, "url": self.base+"/"+name, "usable": [
                    {"sku": SKU, "name": "fixture food", "qty": 1, "list_price": 10000}],
                  "_offer": {"sku": SKU, "name": "fixture food", "list_price": 10000}}
                 for name in ("active", "other", "terminal")]
        self.executors["terminal"].initial = "TASK_STATE_COMPLETED"
        got, ctl, threads = {}, mb.quote_ctl(), {}
        for shop in shops:
            t = mb.threading.Thread(target=self.quote, args=(shop["name"], got, ctl))
            threads[shop["name"]] = t
            t.start()
        for t in threads.values():
            t.join(5)
            self.assertFalse(t.is_alive())
        with mock.patch.object(mb, "STARTED", mb.time.time()):
            best, result = mb.best_quote(shops[0], shops[1:], {"kind": "food"}, {}, 20000,
                lambda sku: 0, {"quote_shops": 3}, pre={"got": got, "ctl": ctl, "threads": threads})
        self.finish_workers()
        self.assertEqual(best["name"], "active")
        self.assertTrue(mb.resumable(result["active"]))
        self.assertTrue(result["other"]["closed"])
        self.assertTrue(result["terminal"]["closed"])
        self.assertEqual(self.handlers["terminal"].cancel_requests, [])
        self.assertEqual(self.handlers["other"].cancel_requests, [result["other"]["task"]["id"]])
        q = result["active"]
        out = buyer.negotiate(best["url"], "fixture food", 6700,
                              resume=(q["url"], q["task"]), take_at=5000)
        self.assertEqual(out["state"], "TASK_STATE_COMPLETED")
        self.assertEqual(out["receipt"]["item"], SKU)
        self.assertEqual(out["receipt"]["price"], 5000)
        self.assertEqual(out["receipt"]["task"], q["task"]["id"])
        self.assertNotEqual(out["receipt"]["task"], result["terminal"]["task"]["id"])
        self.assertEqual(self.handlers["active"].cancel_requests, [])

    def test_late_active_is_cancelled_late_terminal_is_not_and_late_winner_is_kept(self):
        for state, winner, expected_cancel in (
                ("TASK_STATE_INPUT_REQUIRED", "active", 1),
                ("TASK_STATE_REJECTED", "active", 0),
                ("TASK_STATE_INPUT_REQUIRED", "late", 0)):
            with self.subTest(state=state, winner=winner):
                executor = self.executors["late"]
                executor.initial = state
                executor.entered.clear()
                executor.release.clear()
                got, ctl = {}, mb.quote_ctl()
                before = len(self.handlers["late"].cancel_requests)
                child = mb.threading.Thread(target=self.quote, args=("late", got, ctl))
                child.start()
                self.assertTrue(executor.entered.wait(5))
                self.assertNotIn("late", got)
                with ctl["lock"]:
                    ctl.update(done=True, winner=winner)
                executor.release.set()
                child.join(5)
                self.assertFalse(child.is_alive())
                q = got["late"]
                self.assertEqual(len(self.handlers["late"].cancel_requests)-before, expected_cancel)
                self.assertEqual(mb.resumable(q), winner == "late")
                self.assertEqual(bool(q.get("closed")), winner != "late")

    def test_eight_cached_active_tasks_that_finish_before_cancel_keep_the_race_behavior(self):
        qs = [self.quote("active") for _ in range(8)]
        for q in qs:
            done = buyer.send(q["url"], "5000", q["task"]["id"])
            self.assertEqual(done["status"]["state"], "TASK_STATE_COMPLETED")
        # The cache says active; the SDK now says terminal. No new GetTask/ retry.
        gate, errors = threading.Barrier(8), []
        def cancel(q):
            try:
                gate.wait(5)
                mb.cancel_quote(q, "active")
            except BaseException as exc:
                errors.append(type(exc).__name__)
        workers = [mb.threading.Thread(target=cancel, args=(q,)) for q in qs]
        for worker in workers:
            worker.start()
        self.finish_workers()
        self.assertEqual(errors, [])
        self.assertTrue(all(q["closed"] for q in qs))
        self.assertCountEqual(self.handlers["active"].cancel_requests, [q["task"]["id"] for q in qs])
        self.assertEqual(sum(str(call.args[0]).startswith("<- error -32002")
                             for call in buyer.log.call_args_list), 8)
        self.assertEqual(self.handlers["active"].get_requests, [])

    def test_already_closed_and_missing_task_id_keep_the_original_noop(self):
        cases = (None, {}, {"task": {}}, {"task": {"id": ""}},
                 {"closed": True, "task": {"id": "not-sent"}})
        for q in cases:
            original = copy.deepcopy(q)
            mb.cancel_quote(q, "unused")
            self.assertEqual(q, original)
        self.assertTrue(all(not h.cancel_requests for h in self.handlers.values()))


if __name__ == "__main__":
    unittest.main()
