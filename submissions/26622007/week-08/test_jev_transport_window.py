"""Finite treatment gates at the reader/POST seam; immutable ITT assignment."""
import json
import tempfile
import unittest
from unittest.mock import patch
from seller_jev_transport import JevTransport

class FiniteTransportTest(unittest.TestCase):
    def enroll(self, root):
        t = JevTransport(root)
        t.root.mkdir(parents=True)
        cfg = dict(enabled=True,id="finite-30m",start=1000,end=2800,seed=25,stop=False)
        (t.root/"seller_concession_trial.json").write_text(json.dumps(cfg))
        for n in range(4):
            d = dict(unit="unit",id="sku",kind="goods",cost=80,reserve=100,open=200,ask=194,warranty=6,rounds=0)
            t.quote("task-"+str(n),d,1001)
            if d["jev_transport"]["arm"] == "B":
                return t,d,cfg
        self.fail("paired assignment did not yield B")

    def check_off(self, mode):
        with tempfile.TemporaryDirectory() as root:
            t,d,cfg = self.enroll(root)
            original = dict(d["jev_transport"])
            reader = t.reader(d)
            now = 1100
            if mode == "end": now = 2800
            if mode == "disabled": (t.root/"disabled").touch()
            with patch("seller_jev_transport.time.time",return_value=now),patch("fast_classify.jev_offer",return_value=(150,.95)) as classify:
                reader("offer 150")
                self.assertTrue(callable(classify.call_args.kwargs["post"]))
            self.assertEqual(d["jev_transport"],original)

    def test_dispatch_rechecks_all_off_conditions(self):
        for mode in ("before","end","nan-now","disabled","stop","new-id","missing","malformed","nan","infinity","bool-time","reversed","too-long","disabled-config","contaminated"):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as root:
                t,d,cfg = self.enroll(root)
                original = dict(d["jev_transport"])
                captured = []
                def prepare(text, **kwargs):
                    captured.append(kwargs["post"])
                    return (150,.95)
                with patch("seller_jev_transport.time.time",return_value=1100),patch("fast_classify.jev_offer",side_effect=prepare):
                    t.reader(d)("offer")
                p = t.root/"seller_concession_trial.json"
                now = 1100
                if mode == "before": now = 999
                elif mode == "end": now = 2800
                elif mode == "nan-now": now = float("nan")
                elif mode == "disabled": (t.root/"disabled").touch()
                elif mode == "missing": p.unlink()
                elif mode == "malformed": p.write_text("{")
                elif mode == "contaminated": d["jev_transport"]["contaminated"] = True
                else:
                    if mode == "stop": cfg["stop"] = True
                    elif mode == "new-id": cfg["id"] = "new-id"
                    elif mode == "nan": cfg["end"] = float("nan")
                    elif mode == "infinity": cfg["start"] = float("-inf")
                    elif mode == "bool-time": cfg["start"] = True
                    elif mode == "reversed": cfg["end"] = 1000
                    elif mode == "too-long": cfg["end"] = 2801
                    elif mode == "disabled-config": cfg["enabled"] = False
                    p.write_text(json.dumps(cfg))
                with patch("seller_jev_transport.time.time",return_value=now),patch.object(t.pool,"post") as pooled,patch("httpx.post",return_value="A") as baseline:
                    self.assertEqual(captured[0]("http://127.0.0.1/",timeout=.7),"A")
                    pooled.assert_not_called()
                    baseline.assert_called_once_with("http://127.0.0.1/",timeout=.7)
                if mode != "contaminated": self.assertEqual(d["jev_transport"],original)

    def test_active_dispatch_uses_pool_and_boundaries(self):
        with tempfile.TemporaryDirectory() as root:
            t,d,cfg = self.enroll(root)
            for now in (1000,2799.999):
                with patch("seller_jev_transport.time.time",return_value=now),patch.object(t.pool,"post",return_value="B") as pooled,patch("httpx.post") as baseline:
                    def send(text,**kwargs): return kwargs["post"]("http://127.0.0.1/",timeout=.7)
                    with patch("fast_classify.jev_offer",side_effect=send):
                        self.assertEqual(t.reader(d)("offer"),"B")
                    pooled.assert_called_once_with("http://127.0.0.1/",timeout=.7)
                    baseline.assert_not_called()

    def last_event(self, t, d, event="turn"):
        t.event("exposure", d, event, 150, 1200)
        return json.loads((t.root/"seller_concession_trial.jsonl").read_text().splitlines()[-1])

    def test_actual_dispatch_not_sticky_itt_or_event_time_gate(self):
        for mode in ("end", "disabled", "stop", "new-id", "missing", "B-then-off", "A-then-on"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as root:
                t,d,cfg = self.enroll(root)
                original = dict(d["jev_transport"])
                reader = t.reader(d)
                p = t.root/"seller_concession_trial.json"
                if mode == "disabled": (t.root/"disabled").touch()
                elif mode == "stop": p.write_text(json.dumps(dict(cfg,stop=True)))
                elif mode == "new-id": p.write_text(json.dumps(dict(cfg,id="replacement")))
                elif mode == "missing": p.unlink()
                elif mode == "A-then-on": (t.root/"disabled").touch()
                now = 2800 if mode == "end" else 1100
                expected = "B" if mode == "B-then-off" else "A"
                def send(text, **kwargs):
                    return kwargs["post"]("http://127.0.0.1/", timeout=.7)
                with patch("seller_jev_transport.time.time",return_value=now), patch("fast_classify.jev_offer",side_effect=send), patch.object(t.pool,"post",return_value="B") as pool, patch("httpx.post",return_value="A") as baseline:
                    self.assertEqual(reader("offer"),expected)
                    self.assertEqual(pool.call_count,int(expected == "B"))
                    self.assertEqual(baseline.call_count,int(expected == "A"))
                if mode == "B-then-off": (t.root/"disabled").touch()
                elif mode == "A-then-on": (t.root/"disabled").unlink()
                rec = self.last_event(t,d)
                self.assertEqual(rec["arm"],"B")
                self.assertEqual(rec["effective_arm"],expected)
                self.assertEqual(rec["dispatches"][0]["arm"],expected)
                self.assertEqual(rec["dispatches"][0]["ts"],now)
                self.assertTrue(rec["dispatches"][0]["reason"])
                self.assertEqual(d["jev_transport"],original)

    def test_retained_callback_fallback_matches_event_in_five_off_cases(self):
        for mode in ("end","disabled","stop","new-id","missing"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as root:
                t,d,cfg=self.enroll(root)
                captured=[]
                def prepare(text, **kwargs): captured.append(kwargs["post"])
                with patch("seller_jev_transport.time.time",return_value=1100), patch("fast_classify.jev_offer",side_effect=prepare):
                    t.reader(d)("offer")
                p=t.root/"seller_concession_trial.json"
                if mode=="disabled": (t.root/"disabled").touch()
                elif mode=="stop": p.write_text(json.dumps(dict(cfg,stop=True)))
                elif mode=="new-id": p.write_text(json.dumps(dict(cfg,id="replacement")))
                elif mode=="missing": p.unlink()
                with patch("seller_jev_transport.time.time",return_value=2800 if mode=="end" else 1100), patch.object(t.pool,"post") as pool, patch("httpx.post",return_value="A") as baseline:
                    self.assertEqual(captured[0]("http://127.0.0.1/",timeout=.7),"A")
                    pool.assert_not_called()
                    baseline.assert_called_once()
                rec=self.last_event(t,d)
                self.assertEqual(rec["arm"],"B")
                self.assertEqual(rec["effective_arm"],"A")

    def test_no_dispatch_reset_exception_and_terminal_are_not_sticky(self):
        import httpx
        with tempfile.TemporaryDirectory() as root:
            t,d,cfg = self.enroll(root)
            reader = t.reader(d)
            self.assertEqual(self.last_event(t,d)["effective_arm"],"no-dispatch")
            def send(text, **kwargs):
                return kwargs["post"]("http://127.0.0.1/",timeout=.7)
            with patch("seller_jev_transport.time.time",return_value=1100), patch("fast_classify.jev_offer",side_effect=send), patch.object(t.pool,"post",side_effect=httpx.ReadTimeout("fixture")):
                with self.assertRaises(httpx.ReadTimeout): reader("offer")
            self.assertEqual(self.last_event(t,d)["effective_arm"],"B")
            terminal = self.last_event(t,d,"deal")
            self.assertIsNone(terminal["effective_arm"])
            self.assertEqual(terminal["dispatches"],[])
            self.assertEqual(terminal["exposure_scope"],"itt-only")
            t.reader(d)  # next lazy turn, including JEV_ON=false caller path
            self.assertEqual(self.last_event(t,d)["effective_arm"],"no-dispatch")
            # Even an old closure completing late cannot fill the new turn.
            with patch("seller_jev_transport.time.time",return_value=1100), patch("fast_classify.jev_offer",side_effect=send), patch.object(t.pool,"post",return_value="B"):
                reader("late")
            self.assertEqual(self.last_event(t,d)["dispatches"],[])
            self.assertEqual(d["jev_transport"]["arm"],"B")

    def test_two_talks_dispatch_concurrently_without_shared_exposure(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        with tempfile.TemporaryDirectory() as root:
            t,d,cfg = self.enroll(root)
            other=dict(d,jev_transport=dict(d["jev_transport"],arm="A"))
            readers=[t.reader(d),t.reader(other)]
            barrier=Barrier(2)
            def send(text, **kwargs):
                barrier.wait(timeout=5)
                return kwargs["post"]("http://127.0.0.1/",timeout=.7)
            with patch("seller_jev_transport.time.time",return_value=1100), patch("fast_classify.jev_offer",side_effect=send), patch.object(t.pool,"post",return_value="B"), patch("httpx.post",return_value="A"), ThreadPoolExecutor(2) as ex:
                futures=[ex.submit(reader,"offer") for reader in readers]
                self.assertEqual([f.result(timeout=5) for f in futures],["B","A"])
            self.assertEqual(self.last_event(t,d)["effective_arm"],"B")
            self.assertEqual(self.last_event(t,other)["effective_arm"],"A")

    def test_logical_dispatch_precedes_pool_send_and_is_not_cancelled(self):
        with tempfile.TemporaryDirectory() as root:
            t,d,cfg = self.enroll(root)
            def send(text, **kwargs): return kwargs["post"]("http://127.0.0.1/",timeout=.7)
            def inflight(*args, **kwargs):
                (t.root/"disabled").touch()  # pool/client preparation after selection
                return "already-selected-B"
            with patch("seller_jev_transport.time.time",return_value=1100), patch("fast_classify.jev_offer",side_effect=send), patch.object(t.pool,"post",side_effect=inflight), patch("httpx.post",return_value="A"):
                reader=t.reader(d)
                self.assertEqual(reader("offer"),"already-selected-B")
                self.assertEqual(self.last_event(t,d)["effective_arm"],"B")
                self.assertEqual(reader("offer"),"A")
            rec=self.last_event(t,d)
            self.assertEqual(rec["effective_arm"],"mixed")
            self.assertEqual([x["arm"] for x in rec["dispatches"]],["B","A"])

    def test_config_generator_and_consumer_reject_nonfinite_windows(self):
        for start,end in ((float("nan"),2800),(1000,float("inf")),(True,1800),(1000,1000),(1000,2801)):
            with self.subTest(start=start,end=end),tempfile.TemporaryDirectory() as root:
                with self.assertRaises(ValueError): JevTransport.trial_config("finite",start,end,25)
                t = JevTransport(root)
                t.root.mkdir(parents=True)
                (t.root/"seller_concession_trial.json").write_text(json.dumps(dict(enabled=True,id="finite",start=start,end=end,seed=25)))
                self.assertIsNone(t.config())
        with tempfile.TemporaryDirectory() as root:
            t = JevTransport(root)
            t.root.mkdir(parents=True)
            cfg = t.trial_config("finite",1000,2800,25)
            (t.root/"seller_concession_trial.json").write_text(json.dumps(cfg))
            self.assertEqual(t.config(),cfg)

    def test_adopted_without_finite_config_fails_closed(self):
        with tempfile.TemporaryDirectory() as root:
            t = JevTransport(root)
            t.root.mkdir(parents=True)
            (t.root/"adopted").touch()
            d = dict(jev_transport=dict(id="adopted-v1",arm="B"))
            with patch("fast_classify.jev_offer",return_value=(150,.95)) as classify:
                t.reader(d)("offer")
                self.assertTrue(callable(classify.call_args.kwargs["post"]))

    def test_existing_b_stops_at_end(self): self.check_off("end")
    def test_existing_closure_stops_when_disabled(self): self.check_off("disabled")
