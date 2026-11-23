from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

import uvicorn
from starlette.applications import Starlette

from a2a.helpers import (
    new_data_part,
    new_task_from_user_message,
    new_text_message,
)
from a2a.server.agent_execution import (
    AgentExecutor,
    RequestContext,
)
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import (
    create_agent_card_routes,
    create_jsonrpc_routes,
)
from a2a.server.tasks import (
    InMemoryTaskStore,
    TaskUpdater,
)
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentSkill,
)

from harness import (
    seller_opening,
    seller_turn,
)


HOST = "127.0.0.1"
SHOPS_PATH = Path("challenge2_shops.json")

SESSIONS: dict[str, dict[str, Any]] = {}


def load_shops() -> list[dict[str, Any]]:
    return json.loads(
        SHOPS_PATH.read_text(
            encoding="utf-8"
        )
    )


def get_shop(
    shop_id: str,
) -> dict[str, Any]:

    for shop in load_shops():
        if shop["shop_id"] == shop_id:
            return shop

    raise ValueError(
        f"Unknown shop_id: {shop_id}"
    )


def find_item(
    text: str,
    inventory: dict[str, Any],
) -> str | None:

    lowered = text.lower()

    # longest names first, e.g. "side table"
    for item in sorted(
        inventory.keys(),
        key=len,
        reverse=True,
    ):
        if item.lower() in lowered:
            return item

    return None


def find_price(
    text: str,
) -> int | None:

    values = re.findall(
        r"\b\d+\b",
        text,
    )

    if not values:
        return None

    return int(values[-1])


class Challenge2ShopExecutor(AgentExecutor):

    def __init__(
        self,
        shop: dict[str, Any],
    ) -> None:

        self.shop = shop
        self.inventory = shop["inventory"]

    async def execute(
        self,
        context: RequestContext,
        event_queue: EventQueue,
    ) -> None:

        if context.message is None:
            raise ValueError(
                "Request must contain a message."
            )

        user_text = (
            context.get_user_input().strip()
        )

        task = context.current_task

        if task is None:
            task = new_task_from_user_message(
                context.message
            )

            await event_queue.enqueue_event(
                task
            )

        updater = TaskUpdater(
            event_queue,
            task.id,
            task.context_id,
        )

        # -------------------------------------
        # New negotiation
        # -------------------------------------
        if task.id not in SESSIONS:

            item = find_item(
                user_text,
                self.inventory,
            )

            if item is None:
                await updater.reject(
                    message=new_text_message(
                        "This shop does not sell "
                        "the requested item."
                    )
                )
                return

            item_info = self.inventory[item]

            reserve = int(
                item_info["reserve"]
            )

            listed = int(
                item_info[
                    "advertised_price"
                ]
            )

            decision = seller_opening(
                item=item,
                reserve=reserve,
            )

            asking_price = max(
                reserve,
                int(decision["price"]),
            )

            SESSIONS[task.id] = {
                "shop_id": self.shop["shop_id"],
                "item": item,
                "reserve": reserve,
                "listed_price": listed,
                "current_ask": asking_price,
                "turn_number": 0,
                "history": [
                    f"buyer requested {item}",
                    (
                        f"listed price was "
                        f"{listed}"
                    ),
                    (
                        f"seller asked "
                        f"{asking_price}"
                    ),
                ],
            }

            print(
                "[CHALLENGE2 SHOP OPEN] "
                f"shop={self.shop['shop_id']} "
                f"task={task.id} "
                f"item={item!r} "
                f"reserve={reserve} "
                f"listed={listed} "
                f"ask={asking_price} "
                f"harness="
                f"{json.dumps(decision)}",
                flush=True,
            )

            await updater.requires_input(
                message=new_text_message(
                    f"{self.shop['name']} can "
                    f"sell the {item}. "
                    f"My asking price is "
                    f"{asking_price}. "
                    f"What is your offer?"
                )
            )

            return

        # -------------------------------------
        # Existing negotiation
        # -------------------------------------
        session = SESSIONS[task.id]

        item = str(
            session["item"]
        )

        reserve = int(
            session["reserve"]
        )

        current_ask = int(
            session["current_ask"]
        )

        offer = find_price(
            user_text
        )

        if offer is None:
            await updater.requires_input(
                message=new_text_message(
                    "Please send your offer "
                    "as a whole-number price."
                )
            )
            return

        session["turn_number"] += 1

        turn_number = int(
            session["turn_number"]
        )

        history = session["history"]

        history.append(
            f"buyer offered {offer}"
        )

        decision = seller_turn(
            item=item,
            reserve=reserve,
            current_ask=current_ask,
            buyer_offer=offer,
            turn_number=turn_number,
            history=list(history),
        )

        action = str(
            decision["action"]
        ).lower()

        print(
            "[CHALLENGE2 SHOP TURN] "
            f"shop={self.shop['shop_id']} "
            f"task={task.id} "
            f"item={item!r} "
            f"offer={offer} "
            f"decision="
            f"{json.dumps(decision)}",
            flush=True,
        )

        # -------------------------------------
        # Accept
        # -------------------------------------
        if action == "accept":

            if offer < reserve:
                raise RuntimeError(
                    "Harness attempted to "
                    "accept below reserve."
                )

            receipt = {
                "shop_id": (
                    self.shop["shop_id"]
                ),
                "seller": (
                    self.shop["name"]
                ),
                "item": item,
                "price": offer,
            }

            await updater.add_artifact(
                parts=[
                    new_data_part(
                        receipt,
                        media_type=(
                            "application/json"
                        ),
                    )
                ],
                name="receipt",
            )

            await updater.complete(
                message=new_text_message(
                    f"Deal. {self.shop['name']} "
                    f"accepts {offer} "
                    f"for the {item}."
                )
            )

            print(
                "[CHALLENGE2 SHOP DEAL] "
                + json.dumps(receipt),
                flush=True,
            )

            SESSIONS.pop(
                task.id,
                None,
            )

            return

        # -------------------------------------
        # Counter
        # -------------------------------------
        if action == "counter":

            counter = max(
                reserve,
                int(decision["price"]),
            )

            session[
                "current_ask"
            ] = counter

            history.append(
                f"seller countered {counter}"
            )

            await updater.requires_input(
                message=new_text_message(
                    f"My counteroffer for "
                    f"the {item} is "
                    f"{counter}. "
                    f"What is your next offer?"
                )
            )

            return

        # -------------------------------------
        # Reject
        # -------------------------------------
        if action == "reject":

            await updater.reject(
                message=new_text_message(
                    f"No deal for the {item}."
                )
            )

            SESSIONS.pop(
                task.id,
                None,
            )

            return

        raise RuntimeError(
            f"Unknown seller action: {action}"
        )

    async def cancel(
        self,
        context: RequestContext,
        event_queue: EventQueue,
    ) -> None:

        task = context.current_task

        if task is None:
            return

        updater = TaskUpdater(
            event_queue,
            task.id,
            task.context_id,
        )

        SESSIONS.pop(
            task.id,
            None,
        )

        await updater.cancel(
            message=new_text_message(
                "Negotiation canceled."
            )
        )


def build_app(
    shop: dict[str, Any],
    port: int,
) -> Starlette:

    skills: list[AgentSkill] = []

    for item, info in (
        shop["inventory"].items()
    ):

        listed = int(
            info["advertised_price"]
        )

        skills.append(
            AgentSkill(
                id=(
                    "sell_"
                    + item.replace(
                        " ",
                        "_",
                    )
                ),
                name=f"Sell {item}",
                description=(
                    f"Negotiates the sale of "
                    f"{item}. "
                    f"Listed price: {listed}."
                ),
                tags=[
                    "shopping",
                    "negotiation",
                    item,
                ],
                examples=[
                    f"I want to buy a {item}"
                ],
            )
        )

    card = AgentCard(
        name=shop["name"],
        description=(
            f"{shop['name']} is one of "
            f"20 Challenge 2 shops. "
            f"Inventory and listed prices "
            f"are exposed through skills."
        ),
        version="1.0.0",
        default_input_modes=[
            "text/plain",
        ],
        default_output_modes=[
            "text/plain",
            "application/json",
        ],
        capabilities=AgentCapabilities(
            streaming=False,
        ),
        supported_interfaces=[
            AgentInterface(
                url=(
                    f"http://{HOST}:{port}/"
                ),
                protocol_binding="JSONRPC",
                protocol_version="1.0",
            )
        ],
        skills=skills,
    )

    handler = DefaultRequestHandler(
        agent_executor=(
            Challenge2ShopExecutor(shop)
        ),
        task_store=InMemoryTaskStore(),
        agent_card=card,
    )

    return Starlette(
        routes=[
            *create_agent_card_routes(
                card
            ),
            *create_jsonrpc_routes(
                handler,
                "/",
            ),
        ]
    )


def parse_args() -> argparse.Namespace:

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--shop-id",
        required=True,
    )

    parser.add_argument(
        "--port",
        type=int,
        required=True,
    )

    return parser.parse_args()


def main() -> None:

    args = parse_args()

    shop = get_shop(
        args.shop_id
    )

    app = build_app(
        shop,
        args.port,
    )

    print(
        "[CHALLENGE2 SHOP START] "
        f"shop={shop['shop_id']} "
        f"name={shop['name']!r} "
        f"items="
        f"{list(shop['inventory'])} "
        f"url=http://{HOST}:{args.port}",
        flush=True,
    )

    uvicorn.run(
        app,
        host=HOST,
        port=args.port,
    )


if __name__ == "__main__":
    main()