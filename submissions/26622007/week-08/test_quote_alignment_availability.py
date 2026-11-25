"""Offline public IO boundary regressions for policy and journal availability."""
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import buyer_quote_alignment as a

class Availability(unittest.TestCase):
    def test_real_policy_fifo_is_bounded_and_process_exits(self):
        with tempfile.TemporaryDirectory() as md:
            path = os.path.join(md, "policy")
            os.mkfifo(path)
            code = """import sys, time
import buyer_quote_alignment as a
import os, threading
if len(sys.argv) > 2:
    def writer():
        fd = os.open(sys.argv[1], os.O_WRONLY)
        threading.Event().wait()
    threading.Thread(target=writer, daemon=True).start()
a.POLICY_PATH=sys.argv[1]
t=time.monotonic()
assert a.parameters({}) is None
assert time.monotonic()-t < .6
assert a.reserve('/unused', {'order':'o'}, env={}) is None
"""
            for mode in ([], ["read"]):
                run = subprocess.run([sys.executable, "-c", code, path, *mode],
                                     capture_output=True, timeout=2)
                self.assertEqual(run.returncode, 0, run.stderr)

    def test_thread_construction_and_start_failure_recover_slots(self):
        for where in ("constructor", "start"):
            with self.subTest(where=where):
                target = patch.object(a.threading, "Thread", side_effect=RuntimeError("unavailable")) if where == "constructor" else patch.object(a.threading.Thread, "start", side_effect=RuntimeError("unavailable"))
                with target:
                    for _ in range(16):
                        self.assertFalse(a.bounded_append("/unused", {}))
                        self.assertIsNone(a.parameters({}))
                with tempfile.TemporaryDirectory() as md:
                    self.assertTrue(a.bounded_append(os.path.join(md, "journal"), {}))

    def test_policy_saturation_shared_with_journal_and_recovers(self):
        entered, release = threading.Event(), threading.Event()
        original_open = open
        workers = []
        def blocked(*args, **kwargs):
            if args[0] == a.POLICY_PATH:
                workers.append(threading.current_thread())
                entered.set()
                release.wait()
            return original_open(*args, **kwargs)
        try:
            with patch("builtins.open", side_effect=blocked):
                for _ in range(8):
                    self.assertIsNone(a.parameters({}))
                self.assertEqual(len(workers), 8)
                t = time.monotonic()
                self.assertIsNone(a.parameters({}))
                self.assertFalse(a.bounded_append("/unused", {}))
                self.assertLess(time.monotonic()-t, .1)
                release.set()
                for worker in workers:
                    worker.join(2)
        finally:
            release.set()
        with tempfile.TemporaryDirectory() as md:
            self.assertTrue(a.bounded_append(os.path.join(md, "journal"), {}))
