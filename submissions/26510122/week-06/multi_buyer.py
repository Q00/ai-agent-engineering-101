"""Choose shops from Agent Cards and buy a list within one shared budget."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import re

import httpx

from buyer import card_and_url, shop
from harness import decide, trace


def price_hint(card: dict) -> int | None:
    match = re.search(r"indicative minimum price\s+(\d+)", card.get("description", ""), re.I)
    return int(match.group(1)) if match else None


def discover(directory: list[str]) -> list[dict]:
    shops = []
    with httpx.Client(timeout=10) as client:
        for index, url in enumerate(directory, 1):
            card, _ = card_and_url(client, url)
            shops.append({
                "id": f"shop-{index:02d}", "url": url, "name": card.get("name"),
                "description": card.get("description", ""),
                "skills": card.get("skills", []), "minimum_price_hint": price_hint(card),
            })
    trace("directory_discovered", shop_count=len(shops))
    return shops


def sells(shop_card: dict, item: str) -> bool:
    return any(item in skill.get("tags", []) for skill in shop_card["skills"])


def allocate(shops: list[dict], items: list[str], total_budget: int) -> list[dict]:
    plans = []
    remaining = total_budget
    for position, item in enumerate(items):
        candidates = [entry for entry in shops if sells(entry, item)]
        if not candidates:
            raise RuntimeError(f"no shop card advertises {item}")
        remaining_items = items[position + 1:]
        floor_for_others = sum(
            min(entry["minimum_price_hint"] or total_budget for entry in shops if sells(entry, other))
            for other in remaining_items
        )
        answer = decide("shopping planner", {
            "shopping_list": items, "current_item": item, "total_budget": total_budget,
            "remaining_budget": remaining, "other_items_after_this": remaining_items,
            "minimum_to_reserve_for_other_items": floor_for_others,
            "candidate_agent_cards": candidates,
            "objective": (
                "Choose a shop id as action and an integer maximum allocation as price. "
                "Prefer a shop with a low indicative price. Reserve enough money for the rest. "
                "The allocation must cover this shop's indicative minimum price."
            ),
        }, [entry["id"] for entry in candidates])
        chosen = next(entry for entry in candidates if entry["id"] == answer["action"])
        cap = remaining - floor_for_others
        floor = chosen["minimum_price_hint"] or 1
        if cap < floor:
            raise RuntimeError(f"insufficient shared budget for {item}")
        allocation = min(cap, max(floor, int(answer["price"])))
        if allocation != answer["price"]:
            trace("budget_guard", item=item, proposed=answer["price"], effective=allocation)
        plan = {"item": item, "shop": chosen, "allocation": allocation}
        plans.append(plan)
        remaining -= allocation
        trace("shopping_plan", item=item, shop=chosen["name"], allocation=allocation, unallocated=remaining)
    return plans


def receipt(result: dict) -> dict | None:
    for artifact in result.get("artifacts", []):
        for part in artifact.get("parts", []):
            if isinstance(part.get("data"), dict) and "price" in part["data"]:
                return part["data"]
    return None


async def execute(plans: list[dict], total_budget: int) -> dict:
    async def one(plan: dict) -> dict:
        return await asyncio.to_thread(
            shop, plan["shop"]["url"], plan["item"], plan["allocation"],
            compact_history=True,
        )

    results = await asyncio.gather(*(one(plan) for plan in plans))
    purchases = {}
    states = []
    for plan, result in zip(plans, results):
        data = receipt(result)
        if result["state"] == "TASK_STATE_COMPLETED" and data:
            if plan["item"] in purchases:
                raise RuntimeError("duplicate purchase")
            if data["price"] > plan["allocation"]:
                raise RuntimeError("receipt exceeds allocated budget")
            purchases[plan["item"]] = data
        states.append({
            "item": plan["item"], "shop": plan["shop"]["name"],
            "allocation": plan["allocation"], "task_id": result["task_id"],
            "offers": result["offers"], "state": result["state"],
        })
    spent = sum(data["price"] for data in purchases.values())
    outcome = {
        "passed": len(purchases) == len(plans) and spent <= total_budget,
        "total_budget": total_budget, "spent": spent,
        "purchases": purchases, "negotiations": states,
    }
    trace("shopping_result", **outcome)
    return outcome


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", required=True)
    parser.add_argument("--shopping-list", required=True)
    args = parser.parse_args()
    urls = json.loads(Path(args.directory).read_text(encoding="utf-8"))
    request = json.loads(Path(args.shopping_list).read_text(encoding="utf-8"))
    shops = discover(urls)
    plans = allocate(shops, request["items"], request["total_budget"])
    result = asyncio.run(execute(plans, request["total_budget"]))
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
