"""The meta loop (A): reads what happened, runs A/B experiments on the rulebook, rewrites policy.json.

Called from restock.py on every 60 s tick; does real work every EVAL_MINUTES. No process of its own.

Two kinds of tuning:
  1. Experiments, for settings that can differ per deal. Half the deals (by a hash of the deal id) use the current
     value (arm a), half a challenger (arm b). An experiment ends when both arms have enough deals, or after
     MAX_HOURS. The challenger is adopted only if it beats the current value by WIN_MARGIN on the real outcome:
       shop:  profit per negotiation = settled price - unit cost (0 when the buyer never paid), from the ledger
       buyer: surplus per order = value - price of settled purchases (0 when nothing settled)
     Then the model is shown the scorecard and the history and proposes the next experiment; the code clamps the
     value to +-20% of the current one and to fixed bounds.
  2. Feedback rules, for shop-wide settings that cannot split per deal (stock size): many visits finding no stock
     and spare cash -> hold more; little stock-out and stock tying up cash -> hold less.
Every start, decision and rule change is appended to policy_history.jsonl with its numbers and reason.
Never touched: the cash reserve, the floor price (cost + margin), the buyer's budget cap.
"""
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import harness
import policy

HERE = Path(__file__).parent
MARKET_DIR = Path(os.environ.get("MARKET_DIR", HERE / ".market"))
STATE = MARKET_DIR / "tuner_state.json"
HISTORY = HERE / "policy_history.jsonl"
SALES = Path(os.environ.get("SHOP_SALES_R3") or HERE / "sales_r3.jsonl")
BUYER_MEMORY = Path(os.environ.get("BUYER_MEMORY") or HERE / "buyer_memory.jsonl")

EVAL_MINUTES = 15
RULE_MINUTES = 60
MAX_HOURS = 4
WIN_MARGIN = 0.05
STEP = 0.2
MIN_N = {"shop": 100, "buyer": 100}   # was 30/40: values flipped back and forth (0.9 <-> 0.95) on noise
MIN_T = 2.0                            # the challenger must win by about two standard errors, else keep the current
TUNABLE = {                                   # (section, key): (low, high)
    ("shop", "target_scale"): (0.85, 1.25),
    ("shop", "first_accept_share"): (0.70, 0.95),
    ("buyer", "first_offer_share"): (0.40, 0.85),
    ("buyer", "max_pay_share_of_list"): (0.70, 0.98),
}

NEXT_PROMPT = """You tune the rules of an agent that runs a shop and a buyer in an online market.
Scorecard of the last hour:
{kpis}
Recent experiments (newest last):
{history}
Settings you may test now (current value, allowed range):
{options}
Pick ONE setting from the list and a challenger value to test against the current one in an A/B experiment.
Prefer a setting not tested recently, and move in the direction the scorecard and the past results suggest.
Reply with exactly one JSON object and nothing else:
{{"setting": "<section.key from the list>", "value": <number>, "reason": "<one sentence>"}}"""


def log(msg):
    print(msg, flush=True)


def _rows(path):
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return out


def _ts(row):
    if "ts" in row:
        return row["ts"]
    try:
        return datetime.fromisoformat(row["time"]).timestamp()
    except (KeyError, ValueError):
        return 0


def record(row):
    row = {"time": datetime.now(timezone.utc).isoformat(timespec="seconds"), **row}
    with open(HISTORY, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    log(f"[tuner] {row}")


def _load(name, default):
    try:
        return json.loads((MARKET_DIR / name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def settled_units(intel):
    """unit id -> settled amount, from the ledger's settle entries the researcher keeps."""
    return {row[5].get("unit"): row[2] for row in intel.get("settles", []) if len(row) > 5 and row[5].get("unit")}


def outcomes(exp, intel):
    """Per arm: list of outcomes (profit per negotiation for the shop, surplus per order for the buyer)."""
    res = {"a": [], "b": []}
    if exp["section"] == "shop":
        paid = settled_units(intel)
        for r in _rows(SALES):
            if r.get("kind") == "open" and r.get("exp") == exp["id"] and r.get("arm") in res:
                amount = paid.get(r.get("unit"))
                res[r["arm"]].append((amount - r["cost"]) if amount else 0)
    else:
        mem = _rows(BUYER_MEMORY)
        oid = lambda o: o.get("order") if isinstance(o, dict) else o   # review.py stores the whole order object
        settled = {oid(m.get("order")): m for m in mem if m.get("kind") == "settled"}
        for m in mem:
            if m.get("round") == 3 and m.get("exp") == exp["id"] and m.get("arm") in res:
                s = settled.get(m.get("order"))
                res[m["arm"]].append(((s.get("value") or 0) - (s.get("price") or 0)) if s else 0)
    return res


def kpis(intel, since):
    sales = [r for r in _rows(SALES) if _ts(r) >= since]
    opens = [r for r in sales if r.get("kind") == "open"]
    nostock = [r for r in sales if r.get("kind") == "nostock"]
    paid = settled_units(intel)
    sold = [r for r in opens if r.get("unit") in paid]
    mem = [m for m in _rows(BUYER_MEMORY) if _ts(m) >= since]
    orders = [m for m in mem if m.get("round") == 3]
    settled = [m for m in mem if m.get("kind") == "settled"]
    refusals = {}
    for p in _rows(MARKET_DIR / "payments.jsonl")[-300:]:
        if p.get("paid") is False and p.get("reason"):
            k = re.sub(r"[0-9.]+", "N", p["reason"].split("{")[0])[:70]   # e.g. "not paying: N h of shelf life left, ..."
            refusals[k] = refusals.get(k, 0) + 1
    inv = _load("inventory.json", {})
    visits = len(opens) + len(nostock)
    return {
        "shop_visits": visits,
        "stockout_rate": round(len(nostock) / visits, 2) if visits else None,
        "shop_paid_sales": len(sold),
        "shop_profit_on_paid_sales": sum(paid[r["unit"]] - r["cost"] for r in sold),
        "buyer_orders_negotiated": len(orders),
        "buyer_settled": len(settled),
        "buyer_surplus": sum((m.get("value") or 0) - (m.get("price") or 0) for m in settled),
        "buyer_payment_refusals": refusals,
        "balance": inv.get("balance"),
        "units_in_stock": len(inv.get("units", [])),
        "stock_cost": sum(u["cost"] for u in inv.get("units", [])),
    }


def decide(exp, intel, d):
    res = outcomes(exp, intel)
    n_a, n_b = len(res["a"]), len(res["b"])
    age_h = (time.time() - exp["started"]) / 3600
    need = MIN_N[exp["section"]]
    if min(n_a, n_b) < need and age_h < MAX_HOURS:
        return False
    mean = {k: (sum(v) / len(v) if v else 0) for k, v in res.items()}
    var = {k: (sum((x - mean[k]) ** 2 for x in v) / (len(v) - 1) if len(v) > 1 else 0) for k, v in res.items()}
    se = ((var["a"] / max(n_a, 1)) + (var["b"] / max(n_b, 1))) ** 0.5
    t = (mean["b"] - mean["a"]) / se if se else 0      # Welch t: is the gap larger than the noise?
    enough = min(n_a, n_b) >= need
    win = enough and t >= MIN_T and mean["b"] > mean["a"] * (1 + WIN_MARGIN) + (0 if mean["a"] > 0 else 1)
    key = exp["key"]
    if win:
        d[exp["section"]][key] = exp["arms"]["b"]
    record({"kind": "decision", "id": exp["id"], "setting": f"{exp['section']}.{key}", "arms": exp["arms"],
            "n": {"a": n_a, "b": n_b}, "mean": {k: round(v, 1) for k, v in mean.items()}, "t": round(t, 2),
            "adopted": exp["arms"]["b"] if win else exp["arms"]["a"],
            "why": ("challenger won" if win else "not enough deals in time" if not enough else
                    f"gap within noise (t {t:.2f} < {MIN_T})" if t < MIN_T else "challenger did not win")})
    return True


def propose(section, d, k, history):
    options = {f"{s}.{key}": (d[s][key], lo, hi) for (s, key), (lo, hi) in TUNABLE.items() if s == section}
    text = "\n".join(f"- {name}: current {cur}, range {lo} to {hi}" for name, (cur, lo, hi) in options.items())
    hist = "\n".join(json.dumps({x: h.get(x) for x in ("setting", "arms", "mean", "n", "adopted", "why")})
                     for h in history if h.get("kind") == "decision")[-1500:] or "none yet"
    answer = harness.ask(NEXT_PROMPT.format(kpis=json.dumps(k, ensure_ascii=False), history=hist, options=text),
                         log) or {}
    name = answer.get("setting")
    if name not in options:                   # model gave nothing usable: test the setting tested longest ago
        tested = [h.get("setting") for h in history if h.get("kind") == "start"]
        name = min(options, key=lambda o: max((i for i, t in enumerate(tested) if t == o), default=-1))
        answer = {"value": None, "reason": "no usable proposal from the model; next setting in turn"}
    cur, lo, hi = options[name]
    try:
        v = float(answer.get("value"))
    except (TypeError, ValueError):
        v = cur * (1 + STEP / 2)
    v = round(min(hi, max(lo, cur * (1 - STEP), min(cur * (1 + STEP), v))), 3)
    if abs(v - cur) < 0.01:                   # an experiment needs two different values
        v = round(min(hi, cur + 0.05) if cur + 0.05 <= hi else max(lo, cur - 0.05), 3)
    return name, cur, v, answer.get("reason", "")


def restock_rule(k, d):
    """Stock size cannot split per deal: steer it from the stock-out rate."""
    r, cur = d["restock"], d["restock"]["target_units"]
    rate, bal = k["stockout_rate"], k["balance"] or 0
    new = cur
    if rate is not None and k["shop_visits"] >= 20:
        if rate > 0.4 and bal > 2 * r["cash_reserve"]:
            new = min(300, int(cur * 1.2))
        elif rate < 0.15 and k["stock_cost"] > 0.5 * bal:
            new = max(30, int(cur * 0.85))
    if new != cur:
        r["target_units"] = new
        record({"kind": "rule", "setting": "restock.target_units", "from": cur, "to": new,
                "why": f"stock-out rate {rate}, balance {bal}, stock cost {k['stock_cost']}"})


def tick(intel):
    st = _load("tuner_state.json", {"last_eval": 0, "last_rule": 0, "n": 0})
    now = time.time()
    if now - st["last_eval"] < EVAL_MINUTES * 60:
        return None
    st["last_eval"] = now
    d = policy.load()
    d.setdefault("experiments", [])
    history = _rows(HISTORY)
    k = kpis(intel, now - 3600)
    changed = False
    for exp in list(d["experiments"]):
        if decide(exp, intel, d):
            d["experiments"].remove(exp)
            changed = True
    if now - st["last_rule"] >= RULE_MINUTES * 60:
        st["last_rule"] = now
        before = d["restock"]["target_units"]
        restock_rule(k, d)
        changed |= d["restock"]["target_units"] != before
    for section in ("shop", "buyer"):
        if not any(e["section"] == section for e in d["experiments"]):
            name, cur, v, why = propose(section, d, k, history)
            st["n"] += 1
            exp = {"id": f"e{st['n']}", "section": section, "key": name.split(".")[1],
                   "arms": {"a": cur, "b": v}, "started": now}
            d["experiments"].append(exp)
            record({"kind": "start", "id": exp["id"], "setting": name, "arms": exp["arms"], "why": why, "kpis": k})
            changed = True
    if changed:
        policy.save(d)
    (MARKET_DIR / "tuner_state.json").write_text(json.dumps(st), encoding="utf-8")
    return [e["id"] + " " + e["key"] for e in d["experiments"]]
