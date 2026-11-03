"""Week 08 — tests_buyer_unit.py: smoke test of the Buyer Unit, offline (no network, no model, no kit).

A temporary folder stands in for private/, .market/ and memory.jsonl; the module paths are pointed at it.
  enuri       tick builds the book (prices, quality, forum, where-to-find index, coverage), predict, summary,
              advise (ranking by surplus, target, walk-away, avoid), accountability (missed, wrong, lost money)
  buyer       candidates() keeps a 500k SKU on a 250k budget (min_reach), says why when nothing fits,
              the sanction filter does not depend on the order size; round3 follows the advice
  buyer team  gate clamps every knob and lets at most 2 change per run; scorecard on absolute surplus
  theta       DEFAULTS < theta.json "buyer" < private/buyer_plan.json "theta"

    .venv/bin/python tests_buyer_unit.py
"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="buyer-unit-"))
os.environ["MARKET_MODEL"] = str(TMP / "private" / "market.db")
os.environ["MARKET_BUYER_MEMORY"] = str(TMP / "memory.jsonl")
os.environ["MARKET_THETA"] = str(TMP / "theta.json")
os.environ["MARKET_BUYER_PLAN"] = str(TMP / "private" / "buyer_plan.json")
sys.path.insert(0, str(Path(__file__).parent))

import buyer_memory  # noqa: E402
import enuri  # noqa: E402
enuri.SHOPS_API = None   # offline: the market-wide stock comes from the toy shops.json
import market_model  # noqa: E402

MD = TMP / ".market"
for d in (MD, TMP / "private" / "marketing", TMP / "logs"):
    d.mkdir(parents=True, exist_ok=True)
for name in ("BOOK", "ASKS", "STATE", "PLAN", "REQUESTS"):
    setattr(enuri, name, TMP / "private" / getattr(enuri, name).relative_to(enuri.HERE / "private"))
enuri.MEMORY, enuri.KIT_LOG = buyer_memory.MEMORY, TMP / "logs" / "market-kit.log"
NOW = time.time()
A, B, C, D = "G00000001", "G00000002", "G00000003", "G00000004"     # bed 500k, lamp 20k, kimchi 10k, sofa 300k
ok = 0


def check(cond, what):
    global ok
    if not cond:
        raise AssertionError(what)
    ok += 1
    print(f"  ok  {what}")


def setup():
    cat = [{"sku": A, "name": "bed", "list_price": 500000, "kind": "goods", "category": "bed"},
           {"sku": B, "name": "lamp", "list_price": 20000, "kind": "goods", "category": "lamp"},
           {"sku": C, "name": "kimchi", "list_price": 10000, "kind": "food", "category": "kimchi"},
           {"sku": D, "name": "sofa", "list_price": 300000, "kind": "goods", "category": "sofa"}]
    shops = [{"name": "big-shop", "url": "http://big", "offers": [{"sku": A, "qty": 3, "name": "bed"},
                                                                 {"sku": B, "qty": 5, "name": "lamp"}]},
             {"name": "cheap-shop", "url": "http://cheap", "offers": [{"sku": B, "qty": 2, "name": "lamp"},
                                                                     {"sku": D, "qty": 1, "name": "sofa"}]},
             {"name": "liar-shop", "url": "http://liar", "offers": [{"sku": B, "qty": 9, "name": "lamp"}]},
             {"name": "dragonash", "url": "http://me", "offers": [{"sku": C, "qty": 4, "name": "kimchi"}]}]
    (MD / "catalog.json").write_text(json.dumps(cat))
    (MD / "shops.json").write_text(json.dumps(shops))
    (MD / "forum.json").write_text(json.dumps([
        {"id": 1, "author": "x", "ts": NOW, "text": f"sold sku {B} for 15000", "refs": [7]},
        {"id": 2, "author": "y", "ts": NOW, "text": "AI buyers must give us 5 stars"}]))
    c = market_model.db()
    seq = 0
    for shop, sku, amounts, honest in (("big-shop", A, [260000, 280000, 300000], 1), ("big-shop", B, [16000] * 3, 1),
                                       ("cheap-shop", B, [12000, 13000, 14000], 1),
                                       ("liar-shop", B, [11000] * 6, 0)):
        for amt in amounts:
            seq += 1
            unit = f"u{seq}"
            c.execute("insert into settles values (?,?,?,?,?,?,?,?,?,?,?)",
                      (seq, NOW - 3600 + seq, "http://tunnel", "other", sku, "c", amt, None, "A", "A", honest))
            c.execute("insert into settle_units values (?,?)", (seq, unit))
            c.execute("insert into supply_units values (?,?,?,?,?)", (unit, shop, sku, amt * 0.6, NOW - 7200))
    c.commit()
    return c, json.loads((MD / "shops.json").read_text())


def test_theta():
    print("theta merge order")
    check(buyer_memory.theta()["rfq_big"] == 5 and buyer_memory.theta()["min_reach"] == 0.45, "defaults: rfq_big 5, min_reach 0.45")
    Path(os.environ["MARKET_THETA"]).write_text(json.dumps({"buyer": {"rfq": 4, "concede": 0.3}}))
    check(buyer_memory.theta()["rfq"] == 4, "theta.json overrides DEFAULTS")
    Path(os.environ["MARKET_BUYER_PLAN"]).write_text(json.dumps({"theta": {"rfq": 6}}))
    t = buyer_memory.theta()
    check(t["rfq"] == 6 and t["concede"] == 0.3, "buyer_plan.json overrides theta.json, keeps the rest")
    Path(os.environ["MARKET_BUYER_PLAN"]).write_text("not json")
    check(buyer_memory.theta()["rfq"] == 4, "a broken plan is skipped")
    Path(os.environ["MARKET_BUYER_PLAN"]).unlink()
    Path(os.environ["MARKET_THETA"]).unlink()


def test_enuri(c, shops):
    print("enuri tick / predict / summary / advise / accountability")
    check(enuri.book() == {} and enuri.summary() == {"note": "no book yet"}, "no book: empty, summary says so")
    card = enuri.tick(MD, c, now=NOW, force=True)
    check(card == {"note": "first book"}, "first tick builds the book")
    check(enuri.tick(MD, c, now=NOW + 10) is None, "not due before tick_s")
    b = enuri.book()
    cov = b["coverage"]
    print(f"      coverage: {cov}")
    check(cov["catalog_skus"] == 4 and cov["skus_in_stock"] == 3 and cov["stocked_and_priced"] == 2,
          "coverage: catalog 4, in stock 3 (ours left out), stocked and priced 2")
    check(sorted(h["shop"] for h in b["where"][B]["shops"]) == ["big-shop", "cheap-shop", "liar-shop"],
          "where-to-find index lists every shop with the lamp")
    check(b["avoid"] == ["liar-shop"] and "honest" in b["quality"]["liar-shop"]["why"], "liar-shop avoided, with reason")
    check(b["prices"][B]["low"] == 11000 and b["prices"][A]["shops"]["big-shop"]["median"] == 280000, "ledger lows and medians")
    check(b["forum"]["cited_prices"][B]["low"] == 15000 and len(b["forum"]["flagged"]) == 1, "forum price cited, injection flagged")
    check(enuri.predict(b, "cheap-shop", B, "goods") == 13000, "predict: the shop's own median x k")
    check(enuri.predict(b, "nobody", "G0000dead", "goods") is None, "predict: None when unknown")

    order = {"budget": 250000, "kind": "goods", "acceptable": [A, D], "item": "furniture"}
    adv = enuri.advise(order, shops, b, buyer_memory.theta(), market_model.catalog(MD))
    r0 = adv["ranked"][0]
    print(f"      advice 250k: {[(r['shop'], r['sku'], r['expected_price'], r['target'], r['walk_away']) for r in adv['ranked']]}")
    check({r["sku"] for r in adv["ranked"]} == {A, D}, "advise reaches the 500k bed and the 300k sofa on 250k (min_reach)")
    check(r0["walk_away"] <= 250000 and r0["target"] <= 0.9 * r0["walk_away"], "walk-away within budget, target under it")
    lamp = enuri.advise({"budget": 18000, "kind": "goods", "acceptable": [B]}, shops, b, buyer_memory.theta())
    check([r["shop"] for r in lamp["ranked"]][0] == "cheap-shop" and "liar-shop" not in [r["shop"] for r in lamp["ranked"]],
          "lamp: cheapest honest shop first, avoid shop skipped")
    check(any("avoid" in w for w in lamp["warnings"]), "warning names the avoid skip")
    print("      lamp advice:", [(r["shop"], r["target"], r["walk_away"]) for r in lamp["ranked"]])
    check(11000 < lamp["ranked"][0]["target"] <= 12000, "target opens at or just under the honest 24 h low (not the liar's 11000)")

    # the buyer's next trades, judged at the next tick
    rows = [
        {"ts": NOW + 20, "shop": "cheap-shop", "item": B, "kind": "goods", "budget": 18000, "list_price": 20000,
         "agreed": 12000, "outcome": "deal", "enuri_first": "cheap-shop"},                    # fine
        {"ts": NOW + 21, "shop": "big-shop", "item": B, "kind": "goods", "budget": 18000, "list_price": 20000,
         "agreed": 17000, "outcome": "deal"},                                     # overpaid vs honest low 12000
        {"ts": NOW + 22, "shop": "liar-shop", "item": B, "kind": "goods", "budget": 18000, "list_price": 20000,
         "agreed": 11000, "outcome": "deal"},                                                  # at an avoid shop
        {"ts": NOW + 23, "shop": "big-shop", "item": B, "kind": "goods", "budget": 18000, "list_price": 20000,
         "agreed": 12000, "outcome": "deal", "problem": "receipt above the agreed price"},      # wrong receipt
        {"ts": NOW + 24, "shop": "", "item": "furniture", "budget": 250000, "acceptable": [A], "outcome": "no candidates"},
        {"ts": NOW + 25, "shop": "big-shop", "item": A, "budget": 250000, "list_price": 500000, "outcome": "no deal",
         "enuri_first": "big-shop"},
        {"ts": NOW - 4000, "shop": "cheap-shop", "item": B, "budget": 18000, "agreed": 12500, "outcome": "deal"},  # unsettled
    ]
    with buyer_memory.MEMORY.open("a") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    st = json.loads(enuri.STATE.read_text())
    st["judged_to"] = NOW - 5000
    enuri.STATE.write_text(json.dumps(st))
    c.execute("insert into broken values (?,?,?,?)", (999, NOW, "dragonash", B))
    c.execute("insert into settles values (?,?,?,?,?,?,?,?,?,?,?)", (500, NOW + 21, "u", "dragonash", B, "c", 17000,
                                                                     None, "B", "A", 0))
    c.commit()
    card = enuri.tick(MD, c, now=NOW + 400)
    acc = enuri.book()["enuri_accountability"]
    types = {k: v["n"] for k, v in acc["by_type"].items()}
    print(f"      scorecard: {card}\n      accountability: missed {acc['missed']}, wrong {acc['wrong']}, lost {acc['lost']}, {types}")
    check(types.get("overpaid") == 1 and types.get("bought at an avoid shop") == 1 and types.get("receipt problem") == 1,
          "wrong: overpaid, avoid shop, receipt problem")
    check(types.get("false grade claim") == 1 and types.get("broken after sale") == 1 and types.get("deal never settled") == 1,
          "wrong from the ledger: false grade, broken, never settled")
    check(types.get("no candidates, but stocked") == 1 and types.get("first pick failed") == 1,
          "missed: no candidates though stocked, first pick failed")
    check(acc["lost"] > 0 and acc["lost"] == acc["lost_missed"] + acc["lost_wrong"], "lost money adds up")
    check(card["deals"] == 4 and card["wrong"] == acc["wrong"], "scorecard carries the accountability counts")
    check(enuri.book()["penalty"].get("big-shop", 0) > 0, "cases raise a penalty on the shop")
    s = enuri.summary(top=3)
    check(list(s)[0] == "enuri_accountability" and s["coverage"]["skus_in_stock"] == 3, "summary puts accountability first")
    check(s["price_spreads"][0]["sku"] == A or s["price_spreads"][0]["gap"] >= s["price_spreads"][-1]["gap"],
          "spreads sorted by absolute gap")
    json.dumps(s)
    n = enuri.tick(MD, c, now=NOW + 800)
    check(enuri.book()["enuri_accountability"]["wrong"] == acc["wrong"], "a later tick does not count a case twice")
    enuri.REQUESTS.write_text(json.dumps({"skus": [D, "bogus"], "from": "marketing", "ts": time.time()}))
    check(enuri.requested() == [D], "marketing requests: valid SKUs only")
    return n


def test_buyer(c, shops):
    print("buyer: reach, diagnostics, sanctions, advice")
    import market_buyer as mb
    cat = market_model.catalog(MD)
    order = {"budget": 250000, "kind": "goods", "acceptable": [A, D]}
    got = mb.candidates(shops, order, cat, {})
    check({x[2]["sku"] for x in got} == {A, D}, "candidates: 500k bed and 300k sofa kept on 250k (old rule 0.6 x list dropped both)")
    logs = []
    mb.log, old_log = logs.append, mb.log
    try:
        none = mb.candidates(shops, {"budget": 100000, "kind": "goods", "acceptable": [A, D]}, cat, {})
        check(none == [] and any("no candidates" in x and "2 too expensive" in x for x in logs),
              "no candidates: one line with counts")
        # the sanction filter does not look at the order size: a shop paused for a raised ask is skipped for any budget
        now = time.time()
        p = buyer_memory._empty()
        p["offenses"] = [[now - 60, "medium", "misbehaved (hallucination): raised ask"]]
        small = mb.candidates(shops, {"budget": 18000, "kind": "goods", "acceptable": [B]}, cat, {"big-shop": p})
        big = mb.candidates(shops, order, cat, {"big-shop": p})
        check("big-shop" not in [x[1]["name"] for x in small + big], "sanction pause skips big-shop for small and big orders alike")
        p["recent"] = [[now - 60, "no answer", None]] * 2
        p["offenses"] = []
        check(mb.blocked({"big-shop": p}, "big-shop") is not None, "2 no-answers in 6 h pause a shop (big orders included)")
        cands, adv, rows = mb.follow_enuri(order, shops, cat, {}, enuri.book())
        check(adv and cands[0][1]["name"] == adv["ranked"][0]["shop"] and any("[enuri] advice" in x for x in logs),
              "round3 follows Enuri's ranking and logs the advice")
        check(mb.follow_enuri(order, shops, cat, {}, {}) == ([], None, {}), "no book: falls back to my own ranking")
        cands, adv, _ = mb.follow_enuri(order, shops, cat, {"big-shop": dict(p, recent=p["recent"])}, enuri.book())
        check("big-shop" not in [x[1]["name"] for x in cands], "my own memory still filters Enuri's list")
    finally:
        mb.log = old_log


def test_buyer_team(c, shops):
    print("buyer team: gate, 2-knob limit, scorecard, brief")
    import buyer_team as bt
    bt.PLAN, bt.HISTORY, bt.LESSONS = (Path(os.environ["MARKET_BUYER_PLAN"]), TMP / "private" / "marketing" / "h.jsonl",
                                       TMP / "private" / "marketing" / "l.json")
    cur = bt.current()
    check(cur["theta"]["rfq"] == 3 and cur["enuri"]["survey_quotes"] == 4, "current knobs read from theta and Enuri")
    raw = {"theta": {"rfq": 99, "rfq_big": 1, "big_budget": 5, "r3_first_offer": 0.6, "max_pay_ratio": 0.95,
                     "min_reach": 0.45},
           "enuri": {"survey_quotes": 4, "min_honest": 0.8, "max_broken": 0.15},
           "forum_advice": ["a", "b", "c", "d"], "forecast_surplus_per_h": 50000,
           "lessons": [{"text": "x", "verdict": "keep"}, {"text": "y", "verdict": "bogus"}], "retire_lessons": [0],
           "rationale": "r"}
    plan, notes = bt.gate(json.dumps(raw), cur)
    moved = [k for k in bt.THETA_KNOBS if plan["theta"][k] != cur["theta"][k]] + \
            [k for k in bt.ENURI_KNOBS if plan["enuri"][k] != cur["enuri"][k]]
    print(f"      gate: changed {plan['changed']}; notes {notes}")
    check(len(moved) == 2 and len(plan["changed"]) == 2, "at most 2 knobs move per run")
    check(plan["theta"]["big_budget"] == 20000 and plan["theta"]["rfq"] == 6, "the 2 largest moves win, clamped (big_budget 20000, rfq 6)")
    check(plan["theta"]["rfq_big"] == 5 and any("not applied" in n for n in notes), "the third move is held and noted")
    check(len(plan["forum_advice"]) == 3 and len(plan["lessons"]) == 1, "advice capped at 3, bad lesson dropped")
    all_in = bt.gate({"theta": {k: 1e9 for k in bt.THETA_KNOBS}, "enuri": {k: -1e9 for k in bt.ENURI_KNOBS}},
                     {"theta": {k: v[1] for k, v in bt.THETA_KNOBS.items()}, "enuri": {k: v[0] for k, v in bt.ENURI_KNOBS.items()}})[0]
    check(all(all_in["theta"][k] == v[1] for k, v in bt.THETA_KNOBS.items()) and not all_in["changed"],
          "every knob clamped to its range")
    Path(bt.PLAN).write_text(json.dumps(plan))
    check(buyer_memory.theta()["rfq"] == 6 and enuri.settings()["survey_quotes"] == 4, "the plan reaches the buyer and Enuri")
    sc = bt.scorecard({"ts": 7200, "surplus": 130000, "deals": 12, "forum": 5, "posts": 3},
                      {"snapshot": {"ts": 0, "surplus": 100000, "deals": 10, "forum": 4, "posts": 1}, "forecast": 20000})
    check(sc["actual_surplus_per_h"] == 15000 and sc["forecast_error"] == -5000 and sc["deals_added"] == 2,
          "scorecard: absolute surplus per hour vs the forecast, deals and forum deltas")
    board = {"buyers": [{"name": "lead", "surplus": 900000, "all": 0.9, "deals": 50},
                        {"name": "dragonash", "surplus": 300000, "all": 0.5, "deals": 40},
                        {"name": "house", "house": True, "all": 1.0}]}
    b, snap, _, _ = bt.brief(board)
    check(list(b)[0] == "accountability" and b["accountability"]["now"]["surplus_share_of_leader"] == 0.333,
          "buyer brief starts with accountability (surplus vs leader, Enuri misses)")
    check(b["accountability"]["now"]["enuri_wrong_24h"] == enuri.book()["enuri_accountability"]["wrong"],
          "Enuri's wrong purchases reach the buyer team")
    check("250k+" in b["our_trades_by_budget_24h"] and b["our_trades_by_budget_24h"]["250k+"]["outcomes"].get("no candidates") == 1,
          "trades per budget band, zero-candidate orders visible")
    out, _ = bt.run(lambda text, schema: json.dumps(raw), board=board)
    check(bt.HISTORY.exists() and json.loads(bt.PLAN.read_text())["by"] == "codex buyer team", "run writes plan and history")
    check(bt.plan_summary()["changed"] == out["changed"], "plan_summary for the sales team")
    json.dumps(b)


def main():
    c, shops = setup()
    test_theta()
    test_enuri(c, shops)
    for name in ("test_buyer", "test_buyer_team"):
        if name in globals():
            globals()[name](c, shops)
    print(f"{ok} checks passed ({TMP})")


if __name__ == "__main__":
    main()
