import os
import sys
import ast
import json
import operator

from openai import OpenAI


# =========================================================
# TOOL 1: CALCULATOR
# =========================================================

_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
}


def _ev(node):
    if isinstance(node, ast.Constant):
        return node.value

    if isinstance(node, ast.BinOp):
        if type(node.op) not in _OPS:
            raise ValueError("operator not allowed")
        return _OPS[type(node.op)](
            _ev(node.left),
            _ev(node.right)
        )

    if isinstance(node, ast.UnaryOp):
        if type(node.op) not in _OPS:
            raise ValueError("operator not allowed")
        return _OPS[type(node.op)](
            _ev(node.operand)
        )

    raise ValueError("expression not allowed")


def calculator(expression: str) -> str:
    """Evaluate a safe arithmetic expression."""
    return str(
        _ev(ast.parse(expression, mode="eval").body)
    )


# =========================================================
# TOOL 2: READ FILE
# =========================================================

def read_file(path: str) -> str:
    """Read a text file in the current working directory."""
    full = os.path.abspath(path)
    cwd = os.path.abspath(os.getcwd())

    if os.path.commonpath([cwd, full]) != cwd:
        return "denied: path outside the working directory"

    with open(full, encoding="utf-8") as f:
        return f.read()[:4000]


# =========================================================
# TOOL 3: WRITE NOTE
# Week 01 additional tool
# =========================================================

def write_note(path: str, content: str) -> str:
    """Save text to a file in the current working directory."""
    full = os.path.abspath(path)
    cwd = os.path.abspath(os.getcwd())

    if os.path.commonpath([cwd, full]) != cwd:
        return "denied: path outside the working directory"

    with open(full, "w", encoding="utf-8") as f:
        f.write(content)

    return f"saved to {path}"


# Map tool names to Python implementations
TOOLS_IMPL = {
    "calculator": calculator,
    "read_file": read_file,
    "write_note": write_note,
}


# Tool schemas shown to the model
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "calculator",
            "description": "Evaluate an arithmetic expression.",
            "parameters": {
                "type": "object",
                "properties": {
                    "expression": {"type": "string"}
                },
                "required": ["expression"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a text file in the current working directory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"}
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_note",
            "description": "Save text to a file in the current working directory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
    },
]


MODEL = os.environ.get("AGENT_MODEL", "openrouter/free")


# =========================================================
# AGENT LOOP
# =========================================================

def run(goal: str, max_steps: int = 8):
    client = OpenAI()

    messages = [
        {
            "role": "user",
            "content": goal
        }
    ]

    for step in range(max_steps):
        print(f"\n--- Step {step + 1} ---")

        response = client.chat.completions.create(
            model=MODEL,
            tools=TOOLS,
            messages=messages,
        )

        message = response.choices[0].message
        messages.append(message)

        # Final answer when the model stops calling tools
        if not message.tool_calls:
            return message.content or ""

        # Execute each requested tool
        for call in message.tool_calls:
            tool_name = call.function.name
            arguments = json.loads(call.function.arguments)

            if tool_name not in TOOLS_IMPL:
                result = f"unknown tool: {tool_name}"
            else:
                result = TOOLS_IMPL[tool_name](**arguments)

            print(
                f"[tool] {tool_name}({arguments}) -> {result}"
            )

            messages.append({
                "role": "tool",
                "tool_call_id": call.id,
                "content": str(result),
            })

    return "stopped: max steps exceeded"


# =========================================================
# RUN
# =========================================================

if __name__ == "__main__":
    goal = (
        sys.argv[1]
        if len(sys.argv) > 1
        else
        "Read notes.txt, add all the numbers, "
        "and save the final total to result.txt using write_note."
    )

    print(run(goal))
