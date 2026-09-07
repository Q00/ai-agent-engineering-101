"""Edited from Week 01 starter — OpenAI-compatible API version (works with OpenRouter).



Two tools: calculator, read_file. Your assignment: add a third.
Requires: pip install openai, and in the environment:
   
Install:
  pip install openai

Environment:
  MODEL_API_KEY    Meta Model API key
  AGENT_MODEL      optional; defaults to muse-spark-1.3

Example model IDs (September 2026):
  Meta paid:       muse-spark-1.3

Every run appends structured JSON Lines events to ./logs/run.log

Execute:
    python <filename>.py

Note:
    Read dev.meta.ai/docs, and GET API KEYS, then add it to custom .env file
    Also add base URL to .env file or using DEFAULT_BASE_URL = "https://api.meta.ai/v1"
    Finally, install dependencies
"""

from __future__ import annotations

import ast
import json
import math
import operator
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()


WORKDIR = Path.cwd().resolve()
LOG_PATH = Path("logs/run.log")
RESULT_PATH = WORKDIR / "result.txt"
MAX_FILE_CHARS = 8_000
MAX_RESULT_CHARS = 10_000
DEFAULT_BASE_URL = "https://api.meta.ai/v1"

def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def log_event(run_id: str, event: str, **data: Any) -> None:
    """Append one machine-readable event; never logs environment/API keys."""
    record = {"ts": _utc_now(), "run_id": run_id, "event": event, **data}

    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as log_file:
        log_file.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")

# ---- tool 1: calculator (safe, no eval) ----
_OPS = {ast.Add: operator.add, ast.Sub: operator.sub,
        ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.Pow: operator.pow, ast.USub: operator.neg}


def _ev(node):
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.BinOp):
        return _OPS[type(node.op)](_ev(node.left), _ev(node.right))
    if isinstance(node, ast.UnaryOp):
        return _OPS[type(node.op)](_ev(node.operand))
    raise ValueError("expression not allowed")


def calculator(expression: str) -> str:
    """Evaluate an arithmetic expression string, e.g. '3 * (4 + 5)'."""
    return str(_ev(ast.parse(expression, mode="eval").body))


# ---- tool 2: read_file (blocked outside the working directory) ----
def read_file(path: str) -> str:
    """Return the contents of a text file."""
    full = os.path.abspath(path)
    if not full.startswith(os.getcwd()):
        return "denied: path outside the working directory"
    with open(full, encoding="utf-8") as f:
        return f.read()[:4000]

# added
# ---- tool 3: save_result (fixed output path) ----
def save_result(content: str) -> str:
    """Save the model's final web-search summary to result.txt."""
    content = content.strip()
    if not content:
        return "error: content must not be empty"
    if len(content) > MAX_RESULT_CHARS:
        return f"error: content must be at most {MAX_RESULT_CHARS} characters"

    RESULT_PATH.write_text(content + "\n", encoding="utf-8")
    return json.dumps(
        {"saved_to": RESULT_PATH.name, "characters": len(content)},
        ensure_ascii=False,
    )

TOOLS_IMPL = {"calculator": calculator, "read_file": read_file, "save_result": save_result}

# ---- tool schemas handed to the model (the description IS the interface) ----

TOOLS = [
    {
        "type": "function",
        "name": "calculator",
        "description": "Evaluate a small arithmetic expression.",
        "parameters": {
            "type": "object",
            "properties": {
                "expression": {
                    "type": "string",
                    "description": "Arithmetic expression, e.g. 3 * (4 + 5).",
                }
            },
            "required": ["expression"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "read_file",
        "description": (
            "Read a UTF-8 text file in the working directory. For this task, "
            "read notes.txt before calling calculator or using web search."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative path such as notes.txt.",
                }
            },
            "required": ["path"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "save_result",
        "description": (
            "Save the final three-item web-search summary to result.txt. Call this "
            "only after calculator and the built-in web search have completed."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": (
                        "Exactly three concise search results with source URLs."
                    ),
                }
            },
            "required": ["content"],
            "additionalProperties": False,
        },
    },
    # Meta executes this built-in tool; it has no entry in TOOLS_IMPL.
    {"type": "web_search"},
]

MODEL = os.environ.get("AGENT_MODEL", "muse-spark-1.3")

SYSTEM_PROMPT = """You are a careful tool-using shopping assistant.
                    Follow this workflow in order:
                    1. Call read_file on notes.txt and identify its addition instruction and numbers.
                    2. Build an arithmetic expression from those numbers and call calculator. Do not add
                    the numbers mentally and do not skip this tool.
                    3. After observing calculator's result, use that total as the Korean-won price
                    threshold and use the built-in web search to find pizza. If notes.txt explicitly
                    says "이상" or "최소", search at or above the total; otherwise search at or below it.
                    4. Prepare exactly three compact pizza results. Include each result's name, price
                    evidence when visible, and source URL. Never invent missing information; write
                    "확인 필요" when a price is unavailable.
                    5. Call save_result with that three-item summary. Do not give a final answer before
                    save_result succeeds. After it succeeds, briefly confirm that result.txt was saved.
                    """

def _preview(value: str, limit: int = 300) -> str:
    return value if len(value) <= limit else value[:limit] + "...[truncated]"

def _execute_tool(name: str, raw_args: str) -> tuple[dict[str, Any], str]:
    "Validate a requested call and convert its failure into observations"
    try:
        args = json.loads(raw_args)
        if not isinstance(args, dict):
            raise ValueError("arguments must be a JSON")
    
    except (json.JSONDecodeError, ValueError) as exc:
        return {}, f"error:{name} invalid tool arguments {exc}"
    
    implementation = TOOLS_IMPL.get(name)
    if implementation is None:
        return args, f" error: {name} is not a valid tool"
    
    try:
        return args, str(implementation(**args))

    except TypeError as exc:
        return args, f"error: {name} mismatched arguments {exc}"
    except Exception as exc:
        return args, f"error: {type(exc).__name__}: {exc}"

def _has_pending_builtin(output: list[Any]) -> bool:
    """Keep looping when Meta only returned a built-in search turn."""
    pending = {"web_search_call", "web_search"}
    return any(getattr(item, "type", None) in pending for item in output)


def run(goal: str, max_steps: int = 8) -> str:
    run_id = uuid.uuid4().hex[:12]

    base_url = os.environ.get("META_API_BASE_URL") or DEFAULT_BASE_URL
    api_key = os.environ.get("MODEL_API_KEY")

    if not api_key:
        raise RuntimeError("MODEL_API_KEY is not set")

    client = OpenAI(
        base_url=base_url,
        api_key=api_key,
    )

    input_items: list[Any] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": goal},
    ]

    log_event(run_id, "run_started", model=MODEL, goal=goal, max_steps=max_steps)
    print(f"[run {run_id}] log={LOG_PATH.name} model={MODEL} base_url={base_url}")

    try:
        for step in range(1, max_steps + 1):  # <- this loop is what makes it an agent
            print(f"  [step {step}] ask model")
            log_event(run_id, "model_request", step=step)

            response = client.responses.create(
                model=MODEL,
                tools=TOOLS,
                input=input_items,
            )

            output = list(response.output or [])
            input_items.extend(output)

            for item in output:
                if getattr(item, "type", None) == "web_search_call":
                    print(f"  [step {step}] built-in web_search")
                    log_event(
                        run_id,
                        "builtin_tool_finished",
                        step=step,
                        tool="web_search",
                        status=getattr(item, "status", None),
                    )

            function_calls = [
                item for item in output if getattr(item, "type", None) == "function_call"
            ]

            if not function_calls:
                # Search-only turns are not a final answer; continue the loop.
                if _has_pending_builtin(output) and step < max_steps:
                    print(f"  [step {step}] wait for next turn after web_search")
                    continue

                answer = getattr(response, "output_text", None) or ""
                print(f"  [step {step}] final answer")
                usage = getattr(response, "usage", None)
                log_event(
                    run_id,
                    "run_finished",
                    step=step,
                    answer=_preview(answer, 2_000),
                    usage=(usage.model_dump() if usage else None),
                )
                return answer

            for call in function_calls:
                name = call.name
                args, output_text = _execute_tool(name, call.arguments)

                print(f"  [step {step}] tool call {name}({args}) -> {_preview(output_text)}")
                log_event(
                    run_id,
                    "tool_call",
                    step=step,
                    tool=name,
                    args=args,
                    output_preview=_preview(output_text, 2_000),
                )

                input_items.append(
                    {
                        "type": "function_call_output",
                        "call_id": call.call_id,
                        "output": output_text,
                    }
                )

    except Exception as exc:
        log_event(run_id, "run_failed", error_type=type(exc).__name__, error=str(exc))
        raise

    result = "stopped: max steps exceeded"
    log_event(run_id, "run_stopped", reason=result)
    return result

if __name__ == "__main__":
    default_goal = (
        "Read notes.txt and follow its instruction. Use calculator to obtain the sum, "
        "then use that total to search the web for pizza. Save exactly three compact "
        "results with source URLs to result.txt."
    )
    requested_goal = sys.argv[1] if len(sys.argv) > 1 else default_goal
    print(run(requested_goal))