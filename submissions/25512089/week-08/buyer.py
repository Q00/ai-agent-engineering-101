from __future__ import annotations

import argparse
import json
import re
import sys
import time
import uuid
from typing import Any

import httpx

from harness import buyer_turn


A2A_VERSION = "1.0"

HEADERS = {
    "A2A-Version": A2A_VERSION,
    "Content-Type": "application/json",
}

MAX_TURNS = 10
TIME_LIMIT_SECONDS = 280


def fetch_agent_card(
    client: httpx.Client,
    base_url: str,
) -> dict[str, Any]:
    url = (
        base_url.rstrip("/")
        + "/.well-known/agent-card.json"
    )

    response = client.get(
        url,
        headers=HEADERS,
    )

    response.raise_for_status()

    card = response.json()

    print(
        "[BUYER] fetched Agent Card: "
        f"{card.get('name', 'unknown')}",
        flush=True,
    )

    return card


def get_jsonrpc_endpoint(
    card: dict[str, Any],
) -> str:
    interfaces = card.get(
        "supportedInterfaces",
        [],
    )

    for interface in interfaces:
        if (
            str(
                interface.get(
                    "protocolBinding",
                    "",
                )
            ).upper()
            == "JSONRPC"
            and str(
                interface.get(
                    "protocolVersion",
                    "",
                )
            )
            == "1.0"
        ):
            return str(
                interface["url"]
            )

    raise RuntimeError(
        "Seller Agent Card does not expose "
        "a JSONRPC v1.0 interface."
    )


def send_message(
    client: httpx.Client,
    endpoint: str,
    text: str,
    task_id: str | None = None,
) -> dict[str, Any]:

    message: dict[str, Any] = {
        "messageId": str(
            uuid.uuid4()
        ),
        "role": "ROLE_USER",
        "parts": [
            {
                "text": text,
            }
        ],
    }

    if task_id is not None:
        message["taskId"] = task_id

    payload = {
        "jsonrpc": "2.0",
        "id": str(
            uuid.uuid4()
        ),
        "method": "SendMessage",
        "params": {
            "message": message,
        },
    }

    print(
        "[BUYER A2A REQUEST] "
        f"task_id={task_id} "
        f"text={text!r}",
        flush=True,
    )

    response = client.post(
        endpoint,
        headers=HEADERS,
        json=payload,
    )

    response.raise_for_status()

    data = response.json()

    if "error" in data:
        raise RuntimeError(
            "A2A server returned error: "
            + json.dumps(
                data["error"]
            )
        )

    result = data.get(
        "result",
        {},
    )

    if not isinstance(
        result,
        dict,
    ):
        raise RuntimeError(
            f"Invalid A2A result: {result!r}"
        )

    return result


def get_task(
    result: dict[str, Any],
) -> dict[str, Any]:

    task = result.get("task")

    if not isinstance(
        task,
        dict,
    ):
        raise RuntimeError(
            "Expected an A2A Task response, "
            f"received: {result!r}"
        )

    return task


def task_state(
    task: dict[str, Any],
) -> str:
    status = task.get(
        "status",
        {},
    )

    return str(
        status.get(
            "state",
            "",
        )
    ).upper()


def part_text(
    part: Any,
) -> str:
    if not isinstance(
        part,
        dict,
    ):
        return ""

    value = part.get("text")

    if isinstance(
        value,
        str,
    ):
        return value

    return ""


def status_message_text(
    task: dict[str, Any],
) -> str:
    status = task.get(
        "status",
        {},
    )

    message = status.get(
        "message",
        {},
    )

    if not isinstance(
        message,
        dict,
    ):
        return ""

    parts = message.get(
        "parts",
        [],
    )

    return " ".join(
        part_text(part)
        for part in parts
    ).strip()


def extract_last_price(
    text: str,
) -> int:
    values = re.findall(
        r"\b\d+\b",
        text,
    )

    if not values:
        raise RuntimeError(
            "Seller INPUT_REQUIRED message "
            "did not contain a numeric price: "
            f"{text!r}"
        )

    return int(
        values[-1]
    )


def receipt_price(
    task: dict[str, Any],
) -> int | None:
    artifacts = task.get(
        "artifacts",
        [],
    )

    for artifact in artifacts:
        if not isinstance(
            artifact,
            dict,
        ):
            continue

        for part in artifact.get(
            "parts",
            [],
        ):
            if not isinstance(
                part,
                dict,
            ):
                continue

            data = part.get(
                "data"
            )

            if isinstance(
                data,
                dict,
            ):
                price = data.get(
                    "price"
                )

                if isinstance(
                    price,
                    (int, float),
                ):
                    return int(
                        price
                    )

    return None


def run_buyer(
    seller_url: str,
    item: str,
    budget: int,
) -> int:

    started = time.monotonic()

    history: list[str] = []

    with httpx.Client(
        timeout=45.0,
    ) as client:

        card = fetch_agent_card(
            client,
            seller_url,
        )

        endpoint = (
            get_jsonrpc_endpoint(
                card
            )
        )

        print(
            "[BUYER] JSONRPC endpoint: "
            f"{endpoint}",
            flush=True,
        )

        print(
            "[BUYER] "
            f"item={item} "
            f"budget={budget}",
            flush=True,
        )

        result = send_message(
            client,
            endpoint,
            f"I want to buy a {item}",
        )

        task = get_task(
            result
        )

        task_id = str(
            task["id"]
        )

        history.append(
            f"buyer requested {item}"
        )

        for turn_number in range(
            1,
            MAX_TURNS + 1,
        ):

            if (
                time.monotonic()
                - started
                > TIME_LIMIT_SECONDS
            ):
                print(
                    "[BUYER] time limit reached; "
                    "walking away.",
                    flush=True,
                )

                return 0

            state = task_state(
                task
            )

            print(
                "[BUYER] "
                f"task={task_id} "
                f"state={state}",
                flush=True,
            )

            if (
                state
                == "TASK_STATE_COMPLETED"
            ):
                price = receipt_price(
                    task
                )

                if (
                    price is not None
                    and price > budget
                ):
                    raise RuntimeError(
                        "Seller completed a deal "
                        f"at {price}, above buyer "
                        f"budget {budget}."
                    )

                print(
                    "[BUYER] deal completed "
                    f"price={price}",
                    flush=True,
                )

                return 0

            if state in {
                "TASK_STATE_REJECTED",
                "TASK_STATE_CANCELED",
                "TASK_STATE_FAILED",
            }:
                print(
                    "[BUYER] negotiation ended "
                    f"without deal: {state}",
                    flush=True,
                )

                return 0

            if (
                state
                != "TASK_STATE_INPUT_REQUIRED"
            ):
                raise RuntimeError(
                    "Unexpected task state: "
                    f"{state}"
                )

            seller_text = (
                status_message_text(
                    task
                )
            )

            seller_price = (
                extract_last_price(
                    seller_text
                )
            )

            history.append(
                f"seller asked "
                f"{seller_price}"
            )

            print(
                "[BUYER] seller_price="
                f"{seller_price}",
                flush=True,
            )

            # GroqCloud harness decides
            # offer vs walk-away.
            decision = buyer_turn(
                item=item,
                budget=budget,
                seller_price=(
                    seller_price
                ),
                turn_number=(
                    turn_number
                ),
                history=list(
                    history
                ),
            )

            print(
                "[BUYER HARNESS] "
                + json.dumps(
                    decision
                ),
                flush=True,
            )

            action = str(
                decision["action"]
            ).lower()

            if action == "walk":
                history.append(
                    "buyer walked away"
                )

                print(
                    "[BUYER] walking away.",
                    flush=True,
                )

                # Homework explicitly permits
                # simply stopping instead of
                # sending CancelTask.
                return 0

            if action != "offer":
                raise RuntimeError(
                    "Unknown buyer action: "
                    f"{action}"
                )

            offer = int(
                decision["price"]
            )

            # Absolute budget enforcement.
            offer = min(
                budget,
                offer,
            )

            offer = max(
                0,
                offer,
            )

            history.append(
                f"buyer offered {offer}"
            )

            result = send_message(
                client,
                endpoint,
                str(offer),
                task_id=task_id,
            )

            task = get_task(
                result
            )

            returned_task_id = str(
                task["id"]
            )

            if (
                returned_task_id
                != task_id
            ):
                raise RuntimeError(
                    "Seller changed task ID "
                    "during negotiation."
                )

        print(
            "[BUYER] maximum turns reached; "
            "walking away.",
            flush=True,
        )

        return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "url",
    )

    parser.add_argument(
        "item",
    )

    parser.add_argument(
        "budget",
        type=int,
    )

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    try:
        exit_code = run_buyer(
            args.url,
            args.item,
            args.budget,
        )

    except Exception as exc:
        print(
            "[BUYER ERROR] "
            f"{type(exc).__name__}: "
            f"{exc}",
            file=sys.stderr,
            flush=True,
        )

        raise

    raise SystemExit(
        exit_code
    )


if __name__ == "__main__":
    main()