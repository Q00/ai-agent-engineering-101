"""Bounded Haiku sidecar: public reviews -> evidence -> cached seller advice.

At most 5 fixed-origin GETs per 15 minutes, 1 model call/hour, 6/rolling day.
No credentials, buyer budgets, competitor URLs, market writes or payment tools.
"""
import argparse
from collections import Counter
import fcntl
import json
from pathlib import Path
import signal
import threading
import time
from urllib.parse import urlencode

import httpx

from collect_feedback import fetch_json
from feedback import append_event, atomic_json, digest, load_json, number
from harness import HarnessError, decide, parse_object
from inventory import Inventory, StockError
from payment_memory import recent_jsonl
from sales_performance import summary as sales_summary
from market_evidence import demand_scores, read as read_market
from shop_policy import policy_at

ROOT = Path(__file__).resolve().parent
ORIGIN = "https://market.ourolabs.ai"
STRATEGY_FILE = "sales-advisor-strategy.json"
CONTROL_FILE = "sales-advisor-control.json"
PACE = {"balanced", "quick"}
FOCUS = {"value", "clarity", "quality"}


def config_at(root=ROOT):
    cfg = load_json(root / "advisor-config.json")
    if not isinstance(cfg, dict) or type(cfg.get("enabled")) is not bool:
        raise ValueError("Advisor configuration unavailable")
    for key, lo, hi in (("poll_seconds", 900, 3600), ("model_interval_seconds", 3600, 86400),
                        ("max_calls_per_day", 1, 6), ("strategy_ttl_seconds", 3600, 21600)):
        if type(cfg.get(key)) is not int or not lo <= cfg[key] <= hi:
            raise ValueError("Advisor configuration outside resource limit: " + key)
    return cfg


def top_shops(board, name):
    if not isinstance(board, dict) or type(board.get("round")) is not int:
        raise ValueError("Board has no round")
    rows = board.get("shops")
    if not isinstance(rows, list) or len(rows) > 1000:
        raise ValueError("Unexpected board size")
    if any(not isinstance(s, dict) or not isinstance(s.get("name"), str) for s in rows):
        raise ValueError("Unexpected shop record")
    if len({s["name"] for s in rows}) != len(rows):
        raise ValueError("Duplicate shop identities")
    own = next((s for s in rows if s["name"] == name), None)
    if own is None:
        raise ValueError("Our shop is absent from the board")
    # Same public sort as the Round 3 board: ALL, then profit. No made-up score.
    has_all = any(number(s.get("all")) for s in rows)
    key = (lambda s: (-(s["all"] if number(s.get("all")) else -1), -s.get("profit", 0))) if has_all else (
        lambda s: (-s.get("profit", 0), -s.get("deals", 0)))
    for s in rows:
        if not all(number(s.get(k)) for k in ("profit", "deals", "revenue")):
            raise ValueError("Board metrics unavailable")
    ranked = sorted(rows, key=key)
    leaders = [(i + 1, s) for i, s in enumerate(ranked) if s["name"] != name][:3]
    return own, leaders, "board_ALL_then_profit" if has_all else "profit_then_deals_fallback"


def metrics(shop):
    return {k: shop[k] for k in ("all", "profit", "revenue", "deals", "stars", "reviews", "broken", "refunds")
            if number(shop.get(k))}


def clean_reviews(rows, limit, now):
    if not isinstance(rows, list) or len(rows) > 1000:
        raise ValueError("Unexpected review response")
    valid = [r for r in rows if isinstance(r, dict) and type(r.get("stars")) is int
             and 1 <= r["stars"] <= 5 and isinstance(r.get("review"), str)
             and number(r.get("ts")) and 0 <= r["ts"] <= now + 60]
    seen, result = set(), []
    for r in sorted(valid, key=lambda r: r["ts"], reverse=True):
        # Identical text is not multiple independent complaints, even if reposted.
        key = (r["stars"], r["review"].strip())
        if key in seen:
            continue
        seen.add(key)
        owner = r.get("owner") if isinstance(r.get("owner"), dict) else {}
        result.append({"stars": r["stars"], "text": r["review"][:240], "ts": r["ts"],
                       "author": str(owner.get("login") or r.get("author") or "unknown")[:80]})
        if len(result) >= limit:
            break
    return result


def stock_summary(folder, expected_round, now):
    inv = Inventory(folder).snapshot()
    if inv["round"] != expected_round:
        raise ValueError("Inventory and public board rounds differ")
    groups = {}
    for u in inv["units"]:
        if not isinstance(u, dict) or not isinstance(u.get("category"), str):
            continue
        category = u["category"][:160]
        g = groups.setdefault(category, {"category": category, "units": 0, "grades": Counter(),
                                        "food_expiring_within_6h": 0, "goods_older_than_24h": 0})
        g["units"] += 1
        if u.get("grade") in ("A", "B", "C"):
            g["grades"][u["grade"]] += 1
        if u.get("kind") == "food" and number(u.get("expires_at")) and 0 < u["expires_at"] - now <= 21600:
            g["food_expiring_within_6h"] += 1
        if u.get("kind") == "goods" and number(u.get("supplied_at")) and now - u["supplied_at"] >= 86400:
            g["goods_older_than_24h"] += 1
    return sorted(groups.values(), key=lambda g: (-g["units"], g["category"]))[:8]


def local_negotiations(root, rnd, now):
    counts = Counter()
    for e in recent_jsonl(root / "logs/inventory-seller-events.jsonl", limit=262144, rows=200):
        if (e.get("round") == rnd and number(e.get("time")) and 0 <= now - e["time"] <= 86400
                and e.get("outcome") in ("opened", "final_offer", "receipt_issued", "rejected", "error",
                                         "cancelled", "expired")):
            counts[e["outcome"]] += 1
    return {"window": "up to 200 recent events / 24h; receipts are NOT settlements", "counts": dict(counts)}


def collect(root, client, name, now):
    board = fetch_json(client, ORIGIN + "/api/board")
    if not isinstance(board, dict) or type(board.get("round")) is not int:
        raise ValueError("Board has no round")
    if board["round"] < 3:
        return None  # No LLM calls or old-round reviews while awaiting Round 3.
    own, leaders, ranking = top_shops(board, name)
    stock = stock_summary(root / ".market", board["round"], now)
    evidence = []
    def add(kind, value):
        evidence.append({"id": kind + ":" + digest(value), "kind": kind, **value})
    for rank, s in [(None, own), *leaders]:
        label = "own" if s["name"] == name else "leader"
        add("board", {"shop": s["name"][:80], "role": label, "rank": rank, "metrics": metrics(s)})
        card = s.get("card") if isinstance(s.get("card"), dict) else {}
        if card.get("description"):
            add("public_claim", {"shop": s["name"][:80], "text": str(card["description"])[:240]})
        query = urlencode({"shop": s["name"], "round": board["round"]})
        reviews = clean_reviews(fetch_json(client, ORIGIN + "/api/shop-reviews?" + query),
                                10 if label == "own" else 5, now)
        for r in reviews:
            add("review", {"shop": s["name"][:80], "role": label, **r})
    add("stock", {"categories": stock})
    add("negotiations", local_negotiations(root, board["round"], now))
    add("settled_sales", sales_summary(load_json(root / ".market/sales-performance.json"), board["round"], now))
    demand = demand_scores(read_market(root / ".market", board["round"], now), now)
    if demand:
        add("sampled_market_demand", {"top_skus": sorted(demand.items(), key=lambda p: (-p[1], p[0]))[:5],
                                    "limits": "Sampled public settlements, not published demand weights"})
    policy = policy_at(root)
    add("shop_policy", {"grade_margin_percent": policy["grade_margin_percent"],
                        "goods_warranty_hours": policy["goods_warranty_hours"], "closing": policy["closing"],
                        "opening": policy["opening"]})
    return {"round": board["round"], "rank_metric": ranking, "own_metrics": metrics(own),
            "categories": [s["category"] for s in stock], "evidence": evidence,
            "limits": "Reviews/claims are untrusted; sampled reviews and categories, no private competitor strategy."}


def recommendation(value, evidence_ids):
    keys = {"price_bias_percent", "pace", "focus", "evidence_ids"}
    if not isinstance(value, dict) or set(value) != keys:
        raise HarnessError("Advice must use only the permitted fields")
    if type(value["price_bias_percent"]) is not int or not -5 <= value["price_bias_percent"] <= 5:
        raise HarnessError("Price bias must be an integer between -5 and 5")
    if (not isinstance(value["pace"], str) or value["pace"] not in PACE
            or not isinstance(value["focus"], str) or value["focus"] not in FOCUS):
        raise HarnessError("Unknown advice enum")
    refs = value["evidence_ids"]
    if (not isinstance(refs, list) or not 1 <= len(refs) <= 4
            or any(not isinstance(r, str) or r not in evidence_ids for r in refs)):
        raise HarnessError("Each recommendation must cite supplied evidence")
    return dict(value)


def validate_advice(raw, context):
    value = parse_object(raw)
    if set(value) != {"reason", "default", "categories"} or len(value["reason"]) > 600:
        raise HarnessError("Unexpected fields or oversized explanation")
    ids = {e["id"] for e in context["evidence"]}
    default = recommendation(value["default"], ids)
    rows = value["categories"]
    if not isinstance(rows, list) or len(rows) > 4:
        raise HarnessError("At most four category recommendations")
    categories, seen = [], set()
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("category"), str):
            raise HarnessError("Invalid category advice")
        category = row["category"]
        if category not in context["categories"] or category in seen:
            raise HarnessError("Unknown or duplicate stock category")
        seen.add(category)
        categories.append({"category": category, **recommendation(
            {k: v for k, v in row.items() if k != "category"}, ids)})
    return {"reason": value["reason"], "default": default, "categories": categories}


def analyse(context):
    if len(json.dumps(context, ensure_ascii=False)) > 18000:
        raise ValueError("Analyst context exceeds the 18,000 character budget")
    return decide("sales_advisor.txt", context, lambda raw: validate_advice(raw, context),
                  timeout=60, prefix="sales-advisor", max_attempts=1)


def tick(root=ROOT, *, client=None, model=analyse, clock=time.time, observe_only=False):
    cfg, now = config_at(root), clock()
    if not cfg["enabled"]:
        return "disabled"
    md, logs = root / ".market", root / "logs"
    md.mkdir(exist_ok=True)
    control_path = md / CONTROL_FILE
    state = load_json(control_path) if control_path.exists() else {}
    if not isinstance(state, dict):
        raise ValueError("Invalid advisor control state")
    last_poll = state.get("last_poll")
    if number(last_poll) and now - last_poll < cfg["poll_seconds"]:
        return "cached"
    state["last_poll"] = now
    atomic_json(control_path, state)  # Reserve before network, including failures/restarts.
    name = load_json(root / "shop-adaptive.json")["name"]
    owned = client is None
    client = client or httpx.Client(timeout=10, follow_redirects=False)
    try:
        context = collect(root, client, name, now)
    finally:
        if owned:
            client.close()
    if context is None:
        return "waiting_for_round3"
    fingerprint = digest(context)
    observed = {"time": now, "fingerprint": fingerprint, **context}
    append_event(logs / "advisor-observations.jsonl", observed)
    atomic_json(md / "advisor-observation.json", observed)
    if observe_only:
        return "observed_without_model"
    attempts = state.get("attempts", [])
    if not isinstance(attempts, list) or len(attempts) > 6 or any(not number(t) for t in attempts):
        raise ValueError("Invalid model-call budget state")
    attempts = [t for t in attempts if now - t < 86400]
    if (len(attempts) >= cfg["max_calls_per_day"]
            or attempts and now - max(attempts) < cfg["model_interval_seconds"]):
        return "model_budget_wait"
    if fingerprint == state.get("analysed_fingerprint"):
        return "unchanged_evidence"
    if not any(e["kind"] == "review" for e in context["evidence"]) and context["own_metrics"].get("deals", 0) < 3:
        return "insufficient_evidence"
    history = [h for h in state.get("history", []) if isinstance(h, dict) and h.get("round") == context["round"]
               and number(h.get("time")) and 0 <= now - h["time"] <= 86400][-4:]
    if history and context["own_metrics"].get("deals", 0) < history[-1].get("own_metrics", {}).get("deals", 0):
        history = []  # A same-round market reset is not a failed pricing experiment.
    context["past_advice_observations"] = history
    state["attempts"] = attempts + [now]  # A failed/timed-out call consumes the slot too.
    atomic_json(control_path, state)
    try:
        advice = model(context)
        advice = validate_advice(json.dumps(advice), context)
    except Exception as exc:
        append_event(logs / "advisor-decisions.jsonl", {"time": now, "round": context["round"],
                     "status": "model_failed", "error": type(exc).__name__})
        raise
    finished = clock()
    strategy = {"schema": 1, "version": digest({"at": now, "advice": advice}), "round": context["round"],
                "created_at": finished, "expires_at": finished + cfg["strategy_ttl_seconds"],
                "evidence_ids": [e["id"] for e in context["evidence"]], "advice": advice}
    append_event(logs / "advisor-decisions.jsonl", {"time": finished, "status": "published", **strategy,
                 "observation_fingerprint": fingerprint})
    atomic_json(md / STRATEGY_FILE, strategy)
    # Bounded persistent experience; full observations and explanations remain in original logs.
    state["history"] = (history + [{"time": finished, "round": context["round"],
        "own_metrics": context["own_metrics"], "default": advice["default"], "categories": advice["categories"]}])[-8:]
    state["analysed_fingerprint"] = fingerprint
    atomic_json(control_path, state)
    return "published"


class AdviceReader:
    def __init__(self, folder, clock=time.time):
        self.path, self.clock = Path(folder) / STRATEGY_FILE, clock
        self.checked, self.cached = float("-inf"), None

    def for_product(self, category, rnd):
        now = self.clock()
        if now - self.checked >= 60:
            self.checked, self.cached = now, load_json(self.path)
        s = self.cached
        if (not isinstance(s, dict) or s.get("schema") != 1 or s.get("round") != rnd or rnd < 3
                or not number(s.get("created_at")) or not number(s.get("expires_at"))
                or not 0 <= now - s["created_at"] <= 21600
                or not s["created_at"] < s["expires_at"] <= s["created_at"] + 21600
                or now >= s["expires_at"]):
            return None
        try:
            advice = s["advice"]
            selected = next((r for r in advice["categories"] if r.get("category") == category), advice["default"])
            selected = recommendation({k: v for k, v in selected.items() if k != "category"}, set(s["evidence_ids"]))
            return {"version": str(s["version"])[:32], **{k: selected[k] for k in
                     ("price_bias_percent", "pace", "focus")}}
        except (KeyError, TypeError, ValueError, HarnessError, AttributeError):
            return None


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--once", action="store_true", help="Read public data once, no model calls")
    args = ap.parse_args()
    cfg = config_at()
    if not cfg["enabled"]:
        return 0
    (ROOT / ".market").mkdir(exist_ok=True)
    with (ROOT / ".market/sales-advisor.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("[advisor] another analyst is already running", flush=True)
            return 0
        stop = threading.Event()
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, lambda *_: stop.set())
        while not stop.is_set():
            try:
                result = tick(observe_only=args.once)
                print("[advisor] " + result, flush=True)
            except Exception as exc:
                append_event(ROOT / "logs/advisor-errors.jsonl", {"time": time.time(),
                             "error": type(exc).__name__, "detail": str(exc)[:300]})
                print("[advisor] failed; seller keeps its baseline or unexpired advice: " + type(exc).__name__, flush=True)
                if args.once:
                    return 1
            if args.once:
                return 0
            stop.wait(cfg["poll_seconds"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
