import asyncio
import json
import os

from openai import AsyncOpenAI
from mcp import Client
from mcp.client.streamable_http import (
    create_mcp_http_client,
    streamable_http_client,
)

MODEL = os.environ.get("AGENT_MODEL", "gpt-4o-mini")
TEMPERATURE = 0
MAX_COMPLETION_TOKENS = 300
MAX_MODEL_STEPS = 8
MOVES = {"propose", "accept_proposal", "reject_proposal", "refuse"}


def system_prompt(role, item, limit):
    if role == "buyer":
        instruction = (
            f"You are buying {item}. Your private budget is {limit}. "
            "Do not propose or accept a price above your budget. "
            "Try to buy at a low price."
        )
    else:
        instruction = (
            f"You are selling {item}. Your private reserve is {limit}. "
            "Do not propose or accept a price below your reserve. "
            "Try to sell at a high price."
        )

    return instruction + (
        " Keep your private limit secret. Use integer prices. "
        "The buyer starts, and the parties alternate for at most eight moves. "
        "First call get_negotiation to read the current state. "
        "Then choose one move: propose offers a price; accept_proposal "
        "accepts the other party's latest price and ends with a deal; "
        "reject_proposal declines and continues; refuse ends without a deal. "
        "Use one tool call at a time. "
        "If a move is refused, read the error and try another move. "
        "Stop after one move succeeds."
    )


async def model_reply(model_client, messages, tools, log):
    for attempt in range(6):
        try:
            response = await model_client.chat.completions.create(
                model=MODEL,
                temperature=TEMPERATURE,
                max_completion_tokens=MAX_COMPLETION_TOKENS,
                messages=messages,
                tools=tools,
            )
        except Exception as error:
            status = getattr(error, "status_code", None)
            retryable = status == 429 or (
                isinstance(status, int) and 500 <= status < 600
            )
            if not retryable or attempt == 5:
                raise
            reason = f"HTTP {status}"
        else:
            if getattr(response, "choices", None):
                return response.choices[0].message
            if attempt == 5:
                raise RuntimeError("Model returned no choices after retries.")
            reason = "response without choices"

        delay = 2 ** (attempt + 1)
        log(f"[retry] {reason}; waiting {delay} seconds")
        await asyncio.sleep(delay)


async def take_turn(
    negotiation_id, role, item, limit, token, stats, log=print
):
    prompt = system_prompt(role, item, limit)
    messages = [
        {"role": "system", "content": prompt},
        {
            "role": "user",
            "content": (
                f"It is your turn. Negotiation ID: {negotiation_id}. "
                "Read the negotiation and make one move."
            ),
        },
    ]
    log(f"[system {role}] {prompt}")
    refused_this_turn = 0

    async with create_mcp_http_client(
        headers={"Authorization": f"Bearer {token}"}
    ) as http:
        transport = streamable_http_client(
            "http://127.0.0.1:8000/mcp", http_client=http
        )
        async with Client(transport, cache=None) as market:
            available = (await market.list_tools()).tools
            tools = [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description or "",
                        "parameters": tool.input_schema,
                    },
                }
                for tool in available
            ]

            async with AsyncOpenAI(max_retries=0) as model_client:
                for step in range(MAX_MODEL_STEPS):
                    message = await model_reply(
                        model_client, messages, tools, log
                    )
                    messages.append(message.model_dump(exclude_none=True))

                    if message.content:
                        log(f"[agent {role}] {message.content}")

                    if not message.tool_calls:
                        messages.append({
                            "role": "user",
                            "content": (
                                "No move has succeeded yet. "
                                "Use the market tools to make your move."
                            ),
                        })
                        continue

                    for call in message.tool_calls:
                        name = call.function.name
                        arguments = json.loads(call.function.arguments)
                        stats["tool_calls"] += 1
                        log(
                            f"[tool call {role}] {name} "
                            f"{json.dumps(arguments, ensure_ascii=False)}"
                        )

                        result = await market.call_tool(name, arguments)
                        text = "\n".join(
                            block.text for block in result.content
                            if block.type == "text"
                        )
                        log(
                            f"[tool result {role}] "
                            f"is_error={result.is_error} {text}"
                        )
                        messages.append({
                            "role": "tool",
                            "tool_call_id": call.id,
                            "content": text,
                        })

                        if name in MOVES:
                            if result.is_error:
                                refused_this_turn += 1
                            else:
                                stats["recovered_refusals"] += refused_this_turn
                                if refused_this_turn:
                                    log(
                                        f"[recovery {role}] "
                                        f"{refused_this_turn} refused calls "
                                        "followed by a valid move in this turn"
                                    )
                                return True

    log(f"[host limit] {role}: no successful move after eight model steps")
    return False