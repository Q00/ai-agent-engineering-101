"""TEST ONLY: central gap configuration and mocked model boundaries."""
from contextlib import nullcontext
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import benchmark_transport
import harness
import inference_queue


class InferenceGapTests(unittest.TestCase):
    def resolved_slot(self, environment, *, explicit=None):
        with patch.dict("os.environ", environment, clear=True), patch.object(
                inference_queue, "InferenceQueue") as queue:
            queue.return_value.slot.return_value = nullcontext("test-ticket")
            with inference_queue.inference_slot(100, state_path="unused-test-path", interval=explicit) as ticket:
                self.assertEqual(ticket, "test-ticket")
            queue.return_value.slot.assert_called_once_with(100)
            return queue.call_args.kwargs["interval"]

    def test_absent_environment_uses_default_five_seconds(self):
        self.assertEqual(self.resolved_slot({}), 5)

    def test_environment_controls_shared_gap_including_zero(self):
        for raw, expected in (("5", 5), ("2.5", 2.5), ("0", 0), ("10", 10)):
            with self.subTest(raw=raw):
                self.assertEqual(self.resolved_slot({"A2A_INFERENCE_GAP_SECONDS": raw}), expected)

    def test_explicit_interval_overrides_environment_without_changing_existing_test_policy(self):
        self.assertEqual(self.resolved_slot({"A2A_INFERENCE_GAP_SECONDS": "nan"}, explicit=0.025), 0.025)

    def test_invalid_environment_rejected_before_queue_creation(self):
        for value in ("-1", "nan", "inf", "-inf", "", "not-a-number"):
            with self.subTest(value=value), patch.dict("os.environ", {"A2A_INFERENCE_GAP_SECONDS": value}), patch.object(
                    inference_queue, "InferenceQueue") as queue:
                with self.assertRaisesRegex(ValueError, "finite.*nonnegative"):
                    with inference_queue.inference_slot():
                        self.fail("Invalid configuration must never admit an inference")
                queue.assert_not_called()

    def test_invalid_explicit_intervals_still_fail(self):
        for value in (-1, float("nan"), float("inf"), True, "5", 10**1000):
            with self.subTest(value=str(value)[:20]), self.assertRaises(ValueError):
                with inference_queue.inference_slot(interval=value):
                    self.fail("An invalid explicit interval must never admit an inference")

    def test_harness_uses_central_gap_policy_without_hardcoding_an_interval(self):
        with patch.object(harness, "inference_slot", return_value=nullcontext("test-ticket")) as slot:
            with harness.invocation_slot(100):
                pass
        slot.assert_called_once_with(100)

    def test_invalid_gap_stops_actual_harness_before_subprocess_creation(self):
        for raw in ("-1", "nan"):
            with self.subTest(raw=raw), tempfile.TemporaryDirectory() as directory:
                trace = SimpleNamespace(write=Mock(), directory=Path(directory), role="test-only", run_id="test-run")
                with patch.dict("os.environ", {"A2A_INFERENCE_GAP_SECONDS": raw}), patch.object(
                        harness.subprocess, "Popen") as process:
                    with self.assertRaisesRegex(harness.HarnessError, "finite.*nonnegative"):
                        harness.Harness("buyer", trace).decide({"test_only": True}, attempts=1)
                    process.assert_not_called()

    def test_invalid_gap_stops_benchmark_transport_before_subprocess_creation(self):
        for raw in ("-1", "nan"):
            with self.subTest(raw=raw), tempfile.TemporaryDirectory() as directory, patch.dict(
                    "os.environ", {"A2A_INFERENCE_GAP_SECONDS": raw}), patch.object(
                    benchmark_transport.subprocess, "Popen") as process:
                with self.assertRaisesRegex(ValueError, "finite.*nonnegative"):
                    benchmark_transport.invoke([{"role": "user", "content": "TEST ONLY"}],
                        source="test-only", original_request={"test_only": True},
                        codex=sys.executable, trace_dir=directory)
                process.assert_not_called()
                self.assertEqual(list(Path(directory).iterdir()), [])

    def test_low_level_default_and_queue_trace_record_five_second_policy(self):
        with tempfile.TemporaryDirectory() as directory:
            trace_path = Path(directory) / "events.jsonl"
            with patch.dict("os.environ", {"A2A_QUEUE_TRACE": str(trace_path)}, clear=True):
                queue = inference_queue.InferenceQueue(Path(directory) / "state.json")
                self.assertEqual(queue.interval, 5)
                with queue.slot():
                    pass
            records = [json.loads(line) for line in trace_path.read_text().splitlines()]
            self.assertEqual([record["event"] for record in records], ["enqueued", "admitted", "released"])
            self.assertTrue(all(record["interval_seconds"] == 5 for record in records))


if __name__ == "__main__":
    unittest.main()
