from __future__ import annotations

import json
import re
from concurrent.futures import (
    ThreadPoolExecutor,
    as_completed,
)
from collections import deque
from pathlib import Path
from typing import Any

import httpx

from buyer import (
    fetch_agent_card,
    get_jsonrpc_endpoint,
    get_task,
    receipt_price,
    send_message,
    status_message_text,
    task_state,
    extract_last_price,
)
from challenge2_harness import plan_shopping
from harness import buyer_turn


SHOPS_PATH = Path("challenge2_shops.json")

SHOPPING_LIST = [
    "lamp",
    "chair",
    "side table",
]

TOTAL_BUDGET = 450
MAX_TURNS = 8


def load_directory() -> list[dict[str, Any]]:
    return json.loads(
        SHOPS_PATH.read_text(
            encoding="utf-8"
        )
    )


def parse_card(
    *,
    configured_shop: dict[str, Any],
    card: dict[str, Any],
) -> dict[str, Any]:

    endpoint = get_jsonrpc_endpoint(card)

    skills: list[dict[str, Any]] = []

    for skill in card.get("skills", []):

        tags = [
            str(tag).lower()
            for tag in skill.get("tags", [])
        ]

        item = None

        for candidate in [
            "lamp",
            "chair",
            "bookshelf",
            "desk",
            "side table",
        ]:
            if candidate in tags:
                item = candidate
                break

        if item is None:
            continue

        description = str(
            skill.get("description", "")
        )

        match = re.search(
            r"Listed price:\s*(\d+)",
            description,
            flags=re.IGNORECASE,
        )

        listed_price = (
            int(match.group(1))
            if match
            else None
        )

        skills.append(
            {
                "item": item,
                "listed_price": listed_price,
            }
        )

    return {
        "shop_id": configured_shop["shop_id"],
        "name": card.get(
            "name",
            configured_shop["name"],
        ),
        "url": endpoint.rstrip("/"),
        "skills": skills,
    }


def read_all_agent_cards(
    directory: list[dict[str, Any]],
) -> list[dict[str, Any]]:

    discovered: list[dict[str, Any]] = []

    with httpx.Client(
        timeout=10.0,
    ) as client:

        for shop in directory:

            card = fetch_agent_card(
                client,
                shop["url"],
            )

            info = parse_card(
                configured_shop=shop,
                card=card,
            )

            discovered.append(info)

            print(
                "[CHALLENGE2 CARD] "
                f"shop={info['shop_id']} "
                f"name={info['name']!r} "
                f"skills={json.dumps(info['skills'])}",
                flush=True,
            )

    return discovered


def compact_summary(
    *,
    item: str,
    shop_url: str,
    budget: int,
    task_id: str,
    state: str,
    turn: int,
    seller_price: int | None,
) -> str:

    text = (
        f"Negotiation item={item}; "
        f"shop={shop_url}; "
        f"budget={budget}; "
        f"taskId={task_id}; "
        f"state={state}; "
        f"turn={turn}; "
        f"latestSellerPrice={seller_price}."
    )

    return text[:800]


def negotiate_one(
    allocation: dict[str, Any],
    cards_by_url: dict[str, dict[str, Any]],
) -> dict[str, Any]:

    item = str(
        allocation["item"]
    )

    shop_url = str(
        allocation["shop_url"]
    ).rstrip("/")

    budget = int(
        allocation["budget"]
    )

    card_info = cards_by_url[
        shop_url
    ]

    endpoint = shop_url

    recent_messages: deque[str] = deque(
        maxlen=2
    )

    print(
        "[CHALLENGE2 NEGOTIATION START] "
        f"item={item!r} "
        f"shop={card_info['shop_id']} "
        f"budget={budget}",
        flush=True,
    )

    with httpx.Client(
        timeout=45.0,
    ) as client:

        result = send_message(
            client,
            endpoint,
            f"I want to buy a {item}",
        )

        task = get_task(result)
        task_id = str(task["id"])

        for turn in range(
            1,
            MAX_TURNS + 1,
        ):

            state = task_state(task)

            if state == "TASK_STATE_COMPLETED":

                price = receipt_price(task)

                if price is None:
                    raise RuntimeError(
                        f"{item}: completed "
                        "without numeric receipt price"
                    )

                if price > budget:
                    raise RuntimeError(
                        f"{item}: paid {price} "
                        f"above allocation {budget}"
                    )

                print(
                    "[CHALLENGE2 PURCHASE] "
                    f"item={item!r} "
                    f"shop={card_info['shop_id']} "
                    f"price={price} "
                    f"budget={budget} "
                    f"task={task_id}",
                    flush=True,
                )

                return {
                    "success": True,
                    "item": item,
                    "shop_id": (
                        card_info["shop_id"]
                    ),
                    "shop_url": shop_url,
                    "price": price,
                    "budget": budget,
                    "task_id": task_id,
                }

            if state in {
                "TASK_STATE_REJECTED",
                "TASK_STATE_CANCELED",
                "TASK_STATE_FAILED",
            }:

                print(
                    "[CHALLENGE2 PURCHASE FAILED] "
                    f"item={item!r} "
                    f"shop={card_info['shop_id']} "
                    f"state={state}",
                    flush=True,
                )

                return {
                    "success": False,
                    "item": item,
                    "shop_id": (
                        card_info["shop_id"]
                    ),
                    "budget": budget,
                    "state": state,
                }

            if state != "TASK_STATE_INPUT_REQUIRED":
                raise RuntimeError(
                    f"{item}: unexpected "
                    f"task state {state}"
                )

            seller_text = (
                status_message_text(task)
            )

            seller_price = (
                extract_last_price(
                    seller_text
                )
            )

            recent_messages.append(
                "seller: " + seller_text
            )

            summary = compact_summary(
                item=item,
                shop_url=shop_url,
                budget=budget,
                task_id=task_id,
                state=state,
                turn=turn,
                seller_price=seller_price,
            )

            context_for_model = [
                "SUMMARY: " + summary,
                *list(recent_messages),
            ]

            print(
                "[CHALLENGE2 CONTEXT] "
                f"item={item!r} "
                f"summary_len={len(summary)} "
                f"recent_messages="
                f"{len(recent_messages)} "
                f"task={task_id}",
                flush=True,
            )

            decision = buyer_turn(
                item=item,
                budget=budget,
                seller_price=seller_price,
                turn_number=turn,
                history=context_for_model,
            )

            print(
                "[CHALLENGE2 BUYER HARNESS] "
                f"item={item!r} "
                f"decision="
                f"{json.dumps(decision)}",
                flush=True,
            )

            action = str(
                decision["action"]
            ).lower()

            if action == "walk":

                return {
                    "success": False,
                    "item": item,
                    "shop_id": (
                        card_info["shop_id"]
                    ),
                    "budget": budget,
                    "state": "WALKED_AWAY",
                }

            if action != "offer":
                raise RuntimeError(
                    f"{item}: unknown "
                    f"buyer action {action}"
                )

            offer = int(
                decision["price"]
            )

            # Absolute item-budget protection.
            offer = min(
                budget,
                offer,
            )

            offer = max(
                0,
                offer,
            )

            recent_messages.append(
                f"buyer: offered {offer}"
            )

            result = send_message(
                client,
                endpoint,
                str(offer),
                task_id=task_id,
            )

            task = get_task(result)

            returned_task_id = str(
                task["id"]
            )

            if returned_task_id != task_id:
                raise RuntimeError(
                    f"{item}: seller changed "
                    "taskId during negotiation"
                )

        return {
            "success": False,
            "item": item,
            "shop_id": card_info["shop_id"],
            "budget": budget,
            "state": "MAX_TURNS",
        }


def main() -> None:

    directory = load_directory()

    if len(directory) != 20:
        raise RuntimeError(
            f"Expected 20 shops, "
            f"got {len(directory)}"
        )

    print(
        "[CHALLENGE2] "
        f"shopping_list={SHOPPING_LIST} "
        f"total_budget={TOTAL_BUDGET}",
        flush=True,
    )

    # -----------------------------------------
    # 1. Read all 20 Agent Cards
    # -----------------------------------------
    discovered = read_all_agent_cards(
        directory
    )

    if len(discovered) != 20:
        raise RuntimeError(
            "Did not read all 20 Agent Cards"
        )

    print(
        "[CHALLENGE2 CARDS COMPLETE] "
        f"count={len(discovered)}",
        flush=True,
    )

    # -----------------------------------------
    # 2. Harness chooses shops + allocations
    # -----------------------------------------
    plan = plan_shopping(
        shops=discovered,
        shopping_list=SHOPPING_LIST,
        total_budget=TOTAL_BUDGET,
    )

    allocations = plan["allocations"]

    print(
        "[CHALLENGE2 PLAN] "
        + json.dumps(
            plan,
            ensure_ascii=False,
        ),
        flush=True,
    )

    cards_by_url = {
        str(shop["url"]).rstrip("/"): shop
        for shop in discovered
    }

    # -----------------------------------------
    # 3. Negotiate with several shops at once
    # -----------------------------------------
    results: list[dict[str, Any]] = []

    with ThreadPoolExecutor(
        max_workers=len(allocations)
    ) as executor:

        futures = [
            executor.submit(
                negotiate_one,
                allocation,
                cards_by_url,
            )
            for allocation in allocations
        ]

        for future in as_completed(
            futures
        ):
            results.append(
                future.result()
            )

    print(
        "[CHALLENGE2 RESULTS] "
        + json.dumps(
            results,
            ensure_ascii=False,
        ),
        flush=True,
    )

    # -----------------------------------------
    # 4. Mechanical verification
    # -----------------------------------------
    purchases = [
        result
        for result in results
        if result.get("success")
    ]

    purchased_items = [
        str(result["item"])
        for result in purchases
    ]

    if len(purchases) != len(
        SHOPPING_LIST
    ):
        raise RuntimeError(
            "Not every shopping-list item "
            "was purchased."
        )

    if set(purchased_items) != set(
        SHOPPING_LIST
    ):
        raise RuntimeError(
            "Purchased items do not match "
            "the shopping list."
        )

    if len(purchased_items) != len(
        set(purchased_items)
    ):
        raise RuntimeError(
            "Duplicate item purchase detected."
        )

    total_spend = sum(
        int(result["price"])
        for result in purchases
    )

    if total_spend > TOTAL_BUDGET:
        raise RuntimeError(
            f"Total spend {total_spend} "
            f"exceeds budget {TOTAL_BUDGET}."
        )

    distinct_shops = {
        result["shop_id"]
        for result in purchases
    }

    if len(distinct_shops) < 3:
        raise RuntimeError(
            "Expected purchases from at least "
            "three different shops."
        )

    print(
        "[CHALLENGE2 VERIFY] "
        f"cards=20 "
        f"items={len(purchases)}/"
        f"{len(SHOPPING_LIST)} "
        f"distinct_shops="
        f"{len(distinct_shops)} "
        f"total_spend={total_spend}/"
        f"{TOTAL_BUDGET} "
        "duplicates=0",
        flush=True,
    )

    print(
        "[CHALLENGE2] PASS",
        flush=True,
    )


if __name__ == "__main__":
    main()