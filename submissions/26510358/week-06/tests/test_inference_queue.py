"""FIFO admission, crash recovery, and pacing across real processes/threads."""
from pathlib import Path
import json
import multiprocessing
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from inference_queue import InferenceQueue, QueueTimeout


def process_worker(path, label, events, hold=None, timeout=5, interval=0):
    queue = InferenceQueue(path, interval=interval, poll_interval=0.005)
    try:
        with queue.slot(time.monotonic() + timeout):
            events.put((label, "entered", time.monotonic()))
            if hold is not None:
                hold.wait(timeout=10)
    except QueueTimeout:
        events.put((label, "expired", time.monotonic()))


class InferenceQueueTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "queue.json"
        self.queue = InferenceQueue(self.path, interval=0, poll_interval=0.005)
        self.context = multiprocessing.get_context("fork")
        self.children = []

    def tearDown(self):
        for child in self.children:
            if child.is_alive():
                child.terminate()
            child.join(timeout=2)
        self.temporary.cleanup()

    def wait_for(self, predicate, timeout=3):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.005)
        self.fail("Expected queue condition did not become true")

    def start_process(self, *args, **kwargs):
        child = self.context.Process(target=process_worker, args=(str(self.path), *args), kwargs=kwargs)
        self.children.append(child)
        child.start()
        return child

    def test_thread_arrival_order_cannot_be_overtaken_by_later_callers(self):
        order, errors, threads = [], [], []
        def worker(label):
            try:
                with self.queue.slot(time.monotonic() + 3):
                    order.append(label)
                    time.sleep(0.01)
            except Exception as exc:
                errors.append(exc)
        with self.queue.slot():
            for index, label in enumerate(("first", "second", "late"), start=1):
                thread = threading.Thread(target=worker, args=(label,))
                threads.append(thread)
                thread.start()
                self.wait_for(lambda index=index: len(self.queue.snapshot()["queue"]) == index)
        for thread in threads:
            thread.join(timeout=3)
        self.assertFalse(errors)
        self.assertEqual(order, ["first", "second", "late"])
        self.assertIsNone(self.queue.snapshot()["owner"])

    def test_processes_receive_slots_in_fifo_ticket_order(self):
        events = self.context.Queue()
        with self.queue.slot():
            for index, label in enumerate(("first", "second", "late"), start=1):
                self.start_process(label, events)
                self.wait_for(lambda index=index: len(self.queue.snapshot()["queue"]) == index)
        # Queue feeder threads may deliver process messages out of order; the
        # monotonic timestamp records admission while the worker owns the slot.
        observations = sorted((events.get(timeout=3) for _ in range(3)), key=lambda event: event[2])
        self.assertEqual([event[0] for event in observations], ["first", "second", "late"])
        self.assertTrue(all(event[1] == "entered" for event in observations))

    def test_expired_waiter_unregisters_without_blocking_successor(self):
        events = self.context.Queue()
        with self.queue.slot():
            self.start_process("expired", events, timeout=0.15)
            self.wait_for(lambda: len(self.queue.snapshot()["queue"]) == 1)
            self.start_process("survivor", events)
            self.wait_for(lambda: len(self.queue.snapshot()["queue"]) == 2)
            observation = events.get(timeout=2)
            self.assertEqual(observation[:2], ("expired", "expired"))
            self.wait_for(lambda: len(self.queue.snapshot()["queue"]) == 1)
        self.assertEqual(events.get(timeout=3)[:2], ("survivor", "entered"))

    def test_dead_owner_is_recovered_and_recovery_respects_pacing(self):
        events, hold = self.context.Queue(), self.context.Event()
        owner = self.start_process("owner", events, hold=hold, interval=0.08)
        self.assertEqual(events.get(timeout=3)[:2], ("owner", "entered"))
        self.start_process("successor", events, interval=0.08)
        self.wait_for(lambda: len(self.queue.snapshot()["queue"]) == 1)
        owner.terminate()
        owner.join(timeout=2)
        killed_at = time.monotonic()
        observation = events.get(timeout=3)
        self.assertEqual(observation[:2], ("successor", "entered"))
        self.assertGreaterEqual(observation[2] - killed_at, 0.06)
        self.wait_for(lambda: self.queue.snapshot()["owner"] is None)

    def test_killed_waiter_is_removed_before_live_successor(self):
        events = self.context.Queue()
        with self.queue.slot():
            dead = self.start_process("dead-waiter", events)
            self.wait_for(lambda: len(self.queue.snapshot()["queue"]) == 1)
            self.start_process("survivor", events)
            self.wait_for(lambda: len(self.queue.snapshot()["queue"]) == 2)
            dead.terminate()
            dead.join(timeout=2)
            self.wait_for(lambda: len(self.queue.snapshot()["queue"]) == 1)
        self.assertEqual(events.get(timeout=3)[:2], ("survivor", "entered"))

    def test_live_owner_is_not_preempted_when_its_deadline_expires(self):
        entered = threading.Event()
        def waiter():
            with self.queue.slot(time.monotonic() + 2):
                entered.set()
        with self.queue.slot(time.monotonic() + 0.1):
            thread = threading.Thread(target=waiter)
            thread.start()
            self.wait_for(lambda: len(self.queue.snapshot()["queue"]) == 1)
            time.sleep(0.15)
            self.assertIsNotNone(self.queue.snapshot()["owner"])
            self.assertFalse(entered.is_set())
        thread.join(timeout=3)
        self.assertTrue(entered.is_set())

    def test_completion_to_next_start_interval_is_enforced(self):
        queue = InferenceQueue(self.path, interval=0.06, poll_interval=0.005)
        with queue.slot():
            pass
        previous_exit = time.monotonic()
        for _ in range(2):
            with queue.slot(time.monotonic() + 2):
                self.assertGreaterEqual(time.monotonic() - previous_exit, 0.05)
            previous_exit = time.monotonic()

    def test_owner_exception_releases_slot_and_queued_timeout_does_not_leave_ticket(self):
        with self.assertRaisesRegex(RuntimeError, "failed invocation"):
            with self.queue.slot():
                raise RuntimeError("failed invocation")
        self.assertIsNone(self.queue.snapshot()["owner"])
        with self.assertRaises(QueueTimeout):
            with self.queue.slot(time.monotonic() - 1):
                self.fail("An expired caller cannot enter")
        self.assertEqual(self.queue.snapshot()["queue"], [])

    def test_head_waiter_progresses_despite_busy_control_readers(self):
        stop, entered, errors, readers = threading.Event(), threading.Event(), [], []
        def reader():
            try:
                while not stop.is_set():
                    self.queue.snapshot()
                    time.sleep(0.001)
            except Exception as exc:
                errors.append(exc)
        def waiter():
            try:
                with self.queue.slot(time.monotonic() + 2):
                    entered.set()
            except Exception as exc:
                errors.append(exc)
        try:
            with self.queue.slot():
                thread = threading.Thread(target=waiter)
                thread.start()
                self.wait_for(lambda: len(self.queue.snapshot()["queue"]) == 1)
                for _ in range(12):
                    reader_thread = threading.Thread(target=reader)
                    readers.append(reader_thread)
                    reader_thread.start()
                time.sleep(0.03)
            self.assertTrue(entered.wait(timeout=2))
            thread.join(timeout=2)
        finally:
            stop.set()
            for reader_thread in readers:
                reader_thread.join(timeout=2)
        self.assertFalse(errors)

    def test_queue_trace_proves_admission_release_and_labels_wait_deadline(self):
        trace_path = Path(self.temporary.name) / "queue-events.jsonl"
        errors = []
        with patch.dict("os.environ", {"A2A_QUEUE_TRACE": str(trace_path)}):
            queue = InferenceQueue(self.path, interval=0, poll_interval=0.005)
            with queue.slot():
                def waiter():
                    try:
                        with queue.slot(time.monotonic() + 0.03):
                            self.fail("Waiting caller should expire while the owner is active")
                    except QueueTimeout as exc:
                        errors.append(str(exc))
                thread = threading.Thread(target=waiter)
                thread.start()
                thread.join(timeout=2)
        records = [json.loads(line) for line in trace_path.read_text().splitlines()]
        self.assertEqual(len([r for r in records if r["event"] == "admitted"]), 1)
        self.assertEqual(len([r for r in records if r["event"] == "released"]), 1)
        expired = [r for r in records if r["event"] == "deadline_expired"]
        self.assertEqual(len(expired), 1)
        self.assertIn("FIFO admission", expired[0]["detail"])
        self.assertTrue(errors)
        self.assertIsNone(queue.snapshot()["owner"])
        self.assertEqual(queue.snapshot()["queue"], [])


if __name__ == "__main__":
    unittest.main()
