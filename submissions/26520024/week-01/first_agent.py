"""Read sample GPU runtimes, calculate their total, and save a summary.

Based on the course Anthropic starter. Use --tools 2 for the comparison run.
"""
import argparse
import ast
import json
import math
import operator
import os
from pathlib import Path
import sys

import anthropic

WORKSPACE = Path(__file__).resolve().parent
MAX_TEXT = 4000
DEFAULT_MODEL = "claude-sonnet-4-5"
DEFAULT_GOAL = (
    "Read notes.txt and calculate the total GPU runtime in hours. "
    "Save the input values and the total to outputs/gpu-summary.txt. "
    "If no file-writing tool is available, report the total and explain "
    "that you could not save it."
)

_OPS = {ast.Add: operator.add, ast.Sub: operator.sub,
        ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.Pow: operator.pow, ast.USub: operator.neg,
        ast.UAdd: operator.pos}


def _number(value):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError("only finite real numbers are allowed")
    if abs(value) > 1e12:
        raise ValueError("number exceeds the magnitude limit of 1e12")
    return value


def _ev(node):
    if isinstance(node, ast.Constant):
        return _number(node.value)
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        left, right = _ev(node.left), _ev(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > 12:
            raise ValueError("exponent magnitude must not exceed 12")
        return _number(_OPS[type(node.op)](left, right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _number(_OPS[type(node.op)](_ev(node.operand)))
    raise ValueError("expression not allowed")


def calculator(expression: str) -> str:
    """Evaluate an arithmetic expression string, e.g. '3 * (4 + 5)'."""
    if not isinstance(expression, str) or len(expression) > 200:
        raise ValueError("expression must be a string of at most 200 characters")
    tree = ast.parse(expression, mode="eval")
    if len(list(ast.walk(tree))) > 64:
        raise ValueError("expression has too many operations")
    return str(_ev(tree.body))


def _workspace_path(path: str) -> Path:
    if not isinstance(path, str) or Path(path).is_absolute():
        raise ValueError("path must be relative to the submission directory")
    full = (WORKSPACE / path).resolve()
    if not full.is_relative_to(WORKSPACE):
        raise ValueError("path is outside the submission directory")
    if full.suffix != ".txt":
        raise ValueError("only .txt files are allowed")
    return full


def read_file(path: str) -> str:
    """Read one complete, bounded text file inside the submission directory."""
    with _workspace_path(path).open(encoding="utf-8") as stream:
        text = stream.read(MAX_TEXT + 1)
    if len(text) > MAX_TEXT:
        raise ValueError("file exceeds 4000 characters; no partial data returned")
    return text


def write_note(path: str, content: str) -> str:
    """Append a note to a text file under outputs/, preserving existing notes."""
    full = _workspace_path(path)
    if not full.is_relative_to(WORKSPACE / "outputs"):
        raise ValueError("notes must be saved under outputs/")
    if not isinstance(content, str) or not content.strip():
        raise ValueError("content must be a nonempty string")
    if len(content) > MAX_TEXT:
        raise ValueError("note exceeds 4000 characters")
    full.parent.mkdir(parents=True, exist_ok=True)
    with full.open("a", encoding="utf-8") as stream:
        stream.write(content)
        if not content.endswith("\n"):
            stream.write("\n")
    return f"Appended a note to {full.relative_to(WORKSPACE)}"


TOOLS_IMPL = {
    "calculator": calculator,
    "read_file": read_file,
    "write_note": write_note,
}

# ---- tool schemas handed to the model (the description IS the interface) ----
TOOLS = [
    {"name": "calculator",
     "description": "Evaluate a numeric expression using +, -, *, /, or **. "
                    "Limit: 200 characters, magnitude 1e12, exponent magnitude 12.",
     "input_schema": {"type": "object",
                      "properties": {"expression": {"type": "string"}},
                      "required": ["expression"],
                      "additionalProperties": False}},
    {"name": "read_file",
     "description": "Read a .txt file using a path relative to this submission "
                    "directory. Files over 4000 characters are rejected.",
     "input_schema": {"type": "object",
                      "properties": {"path": {"type": "string"}},
                      "required": ["path"],
                      "additionalProperties": False}},
    {"name": "write_note",
     "description": "Append a UTF-8 note to a .txt file under outputs/, using "
                    "a path relative to this submission directory. Use this "
                    "when the user asks to save or record a result. Existing "
                    "text is preserved; a missing file is created. Content "
                    "must be nonempty and at most 4000 characters; a trailing "
                    "newline is added if missing. This tool does not calculate "
                    "values or read input data.",
     "input_schema": {"type": "object",
                      "properties": {"path": {"type": "string"},
                                     "content": {"type": "string"}},
                      "required": ["path", "content"],
                      "additionalProperties": False}},
]


def run(goal: str, max_steps: int = 8, *, tool_count: int = 3,
        model: str = DEFAULT_MODEL):
    if tool_count not in (2, 3) or not 1 <= max_steps <= 32:
        raise ValueError("tools must be 2 or 3; max_steps must be between 1 and 32")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise ValueError("ANTHROPIC_API_KEY is not set; no model call was made")
    tools = TOOLS[:tool_count]
    available = {tool["name"] for tool in tools}
    messages = [{"role": "user", "content": goal}]
    print("[config] " + json.dumps({
        "model": model, "sdk": anthropic.__version__, "tools": tools,
        "max_steps": max_steps, "max_tokens": 1024,
        "timeout_seconds": 60, "max_retries": 0,
    }), flush=True)
    print(f"[goal] {goal}", flush=True)

    with anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"],
                             timeout=60.0, max_retries=0) as client:
        for step in range(max_steps):
            resp = client.messages.create(
                model=model, max_tokens=1024, tools=tools, messages=messages)
            messages.append({"role": "assistant", "content": resp.content})
            print(f"[response {step + 1}] model={resp.model} "
                  f"stop_reason={resp.stop_reason}", flush=True)
            for block in resp.content:
                if block.type == "text":
                    print(f"[assistant] {block.text}", flush=True)

            if resp.stop_reason == "end_turn":
                answer = "".join(b.text for b in resp.content if b.type == "text")
                if not answer.strip():
                    raise RuntimeError("model ended the turn without an answer")
                return answer
            if resp.stop_reason != "tool_use":
                raise RuntimeError(f"incomplete response: {resp.stop_reason}")

            results = []
            for block in resp.content:
                if block.type != "tool_use":
                    continue
                is_error = False
                try:
                    if block.name not in available:
                        raise ValueError("tool is not available in this run")
                    out = TOOLS_IMPL[block.name](**block.input)
                except (OSError, ValueError, TypeError, ArithmeticError,
                        SyntaxError) as exc:
                    is_error = True
                    out = f"{type(exc).__name__}: {exc}"
                print("[tool] " + json.dumps({
                    "name": block.name, "input": block.input,
                    "output": out, "is_error": is_error,
                }), flush=True)
                results.append({"type": "tool_result",
                                "tool_use_id": block.id,
                                "content": str(out), "is_error": is_error})
            if not results:
                raise RuntimeError("tool_use response contained no tool calls")
            messages.append({"role": "user", "content": results})

    raise RuntimeError("stopped: max steps exceeded")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("goal", nargs="?", default=DEFAULT_GOAL)
    parser.add_argument("--tools", type=int, choices=(2, 3), default=3)
    parser.add_argument("--max-steps", type=int, default=8)
    parser.add_argument("--model", default=os.environ.get("AGENT_MODEL", DEFAULT_MODEL))
    args = parser.parse_args()
    try:
        answer = run(args.goal, args.max_steps, tool_count=args.tools, model=args.model)
    except anthropic.APIError as exc:
        print(f"[api_error] {type(exc).__name__}; "
              f"status={getattr(exc, 'status_code', None)}", file=sys.stderr)
        return 1
    except (ValueError, RuntimeError) as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 1
    print(f"[final] {answer}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
