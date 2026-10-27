"""Week-07 reputation-aware market buyer for the A2A class market.

Required last output line: {"shop": URL, "receipt": original-or-null, "stars": 1..5, "review": string}.
The Week-06 buyer still makes real LLM-backed negotiation decisions via buy().
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

from buyer import buy

MEMORY = Path(__file__).resolve().parent / ".market" / "buyer_memory.jsonl"


def load_memory(path: Path = MEMORY) -> list[dict]:
    if not path.exists():
        return []
    entries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            record = json.loads(line)
            if isinstance(record, dict):
                entries.append(record)
        except json.JSONDecodeError:
            continue
    return entries[-500:]


def store_memory(event: dict, path: Path = MEMORY):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")


def reputation(shop: dict, history: list[dict]) -> tuple[float, str]:
    """Bayesian-smoothed rating plus first-hand trade history.

    Unreviewed shops receive a neutral prior so they are not permanently excluded.
    External reviews are signals, not authenticated proof until Round 2 verification.
    """
    reviews = shop.get("reviews") or []
    stars = [int(r["stars"]) for r in reviews if isinstance(r, dict)
             and isinstance(r.get("stars"), int) and 1 <= r["stars"] <= 5]
    # Two neutral pseudo-reviews express cold-start uncertainty.
    score = (7.0 + sum(stars)) / (2 + len(stars))
    dishonesty = sum("receipt" in str(r.get("review", "")).lower() and
                     any(b in str(r.get("review", "")).lower() for b in ("higher", "overcharg", "avoid", "said", "agreed"))
                     for r in reviews if isinstance(r, dict))
    score -= 0.3 * dishonesty
    ours = [h for h in history if h.get("shop_name") == shop.get("name")]
    good = sum(bool(h.get("deal")) for h in ours)
    bad = len(ours) - good
    score += 0.20 * min(good, 5) - 0.35 * min(bad, 5)
    reason = f"{len(stars)} public reviews, smoothed score={score:.2f}, own deals={good}/{len(ours)}"
    return score, reason


def choose_shop(shops: list[dict], memory: list[dict]) -> tuple[dict, str]:
    eligible = [s for s in shops if isinstance(s, dict) and s.get("url") and s.get("name")]
    if not eligible:
        raise ValueError("No shops with a name and card URL")
    ranked = sorted(((reputation(s, memory)[0], s["name"], s) for s in eligible),
                    key=lambda x: (-x[0], x[1]))
    picked = ranked[0][2]
    return picked, reputation(picked, memory)[1]


def review_result(result: dict, budget: int):
    if not result.get("deal"):
        return 2, "No agreement was reached within the allowed budget; no purchase receipt."
    receipt = result.get("receipt") or {}
    price = float(receipt.get("price", budget))
    if price > budget:
        return 1, "The returned price exceeded the order budget."
    if price <= budget * 0.9:
        return 5, f"Completed the purchase at {price:g} within budget; a receipt was provided."
    return 4, f"Completed the purchase at {price:g}, near the budget ceiling; a receipt was provided."


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--shops", required=True)
    p.add_argument("--item", required=True)
    p.add_argument("--budget", required=True, type=int)
    args = p.parse_args()
    if args.budget < 0:
        raise ValueError("Budget must be nonnegative")
    shops = json.loads(Path(args.shops).read_text(encoding="utf-8"))
    memory = load_memory()
    chosen, reason = choose_shop(shops, memory)
    print(f"[choose] {chosen['name']} {chosen['url']} — {reason}", flush=True)
    print("[choose] Treat unverified Round-1 reviews as reputation signals, not proof.", flush=True)
    try:
        result = buy(chosen["url"], args.item, args.budget)
        stars, review = review_result(result, args.budget)
        receipt = result.get("receipt") if result.get("deal") else None
        event = {"shop_name": chosen["name"], "item": args.item, "budget": args.budget,
                 "deal": bool(result.get("deal")), "price": result.get("price"), "stars": stars}
    except Exception as e:
        print(f"[market_buyer] ERROR {type(e).__name__}: {e}", file=sys.stderr, flush=True)
        stars, review, receipt = 1, f"Trade could not complete due to a protocol or provider error ({type(e).__name__}).", None
        event = {"shop_name": chosen["name"], "item": args.item, "budget": args.budget,
                 "deal": False, "price": None, "stars": stars, "error": type(e).__name__}
    try:
        store_memory(event)
    except OSError as e:
        print(f"[memory] Could not persist event: {e}", file=sys.stderr, flush=True)
    # This MUST be the final line of stdout for the course market kit.
    print(json.dumps({"shop": chosen["url"], "receipt": receipt, "stars": stars, "review": review},
                     ensure_ascii=False, separators=(",", ":")), flush=True)


if __name__ == "__main__":
    main()
