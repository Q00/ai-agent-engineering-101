"""PC test of the analysis side: ledger collection, the forum posts, the ledger tool, the analysis
models and the runner's schedule, with a fake ledger, a fake /api/shops and a scripted model.

    python test_analysis.py

No network and no key: every model call goes to a scripted chat function, so what is tested is
the code around the models -- what they are shown, what the code lets them change, and when.
"""
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(tempfile.mkdtemp(prefix="round3-analysis-test-"))
NOW = time.time()
os.environ.update(MARKET_DIR=str(ROOT / ".market"), ROUND_END_TS=str(NOW + 10 * 86400),
                  STRATEGY_FILE=str(ROOT / "strategy.json"))

import analysis   # noqa: E402
import analysts   # noqa: E402
import common     # noqa: E402
import context    # noqa: E402
import forum      # noqa: E402
import harness    # noqa: E402
import insights   # noqa: E402
import restock    # noqa: E402
import tools      # noqa: E402

H = 3600
MD = common.MARKET_DIR
KIMCHI, BEDDING, APPLE = "Gaaaa0001", "Gbbbb0001", "Gcccc0001"
KCAT, BCAT, ACAT = "식품>김치/반찬/즉석요리>김치", "가구/침구>침구/홈데코>침구", "식품>과일>사과"
CATALOG = {s["sku"]: s for s in [
    {"sku": KIMCHI, "name": "테스트 포기김치 2kg", "category": KCAT, "list_price": 20000, "kind": "food", "shelf_hours": 168},
    {"sku": BEDDING, "name": "테스트 차렵이불 SS", "category": BCAT, "list_price": 39900, "kind": "goods", "shelf_hours": None},
    {"sku": APPLE, "name": "테스트 사과 3kg", "category": ACAT, "list_price": 15000, "kind": "food", "shelf_hours": 48},
]}
DEMAND = [{"category": KCAT, "kind": "food", "shelf_hours": 168, "median_price": 20000, "skus": 3},
          {"category": BCAT, "kind": "goods", "shelf_hours": None, "median_price": 35000, "skus": 2},
          {"category": ACAT, "kind": "food", "shelf_hours": 48, "median_price": 15000, "skus": 1}]
POSTS = [{"id": 101, "author": "Alpha Buyer", "role": "buyer", "text": "포기김치 구합니다, 48시간 이상", "thread": None},
         {"id": 102, "author": "Kiro729", "role": "buyer", "text": "김치 구합니다", "thread": None},
         {"id": 103, "author": "Beta", "role": "shop", "text": "침구 wanted? we have it", "thread": 101},
         {"id": 104, "author": "Gamma", "role": "buyer", "text": "looking for 침구, anyone?", "thread": None}]
ENTRIES = []
results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail != "" else ""), flush=True)


def add(kind, ts, **data):
    ENTRIES.append({"seq": len(ENTRIES) + 1, "ts": ts, "kind": kind, "data": data})


def build_ledger():
    for i in range(3):
        add("settle", NOW - 30 * H, order=f"g{i}", buyer="x", shop="Gamma", item=KIMCHI, amount=15000, grade="A")
    for i in range(5):
        add("order", NOW - 2 * H, order=f"o{i}")
    for i in range(12):
        add("settle", NOW - (i + 1) * 600, order=f"a{i}", buyer=f"a{i}", shop="Alpha", item=KIMCHI, amount=14000,
            grade="A" if i < 10 else "B", claim="A", claim_honest=i < 10)
    for i in range(8):
        add("settle", NOW - (i + 1) * 700, order=f"b{i}", buyer=f"c{i}", shop="Beta", item=KIMCHI, amount=14000,
            grade="B", claim="A", claim_honest=i < 3)
    for i in range(6):
        add("settle", NOW - 10 * H + i * 60, order=f"k{i}", buyer=f"b{i}", shop="Kiro729 Shop", item=BEDDING,
            amount=27930, grade="B", claim="B", claim_honest=True)
    for i in range(2):
        add("broken", NOW - 9 * H, buyer=f"b{i}", shop="Kiro729 Shop", item=BEDDING)


def fake_ledger(after):
    return [e for e in ENTRIES if e["seq"] > after][:500]


def fake_shops(path):
    return {"shops": [{"name": "Alpha", "stock": {KIMCHI: 2}}, {"name": "Kiro729 Shop", "stock": {BEDDING: 1}}]}


def outbox():
    return common.read_jsonl(MD / "outbox.jsonl")


class Model:
    """A scripted chat function: answers in order and keeps what it was shown."""
    def __init__(self, *answers):
        self.answers, self.seen = list(answers), []

    def __call__(self, messages, model, tools=None):
        self.seen.append({"messages": json.loads(json.dumps(messages, ensure_ascii=False)), "model": model, "tools": tools})
        return self.answers.pop(0) if self.answers else None


def tool_call(cid, **args):
    return {"content": None, "tool_calls": [{"id": cid, "type": "function",
                                             "function": {"name": "ledger_query", "arguments": json.dumps(args)}}]}


def answer(obj):
    return {"content": json.dumps(obj, ensure_ascii=False)}


def setup():
    MD.mkdir(parents=True)
    (ROOT / "strategy.json").write_text('{"phase": "explore"}', encoding="utf-8")
    common.write_json(MD / "catalog.json", CATALOG)
    common.write_json(MD / "demand.json", DEMAND)
    common.write_json(MD / "forum.json", POSTS)
    common.write_json(MD / "inventory.json", {"round": 3, "balance": 4_000_000, "units": [
        {"unit": "u-1", "sku": KIMCHI, "name": CATALOG[KIMCHI]["name"], "category": KCAT, "kind": "food",
         "cost": 12000, "grade": "A", "supplied_at": NOW - H, "expires_at": NOW + 150 * H}]})
    common.record("unmet", order="o-9", category="사과", kind="food")
    build_ledger()
    analysis.LOG = ROOT / "analysis.log"                  # the real logs/analysis.log is for the server's runs


def test_collect():
    print("\n[collect] the public ledger, shops' stock and the wanted categories")
    check("no market index before there is a summary", analysis.index(NOW, {}) is None)
    note = analysis.collect(NOW, fake_ledger, fake_shops)
    print("    " + note)
    settles = common.read_jsonl(MD / "ledger" / "settles.jsonl")
    check("only settle and broken entries are kept (29 settles, 2 broken; 5 order entries dropped)",
          len(settles) == 29 and len(common.read_jsonl(MD / "ledger" / "broken.jsonl")) == 2, len(settles))
    check("the next collection reads on from where this one stopped", analysis.ledger.collect(fake_ledger) == 0)
    s = common.market_file("market_summary.json")
    check("summary: 26 settles in the last 24 h (Gamma's three from 30 h ago left out)", s["settles_24h"] == 26, s["settles_24h"])
    check("kimchi sold 20 in 6 h at 70% of list", s["by_category"][KCAT]["sold_6h"] == 20
          and s["by_category"][KCAT]["price_ratio"] == 0.7, s["by_category"][KCAT])
    check("grade claims per shop: Alpha 10/12 true, Beta 3/8", (s["by_shop"]["Alpha"]["honest"], s["by_shop"]["Alpha"]["claims"],
                                                                 s["by_shop"]["Beta"]["honest"], s["by_shop"]["Beta"]["claims"]) == (10, 12, 3, 8))
    check("a breakdown is matched to the grade its buyer got (2 of 6 grade B)",
          s["broken_by_grade"] == {"B": 2} and s["goods_sold_by_grade"] == {"B": 6})
    stock = common.read_json(MD / "analysis" / "shops_stock.json")
    check("other shops' stock: our own shop's list left out", stock["stocked"] == [KCAT], stock)
    w = common.read_json(MD / "analysis" / "wanted.json")["categories"]
    check("wanted: our buyer's unmet order (사과) and other agents' wanted posts (김치, 침구), our own post not counted",
          w == {"사과": 1, "김치": 1, "침구": 1}, w)


def test_tool():
    print("\n[tool] ledger_query")
    q = tools.ledger_query(category="김치")
    check("by category name: 20 deals, 70% of list, 13 of 20 claims true, Alpha and Beta",
          (q["settled"], q["avg_price_share"], q["claims"], q["claims_true"], q["top_shops"]) == (20, 0.7, 20, 13, {"Alpha": 12, "Beta": 8}), q)
    check("the full category path works too", tools.ledger_query(category=KCAT)["settled"] == 20)
    q = tools.ledger_query(sku=BEDDING)
    check("by SKU, with the breakdowns", (q["settled"], q["broken"]) == (6, 2), q)
    check("by shop and window: Gamma 0 in 24 h, 3 in 48 h",
          (tools.ledger_query(shop="Gamma")["settled"], tools.ledger_query(shop="Gamma", hours=48)["settled"]) == (0, 3))
    check("the window is held to 1..72 hours", tools.ledger_query(hours=1000)["hours"] == 72)
    check("a tool the model made up is refused", "error" in json.loads(tools.run("delete_ledger", {})))
    check("a bad argument comes back as an error, not a crash", "error" in json.loads(tools.run("ledger_query", {"hours": "many"})))
    out = tools.run("ledger_query", {"category": "김치", "path": "../../.env"})
    check("arguments outside the schema are dropped; the answer fits in 1,500 characters",
          json.loads(out)["settled"] == 20 and len(out) <= tools.LIMIT)
    ran = []

    def counting(name, args):
        ran.append(name)
        return tools.run(name, args)
    m = Model(*[tool_call(f"c{i}", category="김치") for i in range(6)])
    m.answers.append({"content": "done"})
    harness.run_tools([{"role": "user", "content": "go"}], "m", [tools.SCHEMA], counting, 3, m)
    check("a model that keeps calling gets 3 calls, then is asked without tools",
          len(ran) == 3 and m.seen[-1]["tools"] is None and len(m.seen) == 4, (len(ran), len(m.seen)))
    two = {"content": None, "tool_calls": tool_call("x", category="김치")["tool_calls"] + tool_call("y", sku=BEDDING)["tool_calls"]}
    ran.clear()
    m = Model(two, json.loads(json.dumps(two)), {"content": "done"})
    harness.run_tools([{"role": "user", "content": "go"}], "m", [tools.SCHEMA], counting, 3, m)
    spent = [x for x in m.seen[-1]["messages"] if x["role"] == "tool" and "tool budget spent" in x["content"]]
    check("two calls at a time: the fourth is answered 'tool budget spent', not run", len(ran) == 3 and len(spent) == 1)


def test_forum():
    print("\n[forum] the market index and replies")
    before = len(outbox())
    note = analysis.index(NOW, state := {})
    line = outbox()[before]
    text, rec = line["post"], json.loads(line["post"].splitlines()[-1])
    print("    " + note)
    print("\n".join("    | " + x for x in text.splitlines()))
    check("posted as our buyer, header '[Kiro729 Market Index #1 ...]'", line["as"] == "buyer"
          and text.startswith("[Kiro729 Market Index #1 ·") and state["index_n"] == 1)
    seqs = {e["seq"] for e in ENTRIES if e["kind"] == "settle"}
    check("refs are real settle entries the market can show", line["refs"] and set(line["refs"]) <= seqs, line["refs"])
    check("the last line is the same numbers as JSON", rec["n"] == 1 and rec["settled_24h"] == 26 and rec["price_share"] == {"김치": 0.7, "침구": 0.7})
    check("the honesty table leaves our own shop out", set(rec["honest_claims"]) == {"Alpha", "Beta"}, rec["honest_claims"])
    check("no shop had it: from the shops' stock lists, without what Alpha stocks",
          rec["no_seller"] == ["침구", "사과"] and rec["no_seller_source"] == "shops' stock lists", rec["no_seller"])
    check("a grade's breakdown share waits for 10 sales of it (6 so far)", rec["breakage"] == {} and "Goods that broke" not in text)
    check("nothing private: no budget, cost or margin", not any(w in text.lower() for w in ("budget", "cost", "margin")))
    st = {}
    before = len(outbox())
    analysis.replies(NOW, st)
    new = outbox()[before:]
    check("a 'wanted' post gets a data reply as our buyer and a stock reply as our shop, in its thread",
          [(x["as"], x["thread"]) for x in new] == [("buyer", 101), ("shop", 101)], [(x["as"], x["thread"]) for x in new])
    check("the data reply names who sold it and at what share", "20 settled deals" in new[0]["post"]
          and "Alpha (12), Beta (8)" in new[0]["post"] and "70% of list" in new[0]["post"], new[0]["post"])
    print("    | " + new[0]["post"] + "\n    | " + new[1]["post"])
    before = len(outbox())
    note = analysis.replies(NOW + 600, st)
    check("10 minutes later: the next post waits (30 minutes between replies per role)", len(outbox()) == before, note)
    analysis.replies(NOW + 1900, st)
    new = outbox()[before:]
    check("after 30 minutes: 침구 answered by our buyer only (our shop has none in stock)",
          [(x["as"], x["thread"]) for x in new] == [("buyer", 104)], new)
    check("our buyer's data reply does not name our own shop", "Kiro729" not in new[0]["post"], new[0]["post"])
    check("our own post and replies in a thread are never answered; each post once",
          sorted(st["answered"]) == [101, 104] and analysis.replies(NOW + 4000, st) == "0 wanted post(s) waiting")


SELLER_PROPOSALS = [{"field": "price_adjust", "value": 0.12, "reason": "kimchi sells at 70% of list, 20 in 6 h"},
                    {"field": "price_adjust", "value": 0.30, "reason": "too far"},
                    {"field": "focus_categories", "value": ["김치"], "reason": "20 sold in 6 h"},
                    {"field": "drop_categories", "value": ["없는것"], "reason": "made up"},
                    {"field": "floor", "value": 0, "reason": "sell below cost"},
                    {"field": "warranty_extra", "value": {"C": 0.25}, "reason": "C breaks"},
                    {"field": "price_adjust", "value": -0.05, "reason": "a second one for the same field"}]
def insight(role, topic, text, evidence, use):
    return {"role": role, "topic": topic, "text": text, "evidence": evidence, "use": use}


I_KIMCHI = insight("seller", "price", "Kimchi sells at about 70% of list", "20 sold in 6 h, all at 70% of list",
                   "open kimchi at 75%; buyers rarely push it to the floor")
I_BETA = insight("buyer", "shops", "Beta's grade claims are mostly false", "3 of 8 claims true in 24 h",
                 "ask Beta for a warranty, or buy the same product elsewhere")
BUYER_PROPOSALS = [{"field": "open_ratio", "value": 0.55, "reason": "asks fall to 70%"},
                   {"field": "max_ratio", "value": 0.99, "reason": "pay anything"},
                   {"field": "avoid_shops", "value": ["Beta"], "reason": "3 of 8 claims true"},
                   {"field": "prefer_shops", "value": ["Nobody"], "reason": "made up"}]


def test_analysts():
    print("\n[analysts] role analyses and the integrated analysis")
    t1 = int(NOW) + 100
    m = Model(tool_call("q1", category="김치"), answer({"report": "Kimchi sells.", "proposals": SELLER_PROPOSALS}))
    r = analysts.role_analysis("seller", m, t1)
    shown = m.seen[1]["messages"][-1]
    check("the seller analysis called ledger_query and saw its answer", shown["role"] == "tool" and '"settled": 20' in shown["content"])
    check("its prompt carries our own records and the market summary", "stock_units" in m.seen[0]["messages"][0]["content"]
          and "top_categories" in m.seen[0]["messages"][0]["content"] and m.seen[0]["model"] == harness.MODEL_MID)
    valid = [p["valid"] for p in r["proposals"]]
    check("proposals checked by code: out of range, unknown category and a field it may not touch are marked invalid",
          valid == [True, False, True, False, False, True, True], valid)
    check("report and proposals saved", common.read_json(MD / "analysis" / "seller_report.json")["report"] == "Kimchi sells."
          and len(common.read_jsonl(MD / "analysis" / "proposals.jsonl")) == 7)
    r = analysts.role_analysis("buyer", Model(answer({"report": "ok", "proposals": BUYER_PROPOSALS})), t1)
    check("buyer: 0.55 and a shop in the ledger accepted; 0.99 and an unknown shop refused",
          [p["valid"] for p in r["proposals"]] == [True, False, True, False], [p["why_not"] for p in r["proposals"]])
    r = analysts.role_analysis("buyer", Model(), t1)
    check("no answer from the model: a report saying so, nothing proposed",
          r["report"] == "(no answer from the model)" and r["proposals"] == [])

    m = Model(answer({"adopt": [f"seller-{t1}-0", f"seller-{t1}-6", f"seller-{t1}-2", f"buyer-{t1}-0", "made-up-id"],
                      "revert": [], "note": "kimchi",
                      "insights": {"new": [I_KIMCHI, insight("both", "demand", "No number here", "many sold", "x"), I_BETA,
                                           insight("seller", "stock", "A third one", "5 units", "y")], "confirm": ["I9"]}}))
    e = analysts.integrated(m, t1 + 10)
    prompt = m.seen[0]["messages"][0]["content"]
    check("the integrated analysis sees only valid proposals, and the private cross-role facts",
          f"seller-{t1}-1" not in prompt and f"seller-{t1}-0" in prompt and "budget_over_median_list" in prompt
          and m.seen[0]["model"] == harness.MODEL_STRONG)
    got = {(c["role"], c["field"]): c["to"] for c in e["changes"]}
    check("adopted: price +0.12 moves only 0.05; one proposal per field; the made-up id ignored",
          got == {("seller", "price_adjust"): 0.05, ("seller", "focus_categories"): ["김치"], ("buyer", "open_ratio"): 0.55}, got)
    check("exploration phase: recorded, not applied", e["applied"] is False and not (MD / "analysis" / "active_rules.json").exists()
          and common.rules()["seller"]["price_adjust"] == 0.0)
    why = [r["why"] for r in e["insights"]["refused"]]
    check("insights kept while exploring: 2 new (I1, I2); no number in the evidence, a third new one and an unknown id refused",
          e["insights"]["added"] == ["I1", "I2"] and why == ["no active insight with this id", "evidence has no number",
                                                              "only 2 new insights per decision"], why)
    check("a new insight starts as testing, with its evidence and use", [(x["id"], x["status"], x["use"][:10]) for x in insights.load()]
          == [("I1", "testing", "open kimch"), ("I2", "testing", "ask Beta f")])
    e = analysts.integrated(Model(), t1 + 20)
    check("no answer from the integrated analysis: nothing changes and the entry says so",
          e["decided"] is False and e["changes"] == [] and "no answer" in e["note"])

    (ROOT / "strategy.json").write_text('{"phase": "focus"}', encoding="utf-8")
    t2 = t1 + 3 * H
    analysts.role_analysis("seller", Model(answer({"report": "again", "proposals": SELLER_PROPOSALS[:1]})), t2)
    m = Model(answer({"adopt": [f"seller-{t2}-0", f"seller-{t1}-2"], "revert": [], "insights": {"confirm": ["I1"], "refute": ["I2"]}}))
    e = analysts.integrated(m, t2 + 10)
    check("its briefing shows the insights by id, to be marked", "I1 [seller, price, testing: confirmed 0, refuted 0]"
          in m.seen[0]["messages"][0]["content"])
    check("focus phase: applied to the rules in force (price 0.0 -> 0.05)", e["applied"] is True
          and common.rules()["seller"]["price_adjust"] == 0.05, common.rules()["seller"])
    check("a proposal from before the last decision is not adopted again", common.rules()["seller"]["focus_categories"] == [])
    e = analysts.integrated(Model(answer({"adopt": [], "revert": ["seller.price_adjust", "buyer.open_ratio"],
                                          "insights": {"confirm": ["I1"], "refute": ["I2"],
                                                       "new": [dict(I_KIMCHI, text="kimchi sells at about 70% of list!")]}})), t2 + 12 * H)
    got = {x["id"]: x for x in insights.load()}
    check("confirmed twice: I1 is confirmed; refuted twice: I2 retired with the reason, kept in the file",
          got["I1"]["status"] == "confirmed" and got["I2"]["status"] == "retired" and "refuted 2" in got["I2"]["retired_why"],
          {k: v["status"] for k, v in got.items()})
    check("an insight that repeats an active one is refused", e["insights"]["refused"] == [
        {"text": "kimchi sells at about 70% of list!", "why": "repeats an active insight"}], e["insights"]["refused"])
    check("revert: the last applied change goes back; one that was never applied is ignored",
          e["reverted"] == [{"role": "seller", "field": "price_adjust", "back_to": 0.0}]
          and common.rules()["seller"]["price_adjust"] == 0.0, e["reverted"])
    log = common.read_jsonl(MD / "analysis" / "changes.jsonl")
    check("every decision is kept with its numbers", [x["applied"] for x in log] == [False, False, True, True]
          and all("seller_margin" in x["metrics"] for x in log))
    (ROOT / "strategy.json").write_text('{"phase": "explore"}', encoding="utf-8")


def parts_of(text):
    """{part title: body} of a briefing."""
    out = {}
    for block in ("\n\n" + text).split("\n\n[")[1:]:
        title, _, body = block.partition("]\n")
        out[title] = body
    return out


def test_context():
    print("\n[context] the five-part briefing")
    t = context.TITLES
    tc = int(NOW) + 100 + 16 * H
    kept = common.read_jsonl(MD / "analysis" / "contexts.jsonl")
    later = next((c for c in kept if c["role"] == "seller" and c["at"] == int(NOW) + 100 + 3 * H), {})
    check("the seller analysis 3 hours later read its earlier report", "Kimchi sells." in later.get("text", ""))
    text = context.form("seller", tc, {"our_records_24h": {"sales_24h": 0}})
    p = parts_of(text)
    print("\n".join("    | " + x for x in text.splitlines()))
    check("five parts in order", list(p) == [t[k] for k in context.LIMITS], list(p))
    ins = p[t["insights"]]
    check("insights: the seller's confirmed one with its evidence and use; the retired buyer one not shown",
          "I1 [seller, price, confirmed: confirmed 2, refuted 0]" in ins and "| use: open kimchi" in ins and "I2" not in ins, ins)
    check("no memos yet: the part says so", p[t["memos"]] == context.NONE)
    rep = p[t["reports"]]
    check("the last two seller reports, newest first", 0 <= rep.find("again") < rep.find("Kimchi sells."))
    check("what became of each proposal: applied with the 12 h after, recorded only, refused by code, not adopted",
          all(s in rep for s in ("adopted and applied as 0.05; next 12 h", "recorded only (exploration phase)",
                                 "refused by code", "not adopted")), rep)

    fake = [{"id": f"I{100 + i}", "role": ["seller", "buyer", "both"][i % 3], "topic": "price", "text": f"insight-{i:02d} " + "x" * 60,
             "evidence": f"{i} sales", "use": "y" * 20, "status": "testing", "confirmed": i, "refuted": 0, "checked": tc}
            for i in range(15)]
    fake.append({"id": "I999", "role": "seller", "topic": "price", "text": "retired insight", "evidence": "1", "use": "z",
                 "status": "retired", "confirmed": 99})
    common.write_json(MD / "analysis" / "insights.json", fake)
    memo = lambda ago, frm, to, text: {"ts": tc - ago, "from": frm, "to": to, "act": "inform", "text": text}
    with open(MD / "analysis" / "memos.jsonl", "a", encoding="utf-8") as f:
        for m in (memo(13 * H, "buyer", "seller", "old memo"), memo(2 * H, "buyer", "seller", "other shops open at 82% of list"),
                  memo(H, "buyer", "buyer", "note to self"), memo(H, "integrated", "all", "phase stays explore"),
                  memo(600, "seller", "all", "my own memo")):
            f.write(json.dumps(m) + "\n")
    with open(MD / "analysis" / "business.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(dict(context.business(tc - 3 * H), at=tc - 3 * H, balance=1)) + "\n")
    text = context.form("seller", tc, {"our_records_24h": {"sales_24h": 0}})
    p = parts_of(text)
    ins = p[t["insights"]]
    check("insights: the seller's and the shared ones, best confirmed first, none retired",
          "insight-14" in ins.splitlines()[0] and "[both" in ins.splitlines()[0] and "insight-13" not in ins
          and "retired insight" not in ins, ins[:80])
    check("insights that do not fit are left out with a line saying so", "more left out" in ins and len(ins) <= context.LIMITS["insights"])
    mem = p[t["memos"]]
    check("memos: to the seller or to all, last 12 h, newest first, not its own",
          0 <= mem.find("phase stays explore") < mem.find("82% of list") and not any(s in mem for s in ("old memo", "note to self", "my own memo")), mem)
    check("new numbers: what moved since 3 hours ago", 'change since 3.0 h ago' in p[t["numbers"]] and '"balance":[1,4000000]' in p[t["numbers"]],
          p[t["numbers"]][:160])
    check("every part within its limit", all(len(p[t[k]]) <= n for k, n in context.LIMITS.items()),
          {k: len(p[t[k]]) for k in context.LIMITS})
    text = context.form("integrated", tc, {"last_decisions": []})
    p = parts_of(text)
    check("the integrated analysis: the latest report of each role", "seller: again" in p[t["reports"]] and "buyer:" in p[t["reports"]])
    check("the integrated analysis: all insights (buyer's too) and all memos but its own",
          "insight-13" in p[t["insights"]] and "note to self" in p[t["memos"]] and "phase stays explore" not in p[t["memos"]])
    check("each briefing is kept", (MD / "analysis" / "last_context_integrated.txt").read_text(encoding="utf-8") == text
          and common.read_jsonl(MD / "analysis" / "contexts.jsonl")[-1]["role"] == "integrated")


def test_insights():
    print("\n[insights] the limits the code holds")
    saved = insights.load()
    now = int(NOW) + 20 * H
    log = insights.update({"confirm": ["I1"], "refute": ["I1"], "new": [
        insight("shop", "price", "Wrong role", "1 sale", "x"), insight("seller", "price", "y" * 201, "1 sale", "x"),
        insight("seller", "price", "No use given", "1 sale", "")]}, now)
    check("marked both ways, an unknown role, a finding over 200 characters, no use: all refused",
          [r["why"] for r in log["refused"]] == ["both confirmed and refuted", "role must be seller, buyer or both",
                                                 "text must be 1 to 200 characters", "use must be 1 to 200 characters"],
          [r["why"] for r in log["refused"]])
    common.write_json(insights.path(), [{"id": f"I{i}", "role": "seller", "topic": "price", "text": f"old {i}", "evidence": "1",
                                         "use": "u", "status": "testing", "confirmed": i, "refuted": 0, "checked": now - H}
                                        for i in range(1, 21)])
    log = insights.update({"new": [insight("buyer", "shops", "New one", "2 deals", "u"),
                                   insight("buyer", "demand", "Another", "3 deals", "u")]}, now)
    live = [x["id"] for x in insights.active(insights.load())]
    check("21st and 22nd insight: the two weakest retire, so 20 stay active", log["added"] == ["I21", "I22"]
          and log["retired"] == ["I1", "I2"] and len(live) == 20 and "I22" in live, log)
    common.write_json(insights.path(), saved)


def test_runner():
    print("\n[runner] the schedule and the lock")
    quiet = lambda messages, model, tools=None: answer({"report": "ok", "proposals": []})
    t0 = NOW + 5 * H
    done = analysis.run_once(t0, quiet, fake_ledger, fake_shops)
    check("first pass: every step is due", done == ["collect", "index", "replies", "seller", "buyer", "integrated"], done)
    check("10 minutes later: replies only", analysis.run_once(t0 + 600, quiet, fake_ledger, fake_shops) == ["replies"])
    check("an hour later: collect and replies", analysis.run_once(t0 + 3600, quiet, fake_ledger, fake_shops) == ["collect", "replies"])
    check("3 hours later: index and the role analyses too",
          analysis.run_once(t0 + 3 * H, quiet, fake_ledger, fake_shops) == ["collect", "index", "replies", "seller", "buyer"])
    lock = MD / "analysis" / "lock"
    lock.write_text("1", encoding="utf-8")
    check("a pass already running: skipped", analysis.run_once(t0 + 24 * H, quiet, fake_ledger, fake_shops) == []
          and "another pass is running" in analysis.LOG.read_text(encoding="utf-8"))
    os.utime(lock, (time.time() - 3600, time.time() - 3600))
    done = analysis.run_once(time.time() + 24 * H, quiet, fake_ledger, fake_shops)
    check("a lock left by a crash (an hour old) does not block", "collect" in done and not lock.exists(), done)

    def broken(path):
        raise RuntimeError("market down")
    done = analysis.run_once(time.time() + 48 * H, quiet, lambda after: (_ for _ in ()).throw(RuntimeError("down")), broken)
    check("one step failing does not stop the rest", "collect" not in done and "seller" in done
          and "collect failed: RuntimeError" in analysis.LOG.read_text(encoding="utf-8"), done)


def test_restock_wanted():
    print("\n[restock] a wanted category")
    inv = {"round": 3, "balance": 5_000_000, "units": []}
    summary = common.market_file("market_summary.json")
    rules = common.RULES["seller"]
    wanted = common.read_json(MD / "analysis" / "wanted.json")["categories"]
    skus = lambda out: {b["sku"] for b in out["buy"]}
    out, _ = restock.plan(inv, CATALOG, DEMAND, summary, [], [], rules, NOW)
    check("apples keep 48 h, so the shop does not list them on its own", APPLE not in skus(out), skus(out))
    out, _ = restock.plan(inv, CATALOG, DEMAND, summary, [], [], rules, NOW, wanted)
    check("...but stocks one when apples are wanted (wanted.json)", APPLE in skus(out), out["buy"])
    out, _ = restock.plan(inv, CATALOG, DEMAND, summary, [], [], dict(rules, drop_categories=["사과"]), NOW, wanted)
    check("a category the rules drop is not stocked even when wanted", APPLE not in skus(out))


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    try:
        setup()
        test_collect()
        test_tool()
        test_forum()
        test_analysts()
        test_insights()
        test_context()
        test_restock_wanted()                # before the runner's later passes age the unmet order out
        test_runner()
    finally:
        shutil.rmtree(ROOT, ignore_errors=True)
    print(f"\n{sum(results)}/{len(results)} checks passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
