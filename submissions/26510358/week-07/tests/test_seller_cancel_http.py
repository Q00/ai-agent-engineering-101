"""Actual local SDK JSONRPC cancellation; TEST ONLY model decisions and async gates."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from copy import deepcopy
import json
from pathlib import Path
import socket
import sys
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
import uvicorn
from a2a.server.tasks import TaskUpdater

from harness import Harness
import seller


ITEM = {"id": "lamp", "name": "TEST ONLY lamp", "description": "TEST ONLY desk lamp",
        "reserve": 100, "category": "lamp", "attributes": {"material": "metal"}}


class RecordingTrace:
    def __init__(self, *_args, **_kwargs):
        self.events = []

    def write(self, event, **fields):
        self.events.append({"event": event, **fields})


class StubPolicy:
    sha256, version = "test-only-policy", "test-only"

    def __init__(self, *_args):
        pass

    def choose(self, *_args):
        return "balanced"


def stub_decision(action):
    price, offer = {"open": (130, None), "accept": (100, 100)}[action]
    return {"action": action, "price": price, "offer": offer, "item_id": "lamp",
            "reason": "TEST ONLY explicit stub output; no model inference"}


def rpc(state, method, params):
    request = {"jsonrpc": "2.0", "id": uuid.uuid4().hex, "method": method, "params": params}
    with httpx.Client(trust_env=False, timeout=5, headers={"A2A-Version": "1.0"}) as client:
        response = client.post(state.url + "/", json=request)
        response.raise_for_status()
        body = response.json()
    with state.wire_lock:
        state.wire.append({"request": request, "response": body})
    if body.get("id") != request["id"]:
        raise AssertionError("Actual SDK response must retain the JSONRPC request ID")
    return body


def send_params(text, task_id=None, *, immediately=False):
    message = {"messageId": uuid.uuid4().hex, "role": "ROLE_USER", "parts": [{"text": text}]}
    if task_id:
        message["taskId"] = task_id
    params = {"message": message}
    if immediately:
        params["configuration"] = {"returnImmediately": True}
    return params


def result_task(body):
    if "error" in body:
        raise AssertionError(f"Unexpected SDK JSONRPC error: {body['error']}")
    result = body["result"]
    return result.get("task", result)


@contextmanager
def actual_sdk_server(*, block_opening=False, gate_receipt=False):
    """Use production build/routes/store/TaskUpdater, replacing only test model/config."""
    state = SimpleNamespace(model_started=threading.Event(), model_release=threading.Event(),
        model_finished=threading.Event(), cancel_entered=threading.Event(),
        artifact_posted=threading.Event(), artifact_release=None, loop=None, executor=None,
        wire=[], wire_lock=threading.Lock(), model_contexts=[])
    original_seller, original_add_artifact = seller.Seller, TaskUpdater.add_artifact

    class ObservedSeller(original_seller):
        def __init__(self, config):
            super().__init__(config)
            state.executor = self

        async def cancel(self, context, event_queue):
            state.cancel_entered.set()
            return await super().cancel(context, event_queue)

    def stub_model(_instance, trusted, **kwargs):
        state.model_contexts.append(deepcopy(trusted))
        action = "open" if trusted["opening"] else "accept"
        if block_opening and trusted["opening"]:
            state.model_started.set()
            try:
                if not state.model_release.wait(timeout=5):
                    raise AssertionError("TEST ONLY blocked model was never released")
            finally:
                state.model_finished.set()
        return kwargs["validator"](stub_decision(action))

    async def gated_add_artifact(updater, *args, **kwargs):
        await original_add_artifact(updater, *args, **kwargs)
        if gate_receipt and kwargs.get("name") == "receipt":
            state.loop = asyncio.get_running_loop()
            state.artifact_release = asyncio.Event()
            state.artifact_posted.set()
            await state.artifact_release.wait()

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(128)
    port = sock.getsockname()[1]
    state.url = f"http://127.0.0.1:{port}"
    with patch.object(seller, "Seller", ObservedSeller), \
            patch.object(seller, "Trace", RecordingTrace), \
            patch.object(seller, "StrategyPolicy", StubPolicy), \
            patch.object(seller, "load_catalog", return_value=[deepcopy(ITEM)]), \
            patch.object(Harness, "decide", stub_model), \
            patch.object(TaskUpdater, "add_artifact", gated_add_artifact):
        app = seller.build({"name": "TEST ONLY cancellation HTTP shop", "port": port, "price_policy": str(Path(__file__).with_name("price-policy-test-only.json"))})
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, loop="asyncio",
            log_level="error", access_log=False, timeout_graceful_shutdown=2))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 5
            while not server.started:
                if not thread.is_alive() or time.monotonic() >= deadline:
                    raise AssertionError("TEST ONLY actual SDK server failed to start")
                time.sleep(0.01)
            yield state
        finally:
            state.model_release.set()
            if state.loop is not None and state.artifact_release is not None:
                state.loop.call_soon_threadsafe(state.artifact_release.set)
            server.should_exit = True
            thread.join(timeout=6)
            sock.close()
            if thread.is_alive():
                raise AssertionError("TEST ONLY actual SDK server failed to shut down")


class SellerCancelHTTPTests(unittest.TestCase):
    def test_http_cancel_ack_during_blocked_inference_has_no_late_quote_or_sale(self):
        with actual_sdk_server(block_opening=True) as state:
            # Nonblocking SendMessage returns the actual persisted SDK task while
            # its producer continues to await the explicitly blocked native thread.
            opened = result_task(rpc(state, "SendMessage", send_params("I want to buy a lamp", immediately=True)))
            self.assertTrue(state.model_started.wait(timeout=1))
            self.assertFalse(state.model_finished.is_set())
            task_id = opened["id"]
            canceled = result_task(rpc(state, "CancelTask", {"id": task_id}))
            self.assertEqual(canceled["status"]["state"], "TASK_STATE_CANCELED")
            self.assertFalse(state.model_release.is_set(), "Cancel ACK must not wait for inference")
            self.assertFalse(state.model_finished.is_set())
            state.model_release.set()
            self.assertTrue(state.model_finished.wait(timeout=1))
            # Query via a second real SDK RPC after the blocked thread has finished.
            final = result_task(rpc(state, "GetTask", {"id": task_id}))
            self.assertEqual(final["status"]["state"], "TASK_STATE_CANCELED")
            self.assertFalse(final.get("artifacts"))
            self.assertNotIn(task_id, state.executor.open)
            self.assertEqual(state.executor.terminal[task_id], "canceled")
            self.assertEqual(len(state.model_contexts), 1)
            self.assertFalse(any(e["event"] in {"quote", "sale_completed"} for e in state.executor.trace.events))
            print(json.dumps({"mode": "TEST_ONLY_stub_model_actual_local_JSONRPC", "case": "cancel_before_model_return",
                              "wire": state.wire, "seller_events": state.executor.trace.events}, ensure_ascii=False))

    def test_http_cancel_waits_for_receipt_publication_then_returns_error_after_completed_sale(self):
        with actual_sdk_server(gate_receipt=True) as state, ThreadPoolExecutor(max_workers=2) as workers:
            opened = result_task(rpc(state, "SendMessage", send_params("I want to buy a lamp")))
            task_id = opened["id"]
            self.assertEqual(opened["status"]["state"], "TASK_STATE_INPUT_REQUIRED")
            completion = workers.submit(rpc, state, "SendMessage", send_params("100", task_id))
            try:
                self.assertTrue(state.artifact_posted.wait(timeout=1))
                # Receipt was really enqueued; complete() has not run because of
                # the explicit async gate inside the production publication lock.
                cancellation = workers.submit(rpc, state, "CancelTask", {"id": task_id})
                self.assertTrue(state.cancel_entered.wait(timeout=1))
                self.assertFalse(cancellation.done(), "Cancel must wait for an in-progress publication")
                self.assertFalse(completion.done())
                self.assertNotIn(task_id, state.executor.terminal)
                state.loop.call_soon_threadsafe(state.artifact_release.set)
                completed = result_task(completion.result(timeout=3))
                cancel_response = cancellation.result(timeout=3)
                self.assertEqual(completed["status"]["state"], "TASK_STATE_COMPLETED")
                self.assertEqual(completed["artifacts"][0]["parts"][0]["data"]["price"], 100)
                self.assertIn("error", cancel_response)
                self.assertNotIn("result", cancel_response, "A sold item must never acknowledge CANCELED")
                final = result_task(rpc(state, "GetTask", {"id": task_id}))
                self.assertEqual(final["status"]["state"], "TASK_STATE_COMPLETED")
                self.assertEqual(state.executor.terminal[task_id], "completed")
                self.assertNotIn(task_id, state.executor.open)
                self.assertEqual(sum(e["event"] == "sale_completed" for e in state.executor.trace.events), 1)
                self.assertFalse(any(e["event"] == "sale_canceled" for e in state.executor.trace.events))
            finally:
                if state.loop is not None and state.artifact_release is not None:
                    state.loop.call_soon_threadsafe(state.artifact_release.set)
                print(json.dumps({"mode": "TEST_ONLY_stub_model_actual_local_JSONRPC", "case": "cancel_during_receipt_publication",
                                  "wire": state.wire, "seller_events": state.executor.trace.events}, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
