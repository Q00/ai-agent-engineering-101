"""Offline regression seams authorized by the remediation brief."""
import os
import tempfile
import threading
import time
import subprocess
import sys
from pathlib import Path
import unittest
from unittest.mock import patch
import buyer_quote_alignment as a
from tools.quote_alignment_measure import summarize

ENV = {"BUYER_QUOTE_ALIGN":"1", "BUYER_QUOTE_ALIGN_START":"1000", "BUYER_QUOTE_ALIGN_STUDY":"s"}
CTX = {"study":"s","start":1000.0,"end":2800.0,"order":"o","arm":"B","path":"/unused"}

def read(**kw):
    return a.read_quote(CTX, "Our ask: 8510", {"sku":"G12345678","list_price":10640},
                        None,None,shop="s",open_=True,refused=False,late=False,**kw)

class Remediation(unittest.TestCase):
    def test_io_timeout_returns_and_never_late_publishes(self):
        entered, release = threading.Event(), threading.Event()
        def blocked(*args):
            entered.set()
            release.wait()
            return True
        try:
            with patch.object(a,"append",side_effect=blocked):
                start=time.monotonic()
                self.assertIsNone(read(env=ENV,now=1001))
                self.assertLess(time.monotonic()-start, .6)
                self.assertTrue(entered.is_set())
                self.assertIsNone(a.reserve("/unused",{"order":"o"},now=1001,env=ENV))
                release.set()
        finally:
            release.set()

    def test_post_io_clock_and_off_are_revalidated(self):
        for change in ("clock", "off", "study"):
            env=dict(ENV)
            clock=[1001.]
            def append(*args):
                if change=="clock": clock[0]=2800.
                if change=="off": env["BUYER_QUOTE_ALIGN"]="0"
                if change=="study": env["BUYER_QUOTE_ALIGN_STUDY"]="other"
                return True
            with self.subTest(change=change), patch.object(a.time,"time",side_effect=lambda:clock[0]), patch.object(a,"append",side_effect=append):
                self.assertIsNone(read(env=env))

    def test_source_bootstrap_and_inflight_off_without_environment_reload(self):
        with tempfile.TemporaryDirectory() as md:
            policy=Path(md)/"policy.py"
            def write(enabled=True, study="s"):
                policy.write_text('SOURCE_CONTROL = True\nENABLED = '+str(enabled)+'\nSTART = 1000\nSTUDY = '+repr(study)+'\n')
            write()
            with patch.object(a,"POLICY_PATH",str(policy)), patch.object(a.time,"time",return_value=1001):
                # Empty inherited environment: each new order still sees source policy.
                context=a.reserve(md,{"order":"o"},env={})
                self.assertIsNotNone(context)
                self.assertEqual(a.parameters({}),("s",1000.,2800.))
                def switch_off(*args):
                    write(False)
                    return True
                with patch.object(a,"append",side_effect=switch_off):
                    self.assertIsNone(read(env={}))
                self.assertIsNone(a.reserve(md,{"order":"next"},env=ENV))
                write(study="other")
                self.assertIsNone(read(env={}))
                policy.write_text("SOURCE_CONTROL = call()")
                self.assertIsNone(a.parameters(ENV))

    def test_daemon_writer_cannot_hold_process_exit(self):
        code = """import threading
import buyer_quote_alignment as a
a.append=lambda *args: threading.Event().wait()
assert a.bounded_append('/unused', {}) is False
"""
        run=subprocess.run([sys.executable,"-c",code],capture_output=True,timeout=2)
        self.assertEqual(run.returncode,0,run.stderr)

    def test_decision_lock_free_during_io_and_closed_after_io(self):
        import market_buyer as mb
        from test_buyer_reply import task
        entered, release=threading.Event(),threading.Event()
        ctl=mb.quote_ctl()
        out={}
        def append(*args):
            entered.set()
            release.wait(2)
            return True
        shop={"name":"fixture","url":"https://fixture.invalid","_offer":{"sku":"G12345678","list_price":10640}}
        card={"supportedInterfaces":[{"protocolBinding":"JSONRPC","protocolVersion":"1.0","url":"fixture"}]}
        with patch.dict(os.environ,ENV), patch.object(a.time,"time",return_value=1001), patch.object(a,"append",side_effect=append), patch.object(mb,"serves_acp",return_value=True), patch.object(mb.httpx,"get",return_value=type("Reply",(),{"json":lambda self:card})()), patch.object(mb,"buyer_send",return_value=task("Our ask: 8510")), patch.object(mb,"cancel_quietly"):
            t=threading.Thread(target=mb.quote,args=(shop,"fixture","G12345678",{"_quote_alignment":CTX},out,ctl))
            t.start()
            try:
                self.assertTrue(entered.wait(1))
                acquired=ctl["lock"].acquire(timeout=.05)
                self.assertTrue(acquired)
                if acquired:
                    ctl.update(done=True,winner=None)
                    ctl["lock"].release()
                release.set()
                t.join(1)
                self.assertFalse(t.is_alive())
                self.assertIsNone(out["fixture"]["ask"])
                self.assertTrue(out["fixture"]["closed"])
            finally:
                release.set()
                t.join(2)

    def test_real_flock_and_late_journal_is_only_intent(self):
        import fcntl
        import json
        with tempfile.TemporaryDirectory() as md:
            path=Path(md)/"journal"
            with path.open("a") as f:
                fcntl.flock(f.fileno(),fcntl.LOCK_EX)
                context=dict(CTX,path=str(path))
                start=time.monotonic()
                with patch.object(a.time,"time",return_value=1001):
                    result=a.read_quote(context,"Our ask: 8510",{"sku":"G12345678","list_price":10640},
                                        None,None,shop="s",open_=True,refused=False,late=False,env=ENV)
                self.assertIsNone(result)
                self.assertLess(time.monotonic()-start,.6)
                fcntl.flock(f.fileno(),fcntl.LOCK_UN)
            # A subsequent bounded append waits behind the prior flock writer.
            self.assertTrue(a.bounded_append(str(path),{"event":"barrier"}))
            rows=[json.loads(line) for line in path.read_text().splitlines()]
            self.assertTrue(any(row.get("intended_applied") for row in rows))
            self.assertFalse(any(row.get("applied") for row in rows))

    def test_sparse_turns_distinguish_missing(self):
        assignment={"event":"assignment","study":"s","order":"o","arm":"B","at":1001,"start":1000,"end":2800}
        for turns,missing in [({},1),({"other":2},1),({"code":2},0),({"harness":1},0),({"code":True},1),({"code":-1},1)]:
            with self.subTest(turns=turns):
                r=summarize([assignment],[{"order":"o","turns":turns}],[],[],1000,2800,3100,"s")["arms"]["B"]
                self.assertEqual(r["missing_turns"],missing)
                self.assertEqual(r["harness_turns_per_itt"], None if missing else float(turns.get("harness",0)))
