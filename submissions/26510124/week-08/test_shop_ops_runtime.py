"""No real processes: operations reload/retries must leave the seller untouched."""
from contextlib import redirect_stdout
import io
import unittest
from unittest.mock import Mock, patch

import shop_runtime


class Loops:
    def __init__(self, count):
        self.index, self.count = 0, count

    def is_set(self):
        return self.index >= self.count

    def wait(self, seconds):
        self.index += 1

    def clock(self):
        return self.index * 40


def process(pid):
    p = Mock(pid=pid)
    p.poll.return_value = None
    return p


class OperationsSupervisorTests(unittest.TestCase):
    def run_supervisor(self, launch, stop, cfg, *, legacy=False, cleanup=None):
        cleanup = cleanup or Mock()
        with patch("shop_runtime.config_at", return_value={"enabled": legacy}), redirect_stdout(io.StringIO()):
            result = shop_runtime.supervise(9999, stop=stop, launch=launch, cleanup=cleanup,
                                           clock=stop.clock, ops_config=cfg)
        self.assertEqual(result, 0)
        self.assertEqual(sum(call.args[0] == "seller_r3.py" for call in launch.call_args_list), 1)
        return cleanup

    def test_enable_disable_enable_restarts_only_operator(self):
        seller, first, second = process(1), process(2), process(3)
        launch = Mock(side_effect=[seller, first, second])
        cfg = Mock(side_effect=[{"enabled": True}, {"enabled": False}, {"enabled": True}])
        cleanup = self.run_supervisor(launch, Loops(3), cfg)
        self.assertEqual([c.args for c in launch.call_args_list],
                         [("seller_r3.py", "--port", "9999"), ("shop_operations.py",), ("shop_operations.py",)])
        self.assertEqual(cleanup.call_args_list[0].args, ([first],))
        self.assertEqual(cleanup.call_args_list[-1].args, ([seller, second],))

    def test_invalid_config_stops_only_operator_and_recovers_on_valid_config(self):
        seller, first, second = process(1), process(2), process(3)
        launch = Mock(side_effect=[seller, first, second])
        cfg = Mock(side_effect=[{"enabled": True}, ValueError("bad JSON"), {"enabled": True}])
        cleanup = self.run_supervisor(launch, Loops(3), cfg)
        self.assertEqual(cleanup.call_args_list[0].args, ([first],))
        self.assertEqual(cfg.call_count, 3)
        self.assertEqual(launch.call_count, 3)

    def test_crashed_operator_waits_sixty_seconds_without_restarting_seller(self):
        seller, failed, recovered = process(1), process(2), process(3)
        failed.poll.return_value = 1
        stop = Loops(4)
        starts = []
        results = iter([seller, failed, recovered])
        def start(*args):
            starts.append((args[0], stop.clock()))
            return next(results)
        launch = Mock(side_effect=start)
        cleanup = self.run_supervisor(launch, stop, lambda: {"enabled": True})
        self.assertEqual(starts, [("seller_r3.py", 0), ("shop_operations.py", 0), ("shop_operations.py", 120)])
        self.assertEqual(cleanup.call_args_list[0].args, ([failed],))

    def test_launch_failure_backoff_and_bad_enable_flag_never_stop_seller(self):
        seller, operator = process(1), process(2)
        launch = Mock(side_effect=[seller, OSError("missing operator"), operator])
        cfg = Mock(side_effect=[{"enabled": True}, {"enabled": "yes"}, {"enabled": True}])
        self.run_supervisor(launch, Loops(3), cfg)
        self.assertEqual(launch.call_count, 3)

    def test_legacy_and_operations_analysts_never_start_together(self):
        seller, analyst = process(1), process(2)
        launch = Mock(side_effect=[seller, analyst])
        self.run_supervisor(launch, Loops(2), lambda: {"enabled": True}, legacy=True)
        self.assertEqual([c.args[0] for c in launch.call_args_list], ["seller_r3.py", "sales_advisor.py"])


if __name__ == "__main__":
    unittest.main()
