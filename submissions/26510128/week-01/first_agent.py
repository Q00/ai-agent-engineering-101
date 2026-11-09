"""Week 01 assignment - OpenRouter/OpenAI-compatible Agent
Three tools:
1. calculator
2. read_file
3. write_note
"""

import os
import sys
import ast
import json
import operator

from openai import OpenAI


# -------------------------------------------------
# Tool 1: calculator
# -------------------------------------------------

_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.USub: operator.neg
}


def _ev(node):
    if isinstance(node, ast.Constant):
        return node.value

    if isinstance(node, ast.BinOp):
        return _OPS[type(node.op)](
            _ev(node.left),
            _ev(node.right)
        )

    if isinstance(node, ast.UnaryOp):
        return _OPS[type(node.op)](
            _ev(node.operand)
        )

    raise ValueError("expression not allowed")


def calculator(expression: str) -> str:
    """Evaluate an arithmetic expression."""
    return str(
        _ev(
            ast.parse(
                expression,
                mode="eval"
            ).body
        )
    )


# -------------------------------------------------
# Tool 2: read_file
# -------------------------------------------------

def read_file(path: str) -> str:
    """Read a text file in the working directory."""

    full = os.path.abspath(path)

    if not full.startswith(os.getcwd()):
        return "denied: path outside the working directory"

    with open(full, encoding="utf-8") as f:
        return f.read()[:4000]


# -------------------------------------------------
# Tool 3: write_note
# -------------------------------------------------

def write_note(path: str, content: str) -> str:
    """Write text to a file in the working directory."""

    full = os.path.abspath(path)

    if not full.startswith(os.getcwd()):
        return "denied: path outside the working directory"

    with open(full, "w", encoding="utf-8") as f:
        f.write(content)

    return f"saved to {path}"


# -------------------------------------------------
# Connect tool names to actual Python functions
# -------------------------------------------------

TOOLS_IMPL = {
    "calculator": calculator,
    "read_file": read_file,
    "write_note": write_note
}


# -------------------------------------------------
# Tool schemas shown to the LLM
# -------------------------------------------------

TOOLS = [

    # calculator
    {
        "type": "function",
        "function": {
            "name": "calculator",
            "description": "Evaluate an arithmetic expression.",
            "parameters": {
                "type": "object",
                "properties": {
                    "expression": {
                        "type": "string"
                    }
                },
                "required": ["expression"]
            }
        }
    },

    # read_file
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a text file in the working directory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string"
                    }
                },
                "required": ["path"]
            }
        }
    },

    # write_note
    {
        "type": "function",
        "function": {
            "name": "write_note",
            "description":
                "Write text to a file when the user asks to save or record a result.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string"
                    },
                    "content": {
                        "type": "string"
                    }
                },
                "required": [
                    "path",
                    "content"
                ]
            }
        }
    }
]


# -------------------------------------------------
# Model
# -------------------------------------------------

MODEL = os.environ.get(
    "AGENT_MODEL",
    "gpt-4o-mini"
)


# -------------------------------------------------
# Agent loop
# -------------------------------------------------

def run(goal: str, max_steps: int = 8):

    client = OpenAI()

    messages = [
        {
            "role": "user",
            "content": goal
        }
    ]

    for step in range(max_steps):

        response = client.chat.completions.create(
            model=MODEL,
            tools=TOOLS,
            messages=messages
        )

        message = response.choices[0].message

        messages.append(message)

        # If the model gives a final answer,
        # stop the loop.
        if not message.tool_calls:
            return message.content or ""

        # Execute tool calls selected by the model.
        for call in message.tool_calls:

            args = json.loads(
                call.function.arguments
            )

            tool_name = call.function.name

            result = TOOLS_IMPL[tool_name](**args)

            print(
                f"  [tool] {tool_name}({args}) -> {result}"
            )

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": str(result)
                }
            )

    return "stopped: max steps exceeded"


# -------------------------------------------------
# Main
# -------------------------------------------------

if __name__ == "__main__":

    goal = (
        sys.argv[1]
        if len(sys.argv) > 1
        else
        "Read notes.txt, calculate the sum of Attendees, "
        "Pizza budget, Drinks, and Reimbursed so far, "
        "then save the result to result.txt."
    )

    print(run(goal))