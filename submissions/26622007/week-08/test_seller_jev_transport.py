import json,tempfile,unittest
from unittest.mock import patch
from seller_jev_transport import JevTransport

class TransportTrialTest(unittest.TestCase):
    def deal(self):return dict(unit="unit",id="sku",kind="goods",cost=80,reserve=100,open=200,ask=194,warranty=6,rounds=0)
    def test_control_sticky_and_b_only_changes_transport(self):
        with tempfile.TemporaryDirectory() as root:
            t=JevTransport(root);a=self.deal();t.quote("control",a,1000)
            (t.root/"seller_concession_trial.json").write_text(json.dumps(t.trial_config("finite",1000,2800,25)))
            b=self.deal();t.quote("treatment",b,1001)
            if b["jev_transport"]["arm"]!="B":
                b=self.deal();t.quote("treatment-2",b,1001)
            before={k:b[k] for k in ("ask","reserve","open","warranty","cost")}
            with patch("seller_jev_transport.time.time",return_value=1100),patch("fast_classify.jev_offer",return_value=(150,.95)) as read:
                self.assertEqual(t.reader(a)("input"),(150,.95));self.assertTrue(callable(read.call_args.kwargs["post"]))
                self.assertEqual(t.reader(b)("input"),(150,.95));self.assertTrue(callable(read.call_args.kwargs["post"]))
            self.assertEqual({k:b[k] for k in before},before)
            again=self.deal();t.quote("control",again,1002);self.assertEqual(again["jev_transport"]["arm"],"A")
            (t.root/"disabled").touch();new=self.deal();t.quote("new",new,1003);self.assertEqual(new["jev_transport"]["arm"],"A")
    def test_events_use_only_transport_assignment(self):
        with tempfile.TemporaryDirectory() as root:
            t=JevTransport(root);t.root.mkdir(parents=True);(t.root/"adopted").touch();d=self.deal();d["late_speed"]={"arm":"B","id":"historical-price"}
            t.quote("new",d,1000);t.event("new",d,"deal",150,1001)
            events=[json.loads(x) for x in (t.root/"seller_concession_trial.jsonl").read_text().splitlines()]
            self.assertEqual({x["id"] for x in events},{"adopted-v1"})
            self.assertEqual(events[-1]["price"],150)

class ClassifierTransportTest(unittest.TestCase):
    def test_wire_arguments_and_judgment_are_identical(self):
        import fast_classify
        calls=[]
        class Response:
            status_code=200
            def json(self):return {"answers":{"offer":{"choice":"150","confidence":.95}}}
        def post(*args,**kwargs):calls.append((args,kwargs));return Response()
        with patch("harness._openrouter_key",return_value="fixture"),patch("harness.record_call"),patch("httpx.post",side_effect=post):
            baseline=fast_classify.jev_offer("I can offer 150.")
            pooled=fast_classify.jev_offer("I can offer 150.",post=post)
        self.assertEqual(baseline,pooled)
        self.assertEqual(calls[0],calls[1])
        self.assertEqual(calls[1][1]["timeout"],.7)
    def test_transport_failure_retains_down_and_single_record(self):
        import fast_classify,httpx
        with patch("harness._openrouter_key",return_value="fixture"),patch("harness.record_call") as record:
            def fail(*args,**kwargs):raise httpx.ReadTimeout("fixture")
            self.assertEqual(fast_classify.jev_offer("I can offer 150.",post=fail),fast_classify.JEV_DOWN)
            self.assertEqual(record.call_count,1)
