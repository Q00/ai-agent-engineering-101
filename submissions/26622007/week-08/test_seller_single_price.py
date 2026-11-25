"""Public outgoing wording seam; all state isolated in TemporaryDirectory."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import negotiate
import seller_single_price as sp

class SinglePriceTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.r=sp.SinglePrice(self.root)
        self.cfg=sp.trial_config("single-price-test",100,1900,"seed")
        self.r.root.mkdir(parents=True)
        self.save(self.cfg)
    def save(self,c):
        (self.r.root/"config.json").write_text(json.dumps(c))
    def task(self,arm):
        for i in range(100):
            t=str(i)
            if sp.task_arm(t,self.cfg)==arm:return t
        self.fail("no arm")
    def rows(self):
        return [json.loads(l) for l in (self.r.root/"trace.jsonl").read_text().splitlines() if l.strip() and json.loads(l).get("kind")=="intent"]
    def test_exact_echo_only_price_and_final_stance_preserved(self):
        before="Thanks for your offer of 9000. This is already the lowest price we can sell it for. This is our final price. My price: 10000."
        after="Thanks for your offer. This is already the lowest price we can sell it for. This is our final price. My price: 10000."
        self.assertEqual(self.r.render(self.task("B"),before,10000,now=100),after)
        self.assertEqual(self.r.render(self.task("A"),before,10000,now=101),before)
    def test_actual_generated_lines_have_same_decision_price_and_history(self):
        for source in (negotiate.LINES,negotiate.HOLD,negotiate.FINAL):
            for line in source:
                with self.subTest(line=line):
                    deal=dict(reserve=10000,ask=15000,rounds=1,history=[("you asked",15000)])
                    original=copy.deepcopy(deal)
                    floor=16000 if source is negotiate.HOLD else 10000
                    with mock.patch.object(negotiate,"counter_price",return_value=15000 if source is negotiate.HOLD else (10000 if source is negotiate.FINAL else 13000)),mock.patch.object(negotiate.random,"choice",return_value=line):
                        d=negotiate.seller_turn("9000",deal,floor)
                    self.assertEqual(deal,original)
                    p=d["price"];suffix=" This is our final price." if p<=deal["reserve"] else ""
                    text=d["message"]+suffix+f" My price: {p}."
                    b=self.r.render(self.task("B"),text,p,now=110)
                    self.assertNotIn("9000",b)
                    self.assertTrue(b.endswith(f"My price: {p}."))
                    self.assertEqual(suffix in b, suffix in text)
                    self.assertEqual(d["action"],"counter")
                    self.assertEqual(d["buyer_offer"],9000)
    def test_nonallowed_facts_fair_questions_and_rejects_are_unchanged(self):
        examples=[
            "Thanks for your offer of 9000. I can come down a little. Warranty: 6 hours. My price: 10000.",
            "Thanks for your offer of 9000. I can come down a little. Certificate attached. My price: 10000.",
            "Thanks for your offer of 9000. I can come down a little. My price: 10001.",
            "Thank you for your offer of 9000. We are so sorry we could not meet your budget this time! No deal.",
            "Please tell me the price you offer. My price: 10000.",
            "You offered more than our price. Our price is only 10000; send it to close the deal. My price: 10000.",
            "Thanks for your offer of 9000. I can come down a little. Delivery tomorrow? My price: 10000."]
        for s in examples:self.assertEqual(self.r.render(self.task("B"),s,10000,now=120),s)
        self.assertTrue(all(not r["eligible"] and r["effective_arm"]=="unknown" for r in self.rows()))
    def test_render_clock_boundaries_and_late_b_control(self):
        t=self.task("B");s="I appreciate the offer of 9000, but this price stays. My price: 10000."
        for now in (99,1900,2000):
            self.assertEqual(self.r.render(t,s,10000,now=now),s)
        self.assertNotEqual(self.r.render(t,s,10000,now=100),s)
        self.assertNotEqual(self.r.render(t,s,10000,now=1899.9),s)
        self.assertEqual(self.r.render(t,s,10000,now=1900),s)
        self.assertEqual(self.rows()[-1]["itt_arm"],"B")
        self.assertEqual(self.rows()[-1]["effective_arm"],"unknown")
        (self.r.root/"disabled").touch()
        self.assertEqual(self.r.render(t,s,10000,now=101),s)
    def test_invalid_missing_future_and_config_changes_fail_closed(self):
        s="Thank you for 9000. I can meet you partway. My price: 10000.";t=self.task("B")
        self.assertNotEqual(self.r.render(t,s,10000,now=100),s)
        for c in ({},dict(self.cfg,enabled=False),dict(self.cfg,start=200,end=2000),dict(self.cfg,salt="changed"),dict(self.cfg,end=1901),dict(self.cfg,start=float("nan")),dict(self.cfg,start=True)):
            self.save(c)
            self.assertEqual(self.r.render(t,s,10000,now=101),s)
        (self.r.root/"config.json").unlink()
        self.assertEqual(self.r.render(t,s,10000,now=101),s)
        self.save(self.cfg)
        for now in (None,float("nan"),float("inf"),True):
            if now is None:continue
            self.assertEqual(self.r.render(t,s,10000,now=now),s)
        self.assertEqual(self.r.render(t,s,10000,now=99),s)
    def test_all_itt_immutable_no_legacy_cap_and_hash_only_trace(self):
        s="Opening quote with warranty facts. My price: 10000."
        for i in range(250):
            self.assertEqual(self.r.render(f"task-{i}",s,10000,now=110),s)
        rows=self.rows()
        self.assertEqual(len(rows),250)
        self.assertTrue(all(r["itt_arm"] in ("A","B") and not r["eligible"] for r in rows))
        b="Thank you for 9000. I can meet you partway. My price: 10000."
        self.r.render("task-249",b,10000,now=120)
        self.assertEqual(self.rows()[-1]["itt_arm"],rows[-1]["itt_arm"])
        raw=(self.r.root/"trace.jsonl").read_text()
        self.assertNotIn("task-249",raw);self.assertNotIn("warranty",raw);self.assertNotIn("Thank you",raw)
    def test_sink_and_db_failures_never_change_response_or_propagate(self):
        t=self.task("B");s="Thank you for 9000. I can meet you partway. My price: 10000."
        with mock.patch.object(self.r,"record",side_effect=OSError("sink")):
            self.assertEqual(self.r.render(t,s,10000,now=100),s)
        with mock.patch.object(sp.sqlite3,"connect",side_effect=OSError("db")):
            self.assertEqual(self.r.render(t,s,10000,now=100),s)
    def test_epoch_reuse_new_tasks_and_db_cleanup_fail_closed(self):
        s="Thank you for 9000. I can meet you partway. My price: 10000."
        self.assertNotEqual(self.r.render(self.task("B"),s,10000,now=100),s)
        self.save(dict(self.cfg,start=200,end=2000))
        self.assertEqual(self.r.render("new-task-after-epoch-edit",s,10000,now=300),s)
        self.save(self.cfg)
        real=sp.sqlite3.connect
        class BadClose:
            def __init__(self,*a,**k):self.db=real(*a,**k)
            def __enter__(self):return self
            def __exit__(self,*args):return self.db.__exit__(*args)
            def __getattr__(self,k):return getattr(self.db,k)
            def close(self):
                self.db.close()
                raise OSError("cleanup")
        with mock.patch.object(sp.sqlite3,"connect",side_effect=BadClose):
            self.assertEqual(self.r.render(self.task("B"),s,10000,now=110),s)
    def test_production_clock_rechecked_after_enrollment_crosses_end(self):
        s="Thank you for 9000. I can meet you partway. My price: 10000."
        with mock.patch.object(sp.time,"time",side_effect=[1899,1900]):
            self.assertEqual(self.r.render(self.task("B"),s,10000),s)
        self.assertEqual(self.rows()[-1]["effective_arm"],"unknown")
    def test_final_selection_after_all_blocking_trace_and_config_io(self):
        s="Thank you for 9000. I can meet you partway. My price: 10000."
        t=self.task("B")
        for boundary in ("record", "config"):
            for change in ("end","off","missing","edited"):
                with self.subTest(boundary=boundary,change=change):
                    self.save(self.cfg)
                    (self.r.root/"disabled").unlink(missing_ok=True)
                    clock=[1899.9]
                    real=getattr(self.r,boundary)
                    calls=[0]
                    def delay(*args):
                        result=real(*args);calls[0]+=1
                        if boundary=="record" or calls[0]==2:
                            if change=="end":clock[0]=1900.1
                            elif change=="off":(self.r.root/"disabled").touch()
                            elif change=="missing":(self.r.root/"config.json").unlink(missing_ok=True)
                            else:self.save(dict(self.cfg,salt="edited"))
                        return result
                    with mock.patch.object(sp.time,"time",side_effect=lambda:clock[0]),mock.patch.object(self.r,boundary,side_effect=delay):
                        self.assertEqual(self.r.render(t,s,10000),s)

    def test_append_then_close_failure_never_certifies_a_wrong_return(self):
        s="Thank you for 9000. I can meet you partway. My price: 10000."
        close=sp.os.close
        def bad_close(fd):
            close(fd)
            raise OSError("close failed after append")
        with mock.patch.object(sp.os,"close",side_effect=bad_close):
            self.assertEqual(self.r.render(self.task("B"),s,10000,now=110),s)
        row=self.rows()[-1]
        self.assertEqual(row["kind"],"intent")
        self.assertEqual(row["effective_arm"],"unknown")
        self.assertIsNone(row["render_sha"])

    def test_previous_selection_is_observed_only_on_next_same_thread_call(self):
        s="Thank you for 9000. I can meet you partway. My price: 10000."
        shown=self.r.render(self.task("B"),s,10000,now=110)
        first=self.rows()[0]
        self.assertNotEqual(shown,s)
        self.assertIsNone(first["render_sha"])
        (self.r.root/"disabled").touch()
        self.assertEqual(self.r.render(self.task("B"),s,10000,now=111),s)
        raw=[json.loads(l) for l in (self.r.root/"trace.jsonl").read_text().splitlines() if l.strip()]
        observed=[r for r in raw if r["kind"]=="selection_observed"]
        self.assertEqual(len(observed),1)
        self.assertEqual(observed[0]["event"],first["event"])
        self.assertEqual(observed[0]["render_sha"],sp.sha(shown))
        self.assertEqual(observed[0]["effective_arm"],"B")
        self.assertEqual(self.rows()[-1]["effective_arm"],"unknown")

    def test_partial_write_and_postclose_errors_recover_without_false_exposure(self):
        s="Thank you for 9000. I can meet you partway. My price: 10000."
        write=sp.os.write
        def short(fd,data):
            return write(fd,data[:25])
        with mock.patch.object(sp.os,"write",side_effect=short):
            self.assertEqual(self.r.render(self.task("B"),s,10000,now=110),s)
        shown=self.r.render(self.task("B"),s,10000,now=111)
        self.assertNotEqual(shown,s)
        close=sp.os.close
        def bad_close(fd):
            close(fd);raise OSError("observation close")
        with mock.patch.object(sp.os,"close",side_effect=bad_close):
            self.assertEqual(self.r.render(self.task("B"),s,10000,now=112),s)
        (self.r.root/"disabled").touch()
        self.assertEqual(self.r.render(self.task("B"),s,10000,now=113),s)
        rows=[];malformed=0
        for line in (self.r.root/"trace.jsonl").read_text().splitlines():
            if not line.strip():continue
            try:rows.append(json.loads(line))
            except json.JSONDecodeError:malformed+=1
        self.assertEqual(malformed,1)
        intents={r["event"]:r for r in rows if r["kind"]=="intent"}
        observations=[r for r in rows if r["kind"]=="selection_observed"]
        self.assertEqual(len(observations),2) # failed-close retry, same fact/id
        self.assertEqual(observations[0],observations[1])
        self.assertEqual(observations[0]["render_sha"],sp.sha(shown))
        self.assertIn(observations[0]["event"],intents)
        self.assertEqual(len(intents),2) # failed flush creates no new B intent

    def test_pending_observation_io_crossing_end_cannot_expose_new_b(self):
        s="Thank you for 9000. I can meet you partway. My price: 10000."
        self.assertNotEqual(self.r.render(self.task("B"),s,10000,now=110),s)
        clock=[1899]
        record=self.r.record
        def delayed(row):
            record(row)
            if row["kind"]=="selection_observed":clock[0]=1900
        with mock.patch.object(self.r,"record",side_effect=delayed),mock.patch.object(sp.time,"time",side_effect=lambda:clock[0]):
            self.assertEqual(self.r.render(self.task("B"),s,10000),s)

    def test_final_clock_observation_has_no_following_io(self):
        s="Thank you for 9000. I can meet you partway. My price: 10000."
        calls=[0]
        record=self.r.record
        def clock():
            calls[0]+=1
            return 110
        def checked_record(row):
            self.assertLess(calls[0],2)
            return record(row)
        with mock.patch.object(sp.time,"time",side_effect=clock),mock.patch.object(self.r,"record",side_effect=checked_record):
            shown=self.r.render(self.task("B"),s,10000)
        self.assertNotEqual(shown,s)
        self.assertEqual(calls[0],2)

    def test_other_thread_cannot_flush_a_not_yet_returned_threads_selection(self):
        import threading
        s="Thank you for 9000. I can meet you partway. My price: 10000."
        self.r.render(self.task("B"),s,10000,now=110)
        result=[]
        thread=threading.Thread(target=lambda:result.append(self.r.render("other-thread",s,10000,now=111)))
        thread.start();thread.join(timeout=5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(result),1)
        raw=[json.loads(l) for l in (self.r.root/"trace.jsonl").read_text().splitlines() if l.strip()]
        self.assertTrue(all(r["kind"]=="intent" for r in raw))
        self.r.render(self.task("B"),s,10000,now=112)
        raw=[json.loads(l) for l in (self.r.root/"trace.jsonl").read_text().splitlines() if l.strip()]
        self.assertEqual(len([r for r in raw if r["kind"]=="selection_observed"]),1)

    def test_config_builder_requires_fresh_finite_1800_window(self):
        for args in (("bad/id",100,1900,"s"),("ok",100,1901,"s"),("ok",float("nan"),1900,"s"),("ok",100,1900,"")):
            with self.assertRaises(ValueError):sp.trial_config(*args)
