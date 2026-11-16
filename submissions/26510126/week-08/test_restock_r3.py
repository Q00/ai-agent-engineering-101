"""Offline check of restock_r3.forum_wanted against forum floods and bait (no market).

    python test_restock_r3.py      # prints PASS/FAIL per case

Round 3 lets agents post without being asked. A "wanted" post raises a category in our restock, so a flood of them
could make the shop buy stock that never sells. Cases: one author posting fifty times counts once; many authors count
at most WANT_CAP; a post naming every category, a very long post and our own posts count nothing; old posts expire.
"""
import json
import tempfile
import time
from pathlib import Path

import restock_r3

restock_r3.STRATEGY = Path(tempfile.mkdtemp(prefix="restock-strategy-")) / "none.json"   # the defaults, not the live file
_seed = restock_r3.a_hist_seed
restock_r3.a_hist_seed = lambda now: {}   # the first a_hist reads the live talks log; the cases set their own units
_fseed = restock_r3.f_hist_seed
restock_r3.f_hist_seed = lambda now: {}   # the same for food
_deals = restock_r3.our_deals
restock_r3.our_deals = lambda st, now: st.setdefault("deals", {})   # no live talks log in the cases

NOW = time.time()
DEMAND = [{"item": "김치", "category": "식품>김치"}, {"item": "의자", "category": "가구>의자"},
          {"item": "침대", "category": "가구>침대"}, {"item": "라면", "category": "식품>라면"},
          {"item": "생수", "category": "식품>생수"}]
CATALOG = {"G0000abcd": {"category": "가구>침대"}}
ok = []


def case(name, cond, detail=""):
    ok.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {name}{': ' + str(detail) if detail else ''}", flush=True)


def wanted(posts):
    md = Path(tempfile.mkdtemp(prefix="restock-"))
    (md / "forum.json").write_text(json.dumps(posts, ensure_ascii=False), encoding="utf-8")
    return restock_r3.forum_wanted(md, DEMAND, CATALOG, NOW)


post = lambda who, text, age=60: {"author": who, "text": text, "ts": NOW - age}
w = wanted([post("spammer", "김치 구합니다!") for _ in range(50)])
case("one author posting fifty times counts once", w.get("식품>김치") == 1, dict(w))
w = wanted([post(f"buyer{i}", "의자 구합니다") for i in range(10)])
case(f"ten authors count at most {restock_r3.WANT_CAP}", w.get("가구>의자") == restock_r3.WANT_CAP, dict(w))
w = wanted([post("lister", "구합니다: 김치 의자 침대 라면 생수 전부")])
case("a post naming more than three categories counts nothing", not w, dict(w))
w = wanted([post("long", "김치 구합니다 " + "x" * 5000)])
case("a post over the length cap counts nothing", not w, dict(w))
w = wanted([post(restock_r3.US, "침대 구합니다")])
case("our own post counts nothing", not w, dict(w))
w = wanted([post("old", "라면 구합니다", age=7 * 3600)])
case("a post older than six hours counts nothing", not w, dict(w))
w = wanted([post("sku", "looking for G0000abcd"), "not a dict", {"text": None}])
case("a SKU in a post maps to its category; malformed posts are skipped", w.get("가구>침대") == 1, dict(w))

# food first, and goods held under goods_frac of stock_value (goods pay storage and may break)
md = Path(tempfile.mkdtemp(prefix="restock-main-"))
demand = [{"item": f"g{i}", "category": f"G>{i}", "kind": "goods", "median_price": 50000} for i in range(30)]
demand += [{"item": f"f{i}", "category": f"F>{i}", "kind": "food", "median_price": 10000} for i in range(30)]
catalog = {f"SG{i}": {"category": f"G>{i}", "kind": "goods", "list_price": 50000, "name": f"g{i}"} for i in range(30)}
catalog.update({f"SF{i}": {"category": f"F>{i}", "kind": "food", "list_price": 10000, "shelf_hours": 200, "name": f"f{i}"}
                for i in range(30)})
held = [{"unit": f"u{i}", "sku": f"SG{i}", "category": f"G>{i}", "kind": "goods", "grade": "B", "cost": 30000,
         "supplied_at": NOW, "expires_at": None} for i in range(20)]               # 600,000 of goods already
(md / "inventory.json").write_text(json.dumps({"round": 3, "balance": 4_000_000, "units": held}), encoding="utf-8")
(md / "catalog.json").write_text(json.dumps(catalog), encoding="utf-8")
(md / "demand.json").write_text(json.dumps(demand), encoding="utf-8")
restock_r3.ledger_settles = lambda after: ([], after)
import contextlib, io, sys
out = io.StringIO()
sys.argv = ["restock_r3.py", "--state", str(md)]
with contextlib.redirect_stdout(out):
    restock_r3.main()
buy = json.loads(out.getvalue().splitlines()[-1])["buy"]
kinds = [catalog[b["sku"]]["kind"] for b in buy]
case("food first: with 600,000 of goods held (cap 0.35 x 2,000,000 = 700,000), the buy is food plus at most "
     "one 30,000 goods unit", kinds.count("food") >= 9 and kinds.count("goods") <= 1, {"food": kinds.count("food"), "goods": kinds.count("goods")})

# the cash floor holds under any reserve the meta-harness sets: reserve 0 and a huge stock cap still keep 700,000
real = restock_r3.strategy
restock_r3.strategy = lambda: dict(real(), reserve_cash=0, stock_value=10_000_000)
(md / "inventory.json").write_text(json.dumps({"round": 3, "balance": 800_000, "units": []}), encoding="utf-8")
out = io.StringIO()
with contextlib.redirect_stdout(out):
    restock_r3.main()
res = json.loads(out.getvalue().splitlines()[-1])
spent = sum(catalog[b["sku"]]["list_price"] * restock_r3.EST_COST * b["qty"] for b in res["buy"])
case("reserve 0 from the meta-harness: code still keeps 700,000 (balance 800,000, so at most 100,000 spent)",
     0 < spent <= 100_000, {"spent (estimated)": spent, "lines": len(res["buy"])})
(md / "inventory.json").write_text(json.dumps({"round": 3, "balance": 650_000, "units": []}), encoding="utf-8")
out = io.StringIO()
with contextlib.redirect_stdout(out):
    restock_r3.main()
res = json.loads(out.getvalue().splitlines()[-1])
case("under the cash floor nothing is bought", res["buy"] == [] and "cash floor 700000" in res["why"], res["why"])
restock_r3.strategy = real

# runs 20 minutes apart (the rule may change): depth scales up to 4x where things sell, and the market's own
# bankruptcy line from inventory.json sets the cash floor
(md / "inventory.json").write_text(json.dumps({"round": 3, "balance": 4_000_000, "units": [], "bankrupt_line": 900_000}),
                                   encoding="utf-8")
(md / "restock_state.json").write_text(json.dumps({"after": 1, "seen": [], "t": time.time() - 1200,
                                                   "score": {"F>1": 8.0}, "sku_score": {}}), encoding="utf-8")
out = io.StringIO()
with contextlib.redirect_stdout(out):
    restock_r3.main()
res = json.loads(out.getvalue().splitlines()[-1])
q = {b["sku"]: b["qty"] for b in res["buy"]}
case("runs 20 min apart: depth x4 for a selling food category (4 units, not 2)", q.get("SF1") == 4 and "x4.0" in res["why"],
     {"SF1": q.get("SF1"), "why": res["why"]})
(md / "inventory.json").write_text(json.dumps({"round": 3, "balance": 1_000_000, "units": [], "bankrupt_line": 900_000}),
                                   encoding="utf-8")
restock_r3.strategy = lambda: dict(real(), reserve_cash=0)        # the default 1,500,000 reserve would hide the line
out = io.StringIO()
with contextlib.redirect_stdout(out):
    restock_r3.main()
restock_r3.strategy = real
res = json.loads(out.getvalue().splitlines()[-1])
case("a raised bankruptcy line (900,000) raises the cash floor to 1,100,000: nothing bought at 1,000,000",
     res["buy"] == [] and "cash floor 1100000" in res["why"], res["why"])

# supply windows (agent.md 11/3 20:23): closed -> no lines (the kit would wait 90 s per line); open -> deeper
fresh_state = json.dumps({"after": 1, "seen": [], "t": time.time() - 30, "score": {"F>1": 8.0}, "sku_score": {}})
for is_open in (False, True):
    (md / "inventory.json").write_text(json.dumps({"round": 3, "balance": 4_000_000, "units": [],
        "restock": {"open": is_open, "opens_at": time.time() + 600, "closes_at": time.time() + 780}}), encoding="utf-8")
    (md / "restock_state.json").write_text(fresh_state, encoding="utf-8")
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        restock_r3.main()
    res = json.loads(out.getvalue().splitlines()[-1])
    if is_open:
        q = {b["sku"]: b["qty"] for b in res["buy"]}
        case("window open: depth x2 for a selling food category (4 units), lines bought", q.get("SF1") == 4 and len(res["buy"]) == restock_r3.DEFAULTS["lines"],
             {"SF1": q.get("SF1"), "lines": len(res["buy"]), "why": res["why"]})
    else:
        st_after = json.loads((md / "restock_state.json").read_text())
        case("window closed: no lines, and the scores still update", res["buy"] == [] and "supply closed until" in res["why"]
             and st_after["t"] > time.time() - 5, res["why"])

# hidden demand (demand_r3.py): orders our buyer saw point at a reference price; restock stocks the SKU nearest it
# and gives the category depth, even with no settle anywhere
import demand_r3
demand_r3.LOG = md / "demand_r3.jsonl"
cat3 = dict(catalog, **{f"SH{i}": {"category": "F>hot", "kind": "food", "list_price": p, "shelf_hours": 200, "name": f"h{i}"}
                        for i, p in enumerate((5000, 10000, 20000))})
(md / "catalog.json").write_text(json.dumps(cat3), encoding="utf-8")
(md / "demand.json").write_text(json.dumps(demand + [{"item": "hot", "category": "F>hot", "kind": "food", "median_price": 10000}]),
                                encoding="utf-8")
for i in range(6):                                       # bands around 20,000: acceptable 10,000 and 20,000 (ref 15,000)
    demand_r3.record_order({"order": f"o{i}", "category": "F>hot", "kind": "food", "acceptable": ["SH1", "SH2"]}, cat3,
                           [{"offers": [{"sku": "SH1", "qty": 1}]}])
demand_r3.record_ask("I want to buy h2 (G0000beef)", cat3, have=False)   # not in the catalog: no SKU, still logged
cw, sw, ref = demand_r3.signals()
case("orders become category weight, a nearest-reference SKU and a reference price",
     round(cw["F>hot"]) == 6 and sw["SH1"] + sw["SH2"] >= 5.9 and round(ref["F>hot"]) == 15000, (dict(cw), dict(sw), ref))
(md / "inventory.json").write_text(json.dumps({"round": 3, "balance": 4_000_000, "units": []}), encoding="utf-8")
(md / "restock_state.json").write_text(json.dumps({"after": 1, "seen": [], "t": time.time() - 30, "score": {}, "sku_score": {}}),
                                       encoding="utf-8")
out = io.StringIO()
with contextlib.redirect_stdout(out):
    restock_r3.main()
res = json.loads(out.getvalue().splitlines()[-1])
q = {b["sku"]: b["qty"] for b in res["buy"]}
st3 = json.loads((md / "restock_state.json").read_text())
case("restock buys the ordered category first, at the reference price (not the 10,000 median alone), with depth",
     res["buy"][0]["sku"] in ("SH1", "SH2") and sum(q.get(k, 0) for k in ("SH1", "SH2")) >= 3,
     {"first": res["buy"][0], "hot": {k: q.get(k) for k in ("SH0", "SH1", "SH2")}})
case("order weights are not written into the decaying scores (no compounding)", "F>hot" not in st3.get("score", {}), st3.get("score"))
# the market's surge (11/4 21:14): a SKU priced over surge_max x its plain cost is left out, the next one taken
(md / "inventory.json").write_text(json.dumps({"round": 3, "balance": 4_000_000, "units": [],
                                              "surge": [{"sku": "SH1", "units": 300, "factor": 1.47},
                                                        {"sku": "SH2", "units": 60, "factor": 1.02}]}), encoding="utf-8")
(md / "restock_state.json").write_text(json.dumps({"after": 1, "seen": [], "t": time.time() - 30, "score": {}, "sku_score": {}}),
                                       encoding="utf-8")
out = io.StringIO()
with contextlib.redirect_stdout(out):
    restock_r3.main()
res = json.loads(out.getvalue().splitlines()[-1])
skus = [b["sku"] for b in res["buy"]]
case("a SKU the market surged to 1.47x is not bought; one at 1.02x still is", "SH1" not in skus and "SH2" in skus
     and "surge" in res["why"], {"buy": skus, "why": res["why"]})
# a dear SKU (list 150,000 or more) is bought dear_depth units deep (11/4: the top shops sold 470-575 such units in 6 h)
assert "dear_depth" in restock_r3.DEFAULTS
case("dear goods get dear_depth units a line", restock_r3.DEFAULTS["dear_depth"] >= 2 and restock_r3.DEFAULTS["lines"] >= 14)
# dear depth only for 300k+ (11/5: 300k+ sold 10.3 an hour, the 150-300k band 1.3): a 200,000 SKU is bought 2 deep, a 400,000 one 4
cat5 = {"SM": {"category": "G>mid", "kind": "goods", "list_price": 200000, "name": "mid"},
        "SX": {"category": "G>top", "kind": "goods", "list_price": 400000, "name": "top"}}
(md / "catalog.json").write_text(json.dumps(cat5), encoding="utf-8")
(md / "demand.json").write_text(json.dumps([{"item": "mid", "category": "G>mid", "kind": "goods", "median_price": 200000},
                                           {"item": "top", "category": "G>top", "kind": "goods", "median_price": 400000}]),
                                encoding="utf-8")
(md / "inventory.json").write_text(json.dumps({"round": 3, "balance": 40_000_000, "units": []}), encoding="utf-8")
restock_r3.strategy = lambda: dict(real(), reserve_cash=0, stock_value=40_000_000, goods_frac=1.0, max_list=500_000)
(md / "restock_state.json").write_text(json.dumps({"after": 1, "seen": [], "t": time.time() - 30, "score": {}, "sku_score": {"SX": 2000}}),
                                       encoding="utf-8")
out = io.StringIO()
with contextlib.redirect_stdout(out):
    restock_r3.main()
res = json.loads(out.getvalue().splitlines()[-1])
q = {b["sku"]: b["qty"] for b in res["buy"]}
case("a 200,000 SKU is held at 2 units, a 400,000 one gets dear_depth", q.get("SM", 0) <= 2 and q.get("SX", 0) >= 3, q)
# 6 h: 300k+ sold 2.2 an hour on 35 held units: a 400,000 SKU with no sales score is held at 2 like the band below
cat5["SC"] = {"category": "G>cold", "kind": "goods", "list_price": 400000, "name": "cold"}
(md / "catalog.json").write_text(json.dumps(cat5), encoding="utf-8")
(md / "demand.json").write_text(json.dumps([{"item": "top", "category": "G>top", "kind": "goods", "median_price": 400000},
                                           {"item": "cold", "category": "G>cold", "kind": "goods", "median_price": 400000}]),
                                encoding="utf-8")
(md / "restock_state.json").write_text(json.dumps({"after": 1, "seen": [], "t": time.time() - 30, "score": {}, "sku_score": {"SX": 2000, "SC": 53}}),
                                       encoding="utf-8")
out = io.StringIO()
with contextlib.redirect_stdout(out):
    restock_r3.main()
res = json.loads(out.getvalue().splitlines()[-1])
q = {b["sku"]: b["qty"] for b in res["buy"]}
case("a 400,000 SKU under DEAR_HOT (sku_score 53) is held at 2 units, a best seller (2,000) gets dear_depth", q.get("SC", 0) <= 2 and q.get("SX", 0) >= 3, q)
# stock at its cap (69.5M of 70M) with 53.2M in 300k+ units, 65 of them non-hot: a non-hot 400,000 SKU is held at 1
case("a 400,000 SKU under DEAR_HOT (sku_score 53) is held at 1 unit", q.get("SC", 0) == 1, q)
# 11/6: a best seller (sku_score 1,500) planned 20 units (4.8M at cost) against 2.3M of goods room and was skipped every
# window; now it is bought at its depth, trimmed to what the room affords
restock_r3.strategy = lambda: dict(real(), reserve_cash=0, stock_value=1_000_000, goods_frac=1.0, max_list=500_000, dear_depth=8)
(md / "restock_state.json").write_text(json.dumps({"after": 1, "seen": [], "t": time.time() - 30, "score": {}, "sku_score": {"SX": 1500}}),
                                       encoding="utf-8")
out = io.StringIO()
with contextlib.redirect_stdout(out):
    restock_r3.main()
q = {b["sku"]: b["qty"] for b in json.loads(out.getvalue().splitlines()[-1])["buy"]}
case("a best-selling 400,000 SKU too dear for a full line is bought trimmed to the room (1M / 240k = 4), not skipped",
     q.get("SX") == 4, q)
# 6 h: the market sold 334-341 units of each top 300k+ SKU while we held 5-8: depth is dear_depth x sku_score/DEAR_HOT, 1-3x
restock_r3.strategy = lambda: dict(real(), reserve_cash=0, stock_value=50_000_000, goods_frac=1.0, max_list=500_000, dear_depth=4)
qs = {}
for sc in (300, 600):                    # scores decay a little between runs, so the depth is checked as a range
    (md / "restock_state.json").write_text(json.dumps({"after": 1, "seen": [], "t": time.time() - 30, "score": {}, "sku_score": {"SX": sc}}),
                                           encoding="utf-8")
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        restock_r3.main()
    qs[sc] = {b["sku"]: b["qty"] for b in json.loads(out.getvalue().splitlines()[-1])["buy"]}.get("SX", 0)
case("a 400,000 SKU at sku_score 300 is held about 6 deep, one at 600 about 12 (dear_depth 4 x score/DEAR_HOT)",
     5 <= qs[300] <= 6 and 11 <= qs[600] <= 12, qs)
# 6 h: the top 300k+ SKUs sold 158-398 units while we held 0-12: at sku_score 1,000 the depth is 5x dear_depth, 20
(md / "restock_state.json").write_text(json.dumps({"after": 1, "seen": [], "t": time.time() - 30, "score": {}, "sku_score": {"SX": 1000}}),
                                       encoding="utf-8")
out = io.StringIO()
with contextlib.redirect_stdout(out):
    restock_r3.main()
q = {b["sku"]: b["qty"] for b in json.loads(out.getvalue().splitlines()[-1])["buy"]}
case("a 400,000 SKU at sku_score 1,000 is held about 20 deep (heat capped at 5x dear_depth)", 19 <= q.get("SX", 0) <= 20, q)
# a hot SKU the market surged to 1.08x (under surge_max 1.15) gets no heat: dear_depth 4, not 20
(md / "inventory.json").write_text(json.dumps({"round": 3, "balance": 40_000_000, "units": [],
                                              "surge": [{"sku": "SX", "units": 80, "factor": 1.08}]}), encoding="utf-8")
(md / "restock_state.json").write_text(json.dumps({"after": 1, "seen": [], "t": time.time() - 30, "score": {}, "sku_score": {"SX": 1000}}),
                                       encoding="utf-8")
out = io.StringIO()
with contextlib.redirect_stdout(out):
    restock_r3.main()
q = {b["sku"]: b["qty"] for b in json.loads(out.getvalue().splitlines()[-1])["buy"]}
case("a 400,000 SKU at sku_score 1,000 surged to 1.08x is held 4 deep (no heat), not 20", q.get("SX", 0) == 4, q)
(md / "inventory.json").write_text(json.dumps({"round": 3, "balance": 40_000_000, "units": []}), encoding="utf-8")
# 495 goods units (21.4M) sat unsold over a day: a hot 400,000 SKU (score 1,000) holding a unit first seen 30 h ago gets no heat
(md / "inventory.json").write_text(json.dumps({"round": 3, "balance": 40_000_000, "units": [
    {"unit": "UX", "sku": "SX", "category": "G>hot", "kind": "goods", "grade": "A", "cost": 200000}]}), encoding="utf-8")
(md / "restock_state.json").write_text(json.dumps({"after": 1, "seen": [], "t": time.time() - 30, "score": {}, "sku_score": {"SX": 1000},
                                                   "first": {"UX": time.time() - 30 * 3600}}), encoding="utf-8")
out = io.StringIO()
with contextlib.redirect_stdout(out):
    restock_r3.main()
q = {b["sku"]: b["qty"] for b in json.loads(out.getvalue().splitlines()[-1])["buy"]}
case("a 400,000 SKU at sku_score 1,000 holding a unit unsold over 24 h is held 4 deep (no heat), 3 more bought", q.get("SX", 0) == 3, q)
(md / "inventory.json").write_text(json.dumps({"round": 3, "balance": 40_000_000, "units": []}), encoding="utf-8")
# the 100-300k band settled 183 units an hour and we sold 0.3: a 200,000 SKU at DEAR_HOT or more takes dear_depth x heat capped at 2x
cat5["SC"] = {"category": "G>cold", "kind": "goods", "list_price": 400000, "name": "cold"}
(md / "catalog.json").write_text(json.dumps(cat5), encoding="utf-8")
(md / "restock_state.json").write_text(json.dumps({"after": 1, "seen": [], "t": time.time() - 30, "score": {}, "sku_score": {"SM": 1000, "SC": 53}}),
                                       encoding="utf-8")
out = io.StringIO()
with contextlib.redirect_stdout(out):
    restock_r3.main()
q = {b["sku"]: b["qty"] for b in json.loads(out.getvalue().splitlines()[-1])["buy"]}
case("a 200,000 SKU at sku_score 1,000 is held about 8 deep (heat capped at 2x dear_depth), one under DEAR_HOT at 2",
     7 <= q.get("SM", 0) <= 8, q)
# the 150-300k band settled 208 units an hour and we sold 1.8: a hot 200,000 SKU with one deal of ours is floored at half its depth (4), not 2
(md / "restock_state.json").write_text(json.dumps({"after": 1, "seen": [], "t": time.time() - 30, "score": {}, "sku_score": {"SM": 1000, "SC": 53},
                                                   "deals": {"SM|A": [0.6]}}), encoding="utf-8")
out = io.StringIO()
with contextlib.redirect_stdout(out):
    restock_r3.main()
q = {b["sku"]: b["qty"] for b in json.loads(out.getvalue().splitlines()[-1])["buy"]}
case("a hot 200,000 SKU with one own deal is held 4 deep (half of 8), not 2 by own_n / 4", q.get("SM", 0) == 4, q)
# the 100-300k band settled 195 units an hour and we sold 1.7: a 200,000 SKU at half of DEAR_HOT (120) is held 4 deep (no heat), not 2
(md / "restock_state.json").write_text(json.dumps({"after": 1, "seen": [], "t": time.time() - 30, "score": {}, "sku_score": {"SM": 120, "SC": 53}}),
                                       encoding="utf-8")
out = io.StringIO()
with contextlib.redirect_stdout(out):
    restock_r3.main()
q = {b["sku"]: b["qty"] for b in json.loads(out.getvalue().splitlines()[-1])["buy"]}
case("a 200,000 SKU at half of DEAR_HOT (120) is held 4 deep, not 2", q.get("SM", 0) == 4, q)
restock_r3.strategy = real
# goods listed 100-150k are held at 2 units like the 150-300k band (6 h: 100-300k sold 1.0 an hour, 46 h of stock)
cat6 = {"SL": {"category": "G>low", "kind": "goods", "list_price": 120000, "name": "low"}}
(md / "catalog.json").write_text(json.dumps(cat6), encoding="utf-8")
(md / "demand.json").write_text(json.dumps([{"item": "low", "category": "G>low", "kind": "goods", "median_price": 120000}]),
                                encoding="utf-8")
(md / "inventory.json").write_text(json.dumps({"round": 3, "balance": 40_000_000, "units": []}), encoding="utf-8")
restock_r3.strategy = lambda: dict(real(), reserve_cash=0, stock_value=40_000_000, goods_frac=1.0, max_list=500_000)
(md / "restock_state.json").write_text(json.dumps({"after": 1, "seen": [], "t": time.time() - 30, "score": {}, "sku_score": {}}),
                                       encoding="utf-8")
out = io.StringIO()
with contextlib.redirect_stdout(out):
    restock_r3.main()
res = json.loads(out.getvalue().splitlines()[-1])
q = {b["sku"]: b["qty"] for b in res["buy"]}
case("a 120,000 SKU is held at 2 units, not sku_cap", q.get("SL", 0) <= 2, q)
restock_r3.strategy = real
# a cheap goods SKU that keeps selling may be held up to 2x sku_cap (goods under 100k: 3 h of stock, 60M of cash idle); a slow one may not
cat7 = {"SH": {"category": "G>hot", "kind": "goods", "list_price": 50000, "name": "hot"},
        "SS": {"category": "G>slow", "kind": "goods", "list_price": 50000, "name": "slow"}}
(md / "catalog.json").write_text(json.dumps(cat7), encoding="utf-8")
(md / "demand.json").write_text(json.dumps([{"item": "hot", "category": "G>hot", "kind": "goods", "median_price": 50000},
                                           {"item": "slow", "category": "G>slow", "kind": "goods", "median_price": 50000}]),
                                encoding="utf-8")
(md / "inventory.json").write_text(json.dumps({"round": 3, "balance": 40_000_000, "units": []}), encoding="utf-8")
restock_r3.strategy = lambda: dict(real(), reserve_cash=0, stock_value=40_000_000, goods_frac=1.0, max_list=500_000)
(md / "restock_state.json").write_text(json.dumps({"after": 1, "seen": [], "t": time.time() - 30, "score": {},
                                                   "sku_score": {"SH": 400.0, "SS": 40.0}}), encoding="utf-8")
out = io.StringIO()
with contextlib.redirect_stdout(out):
    restock_r3.main()
res = json.loads(out.getvalue().splitlines()[-1])
q = {b["sku"]: b["qty"] for b in res["buy"]}
case("a cheap SKU over DEAR_HOT (400) is bought up to 2x sku_cap, one under it (40) only to sku_cap",
     q.get("SH", 0) > q.get("SS", 0) and q.get("SH", 0) <= 2 * restock_r3.DEFAULTS["sku_cap"]
     and q.get("SS", 0) <= restock_r3.DEFAULTS["sku_cap"], q)
restock_r3.strategy = real
# 464 units (42% of the stock) sat unsold: a goods SKU at sku_score 2 (a few market settles, none of ours) gets 1 unit, one at 40 the depth
(md / "restock_state.json").write_text(json.dumps({"after": 1, "seen": [], "t": time.time() - 30, "score": {},
                                                   "sku_score": {"SH": 2.0, "SS": 40.0}}), encoding="utf-8")
restock_r3.strategy = lambda: dict(real(), reserve_cash=0, stock_value=40_000_000, goods_frac=1.0, max_list=500_000)
out = io.StringIO()
with contextlib.redirect_stdout(out):
    restock_r3.main()
res = json.loads(out.getvalue().splitlines()[-1])
q = {b["sku"]: b["qty"] for b in res["buy"]}
case("a goods SKU at sku_score 2 is held at 1 unit, one at 40 deeper", q.get("SH", 0) == 1 and q.get("SS", 0) > 1, q)
restock_r3.strategy = real
# 482 units (22.1M) sat unsold over a day: a not-hot goods SKU (score 40) holding a unit first seen 30 h ago is not refilled
(md / "inventory.json").write_text(json.dumps({"round": 3, "balance": 40_000_000, "units": [
    {"unit": "U1", "sku": "SS", "category": "G>slow", "kind": "goods", "grade": "A", "cost": 20000}]}), encoding="utf-8")
(md / "restock_state.json").write_text(json.dumps({"after": 1, "seen": [], "t": time.time() - 30, "score": {},
                                                   "sku_score": {"SH": 40.0, "SS": 40.0}, "first": {"U1": time.time() - 30 * 3600}}),
                                       encoding="utf-8")
restock_r3.strategy = lambda: dict(real(), reserve_cash=0, stock_value=40_000_000, goods_frac=1.0, max_list=500_000)
out = io.StringIO()
with contextlib.redirect_stdout(out):
    restock_r3.main()
res = json.loads(out.getvalue().splitlines()[-1])
q = {b["sku"]: b["qty"] for b in res["buy"]}
case("a not-hot goods SKU holding a unit unsold over 24 h gets no restock, one without gets some", q.get("SS", 0) == 0 and q.get("SH", 0) > 0, q)
restock_r3.strategy = real
# 654 goods units (50.4M) sat unsold over a day: a hot cheap SKU (score 600) holding a unit first seen 30 h ago gets no 2-3x depth
(md / "inventory.json").write_text(json.dumps({"round": 3, "balance": 40_000_000, "units": [
    {"unit": "U2", "sku": "SH", "category": "G>hot", "kind": "goods", "grade": "A", "cost": 20000}]}), encoding="utf-8")
(md / "restock_state.json").write_text(json.dumps({"after": 1, "seen": [], "t": time.time() - 30, "score": {},
                                                   "sku_score": {"SH": 600.0, "SS": 40.0}, "first": {"U2": time.time() - 30 * 3600}}),
                                       encoding="utf-8")
restock_r3.strategy = lambda: dict(real(), reserve_cash=0, stock_value=40_000_000, goods_frac=1.0, max_list=500_000)
out = io.StringIO()
with contextlib.redirect_stdout(out):
    restock_r3.main()
res = json.loads(out.getvalue().splitlines()[-1])
q = {b["sku"]: b["qty"] for b in res["buy"]}
case("a hot cheap SKU holding a unit unsold over 24 h is bought no deeper than sku_cap (held 1 included)",
     q.get("SH", 0) + 1 <= restock_r3.DEFAULTS["sku_cap"], q)
restock_r3.strategy = real
(md / "inventory.json").write_text(json.dumps({"round": 3, "balance": 40_000_000, "units": [
    {"unit": "U1", "sku": "SS", "category": "G>slow", "kind": "goods", "grade": "A", "cost": 20000}]}), encoding="utf-8")
# a hot cheap goods SKU's cap grows with its heat up to 3x sku_cap (<30k: 1586 an hour settled, we held 4.3%)
(md / "restock_state.json").write_text(json.dumps({"after": 1, "seen": [], "t": time.time() - 30, "score": {},
                                                   "sku_score": {"SH": 600.0, "SS": 40.0}}), encoding="utf-8")
restock_r3.strategy = lambda: dict(real(), reserve_cash=0, stock_value=40_000_000, goods_frac=1.0, max_list=500_000)
out = io.StringIO()
with contextlib.redirect_stdout(out):
    restock_r3.main()
res = json.loads(out.getvalue().splitlines()[-1])
q = {b["sku"]: b["qty"] for b in res["buy"]}
case("a cheap SKU at sku_score 600 is bought deeper than one at 400 (6), up to 3x sku_cap",
     6 < q.get("SH", 0) <= 3 * restock_r3.DEFAULTS["sku_cap"], q)
restock_r3.strategy = real
# a goods category whose reference is above max_list but within 2x (sofa/chair ~1.4M-2M at max_list 1.4M) is stocked
cat4 = dict(cat3, SD0={"category": "G>dear", "kind": "goods", "list_price": 120000, "name": "dear"})
(md / "catalog.json").write_text(json.dumps(cat4), encoding="utf-8")
(md / "demand.json").write_text(json.dumps(demand + [{"item": "dear", "category": "G>dear", "kind": "goods", "median_price": 120000}]),
                                encoding="utf-8")
(md / "inventory.json").write_text(json.dumps({"round": 3, "balance": 4_000_000, "units": []}), encoding="utf-8")
(md / "restock_state.json").write_text(json.dumps({"after": 1, "seen": [], "t": time.time() - 30, "score": {}, "sku_score": {}}),
                                       encoding="utf-8")
out = io.StringIO()
with contextlib.redirect_stdout(out):
    restock_r3.main()
res = json.loads(out.getvalue().splitlines()[-1])
case("goods at 120,000 (max_list 80,000, under 2x) are stocked by the category loop", "SD0" in [b["sku"] for b in res["buy"]],
     [b["sku"] for b in res["buy"]])
# breadth first: a category we hold none of gets one of the first lines even against deeper, dearer lines
case("breadth_lines and sku_cap are in the defaults", restock_r3.DEFAULTS["breadth_lines"] >= 1 and restock_r3.DEFAULTS["sku_cap"] >= 3)
# 11/6 13:44: breadth lines bought 2 gaming PCs ahead of the best seller; now a best-selling 300k+ SKU goes first and
# a breadth line takes no slow 150k+ goods
cat6 = {"SB": {"category": "G>bed", "kind": "goods", "list_price": 400000, "name": "best"},
        "SP": {"category": "G>pc", "kind": "goods", "list_price": 1200000, "name": "pc"}}
(md / "catalog.json").write_text(json.dumps(cat6), encoding="utf-8")
(md / "demand.json").write_text(json.dumps([{"item": "pc", "category": "G>pc", "kind": "goods", "median_price": 1200000},
                                           {"item": "bed", "category": "G>bed", "kind": "goods", "median_price": 400000}]),
                                encoding="utf-8")
bed = [{"unit": f"b{i}", "sku": "SB", "category": "G>bed", "kind": "goods", "grade": "A", "cost": 240000,
        "supplied_at": NOW, "expires_at": None} for i in range(2)]
(md / "inventory.json").write_text(json.dumps({"round": 3, "balance": 40_000_000, "units": bed}), encoding="utf-8")
(md / "restock_state.json").write_text(json.dumps({"after": 1, "seen": [], "t": time.time() - 30, "score": {}, "sku_score": {"SB": 1500, "SP": 10}}),
                                       encoding="utf-8")
restock_r3.strategy = lambda: dict(real(), reserve_cash=0, stock_value=2_500_000, goods_frac=1.0, max_list=1_500_000, dear_depth=8)
out = io.StringIO()
with contextlib.redirect_stdout(out):
    restock_r3.main()
q = {b["sku"]: b["qty"] for b in json.loads(out.getvalue().splitlines()[-1])["buy"]}
case("the best-selling 300k+ SKU comes before an empty category's slow 1.2M goods (room 2.5M - 0.48M held)",
     q.get("SB") == 8 and "SP" not in q, q)    # depth 8 x 3 at score 1,500: the room (2.02M / 240k) binds
restock_r3.strategy = real
# 66.6M of stock against the 70M cap, 51.3M of it in 97 slow 300k+ units: slow 300k+ units (sku_score under DEAR_HOT) do
# not count against the cap or the goods room, so the best seller still gets its room
cat8 = {"SB": cat6["SB"], "SC": {"category": "G>cold", "kind": "goods", "list_price": 400000, "name": "cold"}}
(md / "catalog.json").write_text(json.dumps(cat8), encoding="utf-8")
(md / "demand.json").write_text(json.dumps([{"item": "bed", "category": "G>bed", "kind": "goods", "median_price": 400000},
                                           {"item": "cold", "category": "G>cold", "kind": "goods", "median_price": 400000}]),
                                encoding="utf-8")
cold = [{"unit": f"c{i}", "sku": "SC", "category": "G>cold", "kind": "goods", "grade": "B", "cost": 240000,
         "supplied_at": NOW, "expires_at": None} for i in range(10)]               # 2.4M of slow 300k+ goods held
(md / "inventory.json").write_text(json.dumps({"round": 3, "balance": 40_000_000, "units": cold}), encoding="utf-8")
(md / "restock_state.json").write_text(json.dumps({"after": 1, "seen": [], "t": time.time() - 30, "score": {}, "sku_score": {"SB": 1500, "SC": 10}}),
                                       encoding="utf-8")
restock_r3.strategy = lambda: dict(real(), reserve_cash=0, stock_value=2_500_000, goods_frac=1.0, max_list=1_500_000, dear_depth=8)
out = io.StringIO()
with contextlib.redirect_stdout(out):
    restock_r3.main()
q = {b["sku"]: b["qty"] for b in json.loads(out.getvalue().splitlines()[-1])["buy"]}
case("10 slow 400,000 units (2.4M) held against a 2.5M cap do not block the best seller: bought 10 (2.5M / 240k)",
     q.get("SB") == 10 and "SC" not in q, q)
restock_r3.strategy = real
# 131 units of 300k+ held, 5.2 sold an hour: with 40 slow 300k+ units held, a slow SKU we hold none of gets no line
cat9 = dict(cat8, SD={"category": "G>dull", "kind": "goods", "list_price": 400000, "name": "dull"})
(md / "catalog.json").write_text(json.dumps(cat9), encoding="utf-8")
(md / "demand.json").write_text(json.dumps([{"item": "bed", "category": "G>bed", "kind": "goods", "median_price": 400000},
                                           {"item": "dull", "category": "G>dull", "kind": "goods", "median_price": 400000}]),
                                encoding="utf-8")
cold40 = [dict(u, unit=f"c{i}") for i, u in enumerate(cold * 4)]                      # 40 slow 300k+ units held
(md / "inventory.json").write_text(json.dumps({"round": 3, "balance": 40_000_000, "units": cold40}), encoding="utf-8")
(md / "restock_state.json").write_text(json.dumps({"after": 1, "seen": [], "t": time.time() - 30, "score": {}, "sku_score": {"SB": 1500, "SD": 10}}),
                                       encoding="utf-8")
restock_r3.strategy = lambda: dict(real(), reserve_cash=0, stock_value=50_000_000, goods_frac=1.0, max_list=1_500_000, dear_depth=8)
out = io.StringIO()
with contextlib.redirect_stdout(out):
    restock_r3.main()
q = {b["sku"]: b["qty"] for b in json.loads(out.getvalue().splitlines()[-1])["buy"]}
case("with 40 slow 300k+ units held, a slow 400,000 SKU we hold none of is not bought, the best seller is", q.get("SB", 0) > 0 and "SD" not in q, q)
restock_r3.strategy = real
# shelf_refused_h ran 3-6 an hour: a selling food category (score 8) no longer skips the shelf bar, it keeps 48 h
cat10 = {"FSHORT": {"category": "F>sold", "kind": "food", "list_price": 10000, "shelf_hours": 5, "name": "short"},
         "FLONG": {"category": "F>sold", "kind": "food", "list_price": 12000, "shelf_hours": 200, "name": "long"}}
(md / "catalog.json").write_text(json.dumps(cat10), encoding="utf-8")
(md / "demand.json").write_text(json.dumps([{"item": "sold", "category": "F>sold", "kind": "food", "median_price": 10000}]),
                                encoding="utf-8")
(md / "inventory.json").write_text(json.dumps({"round": 3, "balance": 4_000_000, "units": []}), encoding="utf-8")
(md / "restock_state.json").write_text(json.dumps({"after": 1, "seen": [], "t": time.time() - 30, "score": {"F>sold": 8.0}, "sku_score": {}}),
                                       encoding="utf-8")
out = io.StringIO()
with contextlib.redirect_stdout(out):
    restock_r3.main()
q = {b["sku"]: b["qty"] for b in json.loads(out.getvalue().splitlines()[-1])["buy"]}
case("a selling food category (score 8) still skips a 5 h SKU at the median price and takes the 200 h one",
     "FSHORT" not in q and q.get("FLONG", 0) > 0, q)
# our 300k+ units cost 0.48 of list, not the flat 0.60: a line is sized at the SKU's own median paid share
(md / "catalog.json").write_text(json.dumps({"SB": {"category": "G>bed", "kind": "goods", "list_price": 400000, "name": "bed"}}),
                                 encoding="utf-8")
(md / "demand.json").write_text(json.dumps([{"item": "bed", "category": "G>bed", "kind": "goods", "median_price": 400000}]),
                                encoding="utf-8")
paid48 = [{"unit": "p0", "sku": "SB", "category": "G>bed", "kind": "goods", "grade": "B", "cost": 192000,
           "list_price": 400000, "supplied_at": NOW, "expires_at": None}]                                   # 0.48 of list
(md / "inventory.json").write_text(json.dumps({"round": 3, "balance": 40_000_000, "units": paid48}), encoding="utf-8")
(md / "restock_state.json").write_text(json.dumps({"after": 1, "seen": [], "t": time.time() - 30, "score": {}, "sku_score": {"SB": 1500}}),
                                       encoding="utf-8")
restock_r3.strategy = lambda: dict(real(), reserve_cash=0, stock_value=2_500_000, goods_frac=1.0, max_list=1_500_000, dear_depth=8)
out = io.StringIO()
with contextlib.redirect_stdout(out):
    restock_r3.main()
q = {b["sku"]: b["qty"] for b in json.loads(out.getvalue().splitlines()[-1])["buy"]}
case("a 400,000 SKU held at 0.48 of list is sized at 192,000 a unit: 12 units from 2.3M of room (0.60 gave 9)",
     q.get("SB") == 12, q)
restock_r3.strategy = real
# A is valued over our own cost draws: 4 A units held at 0.85 of list cannot sell at a market median of 0.80, so the line stops
# taking goods, where the one capped median cost (0.66) passed it at a 17% margin (leak audit #20)
(md / "prices_r3.json").write_text(json.dumps({"skus": {f"SB|{g}": {"n": 10, "median": 0.80} for g in "ABC"}}), encoding="utf-8")
heldA = [{"unit": f"a{i}", "sku": "SB", "category": "G>bed", "kind": "goods", "grade": "A", "cost": 340000,
          "list_price": 400000, "supplied_at": NOW, "expires_at": None} for i in range(4)]                   # 0.85 of list
qs = []
for held in ([], heldA):
    (md / "inventory.json").write_text(json.dumps({"round": 3, "balance": 40_000_000, "units": held}), encoding="utf-8")
    restock_r3.strategy = lambda: dict(real(), reserve_cash=0, stock_value=50_000_000, goods_frac=1.0, max_list=1_500_000, dear_depth=8)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        restock_r3.main()
    qs.append({b["sku"]: b["qty"] for b in json.loads(out.getvalue().splitlines()[-1])["buy"]}.get("SB", 0))
restock_r3.strategy = real
case("a line whose A draws (0.85 of list) sit above the market median 0.80 is skipped, with no A held it is bought",
     qs[0] > 0 and qs[1] == 0, qs)
# a line with a quarter of its A draws above the market median is sized at 3/4 depth: 3 of 4 pooled draws (units of another SKU)
# at 0.60 sell at 0.80, 1 at 0.85 does not
mixed = [dict(heldA[i], sku="SX", cost=240000 if i < 3 else 340000) for i in range(4)]
qs = []
for held in ([], mixed):
    stf = md / "restock_state.json"           # the A draws bought in the case above stay in a_hist (48 h): start clean
    stj = json.loads(stf.read_text(encoding="utf-8")) if stf.exists() else {}
    stj.pop("a_hist", None)
    stf.write_text(json.dumps(stj), encoding="utf-8")
    (md / "prices_r3.json").write_text(json.dumps({"skus": {f"SB|{g}": {"n": 10, "median": 0.80} for g in "ABC"}}), encoding="utf-8")
    (md / "inventory.json").write_text(json.dumps({"round": 3, "balance": 40_000_000, "units": held}), encoding="utf-8")
    restock_r3.strategy = lambda: dict(real(), reserve_cash=0, stock_value=500_000_000, goods_frac=1.0, max_list=1_500_000, dear_depth=2)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        restock_r3.main()
    qs.append({b["sku"]: b["qty"] for b in json.loads(out.getvalue().splitlines()[-1])["buy"]}.get("SB", 0))
restock_r3.strategy = real
case("a line with a quarter of its A draws above the market median is bought at 3/4 depth (10 units, 8)",
     qs[-2] == 10 and qs[-1] == 8, qs[-2:])
# stale goods (held over 24 h) count at half their cost in goods_room: 10 units at 60,000 held 30 h leave room for 2 hot 400,000
# units (700k of 1M), held fresh only 1 (400k)
(md / "catalog.json").write_text(json.dumps({"SB": {"category": "G>bed", "kind": "goods", "list_price": 400000, "name": "bed"},
                                             "SX": {"category": "G>mug", "kind": "goods", "list_price": 100000, "name": "mug"}}),
                                 encoding="utf-8")
(md / "demand.json").write_text(json.dumps([{"item": "bed", "category": "G>bed", "kind": "goods", "median_price": 400000}]),
                                encoding="utf-8")
(md / "prices_r3.json").write_text(json.dumps({"skus": {}}), encoding="utf-8")
pile = [{"unit": f"x{i}", "sku": "SX", "category": "G>mug", "kind": "goods", "grade": "B", "cost": 60000,
         "list_price": 100000, "supplied_at": NOW, "expires_at": None} for i in range(10)]
qs = []
for age in (1, 30):
    (md / "inventory.json").write_text(json.dumps({"round": 3, "balance": 40_000_000, "units": pile}), encoding="utf-8")
    (md / "restock_state.json").write_text(json.dumps({"after": 1, "seen": [], "t": time.time() - 30, "score": {}, "sku_score": {"SB": 1500},
                                                       "first": {u["unit"]: time.time() - age * 3600 for u in pile}}), encoding="utf-8")
    restock_r3.strategy = lambda: dict(real(), reserve_cash=0, stock_value=1_000_000, goods_frac=1.0, max_list=1_500_000, dear_depth=8)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        restock_r3.main()
    qs.append({b["sku"]: b["qty"] for b in json.loads(out.getvalue().splitlines()[-1])["buy"]}.get("SB", 0))
restock_r3.strategy = real
case("10 goods units held 30 h count at half cost in goods_room: 2 hot 400,000 units bought, held fresh 1",
     qs[0] == 1 and qs[1] == 2, qs)
# the market settled 2 of SB in 6 h: a hot line (sku_score 1500, depth 8 x 5) is capped at 2 once 6 h of settles are kept,
# and not capped while the history is younger than 6 h
qs = []
(md / "inventory.json").write_text(json.dumps({"round": 3, "balance": 40_000_000, "units": []}), encoding="utf-8")
for since in (7, 1):
    (md / "restock_state.json").write_text(json.dumps({"after": 1, "seen": [], "t": time.time() - 30, "score": {}, "sku_score": {"SB": 1500},
                                                       "settle_since": time.time() - since * 3600,
                                                       "settle_ts": {"SB": [time.time() - 3600, time.time() - 7200]}}), encoding="utf-8")
    restock_r3.strategy = lambda: dict(real(), reserve_cash=0, stock_value=500_000_000, goods_frac=1.0, max_list=1_500_000, dear_depth=8)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        restock_r3.main()
    qs.append({b["sku"]: b["qty"] for b in json.loads(out.getvalue().splitlines()[-1])["buy"]}.get("SB", 0))
restock_r3.strategy = real
case("a dear line holds at most the market's settles of the SKU in 6 h (2 of 40), once 6 h of history exists",
     qs[0] == 2 and qs[1] > 2, qs)
# one own deal of SB in 24 h capped a hot 300k+ line at 2 (own_n / 4); now the cap floors at half the depth (40 -> 20)
(md / "restock_state.json").write_text(json.dumps({"after": 1, "seen": [], "t": time.time() - 30, "score": {}, "sku_score": {"SB": 1500},
                                                   "deals": {"SB|A": [[0.7, time.time() - 600]]}}), encoding="utf-8")
restock_r3.strategy = lambda: dict(real(), reserve_cash=0, stock_value=500_000_000, goods_frac=1.0, max_list=1_500_000, dear_depth=8)
out = io.StringIO()
with contextlib.redirect_stdout(out):
    restock_r3.main()
restock_r3.strategy = real
q = {b["sku"]: b["qty"] for b in json.loads(out.getvalue().splitlines()[-1])["buy"]}.get("SB", 0)
case("a hot 300k+ SKU with 1 own deal is capped at half its depth (20 of 40), not 2", q == 20, q)

# leak audit #29: the A cost draws keep sold units (a_hist, 48 h); the seed reads goods A talks, one per (sku, cost)
td = Path(tempfile.mkdtemp(prefix="restock-seed-"))
tn = time.time()
rows = [{"t": tn - 3600, "sku": "S1", "kind": "goods", "grade": "A", "list_price": 100, "cost": 60},
        {"t": tn - 1800, "sku": "S1", "kind": "goods", "grade": "A", "list_price": 100, "cost": 60},
        {"t": tn - 600, "sku": "S1", "kind": "goods", "grade": "B", "list_price": 100, "cost": 50},
        {"t": tn - 60 * 3600, "sku": "S2", "kind": "goods", "grade": "A", "list_price": 100, "cost": 70},
        {"t": tn - 600, "sku": "F1", "kind": "food", "grade": "A", "list_price": 100, "cost": 65}]
(td / "talks.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\nnot json\n", encoding="utf-8")
seeded = _seed(tn, path=td / "talks.jsonl")
case("the a_hist seed keeps one goods A entry per (sku, cost) within 48 h, skipping B, food, old and bad lines",
     list(seeded.values()) == [["S1", 0.6, tn - 1800]], seeded)
case("the a_hist seed is empty when the talks log is missing", _seed(tn, path=td / "none.jsonl") == {})
fs = _fseed(tn, path=td / "talks.jsonl")
case("the f_hist seed keeps food entries by (sku, grade, cost) within 48 h, skipping goods",
     list(fs.values()) == [["F1", "A", 0.65, tn - 600]], fs)
# leak audit #32: our agreed goods prices per sku|grade over 24 h, read incrementally from the talks log
rowsd = [{"t": tn - 600, "sku": "S1", "kind": "goods", "grade": "A", "list_price": 100, "price": 74, "event": "deal"},
         {"t": tn - 500, "sku": "S1", "kind": "goods", "grade": "A", "list_price": 100, "price": 70, "event": "walk"},
         {"t": tn - 30 * 3600, "sku": "S1", "kind": "goods", "grade": "A", "list_price": 100, "price": 60, "event": "deal"},
         {"t": tn - 400, "sku": "F1", "kind": "food", "grade": "A", "list_price": 100, "price": 60, "event": "deal"}]
(td / "deals.jsonl").write_text("\n".join(json.dumps(r) for r in rowsd) + "\n", encoding="utf-8")
std = {}
got = _deals(std, tn, path=td / "deals.jsonl")
pos1 = std.get("talks_pos")
with open(td / "deals.jsonl", "a", encoding="utf-8") as f:
    f.write(json.dumps({"t": tn - 10, "sku": "S1", "kind": "goods", "grade": "A", "list_price": 100, "price": 76, "event": "deal"}) + "\n")
got2 = _deals(std, tn, path=td / "deals.jsonl")
case("our_deals keeps goods deals within 24 h and reads only new lines on the next run",
     got.get("S1|A") == [[0.74, tn - 600]] and "F1|A" not in got and pos1 and [x[0] for x in got2["S1|A"]] == [0.74, 0.76], (got, got2))

# leak audit #30: an A unit that cannot sell has about 3.7 days left, so its storage charge is 0.037 of cost, not a week's 0.07
case("the A_HOLD storage charge is 3.7 days at 1% a day (0.037)", abs(restock_r3.A_HOLD - 0.037) < 1e-9, restock_r3.A_HOLD)
print(f"\n{sum(ok)}/{len(ok)} passed")
