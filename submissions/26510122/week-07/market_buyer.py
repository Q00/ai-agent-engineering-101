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
    unflagged_by_memory = [shop for shop in candidates
                           if not memory["shops"].get(shop["name"], {}).get("avoid", False)]
    safe = [shop for shop in unflagged_by_memory if not shop["risk"]["avoid"]]
    eligible = safe or unflagged_by_memory or candidates
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


def receipt_from_outcome(outcome: dict, item: str, budget: int) -> tuple[dict | None, str, str, dict]:
    task_id = outcome.get("task_id")
    offers = outcome.get("offers", [])
    for artifact in outcome.get("artifacts", []):
        for part in artifact.get("parts", []):
            data = part.get("data")
            if not isinstance(data, dict) or "price" not in data:
                continue
            price = data["price"]
            if isinstance(price, float) and price.is_integer():
                price = int(price)
            evidence = {"task_id": task_id, "receipt_task": data.get("task"),
                        "receipt_item": data.get("item"), "receipt_price": price,
                        "offers": offers}
            if (data.get("item") != item or not isinstance(price, int) or isinstance(price, bool)
                    or price > budget or price not in offers):
                return None, "receipt_mismatch", "receipt disagreed with the item, budget, or my offers", evidence
            if not data.get("sig_seller") or not task_id or data.get("task") != task_id:
                return None, "invalid_receipt", "receipt lacked a shop signature or matching A2A task id", evidence
            return data, "deal", "receipt matched my offer, budget, and task", evidence
    return None, "no_deal", "no receipt was issued", {"task_id": task_id, "offers": offers}


def remember(memory: dict, name: str, item: str, receipt: dict | None,
             status: str, detail: str, evidence: dict) -> None:
    entry = memory["shops"].setdefault(name, {"attempts": 0, "deals": 0, "failures": 0})
    entry["attempts"] += 1
    if receipt:
        entry["deals"] += 1
        entry["last_price"] = receipt["price"]
    else:
        entry["failures"] += 1
        if status == "no_deal":
            entry["no_deals"] = entry.get("no_deals", 0) + 1
        elif status in ("receipt_mismatch", "invalid_receipt"):
            entry["trust_failures"] = entry.get("trust_failures", 0) + 1
            entry["avoid"] = True
        elif status == "shop_error":
            entry["errors"] = entry.get("errors", 0) + 1
    entry["last_item"] = item
    entry["last_status"] = status
    entry["last_outcome"] = detail
    entry["last_evidence"] = evidence
    save_memory(memory)
    trace("memory_updated", shop=name, memory=entry)


def public_review(item: str, receipt: dict | None, status: str) -> tuple[int, str]:
    if status == "deal" and receipt:
        return 4, f"Bought {item} for {int(receipt['price'])}; receipt matched my offer and order."
    if status == "receipt_mismatch":
        return 1, f"Receipt for {item} did not match my offer or order."
    if status == "invalid_receipt":
        return 1, f"Receipt for {item} lacked a seller signature or matching task ID."
    if status == "shop_error":
        return 3, f"No completed trade for {item}; a connection or negotiation error occurred."
    return 3, f"No agreement on {item}; no receipt was issued."


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
        receipt, status, detail, evidence = receipt_from_outcome(outcome, args.item, args.budget)
    except Exception as error:
        trace("shop_error", name=chosen["name"], error=f"{type(error).__name__}: {error}")
        receipt, status, detail, evidence = None, "shop_error", "shop connection or negotiation failed", {}
    remember(memory, chosen["name"], args.item, receipt, status, detail, evidence)
    stars, review = public_review(args.item, receipt, status)
    print(json.dumps({"shop": chosen["url"], "receipt": receipt,
                      "stars": stars, "review": review}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
