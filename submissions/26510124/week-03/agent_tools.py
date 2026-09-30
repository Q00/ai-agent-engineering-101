"""Tools shared by every Week 03 contractor.

The implementation keeps the Week 01 idea that the description is the tool's
interface, and the Week 02 idea that both harnesses must receive the exact same
tools.  File tools are confined to this submission directory.  ``write_note``
is exposed to the model but requires an explicit runtime approval flag.
"""

from __future__ import annotations

import ast
import operator
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parent
TOOLSET_VERSION = "week03-tools-v1"


_BINARY_OPS: dict[type[ast.operator], Callable[[Any, Any], Any]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY_OPS: dict[type[ast.unaryop], Callable[[Any], Any]] = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}


def _number(node: ast.AST) -> int | float:
    if isinstance(node, ast.Constant) and type(node.value) in (int, float):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _BINARY_OPS:
        left, right = _number(node.left), _number(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > 20:
            raise ValueError("exponent is limited to 20")
        return _BINARY_OPS[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPS:
        return _UNARY_OPS[type(node.op)](_number(node.operand))
    raise ValueError("only numeric arithmetic is allowed")


def calculator(expression: str) -> str:
    """Evaluate arithmetic without Python's eval."""
    tree = ast.parse(expression, mode="eval")
    value = _number(tree.body)
    if abs(value) > 10**100:
        raise ValueError("result is too large")
    return str(value)


def _inside_submission(path: str) -> Path:
    candidate = (ROOT / path).resolve()
    if not candidate.is_relative_to(ROOT):
        raise PermissionError("path outside the Week 03 submission directory")
    return candidate


def read_file(path: str) -> str:
    """Read at most 4,000 characters from a UTF-8 text file."""
    return _inside_submission(path).read_text(encoding="utf-8")[:4000]


def count_pattern(path: str, pattern: str) -> str:
    """Count text-file lines that match a regular expression."""
    regex = re.compile(pattern)
    with _inside_submission(path).open(encoding="utf-8") as handle:
        return str(sum(1 for line in handle if regex.search(line)))


def check_python(source: str) -> str:
    """Check whether source is syntactically valid Python without running it."""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return f"invalid: line {exc.lineno}: {exc.msg}"
    functions = [node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)]
    return "valid" + (f"; functions={','.join(functions)}" if functions else "")


def write_note(path: str, content: str) -> str:
    """Append one line to a file, creating parent directories when necessary."""
    target = _inside_submission(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        handle.write(content.rstrip("\n") + "\n")
    return f"appended {len(content.rstrip(chr(10)))} chars to {path}"


TOOL_SPECS = [
    {
        "name": "calculator",
        "description": "Evaluate a numeric arithmetic expression safely.",
        "parameters": {
            "type": "object",
            "properties": {"expression": {"type": "string"}},
            "required": ["expression"],
            "additionalProperties": False,
        },
    },
    {
        "name": "read_file",
        "description": (
            "Read a UTF-8 text file inside the Week 03 submission directory "
            "and return its first 4,000 characters."
        ),
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
            "additionalProperties": False,
        },
    },
    {
        "name": "count_pattern",
        "description": (
            "Count lines in a text file inside the Week 03 submission directory "
            "that match a regular expression."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "pattern": {"type": "string"},
            },
            "required": ["path", "pattern"],
            "additionalProperties": False,
        },
    },
    {
        "name": "check_python",
        "description": (
            "Check Python source for syntax errors without executing it. Use this "
            "before returning code."
        ),
        "parameters": {
            "type": "object",
            "properties": {"source": {"type": "string"}},
            "required": ["source"],
            "additionalProperties": False,
        },
    },
    {
        "name": "write_note",
        "description": (
            "Append one line to a file inside the Week 03 submission directory. "
            "Existing content is preserved. This side-effecting tool requires "
            "human approval and may be denied during batch experiments."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
            "additionalProperties": False,
        },
    },
]


TOOL_IMPLEMENTATIONS: dict[str, Callable[..., str]] = {
    "calculator": calculator,
    "read_file": read_file,
    "count_pattern": count_pattern,
    "check_python": check_python,
    "write_note": write_note,
}


@dataclass
class ToolRuntime:
    """Execute calls and retain the observations needed for run logs."""

    allow_write: bool = False
    interventions: int = 0
    events: list[dict[str, Any]] = field(default_factory=list)

    def execute(self, name: str, arguments: dict[str, Any]) -> str:
        if name == "write_note" and not self.allow_write:
            self.interventions += 1
            output = "denied: write_note needs explicit --allow-write-tools approval"
        else:
            function = TOOL_IMPLEMENTATIONS.get(name)
            if function is None:
                output = f"error: unknown tool {name}"
            else:
                try:
                    output = str(function(**arguments))
                except Exception as exc:  # errors become observations, as in Week 02
                    output = f"error: {type(exc).__name__}: {exc}"
        self.events.append({"tool": name, "arguments": arguments, "output": output})
        return output
