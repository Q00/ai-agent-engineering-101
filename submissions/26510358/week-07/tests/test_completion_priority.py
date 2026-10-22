"""TEST ONLY: contention and recovery over the actual process/kernel boundary."""
from contextlib import nullcontext
import json
import multiprocessing
from pathlib import Path
import queue
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import harness
from completion_priority import SellerAdmission
from inference_pool import defer_pool, pool_slot
from inference_queue import QueueTimeout


def process_worker(directory, label, events, *, priority="normal", role="seller", hold=None,
                   capacity=2, timeout=5, fail=False):
    try:
        with pool_slot(role, directory=directory, capacity=capacity, priority=priority,
                       deadline=time.monotonic() + timeout):
            events.put((label, "entered", time.monotonic()))
            if fail:
                raise KeyboardInterrupt("TEST ONLY cancellation")
            if hold is not None:
                hold.wait(10)
    except QueueTimeout:
        events.put((label, "expired", time.monotonic()))
    except KeyboardInterrupt:
        events.put((label, "cancelled", time.monotonic()))
    except BaseException as exc:
        events.put((label, "error", repr(exc)))
        raise


class CompletionPriorityProcessTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)
        self.admission = SellerAdmission(self.directory)
        self.context = multiprocessing.get_context("fork")
        self.events = self.context.Queue()
        self.children = []

    def tearDown(self):
        for child in self.children:
            if child.is_alive():
                child.terminate()
            child.join(timeout=2)
        self.temporary.cleanup()

    def start(self, label, **kwargs):
        child = self.context.Process(target=process_worker,
            args=(str(self.directory), label, self.events), kwargs=kwargs)
        self.children.append(child)
        child.start()
        return child

    def wait_queue(self, count):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if len(self.admission.snapshot()["queue"]) == count:
                return
            time.sleep(0.005)
        self.fail(f"Expected {count} seller waiters: {self.admission.snapshot()}")

    def observed(self, count):
        return sorted((self.events.get(timeout=4) for _ in range(count)), key=lambda event: event[2])

    def test_completion_overtakes_queued_quotes_and_burst_bounds_normal_wait(self):
        release = self.context.Event()
        self.start("running-quote", hold=release)
        self.assertEqual(self.events.get(timeout=3)[:2], ("running-quote", "entered"))
        for index, (label, priority) in enumerate((("normal-1", "normal"),
                ("normal-2", "normal"), ("complete-1", "completion"),
                ("complete-2", "completion"), ("complete-3", "completion")), start=1):
            self.start(label, priority=priority)
            self.wait_queue(index)
        # Priority never interrupts the model that already owns the lane.
        with self.assertRaises(queue.Empty):
            self.events.get(timeout=0.04)
        release.set()
        observations = self.observed(5)
        self.assertEqual([entry[:2] for entry in observations], [
            ("complete-1", "entered"), ("complete-2", "entered"), ("normal-1", "entered"),
            ("complete-3", "entered"), ("normal-2", "entered")])
        self.wait_queue(0)

    def test_aged_normal_precedes_new_completion_at_next_lane(self):
        # Set an old waiting timestamp under the real control lock instead of
        # spending fifteen seconds sleeping. Production has no aging override.
        release = self.context.Event()
        self.start("running", hold=release)
        self.assertEqual(self.events.get(timeout=3)[:2], ("running", "entered"))
        self.start("aged-normal")
        self.wait_queue(1)
        with self.admission._control():
            state = self.admission._load()
            state["queue"][0]["enqueued_at"] = time.monotonic() - 16
            self.admission._save(state)
        self.start("completion", priority="completion")
        self.wait_queue(2)
        release.set()
        self.assertEqual([event[0] for event in self.observed(2)], ["aged-normal", "completion"])

    def test_expired_completion_is_withdrawn_and_does_not_block_quote(self):
        release = self.context.Event()
        self.start("running", hold=release)
        self.assertEqual(self.events.get(timeout=3)[:2], ("running", "entered"))
        self.start("expired", priority="completion", timeout=0.12)
        self.wait_queue(1)
        self.start("quote")
        self.wait_queue(2)
        self.assertEqual(self.events.get(timeout=3)[:2], ("expired", "expired"))
        self.wait_queue(1)
        release.set()
        self.assertEqual(self.events.get(timeout=3)[:2], ("quote", "entered"))
        self.wait_queue(0)

    def test_aged_quote_backlog_cannot_starve_completion_across_two_lanes(self):
        release = self.context.Event()
        self.start("running-1", hold=release, capacity=3)
        self.start("running-2", hold=release, capacity=3)
        self.assertEqual({event[0] for event in self.observed(2)}, {"running-1", "running-2"})
        normals = [f"aged-normal-{index}" for index in range(5)]
        completions = [f"completion-{index}" for index in range(3)]
        for index, label in enumerate(normals + completions, start=1):
            self.start(label, capacity=3,
                       priority="completion" if label in completions else "normal")
            self.wait_queue(index)
        with self.admission._control():
            state = self.admission._load()
            for entry in state["queue"]:
                if entry["priority"] == "normal":
                    entry["enqueued_at"] = time.monotonic() - 16
            self.admission._save(state)
        release.set()
        labels = [event[0] for event in self.observed(len(normals) + len(completions))]
        self.assertEqual([label for label in labels if label in normals], normals)
        self.assertEqual([label for label in labels if label in completions], completions)
        # Aging changes admission opportunities, never interrupts running work
        # or promises a wall-clock maximum. While both queues have work, neither
        # may monopolize the next two available seller lane opportunities.
        self.assertEqual(labels[:6], [value for pair in zip(normals, completions) for value in pair])
        self.wait_queue(0)

    def test_killed_completion_waiter_is_pruned(self):
        release = self.context.Event()
        self.start("running", hold=release)
        self.assertEqual(self.events.get(timeout=3)[:2], ("running", "entered"))
        killed = self.start("killed", priority="completion")
        self.wait_queue(1)
        self.start("survivor")
        self.wait_queue(2)
        killed.kill()
        killed.join(timeout=2)
        self.wait_queue(1)
        release.set()
        self.assertEqual(self.events.get(timeout=3)[:2], ("survivor", "entered"))

    def test_killed_or_cancelled_owner_releases_kernel_lane(self):
        release = self.context.Event()
        killed = self.start("killed-owner", hold=release, priority="completion")
        self.assertEqual(self.events.get(timeout=3)[:2], ("killed-owner", "entered"))
        self.start("survivor", priority="completion", fail=True)
        self.wait_queue(1)
        killed.kill()
        killed.join(timeout=2)
        self.assertEqual(self.events.get(timeout=3)[:2], ("survivor", "entered"))
        self.assertEqual(self.events.get(timeout=3)[:2], ("survivor", "cancelled"))
        with pool_slot("seller", directory=self.directory, capacity=2,
                       deadline=time.monotonic() + 0.4):
            pass
        self.wait_queue(0)

    def test_two_sellers_preserve_third_lane_for_buyer(self):
        release = self.context.Event()
        self.start("seller-1", hold=release, capacity=3)
        self.start("seller-2", hold=release, capacity=3, priority="completion")
        self.assertEqual({event[0] for event in self.observed(2)}, {"seller-1", "seller-2"})
        self.start("seller-waiter", capacity=3, priority="completion")
        self.wait_queue(1)
        self.start("buyer", role="buyer", capacity=3)
        self.assertEqual(self.events.get(timeout=3)[:2], ("buyer", "entered"))
        with self.assertRaises(queue.Empty):
            self.events.get(timeout=0.04)
        release.set()
        self.assertEqual(self.events.get(timeout=3)[:2], ("seller-waiter", "entered"))

    def test_completion_never_bypasses_shared_provider_cooldown(self):
        defer_pool(0.15, directory=self.directory)
        started = time.monotonic()
        self.start("completion", priority="completion", timeout=0.07)
        self.assertEqual(self.events.get(timeout=3)[:2], ("completion", "expired"))
        self.assertGreaterEqual(time.monotonic() - started, 0.06)
        self.wait_queue(0)
        self.start("after-cooldown", priority="completion")
        entry = self.events.get(timeout=3)
        self.assertEqual(entry[:2], ("after-cooldown", "entered"))
        self.assertGreaterEqual(entry[2] - started, 0.13)

    def test_recovery_remains_serial_across_completion_and_buyer(self):
        defer_pool(0, directory=self.directory)
        release = self.context.Event()
        self.start("completion", priority="completion", hold=release, capacity=3)
        self.assertEqual(self.events.get(timeout=3)[:2], ("completion", "entered"))
        self.start("buyer", role="buyer", capacity=3, timeout=0.1)
        self.assertEqual(self.events.get(timeout=3)[:2], ("buyer", "expired"))
        release.set()


class CompletionPriorityHarnessTests(unittest.TestCase):
    def test_invocation_forwards_completion_but_preserves_default_adapter_shapes(self):
        with patch.object(harness, "pool_slot", return_value=nullcontext()) as pool:
            with harness.invocation_slot(123, "seller", priority="completion"):
                pass
            pool.assert_called_once_with("seller", 123, priority="completion")
        with patch.object(harness, "pool_slot", return_value=nullcontext()) as pool:
            with harness.invocation_slot(123, "buyer"):
                pass
            pool.assert_called_once_with("buyer", 123)
        with patch.object(harness, "inference_slot", return_value=nullcontext()) as legacy:
            with harness.invocation_slot(123):
                pass
            legacy.assert_called_once_with(123)

    def test_harness_priority_is_explicit_and_does_not_read_remote_metadata(self):
        for requested, expected in (("normal", {}), ("completion", {"priority": "completion"})):
            with self.subTest(requested=requested), tempfile.TemporaryDirectory() as directory:
                trace = SimpleNamespace(write=Mock(), directory=Path(directory),
                                        role="test-only", run_id="test")
                def process(cmd, **kwargs):
                    output = Path(cmd[cmd.index("--output-last-message") + 1])
                    output.write_text(json.dumps({"action": "reject", "price": None, "offer": None,
                                                 "item_id": "lamp", "reason": "TEST ONLY"}))
                    result = Mock(returncode=0)
                    result.communicate.return_value = ("", "")
                    return result
                with patch.object(harness, "invocation_slot", return_value=nullcontext()) as slot, \
                        patch.object(harness.subprocess, "Popen", side_effect=process):
                    harness.Harness("seller", trace).decide({"metadata": {"priority": "completion"}},
                        priority=requested, attempts=1)
                slot.assert_called_once_with(None, "seller", **expected)

    def test_invalid_priority_fails_before_model_or_pool_creation(self):
        for priority in ("urgent", None, True):
            with self.subTest(priority=priority), patch.object(harness.subprocess, "Popen") as process:
                with self.assertRaises(ValueError):
                    harness.Harness("seller", SimpleNamespace(write=Mock())).decide({}, priority=priority)
                process.assert_not_called()
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                with pool_slot("seller", directory=directory, priority="urgent"):
                    self.fail("Unknown priorities cannot enter a lane")
            self.assertFalse(list(Path(directory).iterdir()))


if __name__ == "__main__":
    unittest.main()
