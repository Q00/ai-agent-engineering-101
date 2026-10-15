"""Real async/thread cancellation races with SDK events and TEST ONLY model output."""
import asyncio
from copy import deepcopy
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from a2a.helpers import new_text_part
from a2a.server.agent_execution import RequestContext
from a2a.server.context import ServerCallContext
from a2a.server.events import EventQueue
from a2a.types import (Message, Role, SendMessageRequest, Task, TaskArtifactUpdateEvent,
                       TaskState, TaskStatus, TaskStatusUpdateEvent)

import seller
from harness import HarnessError


ITEM = {"id": "lamp", "name": "TEST ONLY lamp", "description": "TEST ONLY desk lamp",
        "reserve": 100, "category": "lamp", "attributes": {"material": "metal"}}


class RecordingTrace:
    def __init__(self, *_args, **_kwargs):
        self.events = []

    def write(self, event, **fields):
        self.events.append({"event": event, **fields})


class RecordingQueue(EventQueue):
    """SDK producer queue contract; captures real protobuf events without sockets."""
    def __init__(self):
        self.events = []

    async def enqueue_event(self, event):
        self.events.append(deepcopy(event))


class StubPolicy:
    sha256, version = "test-only-policy", "test-only"

    def __init__(self, *_args):
        pass

    def choose(self, *_args):
        return "balanced"


def task_fixture():
    return Task(id="cancel-race-task", context_id="cancel-race-context",
                status=TaskStatus(state=TaskState.TASK_STATE_INPUT_REQUIRED))


def request_context(text, *, task=None):
    return RequestContext(call_context=ServerCallContext(), task=task,
        task_id="cancel-race-task", context_id="cancel-race-context",
        request=SendMessageRequest(message=Message(message_id="test-only-message",
            role=Role.ROLE_USER, parts=[new_text_part(text)])))


def decision_fixture(action):
    price, offer = {"open": (130, None), "counter": (120, 90), "accept": (100, 100)}[action]
    return {"action": action, "price": price, "offer": offer, "item_id": "lamp",
            "reason": "TEST ONLY simulated model output; no real inference"}


class SellerCancelTests(unittest.IsolatedAsyncioTestCase):
    def make_seller(self):
        with patch.object(seller, "Trace", RecordingTrace), \
                patch.object(seller, "load_catalog", return_value=[deepcopy(ITEM)]), \
                patch.object(seller, "StrategyPolicy", StubPolicy):
            return seller.Seller({"name": "TEST ONLY cancellation seller"})

    async def cancel_during_model(self, action, *, fail=False, queued_turn=False):
        executor, queue = self.make_seller(), RecordingQueue()
        current = task_fixture()
        if action != "open":
            executor.open[current.id] = {"item": deepcopy(ITEM), "ask": 130, "round": 1,
                "quote_id": "test-only-existing-quote", "history": []}
        text = {"open": "I want to buy a lamp", "counter": "90", "accept": "100"}[action]
        # Opening exercises the actual new_task_from_user_message/initial task event.
        context = request_context(text, task=None if action == "open" else current)
        started, release, finished = asyncio.Event(), threading.Event(), threading.Event()
        loop, event_loop_thread = asyncio.get_running_loop(), threading.get_ident()
        model_threads, model_calls = [], []

        def blocked_model(trusted, **kwargs):
            model_threads.append(threading.get_ident())
            model_calls.append(deepcopy(trusted))
            loop.call_soon_threadsafe(started.set)
            try:
                if not release.wait(timeout=3):
                    raise AssertionError("TEST ONLY model release was never signaled")
                if fail:
                    raise HarnessError("TEST ONLY late model failure")
                return kwargs["validator"](decision_fixture(action))
            finally:
                finished.set()

        executor.harness.decide = blocked_model
        running = asyncio.create_task(executor.execute(context, queue))
        tasks = [running]
        try:
            await asyncio.wait_for(started.wait(), timeout=1)
            if queued_turn:
                tasks.append(asyncio.create_task(executor.execute(request_context("100", task=current), queue)))
                await asyncio.sleep(0)  # Let the second actual coroutine wait for the turn lock.
            # Cancellation must acknowledge before the native thread is released.
            await asyncio.wait_for(executor.cancel(request_context("", task=current), queue), timeout=0.5)
            self.assertFalse(release.is_set())
            self.assertFalse(finished.is_set())
            self.assertFalse(running.done())
            status_events = [e for e in queue.events if isinstance(e, TaskStatusUpdateEvent)]
            self.assertEqual([e.status.state for e in status_events], [TaskState.TASK_STATE_CANCELED])
            self.assertNotIn(current.id, executor.open)
            count_at_ack = len(queue.events)
            release.set()
            await asyncio.wait_for(asyncio.gather(*tasks), timeout=1)
            self.assertTrue(finished.is_set())
            self.assertEqual(len(model_calls), 1)
            self.assertTrue(all(thread != event_loop_thread for thread in model_threads))
            self.assertEqual(len(queue.events), count_at_ack, "No late SDK event may follow cancellation ACK")
            self.assertFalse(any(isinstance(e, TaskArtifactUpdateEvent) for e in queue.events))
            self.assertNotIn(current.id, executor.open, "Late opening must not revive canceled deal state")
            self.assertEqual(executor.terminal[current.id], "canceled")
            self.assertEqual(sum(e["event"] == "sale_canceled" for e in executor.trace.events), 1)
            self.assertFalse(any(e["event"] in {"quote", "sale_completed", "negotiation_failed"}
                                 for e in executor.trace.events))
        finally:
            release.set()
            await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=4)

    async def test_cancel_ack_before_opening_model_finishes_drops_late_quote(self):
        await self.cancel_during_model("open")

    async def test_cancel_ack_before_counter_model_finishes_drops_late_quote(self):
        await self.cancel_during_model("counter")

    async def test_cancel_ack_before_accept_model_finishes_drops_late_receipt_and_sale(self):
        await self.cancel_during_model("accept")

    async def test_canceled_task_does_not_restart_a_turn_already_waiting_for_lock(self):
        await self.cancel_during_model("counter", queued_turn=True)

    async def test_late_model_error_does_not_overwrite_cancel_with_failed_status(self):
        await self.cancel_during_model("counter", fail=True)

    async def test_published_sale_cannot_later_acknowledge_cancellation(self):
        executor, queue, current = self.make_seller(), RecordingQueue(), task_fixture()
        executor.open[current.id] = {"item": deepcopy(ITEM), "ask": 130, "round": 1,
                                     "quote_id": "test-only-existing-quote", "history": []}

        def accepted_model(_trusted, **kwargs):
            return kwargs["validator"](decision_fixture("accept"))

        executor.harness.decide = accepted_model
        await executor.execute(request_context("100", task=current), queue)
        statuses = [e.status.state for e in queue.events if isinstance(e, TaskStatusUpdateEvent)]
        self.assertEqual(statuses, [TaskState.TASK_STATE_COMPLETED])
        self.assertEqual(sum(isinstance(e, TaskArtifactUpdateEvent) for e in queue.events), 1)
        # The executor performs no new publication for an already finished sale.
        # The actual HTTP handler converts its completed result to a cancel error.
        await executor.cancel(request_context("", task=current), queue)
        self.assertEqual([e.status.state for e in queue.events if isinstance(e, TaskStatusUpdateEvent)], statuses)
        self.assertEqual(executor.terminal[current.id], "completed")
        self.assertNotIn(current.id, executor.open)


if __name__ == "__main__":
    unittest.main()
