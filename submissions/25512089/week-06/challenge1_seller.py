from __future__ import annotations

import argparse
import json
import re
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

from challenge1_catalog import (
    load_catalog,
    search_catalog,
)
from challenge1_harness import (
    choose_product,
    description_to_criteria,
)
from harness import seller_turn


HOST = "127.0.0.1"

CATALOG = load_catalog()

SESSIONS: dict[str, dict[str, Any]] = {}


def find_price(text: str) -> int | None:
    values = re.findall(r"\b\d+\b", text)

    if not values:
        return None

    return int(values[-1])


class Challenge1SellerExecutor(AgentExecutor):

    async def execute(
        self,
        context: RequestContext,
        event_queue: EventQueue,
    ) -> None:

        if context.message is None:
            raise ValueError(
                "Request must contain a message."
            )

        user_text = context.get_user_input().strip()
        task = context.current_task

        # -----------------------------------------
        # Create a new task
        # -----------------------------------------
        if task is None:
            task = new_task_from_user_message(
                context.message
            )

            await event_queue.enqueue_event(task)

        updater = TaskUpdater(
            event_queue,
            task.id,
            task.context_id,
        )

        # -----------------------------------------
        # First message:
        # buyer describes what they need
        # -----------------------------------------
        if task.id not in SESSIONS:

            buyer_description = user_text

            print(
                "[CHALLENGE1 REQUEST] "
                f"task={task.id} "
                f"description={buyer_description!r}",
                flush=True,
            )

            # Harness interprets natural language.
            criteria = description_to_criteria(
                buyer_description
            )

            print(
                "[CHALLENGE1 CRITERIA] "
                + json.dumps(
                    criteria,
                    ensure_ascii=False,
                ),
                flush=True,
            )

            # Code narrows 4,000 products.
            candidates = search_catalog(
                CATALOG,
                criteria,
                limit=8,
            )

            print(
                "[CHALLENGE1 SEARCH] "
                f"catalog_size={len(CATALOG)} "
                f"candidate_count={len(candidates)} "
                f"candidate_skus="
                f"{[p['sku'] for p in candidates]}",
                flush=True,
            )

            if not candidates:
                print(
                    "[CHALLENGE1 NO MATCH] "
                    f"task={task.id}",
                    flush=True,
                )

                await updater.reject(
                    message=new_text_message(
                        "I could not find a product "
                        "that satisfies your request."
                    )
                )

                return

            # Harness selects product and price.
            selection = choose_product(
                buyer_description=buyer_description,
                candidates=candidates,
            )

            product = selection["product"]

            sku = str(product["sku"])
            name = str(product["name"])
            reserve = int(product["reserve"])

            asking_price = int(
                selection["price"]
            )

            # Absolute reserve protection.
            asking_price = max(
                reserve,
                asking_price,
            )

            print(
                "[CHALLENGE1 SELECTION] "
                f"task={task.id} "
                f"sku={sku} "
                f"name={name!r} "
                f"reserve={reserve} "
                f"ask={asking_price}",
                flush=True,
            )

            SESSIONS[task.id] = {
                "buyer_description": buyer_description,
                "sku": sku,
                "name": name,
                "reserve": reserve,
                "current_ask": asking_price,
                "turn_number": 0,
                "history": [
                    (
                        "buyer described request: "
                        f"{buyer_description}"
                    ),
                    (
                        "seller selected "
                        f"{sku} {name}"
                    ),
                    (
                        f"seller asked {asking_price}"
                    ),
                ],
            }

            await updater.requires_input(
                message=new_text_message(
                    f"I found {name} "
                    f"({sku}) for your request. "
                    f"My asking price is "
                    f"{asking_price}. "
                    f"What is your offer?"
                )
            )

            return

        # -----------------------------------------
        # Existing negotiation
        # -----------------------------------------
        session = SESSIONS[task.id]

        sku = str(session["sku"])
        name = str(session["name"])
        reserve = int(session["reserve"])
        current_ask = int(
            session["current_ask"]
        )

        offer = find_price(user_text)

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

        # Existing model-driven negotiation harness.
        decision = seller_turn(
            item=name,
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
            "[CHALLENGE1 NEGOTIATION] "
            f"task={task.id} "
            f"sku={sku} "
            f"offer={offer} "
            f"decision={json.dumps(decision)}",
            flush=True,
        )

        # -----------------------------------------
        # Accept
        # -----------------------------------------
        if action == "accept":

            if offer < reserve:
                raise RuntimeError(
                    "Harness attempted to accept "
                    "below the product reserve."
                )

            receipt = {
                "seller": (
                    "Arun Large Catalog Seller"
                ),
                "sku": sku,
                "item": name,
                "price": offer,
                "reserve": reserve,
            }

            history.append(
                f"seller accepted {offer}"
            )

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
                    f"Deal. I accept {offer} "
                    f"for {name} ({sku})."
                )
            )

            print(
                "[CHALLENGE1 DEAL] "
                + json.dumps(receipt),
                flush=True,
            )

            SESSIONS.pop(
                task.id,
                None,
            )

            return

        # -----------------------------------------
        # Counteroffer
        # -----------------------------------------
        if action == "counter":

            counter_price = int(
                decision["price"]
            )

            counter_price = max(
                reserve,
                counter_price,
            )

            session[
                "current_ask"
            ] = counter_price

            history.append(
                f"seller countered {counter_price}"
            )

            await updater.requires_input(
                message=new_text_message(
                    f"My counteroffer for "
                    f"{name} ({sku}) is "
                    f"{counter_price}. "
                    f"What is your next offer?"
                )
            )

            return

        # -----------------------------------------
        # Reject
        # -----------------------------------------
        if action == "reject":

            history.append(
                "seller rejected negotiation"
            )

            await updater.reject(
                message=new_text_message(
                    f"No deal for "
                    f"{name} ({sku})."
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
    port: int,
) -> Starlette:

    skill = AgentSkill(
        id="large_catalog_shopping",
        name="Large catalog product shopping",
        description=(
            "Describe the product you need. "
            "The seller searches a catalog of "
            "4,000 products and negotiates "
            "the selected product."
        ),
        tags=[
            "shopping",
            "catalog",
            "search",
            "negotiation",
        ],
        examples=[
            (
                "I need something wooden "
                "to keep about 40 books, "
                "not taller than my desk."
            ),
            (
                "I need a black metal chair."
            ),
        ],
    )

    card = AgentCard(
        name="Arun Large Catalog Seller",
        description=(
            "A model-driven A2A seller with "
            "a 4,000-product catalog."
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
        skills=[
            skill,
        ],
    )

    handler = DefaultRequestHandler(
        agent_executor=(
            Challenge1SellerExecutor()
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
        "--port",
        type=int,
        required=True,
    )

    return parser.parse_args()


def main() -> None:

    args = parse_args()

    app = build_app(
        args.port
    )

    print(
        "[CHALLENGE1 SELLER] "
        f"catalog_size={len(CATALOG)} "
        f"listening=http://{HOST}:"
        f"{args.port}",
        flush=True,
    )

    uvicorn.run(
        app,
        host=HOST,
        port=args.port,
    )


if __name__ == "__main__":
    main()