"""Round 1/2 market buyer: picks a shop from reviews and its own memory, trades over A2A, reviews it.

How it picks (see choose_shop):
  1. Own memory first. A shop that ever sent us a bad receipt (overcharge, wrong item, over budget,
     unsigned) is blacklisted by name. Recent deals raise a shop, errors/timeouts lower it.
  2. Public reviews second, read defensively because in Round 1 anyone may post one:
     only the newest 15, identical texts counted once, a Bayesian star average (prior 3.5, weight 2),
     and a penalty proportional to the share of reviews that report a dishonest receipt.
  3. Round 2 feedback from the kit: logs/market-kit.log says, after each result, whether the market
     verified it and which checks failed. A shop whose receipts fail sig_seller_ok (unsigned, or signed
     with a key that is not the one it registered) gives deals that do not count, so it is marked down.
     An unsigned receipt is a decaying penalty (24 h); an overcharge stays a blacklist.
  4. Reviews from Round 2 (each backed by a verified deal) weigh twice as much as Round 1 reviews.
  5. Big orders (budget >= 250) go to shops that have earned trust (my verified deals there, or at
     least 3 distinct Round 2 reviews); new shops are tried on small orders only. This is the defence
     against "honest on small deals, cheat on the big one".
  6. Market-floor rule (rule_violation): every shop must keep the same reserve (lamp 100, chair 180,
     bookshelf 260) and never ask or sell below it. A signed receipt below the floor, or an ask below it
     that is not just our own offer echoed back, is recorded as a rule violation: evidence in
     logs/challenge-2/rule-violations.jsonl, a neutral factual note in the public review only for a
     signed receipt (proof; a low ask can be a misreading), and a small decaying
     penalty (a shop that ignores one rule may ignore the receipt rule too). Discounts or coupons at or
     above the floor are ordinary competition and are not flagged.
  8. Alliance (instructor-approved, agreed with fair-furniture): fair-furniture's buyer prefers ggari-shop,
     and our buyer prefers fair-furniture in return. Prices, budgets and reviews are not part of it: we
     still negotiate from the floor, keep the budget, and write the review the trade earned. The
     preference yields to every safety rule: a blacklisted shop, one whose receipts fail the kit's
     sig_seller check, or one with dishonest-receipt reviews is not preferred.
  9. Review spam: a review that advertises another store ("… try the store 'Cheapest in Town'") says
     nothing about the shop it is attached to. It is left out of that shop's stars and counts, and the
     store it advertises is marked down a little when many such reviews are seen (its buyer is gaming
     the review board). Counts and examples go to logs/challenge-2/review-spam.json.
  7. A little exploration (15%, small orders only) among shops close to the best, so a new honest shop
     gets a chance. Never explores into a blacklisted or dishonest-reviewed shop.
Memory: .market/buyer-memory.jsonl, one JSON object per trade, keyed by shop name (URLs change).
Every run ends with exactly one result line, even when the shop or the network fails.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import re
import sys
import time
from pathlib import Path
from typing import Any

from buyer import BuyerRunner, make_policy
from negotiation import MARKET_FLOOR
from protocol import A2AClient

DISHONEST_TERMS = (
    "overcharg", "receipt said", "receipt says", "higher than", "mismatch", "dishonest", "scam",
    "wrong item", "no receipt", "unsigned", "not signed", "fake", "lied", "avoid", "사기", "바가지",
)
BAD_OUTCOMES = {"bad_receipt"}
SOFT_BAD_OUTCOMES = {"error", "invalid", "open", "timeout", "suspect_receipt"}
UNSIGNED_OUTCOMES = {"unsigned"}
DECAY_SECONDS = 24 * 3600
BIG_ORDER = 250
ALLIES = ("fair-furniture",)   # instructor-approved alliance; see the docstring (8)
KIT_LOG = Path("logs/market-kit.log")
SHOP_PRICES = Path(".market/shop-prices.json")       # written by market_watch.py from /api/trades
SHOP_PRICES_MAX_AGE = 3600
SHOP_FAULT_CHECKS = ("sig_seller_ok",)
EXPLORE_RATE = 0.15
EXPLORE_WINDOW = 0.75
PRIOR_STARS, PRIOR_WEIGHT = 3.5, 2.0


class MemoryStore:
    """Append-only JSONL memory keyed by stable shop name rather than URL."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        records: list[dict[str, Any]] = []
        for line in self.path.read_text(encoding="utf-8").split("\n"):
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                records.append(value)
        return records

    def append(self, record: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=True) + "\n")


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9가-힣]+", " ", text.lower()).strip()


# An ad names the store in quotes: "try the store 'Cheapest in Town'". Without a quoted name ("I'd visit the
# shop again") it is an ordinary review and is kept.
PROMO = re.compile(
    r"\b(?:try|visit|check out|switch to|go to|shop at|buy (?:it )?at)\s+(?:the\s+)?(?:store|shop)\s*['\"“‘]([^'\"”’]{2,60})['\"”’]",
    re.IGNORECASE)
SPAM_PENALTY_FROM = 5        # promotions seen across the listed shops before the advertised store is marked down
REVIEW_SPAM_LOG = Path("logs/challenge-2/review-spam.json")


def promoted_store(text: str) -> str | None:
    """The store a review advertises, normalized like a shop name ('Cheapest in Town' -> cheapest-in-town)."""
    m = PROMO.search(text or "")
    if not m:
        return None
    name = (m.group(1) or "").strip()
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "unknown"


def review_spam(shops: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Advertised store -> how many listed reviews promote it, on which shops, one example."""
    out: dict[str, dict[str, Any]] = {}
    for shop in shops:
        for review in shop.get("reviews") or []:
            if not isinstance(review, dict):
                continue
            target = promoted_store(str(review.get("review", "")))
            if target:
                rec = out.setdefault(target, {"count": 0, "on_shops": {}, "example": str(review.get("review", ""))[:200]})
                rec["count"] += 1
                rec["on_shops"][str(shop.get("name"))] = rec["on_shops"].get(str(shop.get("name")), 0) + 1
    return out


def spam_penalty(name: str, spam: dict[str, dict[str, Any]] | None) -> float:
    rec = (spam or {}).get(re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-"))
    # several ads on at least 3 different shops: one rival posting a few fake ads cannot frame a shop cheaply
    return 1.0 if rec and rec["count"] >= SPAM_PENALTY_FROM and len(rec["on_shops"]) >= 3 else 0.0


def log_review_spam(spam: dict[str, dict[str, Any]]) -> None:
    if not spam:
        return
    try:
        REVIEW_SPAM_LOG.parent.mkdir(parents=True, exist_ok=True)
        REVIEW_SPAM_LOG.write_text(json.dumps({"updated": time.strftime("%Y-%m-%d %H:%M:%S"), "promoted_stores": spam},
                                              ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError:
        pass


def review_signal(reviews: list[dict[str, Any]]) -> tuple[float, float, int]:
    """(bayesian stars, share of reviews reporting dishonesty, distinct reviews used). Ads are left out."""
    seen: set[str] = set()
    stars: list[float] = []
    dishonest = 0
    for review in reviews[:15]:
        if not isinstance(review, dict):
            continue
        text = str(review.get("review", ""))
        key = _norm(text)
        if promoted_store(text):                     # an ad: its stars say nothing about this shop ...
            if key not in seen and any(term in text.lower() for term in DISHONEST_TERMS):
                seen.add(key)                        # ... but a complaint in it still counts (once)
                dishonest += 1
            continue
        if key in seen:
            continue
        seen.add(key)
        value = review.get("stars")
        weight = 2.0 if _round(review) >= 2 else 1.0        # R2 reviews sit on verified deals
        if isinstance(value, (int, float)) and 1 <= value <= 5:
            stars.append((float(value), weight))
        if any(term in text.lower() for term in DISHONEST_TERMS):
            dishonest += 1
    n = len(seen)
    total = sum(v * w for v, w in stars)
    weights = sum(w for _, w in stars)
    bayes = (PRIOR_STARS * PRIOR_WEIGHT + total) / (PRIOR_WEIGHT + weights)
    return bayes, (dishonest / n if n else 0.0), n


def _round(review: dict[str, Any]) -> int:
    try:
        return int(review.get("round") or 0)
    except (TypeError, ValueError):
        return 0


def r2_reviews(reviews: list[dict[str, Any]]) -> int:
    return len({_norm(str(r.get("review", ""))) for r in reviews if isinstance(r, dict) and _round(r) >= 2})


# Anchored at the start of a kit log line ("HH:MM:SS [buyer] …" / "HH:MM:SS [market] …"): text a shop sends us
# is only ever logged inside a "[buyer]" line, so it can never pass for the market's own verdict.
CHOOSE_JSON = re.compile(r"^\d\d:\d\d:\d\d \[buyer\] \[choose-json\] (\{.*\})$")
CHOOSE_TEXT = re.compile(r"^\d\d:\d\d:\d\d \[buyer\] \[choose\] \S+: (?:highest score -?[\d.]+ for (.+?): |exploring (.+?) \()")
LEDGER = re.compile(r"^\d\d:\d\d:\d\d \[market\] ledger #\d+: (no deal|.+? at [\d.]+), .*?verified (True|False)(?: \(failed: ([^)]*)\))?")
CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f\u2028\u2029]")   # includes U+0085 (NEL), U+2028/2029


def clean(text: object, limit: int = 300) -> str:
    """Market data (shop names, reviews, shop messages) printed to our logs: one line, bounded."""
    return CONTROL.sub(" ", str(text))[:limit]


def kit_feedback(path: Path = KIT_LOG, last_results: int = 60) -> dict[str, dict[str, int]]:
    """Per shop name: verified deals and shop-side check failures in the kit's recent results."""
    try:
        with path.open("rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 600_000))
            lines = f.read().decode("utf-8", errors="replace").split("\n")   # the kit writes one line per \n only
    except OSError:
        return {}
    results: list[tuple[str, bool, list[str]]] = []
    current = None
    for line in lines:
        m = CHOOSE_JSON.search(line)
        if m:
            try:
                current = json.loads(m.group(1)).get("shop")
            except ValueError:
                current = None
            continue
        m = CHOOSE_TEXT.search(line)
        if m:
            current = m.group(1) or m.group(2)
            continue
        m = LEDGER.search(line)
        if m and current:
            if m.group(1) != "no deal":
                failed = [c.strip() for c in (m.group(3) or "").split(",") if c.strip()]
                results.append((current, m.group(2) == "True", failed))
            current = None
    out: dict[str, dict[str, int]] = {}
    for name, verified, failed in results[-last_results:]:
        rec = out.setdefault(name, {"verified": 0, "shop_faults": 0})
        rec["verified"] += int(verified)
        rec["shop_faults"] += int(any(c in SHOP_FAULT_CHECKS for c in failed))
    return out


def own_history(name: str, item: str | None, memories: list[dict[str, Any]]) -> dict[str, Any]:
    own = [m for m in memories if m.get("shop") == name][-20:]
    paid = [m["price"] / m["budget"] for m in own
            if m.get("outcome") == "deal" and m.get("item") == item
            and isinstance(m.get("price"), (int, float)) and m.get("budget")]
    last_price = next((m["price"] for m in reversed(own)
                       if m.get("outcome") == "deal" and m.get("item") == item
                       and isinstance(m.get("price"), (int, float))), None)
    now = time.time()
    unsigned = sum(max(0.0, 1.0 - (now - float(m.get("ts") or 0)) / DECAY_SECONDS)
                   for m in own if m.get("outcome") in UNSIGNED_OUTCOMES)
    violations = sum(max(0.0, 1.0 - (now - float(m.get("ts") or 0)) / DECAY_SECONDS)
                     for m in own if m.get("rule_violation"))
    return {
        "violations": violations,
        "unsigned": unsigned,
        "blacklisted": any(m.get("outcome") in BAD_OUTCOMES for m in own),
        "deals": sum(m.get("outcome") == "deal" for m in own[-10:]),
        "soft_bad": sum(m.get("outcome") in SOFT_BAD_OUTCOMES for m in own[-10:]),
        "paid_ratio": sum(paid) / len(paid) if paid else None,
        "last_price": last_price,
    }


def load_shop_prices(path: Path = SHOP_PRICES) -> dict[str, dict[str, dict[str, float]]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if time.time() - float(data.get("ts", 0)) > SHOP_PRICES_MAX_AGE:
        return {}
    return data.get("shops") or {}


def cheapness(name: str, item: str | None, prices: dict[str, dict[str, dict[str, float]]]) -> float:
    """0.8 for a shop whose recent counted deals on this item close at the floor, 0 at floor +10% or more."""
    floor = MARKET_FLOOR.get(item or "")
    stats = (prices.get(name) or {}).get(item or "")
    if not floor or not stats or stats.get("n", 0) < 2:
        return 0.0
    return 0.8 * max(0.0, 1.0 - (float(stats["median"]) - floor) / (0.10 * floor))


def shop_score(
    shop: dict[str, Any],
    memories: list[dict[str, Any]],
    item: str | None = None,
    budget: int | None = None,
    feedback: dict[str, dict[str, int]] | None = None,
    prices: dict[str, dict[str, dict[str, float]]] | None = None,
    spam: dict[str, dict[str, Any]] | None = None,
) -> float:
    """Score public reviews, our own experience, the kit's verification results and how cheaply the
    shop has been closing this item in the market (higher is better)."""
    reviews = shop.get("reviews") or []
    bayes, dishonest_share, _ = review_signal(reviews)
    score = bayes - 3.0 * dishonest_share
    name = str(shop.get("name", ""))
    hist = own_history(name, item, memories)
    fb = (feedback or {}).get(name, {"verified": 0, "shop_faults": 0})
    if hist["blacklisted"]:
        score -= 10.0
    score -= 1.5 * min(2.0, hist["unsigned"])
    score -= 0.5 * min(2.0, hist["violations"])          # weak: a warning sign, not proof of cheating
    score += cheapness(name, item, prices or {})
    score -= spam_penalty(name, spam)                     # its buyer advertises it in other shops' reviews
    score -= 1.5 * min(2, fb["shop_faults"])
    if budget is not None and budget >= BIG_ORDER:
        proven = fb["verified"] > 0 or hist["deals"] > 0 or r2_reviews(reviews) >= 3
        if not proven:
            score -= 1.0
    score += min(1.2, 0.4 * hist["deals"])
    score -= 0.7 * hist["soft_bad"]
    if hist["paid_ratio"] is not None:
        score += 0.8 * (1.0 - hist["paid_ratio"])         # cheaper for us before -> small bonus
    return score


def choose_shop(
    shops: list[dict[str, Any]],
    memories: list[dict[str, Any]],
    item: str | None = None,
    self_name: str | None = None,
    rng: random.Random | None = None,
    budget: int | None = None,
    feedback: dict[str, dict[str, int]] | None = None,
    prices: dict[str, dict[str, dict[str, float]]] | None = None,
    allies: tuple[str, ...] | None = None,
) -> tuple[dict[str, Any], str]:
    """Choose the allied shop if it is safe, else the highest-trust shop, with bounded exploration."""
    own = {n.strip() for n in (self_name or "").split(",") if n.strip()}   # every shop we run
    candidates = [s for s in shops if s.get("name") not in own]
    if not candidates:
        raise ValueError("no shop to buy from except our own")
    spam = review_spam(shops)
    scored = sorted(((shop_score(s, memories, item, budget, feedback, prices, spam), str(s.get("name", "")), s)
                     for s in candidates),
                    key=lambda v: (-v[0], v[1]))
    for sc, name, shop in scored:                       # alliance first, unless a safety rule says no
        if name in (allies if allies is not None else ALLIES):
            hist = own_history(name, item, memories)
            fb = (feedback or {}).get(name, {"shop_faults": 0})
            dishonest = review_signal(shop.get("reviews") or [])[1]
            big_ok = (budget is None or budget < BIG_ORDER or fb.get("verified", 0) > 0 or hist["deals"] > 0
                      or r2_reviews(shop.get("reviews") or []) >= 3)
            if (not hist["blacklisted"] and hist["unsigned"] < 0.5 and fb["shop_faults"] == 0
                    and dishonest < 0.25 and big_ok):
                return shop, (f"alliance (instructor-approved) with {name}: preferred while it stays honest; "
                              f"score {sc:.2f}, best other {scored[0][1] if scored[0][1] != name else (scored[1][1] if len(scored) > 1 else 'none')}")
    best_score, _, best = scored[0]
    rng = rng or random.Random()
    safe = [(sc, s) for sc, _, s in scored[1:]
            if sc >= best_score - EXPLORE_WINDOW
            and not own_history(str(s.get("name", "")), item, memories)["blacklisted"]
            and review_signal(s.get("reviews") or [])[1] < 0.25]
    small = budget is None or budget < BIG_ORDER
    if safe and small and rng.random() < EXPLORE_RATE:
        sc, chosen = rng.choice(safe)
        return chosen, (f"exploring {chosen.get('name')} (score {sc:.2f}, within {EXPLORE_WINDOW} "
                        f"of the best {best.get('name')} at {best_score:.2f})")
    bayes, dishonest, n = review_signal(best.get("reviews") or [])
    hist = own_history(str(best.get("name", "")), item, memories)
    return best, (f"highest score {best_score:.2f} for {best.get('name')}: reviews {bayes:.2f} stars "
                  f"over {n} distinct, {dishonest:.0%} report bad receipts; my deals there {hist['deals']}, "
                  f"failures {hist['soft_bad']}; runner-up "
                  + (f"{scored[1][1]} at {scored[1][0]:.2f}" if len(scored) > 1 else "none"))


def floor_violation(result: dict[str, Any], item: str) -> dict[str, Any] | None:
    """Facts showing the shop went below the common market floor, or None. Numbers only."""
    floor = MARKET_FLOOR.get(item)
    if floor is None:
        return None
    receipt = result.get("receipt") or {}
    price = receipt.get("price")
    if isinstance(price, (int, float)) and price < floor and receipt.get("sig_seller"):
        return {"kind": "sold_below_floor", "price": int(price), "floor": floor,
                "text": f"Signed receipt for {item} at {int(price)}, below the market floor of {floor}"}
    offers = set(result.get("offers") or [])
    low_asks = [a for a in (result.get("asks") or []) if isinstance(a, int) and a < floor and a not in offers]
    if low_asks:
        return {"kind": "asked_below_floor", "price": min(low_asks), "floor": floor,
                "text": f"Asked {min(low_asks)} for {item}, below the market floor of {floor}"}
    return None


def with_floor_note(review: str, violation: dict[str, Any] | None) -> str:
    """Only a signed receipt goes into the public review: it is proof. A low ask stays in our logs,
    because an ask can be misread (wrong item understood, a number quoted out of context)."""
    if violation is None or violation["kind"] != "sold_below_floor":
        return review
    return f"{review} Note: {violation['text']} (shops must not ask or sell below reserve)."


def rating_for(result: dict[str, Any], budget: int) -> tuple[int, str]:
    """A short review grounded in what we observed. Specific enough for other buyers to use."""
    outcome = result.get("outcome")
    if outcome == "deal":
        price = int(result.get("price") or 0)
        rounds = int(result.get("rounds") or 0)
        stars = 5 if price <= 0.92 * budget or rounds <= 2 else 4
        return stars, f"Deal at {price} after {rounds} offer(s); signed receipt matched the agreed price."
    if outcome == "suspect_receipt":
        return 2, f"Receipt reused a task id from an earlier receipt of this shop ({str(result.get('detail') or '')[:60]})."
    if outcome == "unsigned":
        return 2, (f"Agreed {result.get('price')}, but the receipt has no sig_seller, "
                   "so the deal cannot be verified.")
    if outcome == "bad_receipt":
        return 1, f"Dishonest receipt: {result.get('detail')}. Avoid."
    if outcome == "walk":
        asks = result.get("asks") or []
        low = min(asks) if asks else None
        return 3, (f"No deal: lowest ask {low} stayed above my budget {budget}." if low else
                   "No deal; stayed within budget.")
    if outcome == "no_deal":
        return 3, f"Shop ended the negotiation: {str(result.get('detail') or '')[:80]}"
    return 2, f"No completed trade ({outcome}): {str(result.get('detail') or '')[:80]}"


def flag_dishonest(path: Path, record: dict[str, Any]) -> None:
    """Challenge 2 evidence: every bad receipt is written to logs/challenge-2/."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=True) + "\n")
    except OSError:
        pass


RETRY_OUTCOMES = {"error", "timeout", "open"}
FALLBACK_BUDGET_S = 120      # the kit gives a buyer run 300 s; a second shop is tried only early enough


def trade(chosen: dict[str, Any], args: argparse.Namespace, memories: list[dict[str, Any]],
          memory: "MemoryStore") -> tuple[dict[str, Any], dict[str, Any]]:
    """One negotiation with one shop; appends the outcome to memory and the challenge-2 logs."""
    hint = own_history(str(chosen.get("name", "")), args.item, memories)["last_price"]
    started = time.time()
    client = A2AClient(str(chosen["url"]), timeout=args.timeout)
    try:
        seen = {str(m.get("task")) for m in memories                 # this shop's earlier receipts only
                if m.get("outcome") == "deal" and m.get("task") and m.get("shop") == chosen.get("name")}
        result = BuyerRunner(client, make_policy(args.use_harness, args.harness_cmd, hint),
                             seen_tasks=seen).run(args.item, args.budget)
    except Exception as error:                       # unreachable shop, HTTP error, protocol error
        result = {"outcome": "error", "price": None, "receipt": None,
                  "detail": f"{type(error).__name__}: {str(error)[:160]}",
                  "seconds": round(time.time() - started, 1)}
    finally:
        client.close()

    record = {
        "ts": round(time.time()), "shop": chosen.get("name"), "url": chosen.get("url"),
        "item": args.item, "budget": args.budget, "price": result.get("price"),
        "outcome": result.get("outcome"), "rounds": result.get("rounds"),
        "seconds": result.get("seconds"), "detail": result.get("detail"),
        "task": (result.get("receipt") or {}).get("task") or result.get("task_id"),
    }
    violation = floor_violation(result, args.item)
    if violation is not None:
        record["rule_violation"] = violation["kind"]
        record["rule_detail"] = violation["text"]
        flag_dishonest(Path("logs/challenge-2/rule-violations.jsonl"),
                       {**record, "receipt": result.get("receipt"), "asks": result.get("asks"),
                        "offers": result.get("offers")})
        print(f"[rule] {clean(chosen.get('name'), 80)}: {clean(violation['text'])}", file=sys.stderr, flush=True)
    memory.append(record)
    if result.get("outcome") in BAD_OUTCOMES:
        flag_dishonest(Path("logs/challenge-2/dishonest.jsonl"), {**record, "receipt": result.get("receipt")})
        print(f"[flag] {clean(chosen.get('name'), 80)} is now blacklisted: {clean(result.get('detail'))}", file=sys.stderr, flush=True)
    result["_violation"] = violation
    return result, record


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--shops", type=Path, required=True)
    parser.add_argument("--item", required=True)
    parser.add_argument("--budget", type=int, required=True)
    parser.add_argument("--memory", type=Path, default=Path(".market/buyer-memory.jsonl"))
    parser.add_argument("--self-name", default=None,
                        help="our own shop names, comma separated; never bought from (no self-dealing)")
    parser.add_argument("--harness-cmd")
    parser.add_argument("--use-harness", action="store_true")
    parser.add_argument("--timeout", type=float, default=45, help="seconds per A2A request")
    args = parser.parse_args()

    shops = json.loads(args.shops.read_text(encoding="utf-8"))
    memory = MemoryStore(args.memory)
    memories = memory.load()
    feedback = kit_feedback()
    prices = load_shop_prices()
    spam = review_spam(shops)
    log_review_spam(spam)
    for store, rec in spam.items():
        if rec["count"] >= SPAM_PENALTY_FROM:
            print(f"[spam] {rec['count']} listed reviews advertise '{clean(store, 60)}' (on {len(rec['on_shops'])} shops); "
                  "left out of the stars, store marked down", file=sys.stderr, flush=True)
    try:
        chosen, reason = choose_shop(shops, memories, args.item, args.self_name,
                                     budget=args.budget, feedback=feedback, prices=prices)
    except ValueError as error:          # only our own shops listed: no trade, the kit closes the order
        print(f"[choose] no shop: {clean(error)}", file=sys.stderr, flush=True)
        return
    stats = (prices.get(str(chosen.get("name"))) or {}).get(args.item)
    if stats:
        reason += f"; market closes its {args.item} at median {stats['median']:g} over {int(stats['n'])} deals"
    print(f"[choose] {clean(chosen['url'], 200)}: {clean(reason, 500)}", file=sys.stderr, flush=True)
    fb = feedback.get(str(chosen.get("name")), {})
    print("[choose-json] " + json.dumps({"shop": chosen.get("name"), "url": chosen.get("url"),
                                         "kit_verified": fb.get("verified", 0),
                                         "kit_shop_faults": fb.get("shop_faults", 0)},
                                        ensure_ascii=True), file=sys.stderr, flush=True)

    deadline = time.time() + FALLBACK_BUDGET_S
    result, record = trade(chosen, args, memories, memory)
    # 10/26: an order lost to a shop that did not answer saves nothing. If the chosen shop errors or times out
    # (not a walk or a no-deal: those are real answers), try the next best shop once while time is left.
    if result.get("outcome") in RETRY_OUTCOMES and time.time() < deadline:
        failed = str(chosen.get("name", ""))
        try:
            nxt, why = choose_shop(shops, memories + [record], args.item,
                                   ",".join(x for x in (args.self_name, failed) if x),
                                   budget=args.budget, feedback=feedback, prices=prices, allies=())
            print(f"[fallback] {clean(failed, 80)} gave {result.get('outcome')}; trying {clean(nxt.get('url'), 200)}: "
                  f"{clean(why, 300)}", file=sys.stderr, flush=True)
            chosen = nxt
            result, record = trade(chosen, args, memories, memory)
        except ValueError:
            pass
    stars, review = rating_for(result, args.budget)
    review = with_floor_note(review, result.pop("_violation", None))
    receipt = result.get("receipt") if result.get("outcome") in {"deal", "bad_receipt", "unsigned"} else None
    print(json.dumps({"shop": chosen["url"], "receipt": receipt, "stars": stars, "review": review},
                     ensure_ascii=True))


if __name__ == "__main__":
    main()
