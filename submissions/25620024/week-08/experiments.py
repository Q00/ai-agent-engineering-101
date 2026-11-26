"""Week 08 — experiments.py: A/B tests the sales team runs on the live shop (agile: try, measure, keep the winner).

An experiment splits SKUs in two by a stable hash: arm A and arm B each get their own overrides of the pricing
levers. Every quote records which arm it was in and whether it became a deal, so each run the sales team sees
per arm: quotes, deals, conversion, profit per quote, mean margin. It stops an experiment by naming the winner,
which then becomes the plan. At most 2 run at once; the finance limits and the cost floor still hold.

    private/experiments.json   {"active": [...], "done": [...]}
    exp_log table in market.db  one row per quote in an experiment
"""
import hashlib
import json
import time
from pathlib import Path

import market_model

FILE = Path(__file__).parent / "private" / "experiments.json"
LEVERS = {  # lever -> (low, high); what an arm may set
    "price_share": (0.60, 0.98),     # ask as a share of list (replaces the arm the bandit would draw)
    "undercut": (0.0, 0.05),         # under the 24 h market median
    "haggle_room": (0.0, 0.08),
    "warranty_goods_h": (0, 6),
    "min_margin": (0.05, 0.60),
}


def load():
    try:
        return json.loads(FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"active": [], "done": []}


def save(d):
    FILE.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")


def arm_of(exp, sku):
    h = int(hashlib.sha256(f"{exp['id']}:{sku}".encode()).hexdigest()[:8], 16)
    return "A" if h % 2 == 0 else "B"


def overrides(sku, kind=None):
    """[(exp id, arm, {lever: value})] for every active experiment that covers this SKU."""
    out = []
    for e in load().get("active", []):
        if e.get("kind") and kind and e["kind"] != kind:
            continue
        if e.get("skus") and sku not in e["skus"]:
            continue
        a = arm_of(e, sku)
        out.append((e["id"], a, e["arms"][a]))
    return out


def log(c, task, exp, arm, sku, price, cost, outcome):
    c.execute("create table if not exists exp_log (ts real, task text, exp text, arm text, sku text, price integer,"
              " cost integer, outcome text)")
    c.execute("insert into exp_log values (?,?,?,?,?,?,?,?)", (time.time(), task, exp, arm, sku, price, cost, outcome))
    c.commit()


def results(c):
    """Per active experiment and arm: quotes, deals, conversion, profit per quote, mean margin."""
    c.execute("create table if not exists exp_log (ts real, task text, exp text, arm text, sku text, price integer,"
              " cost integer, outcome text)")
    out = {}
    for e in load().get("active", []):
        r = {}
        for arm in ("A", "B"):
            n, d, prof, marg = c.execute(
                """select count(*), sum(outcome='deal'), coalesce(sum(case when outcome='deal' then price-cost end), 0),
                          avg(case when outcome='deal' then (price*1.0/cost)-1 end) from exp_log where exp=? and arm=?""",
                (e["id"], arm)).fetchone()
            r[arm] = {"quotes": n, "deals": d or 0, "conversion": round((d or 0) / n, 3) if n else None,
                      "profit_per_quote": round(prof / n) if n else None, "mean_margin": round(marg, 3) if marg else None,
                      "settings": e["arms"][arm]}
        out[e["id"]] = {"hypothesis": e.get("hypothesis"), "started_h_ago": round((time.time() - e["start"]) / 3600, 1),
                        "min_quotes_per_arm": e.get("min_quotes", 20), "arms": r}
    return out


def apply_sales_decision(new, stop, cat_kinds=("food", "goods")):
    """The sales team's experiment moves, gated: new experiments clamped to LEVERS, at most 2 active."""
    d, notes = load(), []
    for s in stop or []:
        e = next((x for x in d["active"] if x["id"] == s.get("id")), None)
        if e:
            e.update(end=time.time(), winner=s.get("winner"), why=str(s.get("why", ""))[:300])
            d["active"].remove(e)
            d["done"] = (d.get("done", []) + [e])[-30:]
    for x in new or []:
        if len(d["active"]) >= 2:
            notes.append(f"experiment {x.get('id')} not started: 2 already running")
            break
        arms = {}
        for a in ("A", "B"):
            arms[a] = {k: max(LEVERS[k][0], min(LEVERS[k][1], float(v))) for k, v in ((x.get("arms") or {}).get(a) or {}).items()
                       if k in LEVERS}
        if not arms["A"] and not arms["B"]:
            notes.append(f"experiment {x.get('id')} dropped: no known levers")
            continue
        d["active"].append({"id": str(x.get("id") or f"exp{int(time.time())}")[:40], "start": time.time(),
                            "hypothesis": str(x.get("hypothesis", ""))[:300], "arms": arms,
                            "kind": x.get("kind") if x.get("kind") in cat_kinds else None,
                            "min_quotes": int(max(10, min(200, x.get("min_quotes", 30))))})
    save(d)
    return notes
