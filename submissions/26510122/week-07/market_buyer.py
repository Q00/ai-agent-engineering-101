"""Pick a reviewed shop, negotiate over A2A, and remember the outcome by shop name."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from buyer import shop as negotiate
from harness import decide, trace


MEMORY = Path("state/market-memory.json")


def load_memory() -> dict:
    if not MEMORY.exists():
        return {"shops": {}}
    return json.loads(MEMORY.read_text(encoding="utf-8"))


def save_memory(memory: dict) -> None:
    MEMORY.parent.mkdir(parents=True, exist_ok=True)
    temporary = MEMORY.with_suffix(".tmp")
    temporary.write_text(json.dumps(memory, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(MEMORY)


def review_risk(reviews: list[dict]) -> dict:
    stars = [int(r["stars"]) for r in reviews if isinstance(r.get("stars"), (int, float))]
    complaints = [
        r for r in reviews
        if int(r.get("stars", 5)) <= 2
        and any(term in str(r.get("review", "")).lower()
                for term in ("receipt", "overcharg", "mismatch", "charged", "dishonest"))
    ]
    average = round(sum(stars) / len(stars), 2) if stars else None
    return {"average_stars": average, "receipt_complaints": len(complaints),
            "avoid": len(complaints) >= 2 or (len(stars) >= 2 and average is not None and average <= 2)}


def choose_shop(shops: list[dict], item: str, budget: int, memory: dict) -> tuple[dict, str]:
    if not shops:
        raise ValueError("market gave no shops")
    candidates = [dict(shop, risk=review_risk(shop.get("reviews", []))) for shop in shops]
    safe = [shop for shop in candidates if not shop["risk"]["avoid"]]
    eligible = safe or candidates
    excluded = [shop["name"] for shop in candidates if shop not in eligible]
    actions = [str(i) for i in range(len(eligible))]
    answer = decide("market buyer shop selector", {
        "item": item, "private_budget": budget,
        "candidate_shops": [
            {"id": str(i), "name": shop["name"], "reviews": shop.get("reviews", []),
             "review_risk": shop["risk"],
             "own_memory": memory["shops"].get(shop["name"], {})}
            for i, shop in enumerate(eligible)
        ],
        "excluded_for_repeated_low_rating_or_receipt_complaints": excluded,
        "objective": "Pick a reliable shop for this item. Read reviews and your own past trade memory. "
                     "A receipt price above the agreed price is a serious trust failure. "
                     "Return the candidate id as action and null price.",
    }, actions)
    chosen = eligible[int(answer["action"])]
    reason = (f"selected {chosen['name']} from {len(eligible)} eligible shops; "
              f"average stars {chosen['risk']['average_stars']}; "
              f"receipt complaints {chosen['risk']['receipt_complaints']}; "
              f"previous trades {memory['shops'].get(chosen['name'], {}).get('attempts', 0)}; "
              f"excluded {excluded}")
    trace("shop_choice", item=item, budget=budget, name=chosen["name"], url=chosen["url"], reason=reason)
    return chosen, reason


def receipt_from_outcome(outcome: dict, item: str, budget: int) -> tuple[dict | None, str]:
    for artifact in outcome.get("artifacts", []):
        for part in artifact.get("parts", []):
            data = part.get("data")
            if not isinstance(data, dict) or "price" not in data:
                continue
            price = data["price"]
            if isinstance(price, float) and price.is_integer():
                price = int(price)
            if (data.get("item") != item or not isinstance(price, int) or isinstance(price, bool)
                    or price > budget or price not in outcome.get("offers", [])):
                return None, "receipt disagreed with the item, budget, or my offers"
            if not data.get("sig_seller") or not data.get("task"):
                return None, "receipt lacked a shop signature or A2A task id"
            return data, "receipt matched my offer and budget"
    return None, "no receipt was issued"


def remember(memory: dict, name: str, item: str, receipt: dict | None, reason: str) -> None:
    entry = memory["shops"].setdefault(name, {"attempts": 0, "deals": 0, "failures": 0})
    entry["attempts"] += 1
    if receipt:
        entry["deals"] += 1
        entry["last_price"] = receipt["price"]
    else:
        entry["failures"] += 1
    entry["last_item"] = item
    entry["last_outcome"] = reason
    save_memory(memory)
    trace("memory_updated", shop=name, memory=entry)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--shops", required=True)
    parser.add_argument("--item", required=True)
    parser.add_argument("--budget", required=True, type=int)
    args = parser.parse_args()
    shops = json.loads(Path(args.shops).read_text(encoding="utf-8"))
    memory = load_memory()
    chosen, reason = choose_shop(shops, args.item, args.budget, memory)
    try:
        outcome = negotiate(chosen["url"], args.item, args.budget, compact_history=True)
        receipt, detail = receipt_from_outcome(outcome, args.item, args.budget)
    except Exception as error:
        trace("shop_error", name=chosen["name"], error=f"{type(error).__name__}: {error}")
        receipt, detail = None, "shop connection or negotiation failed"
    remember(memory, chosen["name"], args.item, receipt, detail)
    stars = 4 if receipt else 1 if "disagreed" in detail else 3
    review = (f"{detail}; {reason}" if receipt else f"{detail} for {args.item} within {args.budget}; {reason}")
    print(json.dumps({"shop": chosen["url"], "receipt": receipt,
                      "stars": stars, "review": review}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
