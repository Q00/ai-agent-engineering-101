import asyncio
import json
import os
import time

import httpx2
from openai import OpenAI, RateLimitError, APIStatusError

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


MCP_URL = "http://127.0.0.1:8001/mcp"
MODEL = os.environ.get(
    "AGENT_MODEL",
    "nvidia/nemotron-3-super-120b-a12b:free",
)


def _openai_tools(mcp_tools):
    """Convert MCP tool descriptions into OpenAI function-tool schemas."""
    tools = []

    for tool in mcp_tools:
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description or "",
                    "parameters": tool.inputSchema,
                },
            }
        )

    return tools


def _tool_result_text(result):
    """Serialize an MCP tool result so the model can read it."""
    try:
        return json.dumps(
            result.model_dump(mode="json"),
            ensure_ascii=False,
        )
    except Exception:
        return str(result)


def _model_call(client, messages, tools, max_retries=5):
    """OpenRouter call with retries for rate limits and empty responses."""

    for attempt in range(max_retries):
        try:
            response = client.chat.completions.create(
                model=MODEL,
                messages=messages,
                tools=tools,
                tool_choice="auto",
                temperature=0,
                extra_body={"reasoning": {"enabled": False}},
            )

            if (
                not response.choices
                or response.choices[0] is None
                or getattr(response.choices[0], "message", None) is None
            ):
                raise RuntimeError("model returned no usable choice")

            return response.choices[0].message

        except RateLimitError:
            if attempt == max_retries - 1:
                raise

            wait = 2 ** (attempt + 1)
            print(f"[rate-limit] retrying in {wait}s")
            time.sleep(wait)

        except APIStatusError as exc:
            if exc.status_code < 500 or attempt == max_retries - 1:
                raise

            wait = 2 ** (attempt + 1)
            print(f"[server-error] retrying in {wait}s")
            time.sleep(wait)

        except RuntimeError:
            if attempt == max_retries - 1:
                raise

            wait = 2 ** (attempt + 1)
            print(f"[empty-response] retrying in {wait}s")
            time.sleep(wait)


MOVE_TOOLS = {
    "propose",
    "accept_proposal",
    "reject_proposal",
    "refuse",
}


def _role_prompt(role: str, item: str, private_limit: int) -> str:
    if role == "buyer":
        return f"""
You are the BUYER negotiating for {item}.
Your private authorized budget is {private_limit}.
You must never propose or accept a price above {private_limit}.
Try to reach a deal when possible.
"""

    return f"""
You are the SELLER negotiating {item}.
Your private reserve price is {private_limit}.
You must never propose or accept a price below {private_limit}.
Try to reach a deal when possible.
"""


async def run_turn(
    role: str,
    item: str,
    private_limit: int,
    negotiation_id: str,
    token: str,
    log=print,
    max_steps: int = 8,
):
    """
    Run one party for one negotiation turn.

    The agent first reads the market through get_negotiation, then makes
    one move. If the market refuses a move, the refusal is returned to
    the model and it may try another move in the same turn.
    """

    headers = {
        "Authorization": f"Bearer {token}",
    }

    async with httpx2.AsyncClient(headers=headers) as http_client:
        async with streamable_http_client(
            MCP_URL,
            http_client=http_client,
        ) as (read_stream, write_stream):

            async with ClientSession(
                read_stream,
                write_stream,
            ) as session:

                await session.initialize()

                listed = await session.list_tools()
                tools = _openai_tools(listed.tools)

                client = OpenAI()

                messages = [
                    {
                        "role": "system",
                        "content": _role_prompt(
                            role,
                            item,
                            private_limit,
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            f"You are taking one negotiation turn. "
                            f"The negotiation_id is {negotiation_id}. "
                            "First use get_negotiation to inspect the current "
                            "market state. Then make exactly one negotiation "
                            "move using propose, accept_proposal, "
                            "reject_proposal, or refuse. "
                            "If the market refuses your move, read the tool "
                            "error and try a valid move in the same turn."
                        ),
                    },
                ]

                for _ in range(max_steps):
                    msg = _model_call(
                        client,
                        messages,
                        tools,
                    )

                    messages.append(
                        msg.model_dump(
                            exclude_none=True,
                        )
                    )

                    if msg.content:
                        log(f"[{role} text] {msg.content}")

                    if not msg.tool_calls:
                        log(
                            f"[{role}] model returned no tool call"
                        )
                        continue

                    for call in msg.tool_calls:
                        name = call.function.name

                        try:
                            args = json.loads(
                                call.function.arguments
                            )
                        except json.JSONDecodeError:
                            args = {}

                        # Never allow the model to switch negotiations.
                        args["negotiation_id"] = negotiation_id

                        log(f"[{role} call] {name}({args})")

                        result = await session.call_tool(
                            name,
                            args,
                        )

                        result_text = _tool_result_text(result)

                        is_error = bool(
                            getattr(result, "isError", False)
                        )

                        if is_error:
                            log(
                                f"[error] {name}: "
                                f"{result_text}"
                            )
                        else:
                            log(
                                f"[tool result] {name}: "
                                f"{result_text}"
                            )

                        messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": call.id,
                                "content": result_text,
                            }
                        )

                        # One successful negotiation move ends this host run.
                        if name in MOVE_TOOLS and not is_error:
                            return {
                                "moved": True,
                                "move": name,
                            }

                return {
                    "moved": False,
                    "move": None,
                }


def run_turn_sync(**kwargs):
    return asyncio.run(run_turn(**kwargs))
