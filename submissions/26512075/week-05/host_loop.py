"""One market host execution: authenticate, read the state, make one move.



The runner starts a fresh process each turn and supplies MCP_TOKEN in its
environment. The token is never passed as a model input or CLI argument.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from openai import APIConnectionError, AsyncOpenAI
from dotenv import load_dotenv

load_dotenv()


REQUIRED_TOOLS = {
    "get_negotiation", "propose", "accept_proposal", "reject_proposal", "refuse"
}


def emit(event: str, **fields: Any) -> None:
    print(
        "[host-event] " + json.dumps({"event": event, **fields}, ensure_ascii=False),
        flush=True,
    )


def result_text(result: Any) -> str:
    structured = getattr(result, "structured_content", None)
    if structured is not None:
        return json.dumps(structured, ensure_ascii=False)
    return "\n".join(
        block.text for block in result.content if getattr(block, "text", None)
    )


def id_argument(tool: Any) -> str:
    properties = tool.input_schema.get("properties", {})
    for candidate in ("negotiation_id", "nid"):
        if candidate in properties:
            return candidate
    raise RuntimeError("get_negotiation needs a negotiation_id or nid parameter")


def model_schema(tool: Any) -> dict[str, Any]:
    return {
        "type": "function",
        "name": tool.name,
        "description": tool.description or "",
        "parameters": tool.input_schema,
        "strict": False,
    }


async def run(args: argparse.Namespace) -> bool:
    token = os.environ.get("MCP_TOKEN", "").strip()
    if not token:
        raise RuntimeError("MCP_TOKEN is required for a market host")
    api_key = os.environ.get("API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("Set API_KEY or OPENAI_API_KEY")
    base_url = os.environ.get("BASE_URL", "https://api.openai.com/v1")
    parsed = urlsplit(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("BASE_URL must be a plain model API URL")

    template_bytes = args.system_prompt.read_bytes()
    template = template_bytes.decode("utf-8")
    prompt = template.format(
        role=args.role,
        limit_name="budget" if args.role == "buyer" else "reserve",
        limit=args.limit,
    )
    # Kept for a local assertion. Do not put this digest in emit().
    _prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    del _prompt_hash
    emit(
        "system_prompt",
        role=args.role,
        sha256="masked",
        template_sha256="masked",
    )

    async with httpx2.AsyncClient(
        headers={"Authorization": f"Bearer {token}"},
        timeout=httpx2.Timeout(30.0, read=300.0),
    ) as http:
        transport = streamable_http_client(args.url, http_client=http)
        async with Client(transport) as mcp:
            tools = list((await mcp.list_tools()).tools)
            by_name = {tool.name: tool for tool in tools}
            missing = REQUIRED_TOOLS - by_name.keys()
            if missing:
                raise RuntimeError(f"Market MCP server is missing tools: {sorted(missing)}")
            read_tool = by_name["get_negotiation"]
            view_result = await mcp.call_tool(
                read_tool.name, {id_argument(read_tool): args.negotiation_id}
            )
            if view_result.is_error:
                raise RuntimeError(f"get_negotiation failed: {result_text(view_result)}")
            view = result_text(view_result)
            emit("view", role=args.role, negotiation_id=args.negotiation_id, content=view)

            move_tools = [
                model_schema(tool) for tool in tools if tool.name != read_tool.name
            ]
            model_input = (
                f"Item: {args.item}. Negotiation ID: {args.negotiation_id}. "
                f"Current get_negotiation result:\n{view}\n"
                "Make exactly one move using one tool."
            )
            async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as model_http:
                async with AsyncOpenAI(
                    base_url=base_url,
                    api_key=api_key,
                    http_client=model_http,
                ) as model:
                    previous_id: str | None = None
                    inputs: Any = model_input
                    for attempt in range(args.max_attempts):
                        options: dict[str, Any] = {
                            "model": args.model,
                            "instructions": prompt,
                            "input": inputs,
                            "tools": move_tools,
                            "tool_choice": "required",
                            "parallel_tool_calls": False,
                        }
                        if previous_id:
                            options["previous_response_id"] = previous_id
                        try:
                            response = await model.responses.create(**options)
                        except APIConnectionError as error:
                            cause = error.__cause__
                            host = urlsplit(str(model.base_url)).hostname
                            print(
                                f"[model] connection to {host} failed: "
                                f"{type(cause).__name__}: {cause}",
                                file=sys.stderr,
                            )
                            raise
                        calls = [
                            item for item in response.output if item.type == "function_call"
                        ]
                        previous_id = response.id
                        if len(calls) != 1:
                            emit(
                                "invalid_model_output",
                                role=args.role,
                                attempt=attempt,
                                function_calls=len(calls),
                            )
                            inputs = "Call exactly one move tool with valid arguments."
                            continue
                        call = calls[0]
                        try:
                            arguments = json.loads(call.arguments or "{}")
                            if not isinstance(arguments, dict):
                                raise ValueError("arguments must be a JSON object")
                            if call.name not in by_name or call.name == read_tool.name:
                                raise ValueError("choose one advertised move tool")
                            result = await mcp.call_tool(call.name, arguments)
                            output = result_text(result)
                            refused = bool(result.is_error)
                        except (json.JSONDecodeError, ValueError) as error:
                            arguments = call.arguments
                            output = str(error)
                            refused = True
                        emit(
                            "move_attempt",
                            role=args.role,
                            attempt=attempt,
                            tool=call.name,
                            arguments=arguments,
                            refused=refused,
                            output=output,
                        )
                        print(
                            f"[tool] {call.name}({arguments!r}) -> "
                            f"{'ERROR: ' if refused else ''}{output}",
                            flush=True,
                        )
                        if not refused:
                            emit("result", moved=True, role=args.role, tool=call.name)
                            return True
                        inputs = [{
                            "type": "function_call_output",
                            "call_id": call.call_id,
                            "output": (
                                f"Tool error: {output}. "
                                "Try a legal move as the same party."
                            ),
                        }]

    emit("result", moved=False, role=args.role, reason="no_valid_move")
    return False


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8001/mcp")
    parser.add_argument("--negotiation-id", required=True)
    parser.add_argument("--role", choices=("buyer", "seller"), required=True)
    parser.add_argument("--limit", type=int, required=True)
    parser.add_argument("--item", required=True)
    parser.add_argument(
        "--system-prompt",
        type=Path,
        default=Path(__file__).with_name("system_prompt.txt"),
    )
    parser.add_argument("--model", default=os.environ.get("DEFAULT_MODEL", "gpt-6-luna"))
    parser.add_argument("--max-attempts", type=int, default=4)
    args = parser.parse_args()
    if args.max_attempts < 1 or args.limit < 0:
        parser.error("limit must be nonnegative and max-attempts positive")
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
