"""Bounded public-market observations and conservative seller policy updates.

No LLM calls, customer actions, private endpoints, or competitor instructions.
Prices here are experimental quote targets, never claims about product quality.
"""
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import time

ITEMS = ("lamp", "chair", "bookshelf")
MAX_BYTES = 1_000_000
TTL = 3600


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]


def number(value):
    return type(value) in (int, float) and math.isfinite(value)


def load_json(path, default=None):
    try:
        with Path(path).open("rb") as f:
            data = f.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise ValueError("Oversize local state")
        return json.loads(data)
    except (OSError, ValueError):
        return default


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False) + "\n")
    temp.replace(path)


def append_event(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(value, ensure_ascii=False, allow_nan=False) + "\n")


def recent_events(path, now):
    """At most 256 KB/200 terminal events; never rewrite the original log."""
    try:
        with Path(path).open("rb") as f:
            size = f.seek(0, 2)
            start = max(0, size - 262144)
            f.seek(start)
            raw = f.read(262144)
        lines = raw.splitlines()
        if start:
            lines = lines[1:]
        if raw and not raw.endswith(b"\n"):
            lines = lines[:-1]  # a concurrent append may not be complete yet
    except OSError:
        return []
    unique = {}
    for line in lines[-400:]:
        try:
            event = json.loads(line)
            if (isinstance(event, dict) and isinstance(event.get("task"), str)
                    and number(event.get("time")) and 0 <= now - event["time"] <= 86400
                    and event.get("item") in ITEMS):
                unique[event["task"]] = event
        except (ValueError, UnicodeError):
            continue
    return list(unique.values())[-200:]


def clean_shop(row):
    if not isinstance(row, dict) or not isinstance(row.get("name"), str):
        raise ValueError("Unexpected board shop")
    fields = ("deals", "visits", "profit", "revenue")
    if any(not number(row.get(k)) for k in fields):
        raise ValueError("Board must expose deals, visits, profit and revenue")
    card = row.get("card") if isinstance(row.get("card"), dict) else {}
    return {"name": row["name"][:120], **{k: row[k] for k in fields},
            "stars": row.get("stars") if number(row.get("stars")) else None,
            "alive": row.get("alive") is True,
            "description": str(card.get("description", ""))[:400]}


def public_snapshot(board, shops, name, now):
    if (not isinstance(board, dict) or not isinstance(shops, dict)
            or type(board.get("round")) is not int
            or board["round"] != shops.get("round")
            or not isinstance(board.get("shops"), list)
            or not isinstance(shops.get("shops"), list)):
        raise ValueError("Market schema or round changed during collection")
    if len(board["shops"]) > 1000 or len(shops["shops"]) > 1000:
        raise ValueError("Unexpected market size")
    rows = [clean_shop(s) for s in board["shops"]]
    own = next((s for s in rows if s["name"] == name), None)
    if own is None:
        raise ValueError("Own shop is absent; no strategy update")
    # Same sort as the public board's default Profit view. Offline shops remain
    # eligible, so these are actual positions among all shops, not an online rank.
    ranked = sorted(rows, key=lambda s: (-s["profit"], -s["deals"], s["name"]))
    leaders = [dict(s, rank=i + 1) for i, s in enumerate(ranked) if s["name"] != name][:3]
    registry = next((s for s in shops["shops"] if isinstance(s, dict) and s.get("name") == name), None)
    if registry is None or not isinstance(registry.get("reviews"), list):
        raise ValueError("Own reviews unavailable")
    reviews, seen = [], set()
    for r in registry["reviews"][:10]:
        if (not isinstance(r, dict) or r.get("round") != board["round"]
                or type(r.get("stars")) is not int or not 1 <= r["stars"] <= 5):
            continue
        review = {"stars": r["stars"], "review": str(r.get("review", ""))[:400]}
        key = digest(review)
        if key not in seen:
            reviews.append(review)
            seen.add(key)
    return {"time": now, "round": board["round"], "own": own,
            "leaders": leaders, "rank_metric": "profit_then_deals", "reviews": reviews}


def review_signals(reviews, item):
    """Weak keyword hints, not verified complaints or independent reviewers."""
    counts = Counter()
    terms = {"price": ("expensive", "overpriced", "too high", "비싸", "비쌈"),
             "speed": ("slow", "too many rounds", "too long", "느려", "오래"),
             "clarity": ("unclear", "confusing", "설명 부족", "불명확"),
             "receipt": ("mismatch", "wrong receipt", "영수증 불일치")}
    for r in reviews:
        text = r["review"].lower()
        if r["stars"] > 3 or item not in text:
            continue
        for tag, words in terms.items():
            if any(w in text for w in words):
                counts[tag] += 1
    return dict(counts)


def competitor_signals(leaders):
    """Descriptions remain in audit evidence; only fixed tags reach the model."""
    result = Counter()
    terms = {"speed_claim": ("fast", "instant", "milliseconds"),
             "value_claim": ("fair", "cheap", "discount", "sale"),
             "quality_claim": ("quality", "premium", "durable"),
             "receipt_claim": ("signed", "receipt")}
    for s in leaders:
        text = s["description"].lower()
        for tag, words in terms.items():
            if any(w in text for w in words):
                result[tag] += 1
    return dict(result)


def validate_config(config):
    if not isinstance(config.get("name"), str) or not config["name"]:
        raise ValueError("name required")
    if not number(config.get("interval_seconds")) or config["interval_seconds"] < 900:
        raise ValueError("Polling interval must be at least 900 seconds")
    for item in ITEMS:
        p = config.get("products", {}).get(item, {})
        if any(type(p.get(k)) is not int or p[k] <= 0 for k in ("reserve", "base_quote")):
            raise ValueError(f"Positive reserve/base_quote required: {item}")
        if p["base_quote"] < p["reserve"]:
            raise ValueError("Base quote is below reserve")


def evolve(config, snapshot, events, previous=None):
    """Change a bounded target only after new evidence; never change a reserve."""
    validate_config(config)
    now = snapshot["time"]
    same_round = bool(previous and previous.get("round") == snapshot["round"])
    before = previous if same_round else {}
    previous_own = before.get("market", {}).get("own", {})
    own = snapshot["own"]
    delta = {k: own[k] - previous_own[k] for k in ("deals", "visits", "profit")
             if k in previous_own}
    reset = any(delta.get(k, 0) < 0 for k in ("deals", "visits"))
    if reset:
        before, delta = {}, {}
    version = digest({"round": snapshot["round"], "time": now})
    result = {"schema": 1, "version": version, "round": snapshot["round"],
              "updated_at": now, "expires_at": now + TTL, "market": snapshot,
              "market_delta": delta, "products": {}}
    claims = competitor_signals(snapshot["leaders"])
    for item, product in config["products"].items():
        old = before.get("products", {}).get(item, {})
        target = old.get("target", product["base_quote"])
        watermark = old.get("observed_until", now)
        fresh = [e for e in events if e["item"] == item and watermark < e["time"] <= now
                 and e.get("outcome") in ("receipt_issued", "rejected")]
        # Distinct tasks; receipt issuance is NOT a settled sale.
        fresh = list({e["task"]: e for e in fresh}.values())
        receipts = sum(e["outcome"] == "receipt_issued" for e in fresh)
        rejected = len(fresh) - receipts
        tags = review_signals(snapshot["reviews"], item)
        hint = "balanced"
        if tags.get("speed", 0) >= 2 and claims.get("speed_claim", 0):
            hint = "close_promptly"
        elif tags.get("clarity", 0) >= 2:
            hint = "explain_price_briefly"
        reason = "hold: insufficient new evidence"
        changed_at = old.get("changed_at", now)
        enough = len(fresh) >= 5 and now - changed_at >= 3600
        if before and enough:
            if tags.get("receipt", 0) >= 2:
                reason = "hold: investigate receipt complaints"
            elif (rejected / len(fresh) >= .5 and tags.get("price", 0) >= 3
                  and claims.get("value_claim", 0)):
                target = math.ceil(target * .95)
                reason = "lower: repeated price hints, own rejections, leaders claim value"
            elif (receipts / len(fresh) >= .8 and delta.get("deals", 0) >= 3
                  and not tags.get("price") and not tags.get("receipt")
                  and len(snapshot["reviews"]) >= 3
                  and sum(r["stars"] for r in snapshot["reviews"]) / len(snapshot["reviews"]) >= 4):
                target = math.floor(target * 1.05)
                reason = "raise cautiously: own receipts, counted sales growth and good reviews"
            else:
                reason = "hold: evidence does not support a price change"
            watermark = now  # consume this batch even when retaining the policy
        floor = max(product["reserve"], math.ceil(product["base_quote"] * .85))
        ceiling = math.floor(product["base_quote"] * 1.15)
        target = max(floor, min(ceiling, target))
        if target != old.get("target", product["base_quote"]):
            changed_at = now
        result["products"][item] = {
            "reserve": product["reserve"], "target": target, "hint": hint,
            "changed_at": changed_at, "observed_until": watermark,
            "reason": reason, "new_receipts": receipts, "new_rejections": rejected,
            "review_hints": tags, "competitor_claims": claims,
        }
    return result


class StrategyReader:
    """Local cached snapshot reader. No network/LLM calls on the sale path."""
    def __init__(self, path, clock=time.time):
        self.path, self.clock = Path(path), clock
        self.checked_at, self.cached = float("-inf"), None

    def for_item(self, item, reserve):
        now = self.clock()
        if now - self.checked_at >= 60:
            self.checked_at, self.cached = now, load_json(self.path)
        state = self.cached
        if (not isinstance(state, dict) or state.get("schema") != 1
                or not number(state.get("updated_at")) or not number(state.get("expires_at"))
                or not 0 <= now - state["updated_at"] <= TTL or now > state["expires_at"]):
            return None
        p = state.get("products", {}).get(item)
        if (not isinstance(p, dict) or p.get("reserve") != reserve
                or type(p.get("target")) is not int or p["target"] < reserve):
            return None
        # Do not forward raw competitor text/reviews, names, or private data.
        return {"version": str(state.get("version", ""))[:32],
                "target": p["target"],
                "hint": p.get("hint") if p.get("hint") in
                ("balanced", "close_promptly", "explain_price_briefly") else "balanced",
                "scope": "experimental quote guidance; no verified product-quality evidence"}
