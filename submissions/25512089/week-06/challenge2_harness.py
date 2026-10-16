from __future__ import annotations

import json
from typing import Any

from harness import _call_json


def plan_shopping(
    *,
    shops: list[dict[str, Any]],
    shopping_list: list[str],
    total_budget: int,
) -> dict[str, Any]:
    """
    The harness decides which shop should handle
    each item and how much budget to allocate.
    """

    system = """
You are the planning harness for an A2A shopping agent.

The buyer has:
- a shopping list,
- one total budget,
- Agent Card information from 20 shops.

You must decide:
1. which shop should be used for each item;
2. the maximum budget allocated to that item.

Rules:
- Every requested item must appear exactly once.
- Select only shop URLs provided in the input.
- Select a shop only if its Agent Card skills show
  that it sells the requested item.
- The sum of all item budgets must not exceed the
  total budget.
- Allocate enough budget to make a realistic deal.
- Prefer lower listed prices when possible.
- Use different shops for different items when
  reasonable so negotiations can run concurrently.
- The model must make the shop-selection and
  allocation decisions.

Return ONLY JSON in this form:

{
  "allocations": [
    {
      "item": "lamp",
      "shop_url": "http://127.0.0.1:9301",
      "budget": 100
    }
  ]
}
""".strip()

    user = f"""
Shopping list:
{json.dumps(shopping_list)}

Total budget:
{total_budget}

Available shops derived from Agent Cards:
{json.dumps(
    shops,
    ensure_ascii=False,
    indent=2,
)}

Choose one shop and one maximum budget for every
item in the shopping list.
""".strip()

    allowed_urls = {
        str(shop["url"])
        for shop in shops
    }

    skills_by_url = {
        str(shop["url"]): {
            str(skill["item"])
            for skill in shop["skills"]
        }
        for shop in shops
    }

    last_result: dict[str, Any] | None = None

    for semantic_attempt in range(1, 4):

        result = _call_json(
            system,
            user,
            label="challenge2_shopping_plan",
        )

        last_result = result

        try:
            allocations = result.get(
                "allocations"
            )

            if not isinstance(
                allocations,
                list,
            ):
                raise ValueError(
                    "allocations must be a list"
                )

            if len(allocations) != len(
                shopping_list
            ):
                raise ValueError(
                    "must allocate exactly one "
                    "entry per shopping-list item"
                )

            cleaned: list[dict[str, Any]] = []

            seen_items: set[str] = set()

            for allocation in allocations:

                item = str(
                    allocation["item"]
                ).lower()

                shop_url = str(
                    allocation["shop_url"]
                ).rstrip("/")

                budget = int(
                    allocation["budget"]
                )

                if item not in shopping_list:
                    raise ValueError(
                        f"unexpected item: {item}"
                    )

                if item in seen_items:
                    raise ValueError(
                        f"duplicate item: {item}"
                    )

                if shop_url not in allowed_urls:
                    raise ValueError(
                        f"unknown shop: {shop_url}"
                    )

                if (
                    item
                    not in skills_by_url[
                        shop_url
                    ]
                ):
                    raise ValueError(
                        f"{shop_url} does not "
                        f"advertise {item}"
                    )

                if budget <= 0:
                    raise ValueError(
                        "item budget must be "
                        "positive"
                    )

                seen_items.add(item)

                cleaned.append(
                    {
                        "item": item,
                        "shop_url": shop_url,
                        "budget": budget,
                    }
                )

            if seen_items != set(
                shopping_list
            ):
                raise ValueError(
                    "shopping list not fully "
                    "covered"
                )

            allocated_total = sum(
                entry["budget"]
                for entry in cleaned
            )

            if allocated_total > total_budget:
                raise ValueError(
                    f"allocated {allocated_total}, "
                    f"above total budget "
                    f"{total_budget}"
                )

            # Require multiple distinct shops for
            # the challenge demonstration.
            distinct_shops = {
                entry["shop_url"]
                for entry in cleaned
            }

            if len(cleaned) >= 3 and (
                len(distinct_shops) < 3
            ):
                raise ValueError(
                    "for this challenge run, "
                    "use three distinct shops"
                )

            return {
                "allocations": cleaned,
                "allocated_total": (
                    allocated_total
                ),
            }

        except Exception as exc:

            print(
                "[CHALLENGE2 PLAN RETRY] "
                f"attempt="
                f"{semantic_attempt}/3 "
                f"result={result} "
                f"error={exc}",
                flush=True,
            )

    raise RuntimeError(
        "Challenge 2 shopping plan remained "
        f"invalid: {last_result}"
    )
