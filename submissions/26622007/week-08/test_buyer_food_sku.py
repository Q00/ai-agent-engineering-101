import unittest
from unittest.mock import patch
import buyer_food_sku as f

class FoodSku(unittest.TestCase):
    def test_study_duration_is_bounded_to_thirty_or_sixty_minutes(self):
        order = dict(kind="food", order="duration-test", _buyer_started_at=101)
        with patch.object(f, "ENABLED", True), patch.object(f, "START", 100), patch.object(f, "END", 3700):
            self.assertTrue(f.active(order, 102))
            self.assertFalse(f.active(order, 3700))
        with patch.object(f, "ENABLED", True), patch.object(f, "START", 100), patch.object(f, "END", 3699):
            self.assertFalse(f.active(order, 102))

    def test_paid_prior_policy(self):
        offers = [dict(sku="a", list_price=100), dict(sku="b", list_price=200)]
        rows = [dict(order="old1", shop="url", sku="b", price=120, time=990,
                     use_within_hours=10), dict(order="old2", shop="url", sku="b",
                     price=100, time=980, use_within_hours=10)]
        orders = [dict(order=x["order"], kind="food", use_within_hours=10,
                       ts=995, seconds=dict(end=20), sku="b", shop="name",
                       passed_to_kit=True) for x in rows]
        pays = [dict(order=x["order"], paid=True) for x in rows]
        h = f.evidence(pays, rows, orders, 1000, 5)
        self.assertEqual(f.candidate(offers, offers[0], "url", h, lambda o: 140)["sku"], "b")
        self.assertEqual(f.evidence([dict(order="old1",paid=False)], rows, orders,1000,5), {})
        rows[0]["time"] = 1000
        h = f.evidence(pays, rows, orders,1000,5)
        self.assertIsNone(f.candidate(offers, offers[0], "url", h, lambda o:140))

class Paths(unittest.TestCase):
    def test_canonical_paths_and_fixed_memory_hint(self):
        import market_buyer as m
        a = dict(sku="a", name="a", list_price=100, qty=2)
        b = dict(sku="b", name="b", list_price=200, qty=2)
        s = dict(name="s", url="url", usable=[a,b],
                 _food_sku=dict(url="url", selected="b"))
        self.assertEqual(m.selected_offer(s,150)["sku"], "b")
        asks = dict(a=[100]*10,b=[1]*10)
        self.assertFalse(m.hopeless([s],150,asks,{}))
        with patch.object(m,"quote") as quote:
            threads = m.start_quotes([s],dict(kind="food"),150,lambda sku:0,{},m.quote_ctl())
            for thread in threads.values():
                thread.join()
        self.assertEqual(quote.call_args.args[2], "b")
        self.assertEqual(s["_offer"]["sku"], "b")
        s["_food_sku"] = None
        self.assertEqual(m.selected_offer(s,150)["sku"],"a")

    def test_default_and_scope_no_history_io(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d, patch.object(f,"read_rows",side_effect=AssertionError):
            self.assertIsNone(f.reserve(d,"none",dict(kind="food",order="o",_buyer_started_at=10),[],
                                       100,None,None,10))
            with patch.object(f,"ENABLED",True), patch.object(f,"START",1), patch.object(f,"END",1801):
                self.assertIsNone(f.reserve(d,"none",dict(kind="goods",order="o",_buyer_started_at=10),[],
                                           100,None,None,10))

    def test_reject_bad_paid_price_and_unknown_time(self):
        for price in (True,float("nan"),10**100,-1,"5"):
            self.assertFalse(f.integer(price))
        self.assertFalse(f.number(True))
        self.assertFalse(f.number(float("nan")))
        self.assertEqual(f.evidence([],[],[],True,0),{})

    def test_snapshot_reused_without_history_read(self):
        import tempfile, os, json
        import market_buyer as m
        order=dict(kind="food",order="new",_buyer_started_at=1000,use_within_hours=5)
        s=dict(name="s",url="url",usable=[dict(sku="a",list_price=100),dict(sku="b",list_price=200)])
        with tempfile.TemporaryDirectory() as d, patch.object(f,"ENABLED",True), patch.object(f,"START",900), patch.object(f,"END",2700):
            os.mkdir(os.path.join(d,"measure"))
            for path in ("payments.jsonl","settled.jsonl","measure/orders.jsonl"):
                with open(os.path.join(d,path),"w") as out:
                    out.write("")
            first=f.reserve(d,os.path.join(d,"settled.jsonl"),order,[s],150,m.pick_offer,lambda o:140,1000)
            with patch.object(f,"read_rows",side_effect=AssertionError):
                again=f.reserve(d,"unread",order,[s],150,m.pick_offer,lambda o:140,1001)
            self.assertEqual(first,again)
            self.assertIsNone(first["arm"])
            snapshot = next(os.path.join(d,x) for x in os.listdir(d) if x.endswith(".json"))
            os.unlink(snapshot)
            with patch.object(f,"read_rows",side_effect=AssertionError):
                missing=f.reserve(d,"unread",order,[s],150,m.pick_offer,lambda o:140,1002)
            self.assertEqual(missing["fallback"],"history-or-snapshot-unavailable")

    def test_malformed_snapshot_fails_closed(self):
        import tempfile, os, json
        import market_buyer as m
        order=dict(kind="food",order="new",_buyer_started_at=1000)
        with tempfile.TemporaryDirectory() as d:
            path=os.path.join(d,".food-sku-"+f.hashlib.sha256((f.STUDY+chr(0)+"new").encode()).hexdigest()+".json")
            with open(path,"w") as out:
                json.dump([],out)
            got=f.reserve(d,"none",order,[],100,m.pick_offer,lambda o:70,1000)
            self.assertEqual(got["fallback"],"history-or-snapshot-unavailable")

if __name__ == "__main__":
    unittest.main()

class RealSizeHistory(unittest.TestCase):
    def test_large_projected_reserve(self):
        import tempfile, os, json
        import market_buyer as m
        with tempfile.TemporaryDirectory() as d:
            os.mkdir(os.path.join(d,"measure"))
            pays=[dict(order="p"+str(i),paid=True) for i in range(2)]
            settled=[dict(order=r["order"],shop="url",sku="b",price=100,time=990,use_within_hours=10.0) for r in pays]
            rows=[dict(order="p"+str(i),kind="food",passed_to_kit=True,sku="b",ts=995,
                       seconds=dict(end=20),use_within_hours=10.0,irrelevant="x"*5000) for i in range(2000)]
            for name,data in (("payments.jsonl",pays),("settled.jsonl",settled),("measure/orders.jsonl",rows)):
                with open(os.path.join(d,name),"w") as dst:
                    for r in data: dst.write(json.dumps(r)+"\n")
            self.assertGreater(os.path.getsize(os.path.join(d,"measure/orders.jsonl")),8<<20)
            shop=dict(name="s",url="url",usable=[dict(sku="a",list_price=100),dict(sku="b",list_price=200)])
            with patch.object(f,"ENABLED",True),patch.object(f,"START",900),patch.object(f,"END",2700):
                got=f.reserve(d,os.path.join(d,"settled.jsonl"),dict(kind="food",order="new",_buyer_started_at=1000,use_within_hours=5),[shop],150,m.pick_offer,lambda o:140,1000)
            self.assertIn(got.get("arm"),("A","B"))
            self.assertEqual(got["shops"]["s"]["candidate"],"b")

    def test_projected_bounds_and_strict_sources(self):
        import tempfile, os, json
        with tempfile.TemporaryDirectory() as d:
            path=os.path.join(d,"orders")
            for data in (b'{}',b'[]\n',b'broken\n',b'{"x":"'+b'x'*(128<<10)+b'"}\n',b'{}\n'*50001):
                with open(path,"wb") as dst: dst.write(data)
                with self.assertRaises(ValueError): f.read_order_rows(path)
            with open(path,"wb") as dst: dst.truncate((128<<20)+1)
            with self.assertRaises(ValueError): f.read_order_rows(path)
            r=dict(order="x",kind="food",passed_to_kit=True,sku="b",ts=995,seconds=dict(end=20,private="drop"),use_within_hours=10.0,private="drop")
            with open(path,"w") as dst: dst.write(json.dumps(r)+"\n")
            projected=f.read_order_rows(path)
            self.assertNotIn("private",projected[0])
            self.assertEqual(projected[0]["seconds"],dict(end=20))
            p=[dict(order="x",paid=True)]
            s=[dict(order="x",shop="url",sku="b",price=100,time=990,use_within_hours=10.0)]
            self.assertEqual(f.evidence(p,s,projected,1000,5),{("url","b"):[100]})
            for ps,ss,ms in ((p+p,s,projected),(p,s+s,projected),(p,s,projected+projected)):
                self.assertEqual(f.evidence(ps,ss,ms,1000,5),{})
            for field,value in (("ts",1000),("passed_to_kit",1),("use_within_hours",None),("seconds",None)):
                bad=dict(projected[0],**{field:value})
                self.assertEqual(f.evidence(p,s,[bad],1000,5),{})

    def test_main_ast_two_arms_quote_to_final_offer(self):
        # Execute only the real main canonical final selection assignment, never
        # main's market, harness, RPC or persistence side effects.
        import ast, inspect
        import market_buyer as m
        body=ast.parse(inspect.getsource(m.main)).body[0].body
        final=next(n for n in body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=="offer" for t in n.targets))
        for arm,sku in (("A","a"),("B","b")):
            a=dict(sku="a",name="a",list_price=100,qty=2)
            b=dict(sku="b",name="b",list_price=200,qty=2)
            shop=dict(name="s",url="url",usable=[a,b],_food_sku=dict(url="url",selected=sku))
            with patch.object(m,"quote") as quote:
                self.assertFalse(m.hopeless([shop],150,{},{}))
                threads=m.start_quotes([shop],dict(kind="food"),150,lambda _:0,{},m.quote_ctl())
                for t in threads.values(): t.join()
            scope=dict(selected_offer=m.selected_offer,shop=shop,r3=True,args=type("Args",(),dict(budget=150))())
            exec(compile(ast.fix_missing_locations(ast.Module(body=[final],type_ignores=[])),"main-final-seam","exec"),scope)
            self.assertEqual(quote.call_args.args[2],sku)
            self.assertEqual(shop["_offer"]["sku"],sku)
            self.assertEqual(scope["offer"]["sku"],sku)
            self.assertEqual(scope["offer"]["list_price"],100 if arm=="A" else 200)

    def test_fixed_snapshot_append_and_truncate(self):
        import tempfile, os, builtins
        with tempfile.TemporaryDirectory() as d:
            path=os.path.join(d,"orders")
            original=b'{"order":"one"}\n'
            real_open=builtins.open
            class ChangingFile:
                def __init__(self, shrink): self.src=real_open(path,"rb"); self.shrink=shrink
                def __enter__(self): return self
                def __exit__(self,*args): self.src.close()
                def fileno(self): return self.src.fileno()
                def tell(self): return self.src.tell()
                def readline(self,n):
                    line=self.src.readline(n)
                    with real_open(path,"wb" if self.shrink else "ab") as out:
                        if not self.shrink: out.write(b'{"order":"two"}\n')
                    return line
            for shrink in (False,True):
                with real_open(path,"wb") as out: out.write(original)
                with patch("builtins.open",return_value=ChangingFile(shrink)):
                    if shrink:
                        with self.assertRaises(ValueError): f.read_order_rows(path)
                    else:
                        self.assertEqual([r["order"] for r in f.read_order_rows(path)],["one"])
