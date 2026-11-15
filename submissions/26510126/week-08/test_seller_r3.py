"""Offline check of seller_r3.py against a fake Round 3 inventory (no market, no model).

    python test_seller_r3.py      # prints PASS/FAIL per case; save with | tee logs/seller-r3-test-<time>.txt

Starts the seller on a free port with MARKET_DIR pointing at a temporary folder holding inventory.json and
catalog.json, then negotiates over A2A with buyer.py's Shop client and checks the receipt: price never under the
floor, honest claim, warranty on goods, cleared food sold under cost, no unit promised twice; and the A/B test
(ab_r3.py): off without an experiment file, both arms drawn first, its arm kept and logged, a clear winner found.
"""
import asyncio
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

from buyer import Shop

HERE = Path(__file__).resolve().parent
NOW = time.time()
UNITS = [
    {"unit": "u-goods", "sku": "G1", "name": "Test Lamp", "category": "가구/침구>조명>스탠드", "list_price": 100000,
     "kind": "goods", "cost": 60000, "grade": "A", "supplied_at": NOW - 100, "expires_at": None, "cert": {}},
    {"unit": "u-goods2", "sku": "G2", "name": "Test Chair", "category": "가구/침구>가구>의자", "list_price": 100000,
     "kind": "goods", "cost": 60000, "grade": "B", "supplied_at": NOW - 50, "expires_at": None, "cert": {}},
    {"unit": "u-food", "sku": "F1", "name": "Test Kimchi", "category": "식품>김치/반찬/즉석요리>김치", "list_price": 30000,
     "kind": "food", "cost": 18000, "grade": "A", "supplied_at": NOW - 3600, "expires_at": NOW + 150 * 3600, "cert": {}},
    {"unit": "u-old", "sku": "F2", "name": "Old Kimchi", "category": "식품>김치/반찬/즉석요리>김치", "list_price": 30000,
     "kind": "food", "cost": 18000, "grade": "C", "supplied_at": NOW - 160 * 3600, "expires_at": NOW + 3 * 3600, "cert": {}},
    {"unit": "u-goods3", "sku": "G3", "name": "Test Desk", "category": "가구/침구>가구>책상", "list_price": 100000,
     "kind": "goods", "cost": 50000, "grade": "B", "supplied_at": NOW - 10, "expires_at": None, "cert": {}},
]
CATALOG = {"G1": {"shelf_hours": None}, "G2": {"shelf_hours": None}, "G3": {"shelf_hours": None}, "F1": {"shelf_hours": 168}, "F2": {"shelf_hours": 168}}


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def receipt(task):
    for a in (task or {}).get("artifacts") or []:
        for p in a.get("parts") or []:
            if "data" in p:
                return p["data"]
    return None


async def haggle(base, ask_for, offers):
    async with httpx.AsyncClient(timeout=30) as c:
        shop = Shop(c, base)
        await shop.read_card()
        t = await shop.send(f"I want to buy {ask_for}")
        for o in offers:
            if t is None or t.get("status", {}).get("state") != "TASK_STATE_INPUT_REQUIRED":
                break
            t = await shop.send(str(o), t["id"])
        return t


async def main():
    md = Path(tempfile.mkdtemp(prefix="r3md-"))
    (md / "inventory.json").write_text(json.dumps({"round": 3, "units": UNITS}), encoding="utf-8")
    (md / "catalog.json").write_text(json.dumps(CATALOG), encoding="utf-8")
    (md / "team_r3.json").write_text(json.dumps({"buyer": [{"id": "b-test", "rank": "lead", "style": {}}], "seller": [{"id": "s-test", "rank": "lead", "style": {}}], "changes": []}))
    port = free_port()
    p = subprocess.Popen([sys.executable, str(HERE / "seller_r3.py"), "--port", str(port)], cwd=HERE,
                         env=dict(os.environ, MARKET_DIR=str(md), AB_DIR=str(md), DEMAND_LOG=str(md / "demand_r3.jsonl"),
                                  SELLER_MODEL_SHARE="0",           # the A2A cases test the rules; the model is stubbed below
                                  STRATEGY_FILE=str(md / "no-strategy.json"),   # the defaults, not what the meta-harness set
                                  TEAM_ROSTER=str(md / "team_r3.json"),          # one neutral member, no style
                                  TALKS_LOG=str(md / "talks_r3.jsonl")))
    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            if httpx.get(base + "/.well-known/agent-card.json", timeout=1).status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.1)
    ok = []

    def check(name, cond, detail):
        ok.append(bool(cond))
        print(f"{'PASS' if cond else 'FAIL'}  {name}: {detail}", flush=True)

    try:
        t = await haggle(base, "Test Lamp (G1)", [50000, 70000, 76000, 79000, 80000])
        r = receipt(t)
        check("goods floor, no warranty by default", r and r["price"] >= 78000 and "warranty_hours" not in r and r["claim"] == "A",
              r)
        t = await haggle(base, "Test Kimchi (F1)", [29000])
        r = receipt(t)
        check("fresh food: the ask carries the freshness premium, so 29000 is accepted; honest claim", r and r["price"] == 29000 and r["claim"] == "A" and "warranty_hours" not in r,
              r)   # 150 of 168 h left = 89%: grade A, not near a grade line
        t = await haggle(base, "Old Kimchi (F2)", [9000, 11000, 11000])
        r = receipt(t)
        check("near-date food cleared under cost", r and 10800 <= r["price"] < 18000, r)
        t = await haggle(base, "Test Chair (G2)", ["For G2 I offer 79,000", "OK, 79,000 for G2 then"])
        r = receipt(t)
        check("offer read past the SKU's digits", r and r["price"] == 79000, r)
        t = await haggle(base, "Test Lamp (G1)", [100000])
        check("a sold unit is not offered again within 60 s", receipt(t) is None, (t or {}).get("status", {}).get("state"))
        import seller_r3                                # in-process: a buyer that left without cancelling
        seller_r3.TALKS = md / "talks_inproc.jsonl"     # never the live log
        seller_r3.STRATEGY = md / "no-strategy.json"    # nor the live strategy
        sel = seller_r3.Seller()
        sel.open["gone"] = {"unit": {"unit": "u-x"}, "t": time.time() - seller_r3.OPEN_HOLD - 1}
        sel.open["live"] = {"unit": {"unit": "u-y"}, "t": time.time()}
        held = sel.taken()
        sel2 = seller_r3.Seller()
        sel2.dealt["u-stuck"] = [time.time()] * seller_r3.STUCK_AFTER
        sel2.dealt["u-old"] = [time.time() - seller_r3.STUCK_SECS - 5] * seller_r3.STUCK_AFTER
        sel2.held.add("u-twice")
        sel2.sold["u-recent"] = time.time() - 300
        check("a unit agreed on twice in 30 min and still in stock, or sold in the last 10 minutes, is not offered; after 30 min it is again",
              {"u-stuck", "u-recent", "u-twice"} <= sel2.taken() and "u-old" not in sel2.taken(), sorted(sel2.taken()))
        sel3, t0 = seller_r3.Seller(), time.time() - 7200
        first = [sel3.agreed("u-s", t0), sel3.agreed("u-s", t0 + 60)]
        sel3.dealt["u-s"] = []                          # its 30 minutes ran out, as they do
        again = sel3.agreed("u-s", t0 + 4000)
        sel3.parked["u-s"] = time.time() + 60
        parked_now = "u-s" in sel3.taken()
        sel3.parked["u-s"] = time.time() - 1
        check("a unit set aside once is parked 2 h on its next single agreement, then offered again",
              first[0] is None and "30 min" in (first[1] or "") and "2 h" in (again or "") and parked_now
              and "u-s" not in sel3.taken() and "u-s" not in sel3.held, (first, again, sorted(sel3.taken())))
        again2 = sel3.agreed("u-s", time.time())
        check("after its park, the unit's next single agreement parks it again",
              "2 h" in (again2 or "") and "u-s" in sel3.taken(), (again2, sorted(sel3.taken())))
        sel4 = seller_r3.Seller()
        log = md / "kit.log"
        log.write_text("old line: checkout refused: unit u-0d1d is not in stock for this order\n")
        sel4.read_refusals(1000.0, log)                  # the first read starts at the log's end
        with log.open("a") as f:
            f.write("12:00:00 [acp] checkout refused: unit u-e8b8440 is not in stock for this order\n")
        sel4.read_refusals(1030.0, log)                  # within a minute: not read yet
        early = dict(sel4.refused)
        sel4.read_refusals(1061.0, log)
        check("a unit the kit's checkout refused is left out; old log lines and reads within a minute are skipped",
              early == {} and set(sel4.refused) == {"u-e8b8440"}, (early, sel4.refused))
        with log.open("a") as f:                         # the same checkout retried 2 min later: still one refusal
            f.write("12:02:00 [acp] checkout refused: unit u-e8b8440 is not in stock for this order\n")
        sel4.read_refusals(1180.0, log)
        retried = "u-e8b8440" in sel4.held
        sel4.refused = {}                                # its REFUSED_SECS ran out, as they do
        with log.open("a") as f:                         # offered again and refused again, hours later
            f.write("15:00:00 [acp] checkout refused: unit u-e8b8440 is not in stock for this order\n")
        sel4.read_refusals(1061.0 + 3 * 3600, log)
        check("a unit refused again after its 2 h is held for good; a retry minutes later is not a second refusal",
              not retried and "u-e8b8440" in sel4.held and "u-e8b8440" in sel4.taken(), (retried, sel4.held))
        check("an abandoned negotiation frees its unit", "u-x" not in held and "u-y" in held, sorted(held))
        os.environ["MARKET_DIR"] = str(md)
        seller_r3.MARKET_DIR = md
        md2 = md / "dying"
        md2.mkdir(exist_ok=True)
        dying = dict(UNITS[3], unit="u-dying", expires_at=time.time() + 30)
        (md2 / "inventory.json").write_text(json.dumps({"round": 3, "units": [dying]}), encoding="utf-8")
        seller_r3.MARKET_DIR = md2
        check("a unit within a minute of expiring is not offered (its settle would be refused)",
              seller_r3.find_unit("I want Old Kimchi (F2)") is None, dying["expires_at"] - time.time())
        md3 = md / "warrant"
        md3.mkdir(exist_ok=True)
        sofa = {"sku": "GW", "name": "Sofa", "category": "G>sofa", "list_price": 900000, "kind": "goods", "cost": 500000,
                "expires_at": None, "cert": "x"}
        units3 = [dict(sofa, unit="u-c", grade="C", supplied_at=1), dict(sofa, unit="u-a", grade="A", supplied_at=2)]
        (md3 / "inventory.json").write_text(json.dumps({"round": 3, "units": units3}), encoding="utf-8")
        (md3 / "strat.json").write_text(json.dumps({"warranty_hours": 72}), encoding="utf-8")
        seller_r3.MARKET_DIR, old_strat = md3, seller_r3.STRATEGY
        seller_r3.STRATEGY = md3 / "strat.json"
        plain = (seller_r3.find_unit("I want Sofa (GW)") or {}).get("unit")
        asked = (seller_r3.find_unit("I want Sofa (GW) with a warranty please") or {}).get("unit")
        wants_c = (seller_r3.find_unit("I want Sofa (GW), grade C is fine") or {}).get("unit")
        seller_r3.STRATEGY = old_strat
        check("goods are offered A before C (and so a buyer asking for a warranty gets the warranted unit)",
              plain == "u-a" and asked == "u-a" and wants_c == "u-c", (plain, asked, wants_c))
        seller_r3.MARKET_DIR = md
        near = dict(UNITS[2], expires_at=time.time() + (0.80 * 168 + 0.5) * 3600)   # half an hour above the A line
        check("no grade claim within an hour of a line", seller_r3.grade_now(near)[2] is None, seller_r3.grade_now(near))

        aged = dict(UNITS[0], supplied_at=time.time() - 6 * 86400)    # goods 6 days in stock (storage 1% a day)
        fresh = dict(UNITS[0], supplied_at=time.time() - 3600)
        s0 = seller_r3.strategy()
        check("goods past goods_age_days may go down to cost + 3%", seller_r3.prices(aged, s0)[1] == 61800
              and seller_r3.prices(fresh, s0)[1] == 78000, (seller_r3.prices(aged, s0), seller_r3.prices(fresh, s0)))

        # the market line: an ask above what this SKU settled for lately (75th percentile) comes down to it, the
        # floor too, but never under cost + min_margin (11/4: food opened at 0.92 of list against 0.70 settled)
        (md / "prices_r3.json").write_text(json.dumps({"skus": {fresh["sku"]: {"n": 20, "median": 0.6, "p25": 0.55,
                                                                             "p75": 0.65}}}))
        seller_r3.prices_r3.CACHE["mtime"] = None
        ask_m, floor_m = seller_r3.prices(fresh, s0)
        cost_floor = int(round(fresh["cost"] * (1 + s0["min_margin"])))
        check("the ask follows the market down to its 75th percentile, never under cost + min_margin",
              ask_m <= max(int(fresh["list_price"] * 0.65) + 1, cost_floor) and floor_m >= cost_floor - 1,
              (ask_m, floor_m, fresh["list_price"], fresh["cost"]))
        check("market_ask 0 turns the market line off", seller_r3.prices(fresh, dict(s0, market_ask=0))[0] > ask_m,
              seller_r3.prices(fresh, dict(s0, market_ask=0)))
        (md / "prices_r3.json").write_text("{}")
        seller_r3.prices_r3.CACHE["mtime"] = None

        asks = [json.loads(l) for l in (md / "demand_r3.jsonl").read_text().splitlines()]
        check("hidden demand: requests to our shop are logged, misses marked", any(a["have"] for a in asks)
              and any(not a["have"] for a in asks), [(a.get("sku"), a["have"]) for a in asks][:8])

        import ab_r3                                     # A/B: same folder as the seller subprocess
        ab_r3.EXPERIMENT, ab_r3.LOG = md / "experiment_r3.json", md / "logs" / "ab_r3.jsonl"
        check("no experiment file: the seller logs no A/B events", not ab_r3.LOG.exists(), ab_r3.LOG)
        ab_r3.EXPERIMENT.write_text(json.dumps({"on": True, "id": "t1", "param": "ask_frac", "kind": "goods",
                                                "arms": {"A": 0.95, "B": 0.70}, "min_n": 30, "p_win": 0.95}))
        check("an experiment on goods leaves food alone", ab_r3.choose("food") == (None, None), ab_r3.choose("food"))
        t = await haggle(base, "Test Desk (G3)", [80000, 80000])   # at or over both arms' floors
        r = receipt(t)
        ev = [json.loads(l) for l in ab_r3.LOG.read_text().splitlines()]
        opened = [e for e in ev if e["event"] == "open"]
        ended = [e for e in ev if e["event"] == "deal"]
        gb = seller_r3.DEFAULTS["grade_frac_b"]            # G3 is grade B: its list-based ask follows the B market
        arm_ask = {"A": round(95000 * gb), "B": round(70000 * gb)}
        check("a negotiation draws an arm, asks with it and logs open and deal with the margin",
              r and len(opened) == 1 and opened[0]["ask"] == arm_ask[opened[0]["arm"]] and len(ended) == 1
              and ended[0]["reward"] == round((r["price"] - 50000) / 100000, 4) and r["price"] >= 55000,
              {"receipt": r and r["price"], "events": ev})
        seen = [ab_r3.choose("goods")[1] for _ in range(1)]
        check("the arm with fewer negotiations is drawn first", seen == ["B" if opened[0]["arm"] == "A" else "A"], seen)
        with ab_r3.LOG.open("a") as f:                   # 40 negotiations each: B earns more
            for i in range(40):
                for arm, rew in (("A", 0.05 if i % 2 else 0.0), ("B", 0.15 if i % 3 else 0.0)):
                    f.write(json.dumps({"id": "t1", "arm": arm, "task": f"{arm}{i}", "event": "open"}) + "\n")
                    f.write(json.dumps({"id": "t1", "arm": arm, "task": f"{arm}{i}", "event": "deal" if rew else "walk",
                                        "reward": rew}) + "\n")
        picks = [ab_r3.choose("goods")[1] for _ in range(200)]
        check("the bandit leans to the arm that earns more, and a winner is called", picks.count("B") > 150
              and ab_r3.winner() == "B", {"B share": picks.count("B") / 200, "summary": ab_r3.summary()})
        # raw talks for the replay (simulate_r3.py), and warranty requests (demand_r3.warranty_demand)
        inv = json.loads((md / "inventory.json").read_text())
        inv["units"].append(dict(UNITS[4], unit="u-goods4", sku="G4", name="Test Shelf"))
        (md / "inventory.json").write_text(json.dumps(inv), encoding="utf-8")
        (md / "catalog.json").write_text(json.dumps(dict(CATALOG, G4={"shelf_hours": None})), encoding="utf-8")
        await haggle(base, "Test Shelf (G4), with a warranty please", [40000, 41000, 42000, 43000, 44000, 45000, 46000])
        talks = [json.loads(l) for l in (md / "talks_r3.jsonl").read_text().splitlines()]
        deals = [x for x in talks if x["event"] == "deal"]
        # leak audit #41: a buyer still raising within two steps of the floor gets up to 2 more turns
        import seller_r3 as _s41
        check("rising offers within two steps of the floor get more turns; flat, falling or far offers do not",
              _s41.rising_within([40000, 45000, 50000], 60000) and not _s41.rising_within([40000, 45000, 46000], 60000)
              and not _s41.rising_within([50000, 50000], 52000) and not _s41.rising_within([55000, 50000], 52000)
              and not _s41.rising_within([50000], 51000) and _s41.rising_within([None, 40000, 50000], 70000), "rising_within")
        check("every ended negotiation is one raw line with the buyer's offers, ask, floor and outcome",
              deals and all(x["offers"] and x["ask0"] and x["floor"] and x["price"] for x in deals)
              and any(x["event"] == "walk" for x in talks), [(x["sku"], x["event"], x["offers"], x["price"]) for x in talks])
        check("a request that asks for a warranty is marked", any(x["warranty_ask"] for x in talks)
              and any(json.loads(l).get("warranty") for l in (md / "demand_r3.jsonl").read_text().splitlines()),
              [x["warranty_ask"] for x in talks])
        import simulate_r3, meta_r3
        simulate_r3.TALKS = md / "talks_r3.jsonl"
        res = simulate_r3.run(meta_r3.current(), meta_r3.BOUNDS)
        check("replaying what we did gives what happened (fidelity 1.0 on fixed test buyers), playbooks are scored",
              res["fidelity"] == 1.0 and res["actual"]["deals"] == len(deals) and "costco" in res and "walmart_edlp" in res,
              {k: res[k] for k in ("fidelity", "actual", "current", "costco", "walmart_edlp")})
        # the seller's model turn (stubbed: no model call): honest limits kept whatever it answers
        import harness
        seller_r3.MARKET_DIR = md
        sel = seller_r3.Seller()
        unit = dict(UNITS[0])
        mk = lambda: {"unit": unit, "ask": 95000, "floor": 78000, "offers": [70000], "turns": 1, "model_calls": 0}
        s = dict(seller_r3.strategy(), max_turns=6, step=0.4)   # the live strategy file moves step
        async def fake(answer):
            async def ask(prompt, **kw):
                fake.prompt = prompt
                return answer
            harness.ask = ask
            return await sel.think("t-test", mk(), 70000, s)
        a = await fake({"why": "fair", "action": "counter", "price": 50000})
        b = await fake({"why": "greedy", "action": "counter", "price": 120000})
        c = await fake({"why": "take it", "action": "accept", "price": 70000})
        d = await fake({"oops": 1})
        e = await fake({"why": "ok", "action": "counter", "price": 88000})
        check("the model's counter is kept between the floor and our last ask, an accept under the floor and a bad "
              "answer go to the rules, and the prompt carries the unit, offers, stock and demand",
              a["price"] == 78000 and b["price"] == 95000 and c is None and d is None and e["price"] == 88000
              and "[70000]" in fake.prompt and "Our stock of this SKU: 1" in fake.prompt and "Demand for it" in fake.prompt
              and "fixed rule would counter at 85000" in fake.prompt,
              [a, b, c, d, e])
        await seller_r3.MODEL_SLOTS.acquire(); await seller_r3.MODEL_SLOTS.acquire(); await seller_r3.MODEL_SLOTS.acquire()
        busy = await fake({"why": "x", "action": "counter", "price": 90000})
        for _ in range(3):
            seller_r3.MODEL_SLOTS.release()
        check("with every model slot busy the rules decide at once", busy is None, busy)
        f_old = dict(UNITS[2], unit="u-f-old", expires_at=time.time() + 100 * 3600)
        f_new = dict(UNITS[2], unit="u-f-new", expires_at=time.time() + 700 * 3600)
        need = seller_r3.needed_hours("I need it to last 540.0 hours: please ship a unit with at least 594.0 hours of shelf life left")
        check("food: the unit that clears first among those lasting as long as the buyer asked, else the freshest",
              need == 594.0 and seller_r3.pick([f_old, f_new], need)["unit"] == "u-f-new"
              and seller_r3.pick([f_old, f_new], 0)["unit"] == "u-f-old"
              and seller_r3.pick([f_old, f_new], 900)["unit"] == "u-f-new", need)
        rows = [dict(x, model_on=True) for x in talks[:1]] + talks[1:]
        (md / "talks_mix.jsonl").write_text("".join(json.dumps(x) + "\n" for x in rows))
        simulate_r3.TALKS = md / "talks_mix.jsonl"
        mv = simulate_r3.model_vs_rule()
        check("model and rule talks are compared side by side; fidelity leaves model talks out",
              mv["model"]["talks"] == 1 and mv["rule"]["talks"] == len(talks) - 1
              and simulate_r3.run(meta_r3.current(), meta_r3.BOUNDS)["fidelity"] == 1.0, mv)
    finally:
        p.terminate()
    print(f"\n{sum(ok)}/{len(ok)} passed")


if __name__ == "__main__":
    asyncio.run(main())
