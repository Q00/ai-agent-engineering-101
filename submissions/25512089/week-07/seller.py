from __future__ import annotations

import argparse
import base64
import json
from typing import Any

from cryptography.hazmat.primitives import serialization

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
from adaptive_pricing import adjusted_ask, record_signed_deal


HOST = "127.0.0.1"

RESERVES = {
    "lamp": 100,
    "chair": 180,
    "bookshelf": 260,
}

# Per-task negotiation state.
SESSIONS: dict[str, dict[str, Any]] = {}


def canon(data: dict[str, Any]) -> bytes:
    """Canonical JSON bytes used by both the market and seller signatures."""
    return json.dumps(
        data,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def sign_receipt(
    receipt: dict[str, Any],
    key_path: str = "shop_key.pem",
) -> dict[str, Any]:
    """Sign only the four agreed receipt fields with our registered key."""
    with open(key_path, "rb") as key_file:
        private_key = serialization.load_pem_private_key(
            key_file.read(),
            password=None,
        )

    signed_body = {
        field: receipt[field]
        for field in ("shop", "item", "price", "task")
    }
    receipt["sig_seller"] = base64.b64encode(
        private_key.sign(canon(signed_body))
    ).decode("ascii")
    return receipt


def find_item(text: str) -> str | None:
    text = text.lower()

    for item in RESERVES:
        if item in text:
            return item

    return None


def find_price(text: str) -> int | None:
    """
    Follow-up messages from the trade test carry
    the buyer offer as a number.

    Use the last whole number in the message.
    """
    import re

    values = re.findall(r"\b\d+\b", text)

    if not values:
        return None

    return int(values[-1])


class SellerExecutor(AgentExecutor):

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
        # First message -> create task
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
        # New negotiation
        # -----------------------------------------
        if task.id not in SESSIONS:

            item = find_item(user_text)

            if item is None:
                await updater.reject(
                    message=new_text_message(
                        "I only sell lamp, chair, "
                        "and bookshelf."
                    )
                )
                return

            reserve = RESERVES[item]

            try:
                decision = seller_opening(
                    item=item,
                    reserve=reserve,
                )
            except RuntimeError as exc:
                if not any(x in str(exc).lower() for x in ("429", "cooldown", "rate limit", "quota")):
                    raise
                fallback_price = reserve + max(8, round(reserve * 0.08))
                decision = {
                    "price": fallback_price,
                    "reason": "Groq rate-limit fallback",
                }
                print(
                    f"[SELLER FALLBACK OPEN] item={item} base_ask={fallback_price}",
                    flush=True,
                )

            asking_price = int(
                decision["price"]
            )

            # Hard safety boundary.
            # The harness chooses the price,
            # but code never allows an ask
            # below reserve.
            asking_price = max(
                reserve,
                asking_price,
            )
            asking_price, pricing_info = adjusted_ask(item, reserve, asking_price)
            print('[ADAPTIVE PRICE] ' + json.dumps(pricing_info), flush=True)

            SESSIONS[task.id] = {
                "item": item,
                "reserve": reserve,
                "current_ask": asking_price,
                "turn_number": 0,
                "history": [
                    f"buyer requested {item}",
                    f"seller asked {asking_price}",
                ],
            }

            print(
                "[SELLER HARNESS] "
                f"opening={json.dumps(decision)} "
                f"effective_ask={asking_price}",
                flush=True,
            )

            await updater.requires_input(
                message=new_text_message(
                    f"I can sell the {item}. "
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

        # -----------------------------------------
        # Harness decides accept/counter/reject
        # -----------------------------------------
        try:
            decision = seller_turn(
                item=item,
                reserve=reserve,
                current_ask=current_ask,
                buyer_offer=offer,
                turn_number=turn_number,
                history=list(history),
            )
        except RuntimeError as exc:
            if not any(x in str(exc).lower() for x in ("429", "cooldown", "rate limit", "quota")):
                raise

            if offer >= reserve:
                decision = {
                    "action": "accept",
                    "reason": "Groq rate-limit fallback; offer meets reserve",
                }
                print(
                    f"[SELLER FALLBACK ACCEPT] item={item} offer={offer}",
                    flush=True,
                )
            else:
                counter = max(
                    reserve,
                    round((current_ask + reserve) / 2),
                )
                counter = min(current_ask, counter)
                session["current_ask"] = counter
                history.append(f"seller counteroffered {counter}")
                print(
                    f"[SELLER FALLBACK COUNTER] item={item} "
                    f"offer={offer} counter={counter}",
                    flush=True,
                )
                await updater.requires_input(
                    message=new_text_message(
                        f"I cannot accept {offer} for the {item}. "
                        f"My counteroffer is {counter}. "
                        "What is your next offer?"
                    )
                )
                return

        action = str(
            decision["action"]
        ).lower()

        print(
            "[SELLER HARNESS] "
            f"task={task.id} "
            f"offer={offer} "
            f"decision={json.dumps(decision)}",
            flush=True,
        )

        # -----------------------------------------
        # Accept
        # -----------------------------------------
        if action == "accept":

            # Absolute safety boundary.
            if offer < reserve:
                raise RuntimeError(
                    "Harness attempted to "
                    "accept below reserve."
                )

            # Week 07 requires the same task id and an Ed25519
            # signature over {shop, item, price, task}.
            receipt = sign_receipt({
                "shop": "Arun A2A Seller",
                "item": item,
                "price": int(offer),
                "task": task.id,
            })

            print(
                "[SELLER SIGNED RECEIPT] "
                f"task={task.id} item={item} price={int(offer)}",
                flush=True,
            )

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
                    f"for the {item}."
                )
            )
            record_signed_deal(item=item, price=int(offer), task_id=str(task.id))

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

            # Harness chooses it, but server
            # enforces the seller reserve.
            counter_price = max(
                reserve,
                counter_price,
            )

            session[
                "current_ask"
            ] = counter_price

            history.append(
                f"seller countered "
                f"{counter_price}"
            )

            await updater.requires_input(
                message=new_text_message(
                    f"My counteroffer for the "
                    f"{item} is "
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
    port: int,
) -> Starlette:

    skill = AgentSkill(
        id="negotiate_purchase",
        name="Negotiate a purchase",
        description=(
            "Negotiate the purchase of a "
            "lamp, chair, or bookshelf."
        ),
        tags=[
            "shopping",
            "negotiation",
            "seller",
        ],
        examples=[
            "I want to buy a lamp",
            "I want to buy a chair",
            "I want to buy a bookshelf",
        ],
    )

    card = AgentCard(
        name="Arun A2A Seller",
        description=(
            "A model-driven seller agent "
            "for Week 07."
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
                    f"http://{HOST}:"
                    f"{port}/"
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
        agent_executor=SellerExecutor(),
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
        "[SELLER] "
        f"model-driven A2A seller "
        f"listening on "
        f"http://{HOST}:{args.port}",
        flush=True,
    )

    uvicorn.run(
        app,
        host=HOST,
        port=args.port,
    )


if __name__ == "__main__":
    main()