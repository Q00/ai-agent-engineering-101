"""Read sample GPU runtimes, calculate their total, and save a summary.

Uses the authenticated Codex CLI. Use --tools 2 for the comparison run.
"""
import argparse
import ast
import json
import math
import operator
import os
from pathlib import Path
import subprocess
import sys

from codex_backend import (
    DISABLED_FEATURES, MODEL_INSTRUCTIONS, REASONING_EFFORT,
    backend_info, choose_action,
)

WORKSPACE = Path(__file__).resolve().parent
MAX_TEXT = 4000
DEFAULT_MODEL = "gpt-6-astra"
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
    tools = TOOLS[:tool_count]
    available = {tool["name"] for tool in tools}
    history = []
    print("[config] " + json.dumps({
        "backend": "codex-cli", **backend_info(), "model": model,
        "reasoning_effort": REASONING_EFFORT, "tools": tools,
        "max_steps": max_steps, "timeout_seconds_per_decision": 120,
        "model_instructions": MODEL_INSTRUCTIONS,
        "disabled_codex_features": DISABLED_FEATURES,
        "sandbox": "read-only", "web_search": "disabled",
    }), flush=True)
    print(f"[goal] {goal}", flush=True)

    for step in range(max_steps):
        print(f"[step {step + 1}] requesting a Codex decision", flush=True)
        decision = choose_action(goal, tools, history, model)
        print("[decision] " + json.dumps(decision), flush=True)
        history.append({"role": "assistant", "content": decision})
        if decision["action"] == "final":
            if not decision["answer"].strip():
                raise RuntimeError("model ended the turn without an answer")
            return decision["answer"]
        if decision["action"] != "tool":
            raise RuntimeError("model returned an unknown action")

        name, arguments = decision["tool"], decision["arguments"]
        is_error = False
        try:
            if name not in available:
                raise ValueError("tool is not available in this run")
            out = TOOLS_IMPL[name](**arguments)
        except (OSError, ValueError, TypeError, ArithmeticError, SyntaxError) as exc:
            is_error = True
            out = f"{type(exc).__name__}: {exc}"
        observation = {"name": name, "input": arguments,
                       "output": out, "is_error": is_error}
        print("[tool] " + json.dumps(observation), flush=True)
        history.append({"role": "tool", "content": observation})

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
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 1
    print(f"[final] {answer}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
