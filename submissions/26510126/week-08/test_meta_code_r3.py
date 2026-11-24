"""Tests for meta_code_r3: the gate between the coding agent's copy and the live folder, which idea is next, and the
build budget. No model is called and nothing is deployed: HERE, the logs and the work folder point at temp folders."""
import json
import shutil
import tempfile
import time
from pathlib import Path

import meta_code_r3 as mc

RESULTS = []


def case(name, ok, detail=""):
    RESULTS.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  ({detail})" if detail and not ok else ""))


def main():
    tmp = Path(tempfile.mkdtemp(prefix="meta-code-"))
    live, work = tmp / "live", tmp / "work"
    live.mkdir()
    work.mkdir()
    for d in (live, work):
        (d / "buyer_r3.py").write_text("A = 1\n")
        (d / "seller_r3.py").write_text("B = 1\n")
    mc.HERE, mc.TESTS = live, []
    mc.LOGS = live
    mc.ATTEMPTS, mc.STRATEGIES = live / "attempts.jsonl", live / "strategies.jsonl"

    # 1. the gate: nothing changed, an allowed change, a file outside the set, a forbidden line, too many lines
    case("no change is not deployed", mc.gate(work) == ([], "no change"))
    (work / "buyer_r3.py").write_text("A = 1\nB = 2  # open lower where shops sold cheap\n")
    case("an allowed file with plain lines passes", mc.gate(work) == (["buyer_r3.py"], None), str(mc.gate(work)))
    (work / "seller_r3.py").write_text("B = 2\n")
    case("the seller is outside the allowed set", "outside the allowed set" in str(mc.gate(work)[1]))
    (work / "seller_r3.py").write_text("B = 1\n")
    (work / "buyer_r3.py").write_text("A = 1\nimport subprocess\n")
    case("a line starting a process is refused", "forbidden" in str(mc.gate(work)[1]))
    (work / "buyer_r3.py").write_text("A = 1\nx = o['mandates']['payment']\n")
    case("a line touching the payment mandate is refused", "forbidden" in str(mc.gate(work)[1]))
    (work / "buyer_r3.py").write_text("A = 1\n" + "".join(f"v{i} = {i}\n" for i in range(mc.MAX_LINES + 1)))
    case("more than MAX_LINES added lines is refused", "added lines" in str(mc.gate(work)[1]))
    (work / "buyer_r3.py").write_text("A = 1\n")
    mc.ALLOWED = mc.ALLOWED | {"test_x.py"}
    (live / "test_x.py").write_text("assert 1\nassert 2\n")
    (work / "test_x.py").write_text("assert 1\nassert 2\nassert 3\n")
    added_ok = mc.gate(work) == (["test_x.py"], None)
    (work / "test_x.py").write_text("assert 1\n")
    case("a suite may gain cases but not lose or edit them", added_ok and "tests may only be added" in str(mc.gate(work)[1]),
         str(mc.gate(work)))
    (work / "test_x.py").write_text("assert 1\nassert 2\n")
    (work / "test_x.py").unlink(); (live / "test_x.py").unlink()
    for d in (live, work):
        (d / "t_fail.py").write_text("print('boom')\nprint('0/1 passed')\n")
    mc.CANDIDATES = live / "candidates"
    mc.TESTS = ["t_fail.py"]
    (work / "buyer_r3.py").write_text("A = 2\n")
    why = mc.gate(work)[1]
    saved = list(mc.CANDIDATES.glob("*.log"))
    case("a suite failure keeps the diff and the whole output for the next engineer",
         "t_fail.py" in str(why) and saved and "boom" in saved[0].read_text() and "+A = 2" in saved[0].read_text(),
         str((why, saved)))
    mc.TESTS = []
    (work / "t_fail.py").unlink(); (live / "t_fail.py").unlink()
    (work / "buyer_r3.py").write_text("A = 1\n")

    # 2. the verdict: the change's own metric, then the guards
    before = {"buyer_close_rate": 0.6, "buyer_paid_h": 60, "shop_deals_h": 100, "shelf_refused_h": 8}
    r = {"metric": "buyer_close_rate", "expected": 0.1}
    text, why = mc.judge(r, before, dict(before, buyer_close_rate=0.66))
    case("a change that moved its metric as expected is kept", not why and "+10%" in text, text)
    case("a change whose metric fell more than WORSE is reverted",
         mc.judge(r, before, dict(before, buyer_close_rate=0.45))[1], str(mc.judge(r, before, dict(before, buyer_close_rate=0.45))))
    case("a guard falling more than DROP reverts even a change that met its metric",
         mc.judge(r, before, dict(before, buyer_close_rate=0.7, buyer_paid_h=30))[1])
    case("a lower-is-better metric that fell counts as better",
         not mc.judge({"metric": "shelf_refused_h"}, before, dict(before, shelf_refused_h=4))[1])

    # 2b. before/after: shop numbers net of the market's own change, and the aim (board points/h) decides (11/9 4d10151)
    sb = {"shop_margin_h": 412757, "shop_revenue_h": 2014600, "shop_pts_h": 0.00195, "shop_deals_h": 32.7}
    sa = {"shop_margin_h": 332056, "shop_revenue_h": 3841473, "shop_pts_h": 0.00201, "shop_deals_h": 31.3}
    rm = {"metric": "shop_margin_h", "expected": 0.1, "files": ["restock_r3.py"]}
    text, why = mc.judge(rm, sb, sa, (1.035, 1.048))
    case("margin -20% with board points flat after the market's change is kept, and says so",
         not why and "shop points/h" in text and "net of the market" in text, (text, why))
    _, why = mc.judge(rm, sb, dict(sa, shop_pts_h=0.0015), (1.0, 1.0))
    case("margin and shop points both down past WORSE reverts", any("shop_pts_h" in w for w in why), why)
    _, why = mc.judge(rm, sb, dict(sb, shop_margin_h=600000, shop_pts_h=0.0028), (1.6, 1.0))
    case("a rush hour that lifts the whole market is no gain: +45% margin under x1.6 market value is -9%",
         "-9%" in mc.judge(rm, sb, dict(sb, shop_margin_h=600000, shop_pts_h=0.0028), (1.6, 1.0))[0] and not why)
    _, why = mc.judge(rm, sb, dict(sb, shop_margin_h=300000, shop_pts_h=0.0026), (1.0, 1.0))
    case("the own metric alone does not revert while shop points rose", not why, why)
    _, why = mc.judge(rm, {"shop_margin_h": 400000}, {"shop_margin_h": 300000}, (1.0, 1.0))
    case("with no board points to measure, the own metric still reverts", why == ["shop_margin_h -25%"], why)

    # 3. the engineer's answer: the JSON on its last line, after prose
    case("the hypothesis is read from the last JSON line",
         mc.last_json('I read buyer.py.\n{"hypothesis": "x", "metric": "buyer_paid_h"}')["metric"] == "buyer_paid_h")
    case("no JSON, no hypothesis", mc.last_json("nothing to do") is None)

    # 4. what the midterm grades: points lost per board term, by its weights
    board = {"shops": [{"name": "thisjonghoon", "deals": 1, "profit": 5, "revenue": 1, "stars": 4.0},
                       {"name": "x", "deals": 1, "profit": 9, "revenue": 9, "stars": 4.9}],
             "buyers": [{"name": "thisjonghoon", "deals": 3, "saved": 0.1, "forum": 0.5, "posts": 9},
                        {"name": "x", "deals": 5, "saved": 0.3, "forum": 0.6, "posts": 1}]}
    gap = mc.metrics_r3.score_gap(board)
    case("the costliest term comes first (buyer saved: weight 0.5, beaten by the other)",
         gap[0][:2] == ("buyer", "saved") or gap[0][:2] == ("shop", "profit"), str(gap[:3]))

    # 5. the code A/B: half the orders run the change while it is on trial, every order before and after
    mc.code_ab_r3.TRIAL = tmp / "code_ab.json"
    ids = [f"o-{i:04x}" for i in range(400)]
    case("no trial: the change runs on every order", all(mc.code_ab_r3.on("k1", o) for o in ids[:20]))
    mc.code_ab_r3.start("k1")
    b = sum(mc.code_ab_r3.on("k1", o) for o in ids)
    case("on trial: about half the orders run it, the same ones every time", 160 < b < 240
         and [mc.code_ab_r3.on("k1", o) for o in ids] == [mc.code_ab_r3.on("k1", o) for o in ids], str(b))
    case("another key is not on trial", all(mc.code_ab_r3.on("k2", o) for o in ids[:20]))
    mc.code_ab_r3.stop()
    case("after the verdict the change runs on every order", all(mc.code_ab_r3.on("k1", o) for o in ids[:20]))

    # 6. one code trial at a time: no build while an automatic change waits for its verdict
    mc.CHANGES = live / "changes.jsonl"
    mc.append(mc.CHANGES, {"t": time.time(), "source": "meta (auto)", "commit": "x1", "ab_key": "k9"})
    mc.WORK = tmp / "auto-wait"
    mc.ATTEMPTS = live / "attempts-empty.jsonl"
    mc.build()
    case("no build while another automatic change waits for its verdict", not mc.WORK.exists())
    mc.ATTEMPTS = live / "attempts.jsonl"

    # 7. the build budget: one build every BUILD_EVERY
    mc.append(mc.ATTEMPTS, {"t": time.time() - 60, "key": "x"})
    mc.WORK = tmp / "auto"
    mc.build()
    case("no build within BUILD_EVERY of the last one", not mc.WORK.exists())

    # 8. the engineer's areas: ranked by points lost, discounted by tries in the last day
    d = mc.digest_r3
    case("an attempt is placed by the files it changed", d.area_of({"spec": {"files": ["forum_r3.py"]}}) == "forum")
    case("a skip is placed by its words", d.area_of({"result": "no hypothesis: the buyer's offers"}) == "buyer")
    case("a verdict that measured +12% counts as a gain", d.gained({"verdict": "kept; x: expected +3%, measured +12%"})
         and not d.gained({"verdict": "kept; x: expected +5%, measured -10%"}))
    base = tmp / "digest"
    (base / "logs").mkdir(parents=True)
    now = time.time()
    (base / "logs" / "code_attempts_r3.jsonl").write_text("".join(
        json.dumps({"t": now - 60 * i, "spec": {"files": ["forum_r3.py"]}}) + "\n" for i in range(4)))
    rows = d.focus(base, board, now)
    case("an area tried four times without a gain falls below one not tried", rows[-1][0] == "forum", str(rows))

    base = tmp / "sales"
    (base / ".market").mkdir(parents=True)
    (base / "logs").mkdir()
    url = "https://ours.example"
    led = [{"kind": "join", "ts": 0, "data": {"name": "thisjonghoon", "role": "shop", "url": url}},
           {"kind": "supply", "ts": now - 900, "data": {"shop": "thisjonghoon", "units": ["u-a", "u-b"], "total": 200000}},
           {"kind": "settle", "ts": now - 600, "data": {"shop": url + "/", "unit": "u-a", "amount": 150000}},
           {"kind": "settle", "ts": now - 500, "data": {"shop": url, "unit": "u-z", "amount": 30000}},
           {"kind": "settle", "ts": now - 400, "data": {"shop": "https://other.example", "unit": "u-b", "amount": 99}}]
    (base / ".market" / "ledger-recent.json").write_text(json.dumps(led))
    (base / "logs" / "talks_r3.jsonl").write_text("".join(json.dumps(x) + "\n" for x in (
        {"t": now - 520, "price": 30000, "cost": 20000}, {"t": now - 300, "price": 900000, "cost": 600000})))
    sold = mc.metrics_r3.settled_sales(now - 3600, now, base)
    case("shop metrics count sales paid on the ledger, not receipts signed (a 900k agreement never paid is left out)",
         [(x["price"], x["cost"]) for x in sold] == [(150000, 100000), (30000, 20000)], str(sold))

    for e in led:                                    # the same ledger with SKUs named, and a catalog
        if e["kind"] == "settle":
            e["data"]["sku"] = "G1"
    led.append({"kind": "join", "ts": 0, "data": {"name": "rival", "role": "shop", "url": "https://other.example"}})
    led.append({"kind": "settle", "ts": now - 300, "data": {"shop": "https://other.example", "sku": "G1", "amount": 900000,
                                                             "grade": "A", "warranty_hours": 6}})
    (base / ".market" / "ledger-recent.json").write_text(json.dumps(led))
    (base / ".market" / "catalog.json").write_text(json.dumps({"G1": {"list_price": 1000000}}))
    rv = d.rivals_digest(base, now=now)
    case("the rivals table ranks shops by paid revenue on the ledger and marks ours",
         rv.index("rival:") < rv.index("*thisjonghoon") and "2 sales" in rv, rv)

    import edges_r3
    lv = edges_r3.levers({"shops": [], "buyers": [
        {"name": "thisjonghoon", "deals": 5, "saved": 0.28, "posts": 10},
        {"name": "a", "deals": 5, "saved": 0.29, "posts": 5}, {"name": "b", "deals": 5, "saved": 0.30, "posts": 5},
        {"name": "c", "deals": 5, "saved": 0.27, "posts": 5}]})
    saved = next(x for x in lv if x[1] == "saved")
    case("levers: passing the next rivals on a term names the value needed and the ALL points it buys",
         saved[4][0][:2] == (1, 0.29) and saved[4][0][2] == round(0.5 * 1 / 3, 3), str(lv))

    import meta_r3
    fg = tmp / "fast"
    fg.mkdir()
    saved_paths = (meta_r3.NUMBER_TRIALS, meta_r3.STRATEGY, meta_r3.CODE_CHANGES, mc.metrics_r3.market_share,
                   mc.metrics_r3.outage_secs)
    meta_r3.NUMBER_TRIALS, meta_r3.STRATEGY, meta_r3.CODE_CHANGES = fg / "trials.json", fg / "s.json", fg / "c.jsonl"
    meta_r3.STRATEGY.write_text(json.dumps({"ask_frac": 0.85}))
    meta_r3.NUMBER_TRIALS.write_text(json.dumps([{"t": now - 4000, "changes": {"ask_frac": [0.7, 0.85]}, "reason": "x"}]))
    mc.metrics_r3.outage_secs = lambda *a, **k: 0
    # by value: before 120 sales at 1% of the market's value, after 25 sales at 0.4% (halved): reverted
    share = lambda n, v, sn=None: {"ours": n, "share_value": v, "share_n": sn if sn is not None else n / 6000}
    mc.metrics_r3.market_share = lambda t0, t1, base=None: share(120, 0.01) if t1 <= now - 3999 else share(25, 0.004)
    cut = meta_r3.fast_guard(now)
    reverted = json.loads(meta_r3.STRATEGY.read_text())["ask_frac"] == 0.7 and json.loads(meta_r3.NUMBER_TRIALS.read_text()) == []
    meta_r3.STRATEGY.write_text(json.dumps({"ask_frac": 0.85}))
    meta_r3.NUMBER_TRIALS.write_text(json.dumps([{"t": now - 4000, "changes": {"ask_frac": [0.7, 0.85]}, "reason": "x"}]))
    # fewer, dearer sales: the count share halves but the value share holds, so it is kept (11/9 review)
    mc.metrics_r3.market_share = lambda t0, t1, base=None: share(120, 0.01) if t1 <= now - 3999 else share(30, 0.011)
    kept = not meta_r3.fast_guard(now) and json.loads(meta_r3.STRATEGY.read_text())["ask_frac"] == 0.85
    (meta_r3.NUMBER_TRIALS, meta_r3.STRATEGY, meta_r3.CODE_CHANGES, mc.metrics_r3.market_share,
     mc.metrics_r3.outage_secs) = saved_paths
    case("fast guard: a change whose first hour halves our share of the market's paid value is reverted; "
         "fewer but dearer sales that hold the value share are kept", bool(cut) and reverted and kept, str(cut))

    r6 = {"metric": "buyer_saved_deal", "expected": 0.03}
    _, ab_why = mc.judge(dict(r6, ab_key="k"), {"buyer_saved_deal": 0.30}, {"buyer_saved_deal": 0.282})
    _, span_why = mc.judge(r6, {"buyer_saved_deal": 0.30}, {"buyer_saved_deal": 0.282})
    case("an A/B that measured -6% is reverted, the same move over noisy hours is kept", bool(ab_why) and not span_why,
         str((ab_why, span_why)))

    print(f"\n{sum(RESULTS)}/{len(RESULTS)} passed")
    shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
