"""Situation -> strategy. seller_r3.py, restock.py and market_buyer.py ask this module for their parameters.

    from policy import shop_params, buyer_params
    regime, p, sig = shop_params()      # p["ask_share"], p["floor_mult"], p["buy"], ...

params.json holds a base set per role and overrides per regime; tuning means editing that file, not code.
Signals, read fresh on each call (the seller caches them CACHE seconds):
  shop:  hours since Round 3 opened (first time inventory.json says round 3, kept in policy_shop.json),
         hours to the end (params.json "end"), balance, stock at cost, own Round 3 deals (seller_r3_log.jsonl)
  buyer: hours to the end, deal rate over the last low_fill_window Round 3 orders (buyer_memory.json)
Shop regimes, first match wins:
  LIQUIDATE_LAST (end within 2 h) > LIQUIDATE (6 h) > WIND_DOWN (24 h) > LAUNCH (first 12 h and under 100 deals)
  > CASH_TIGHT (balance under 1M) > FLUSH (balance 8M or more) > STEADY
  Unsold stock at the end is a loss of its whole cost, so the last hours sell at any price above zero.
Sell-through (clearance): within clear_window_hours of the end, a unit whose category will not sell out at our
current sales rate is discounted by how far short it falls (clearance() below), so slow stock is cleared before
every shop dumps at the end, while categories that sell keep full prices.
Competitor asks: our buyer writes the first ask of every other shop it negotiates with to market_intel.jsonl;
competitor_ask() gives the median first ask as a share of list price per category, and the seller moves its own
opening ask toward just under it, within a band around the regime's number.
Reputation: with rep_min+ Round 3 reviews among our newest (restock.py fetches them), an average under rep_low
lowers every ask share by rep_step (cheaper, to win stars back) and one at rep_high or more raises it by rep_step.
Buyer regimes: END_SALE (end within 12 h: shops clear stock, pay less) > LOW_FILL (deal rate under 30 %) > STEADY.
A regime change is written to logs/policy.log with the signals that caused it.
POLICY_NOW (epoch seconds) overrides the clock and POLICY_DIR the state/log folder, for tests.
"""
import json
import math
import os
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
PARAMS = HERE / "params.json"
MARKET_DIR = Path(os.environ.get("MARKET_DIR", HERE / ".market"))
SALES_LOG = Path(os.environ.get("SELLER_LOG", HERE / "seller_r3_log.jsonl"))
MEMORY = Path(os.environ.get("BUYER_MEMORY", HERE / "buyer_memory.json"))
INTEL = Path(os.environ.get("MARKET_INTEL", HERE / "market_intel.jsonl"))
RESTOCK_STATE = Path(os.environ.get("RESTOCK_STATE", HERE / "restock_state.json"))
HALF_LIFE = 6.0                                         # hours, the same as restock.py
OUT = Path(os.environ.get("POLICY_DIR", HERE))          # tests point this elsewhere
LOG = OUT / "logs" / "policy.log"
STATE = {"shop": OUT / "policy_shop.json", "buyer": OUT / "policy_buyer.json"}


def read_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def write_json(path, data):
    """Atomic and never raising: the seller and restock both write policy_shop.json, and a half-written or
    colliding write must neither crash a negotiation nor lose the Round 3 opening time."""
    try:
        tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        pass


def now():
    return float(os.environ.get("POLICY_NOW") or time.time())


def category_rates():
    """Our sales per hour by category. restock.py keeps counts that halve every HALF_LIFE hours; such a count
    settles at rate x HALF_LIFE / ln 2."""
    st = read_json(RESTOCK_STATE, {})
    k = 0.5 ** (max(0.0, now() - st.get("t", now())) / 3600 / HALF_LIFE)
    return {c: v * k * math.log(2) / HALF_LIFE for c, v in (st.get("own_cat") or {}).items()}


def competitor_asks(window_hours):
    """category -> sorted first asks of other shops as a share of list price, over the last window_hours."""
    since, out = now() - window_hours * 3600, {}
    try:
        with open(INTEL, encoding="utf-8") as f:
            lines = f.readlines()[-5000:]
    except OSError:
        return out
    for line in lines:
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("t", 0) >= since and r.get("category") and r.get("list_price") and r.get("first_ask"):
            out.setdefault(r["category"], []).append(r["first_ask"] / r["list_price"])
    return {c: sorted(v) for c, v in out.items()}


def competitor_ask(asks, category, min_samples):
    """Median competitor first ask (share of list) for a category, or None with fewer than min_samples."""
    v = asks.get(category) or []
    return v[len(v) // 2] if len(v) >= min_samples else None


def clearance(stock_in_cat, rate, hours_left, window):
    """1 when the category's stock sells before the end at `rate` per hour, or the end is over `window` hours
    away; below 1, the share of that stock the time left can sell; 0 for a category we have not sold in."""
    if hours_left > window:
        return 1.0
    if hours_left <= 0 or rate <= 0:
        return 0.0
    return min(1.0, hours_left * rate / max(stock_in_cat, 1))


AUTO = Path(os.environ.get("PARAMS_AUTO", HERE / "params_auto.json"))   # strategist.py, mode "apply"
AUTO_BUYER = Path(os.environ.get("PARAMS_AUTO_BUYER", HERE / "params_auto_buyer.json"))   # buyer_strategist.py


def merged(cfg, role, regime):
    p = json.loads(json.dumps(cfg[role]["base"]))
    auto = read_json(AUTO if role == "shop" else AUTO_BUYER, {})   # the strategists' moves sit between base
    p["sku_auto"] = auto.get("sku") or {}
    p["cat_auto"] = auto.get("category") or {}
    p["shop_auto"] = auto.get("shop") or {}          # buyer_strategist.py: per-shop bias and cap adjustment
    for k, v in cfg[role].get(regime, {}).items():
        p[k] = dict(p[k], **v) if isinstance(v, dict) and isinstance(p.get(k), dict) else v
    # 11-08: the shop strategist manages the whole shop, so its settings now win over the situation regimes, except
    # in the end-of-round ones (WIND_DOWN, LIQUIDATE*) whose clearance rules protect the book at the close
    end = regime in ("WIND_DOWN", "LIQUIDATE", "LIQUIDATE_LAST")
    for k, v in (auto.get("globals") or {}).items():
        if end and role == "shop" and k in cfg[role].get(regime, {}):
            continue
        if k.startswith("ask_share_") and isinstance(p.get("ask_share"), dict):   # shop price level per grade
            p["ask_share"] = dict(p["ask_share"], **{k[-1]: v})
        elif k in ("warranty_b", "warranty_c") and role == "shop":              # shop warranty by grade
            g = [x for x in p.get("warranty_grades", []) if x != k[-1].upper()]
            p["warranty_grades"] = sorted(g + ([k[-1].upper()] if v >= 0.5 else []))
        elif k == "buy" and role == "shop":
            p["buy"] = v >= 0.5
        elif k in p:
            p[k] = v
    # 11-15 (user: 'raise it slowly'): keep_cash_ramp {t0, from, to, per_h} moves keep_cash up gradually, so restock
    # slows instead of stopping (60 M at once stopped it and our revenue fell to 10.7 M/h)
    kr = p.get("keep_cash_ramp") if role == "shop" else None
    if isinstance(kr, dict) and kr.get("t0"):
        import time as _t
        p["keep_cash"] = int(min(kr.get("to", p.get("keep_cash", 0)),
                                 kr.get("from", 0) + kr.get("per_h", 0) * max(0.0, _t.time() - kr["t0"]) / 3600))
    return p


def note(role, regime, sig):
    """Remember the regime; on a change, one line in logs/policy.log."""
    st = read_json(STATE[role], None)
    if st is None:
        if STATE[role].exists():
            return                                      # unreadable right now (another process writing): skip
        st = {}
    if st.get("regime") != regime:
        LOG.parent.mkdir(exist_ok=True)
        stamp = datetime.fromtimestamp(sig["now"]).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
        shown = {k: (round(v, 1) if isinstance(v, float) else v) for k, v in sig.items() if k != "now"}
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(f"{stamp} [{role}] {st.get('regime', '-')} -> {regime} {json.dumps(shown, ensure_ascii=False)}\n")
    st["regime"] = regime
    write_json(STATE[role], st)


def shop_signals(cfg):
    t = now()
    inv = read_json(MARKET_DIR / "inventory.json", {})
    st = read_json(STATE["shop"], None)
    readable = st is not None or not STATE["shop"].exists()   # None + file present: someone is writing it
    st = st or {}
    if inv.get("round", 0) >= 3 and not st.get("open_ts") and readable:
        st["open_ts"] = t
        write_json(STATE["shop"], st)
    units = [u for u in inv.get("units") or [] if u.get("grade") != "expired"]
    deals = 0
    if SALES_LOG.is_file():
        with open(SALES_LOG, encoding="utf-8") as f:
            for line in f:
                if '"event": "deal"' in line and '"category": null' not in line:
                    deals += 1
    return {"now": t, "round": inv.get("round", 0),
            "hours_open": (t - st["open_ts"]) / 3600 if st.get("open_ts") else 0.0,
            "hours_left": (datetime.fromisoformat(cfg["end"]).timestamp() - t) / 3600,
            "balance": inv.get("balance") or 0, "stock_cost": sum(u.get("cost") or 0 for u in units),
            "units": len(units), "deals": deals}


def shop_params():
    cfg = read_json(PARAMS, {})
    r, sig = cfg["regimes"], shop_signals(cfg)
    if sig["round"] < 3:
        regime = "STEADY"
    elif sig["hours_left"] <= r["liquidate_last_hours"]:
        regime = "LIQUIDATE_LAST"
    elif sig["hours_left"] <= r["liquidate_hours"]:
        regime = "LIQUIDATE"
    elif sig["hours_left"] <= r["wind_down_hours"]:
        regime = "WIND_DOWN"
    elif sig["hours_open"] < r["launch_hours"] and sig["deals"] < r["launch_deals"]:
        regime = "LAUNCH"
    elif sig["balance"] < r["cash_tight"]:
        regime = "CASH_TIGHT"
    elif sig["balance"] >= r["flush"]:
        regime = "FLUSH"
    else:
        regime = "STEADY"
    p = merged(cfg, "shop", regime)
    from shop_guard import grades_now           # 11-07: the book-lead guard can cut the warranty grades
    p["warranty_grades"] = grades_now(p.get("warranty_grades", []))
    rep = read_json(RESTOCK_STATE, {}).get("rep") or {}
    sig["stars"] = rep.get("avg")
    if rep.get("avg") is not None and rep.get("n", 0) >= p.get("rep_min", 5) and now() - rep.get("t", 0) < 3600:
        step = -p["rep_step"] if rep["avg"] < p["rep_low"] else p["rep_step"] if rep["avg"] >= p["rep_high"] else 0
        p["ask_share"] = {g: round(min(0.98, max(0.3, v + step)), 3) for g, v in p["ask_share"].items()}
    if sig["round"] >= 3:
        note("shop", regime, sig)
    return regime, p, sig


def buyer_params():
    cfg = read_json(PARAMS, {})
    r, t = cfg["regimes"], now()
    trades = [x for x in read_json(MEMORY, {}).get("trades", []) if x.get("category")][-r["low_fill_window"]:]
    rate = sum(x.get("outcome") == "deal" for x in trades) / len(trades) if trades else None
    sig = {"now": t, "hours_left": (datetime.fromisoformat(cfg["end"]).timestamp() - t) / 3600,
           "orders": len(trades), "deal_rate": rate}
    if sig["hours_left"] <= r["end_sale_hours"]:
        regime = "END_SALE"
    elif rate is not None and len(trades) >= r["low_fill_window"] and rate < r["low_fill_rate"]:
        regime = "LOW_FILL"
    else:
        regime = "STEADY"
    if trades:
        note("buyer", regime, sig)
    p = merged(cfg, "buyer", regime)
    # 11-11: guards.py may override a buyer knob for a few hours (e.g. goods_delta after a break spike); the
    # strategist's own settings stay untouched underneath
    try:
        for k, v in (read_json(HERE / ".market" / "guard_overrides.json", {}) or {}).items():
            if isinstance(v, dict) and v.get("until", 0) > t and k in p:
                p[k] = v["value"]
    except Exception:
        pass
    return regime, p, sig


if __name__ == "__main__":
    for name, fn in (("shop", shop_params), ("buyer", buyer_params)):
        regime, p, sig = fn()
        print(name, regime, json.dumps(p, ensure_ascii=False))
        print("   ", {k: (round(v, 1) if isinstance(v, float) else v) for k, v in sig.items()})
