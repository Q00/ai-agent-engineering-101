"""Week 01: read mixed-unit lengths and calculate their total.

Based on the official OpenAI-compatible starter. See README.md for settings.
"""
import argparse
import os
import sys
import ast
import json
import operator
from decimal import Decimal

from openai import OpenAI

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


# ---- tool 3: convert_units (length only; ratios are relative to one meter) ----
METERS_PER_UNIT = {
    "mm": Decimal("0.001"),
    "cm": Decimal("0.01"),
    "m": Decimal("1"),
    "km": Decimal("1000"),
    "inch": Decimal("0.0254"),
    "ft": Decimal("0.3048"),
}


def convert_units(value: float, from_unit: str, to_unit: str) -> str:
    """Convert a finite numeric length via meters, retaining the output unit."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("value must be a finite JSON number")
    amount = Decimal(str(value))
    if not amount.is_finite():
        raise ValueError("value must be finite")
    for unit in (from_unit, to_unit):
        if not isinstance(unit, str) or unit not in METERS_PER_UNIT:
            raise ValueError("supported length units: mm, cm, m, km, inch, ft")
    # Example: 10 inch * 0.0254 meters/inch / 0.01 meters/cm = 25.4 cm.
    result = amount * METERS_PER_UNIT[from_unit] / METERS_PER_UNIT[to_unit]
    if result == 0:
        result = Decimal(0)
    return f"{format(result.normalize(), 'f')} {to_unit}"


TOOLS_IMPL = {"calculator": calculator, "read_file": read_file,
              "convert_units": convert_units}

# ---- tool schemas handed to the model (the description IS the interface) ----
TOOLS = [
    {"type": "function",
     "function": {
         "name": "calculator",
         "description": "Evaluate an arithmetic expression.",
         "parameters": {"type": "object",
                        "properties": {"expression": {"type": "string"}},
                        "required": ["expression"]}}},
    {"type": "function",
     "function": {
         "name": "read_file",
         "description": "Read a text file in the working directory.",
         "parameters": {"type": "object",
                        "properties": {"path": {"type": "string"}},
                        "required": ["path"]}}},
    {"type": "function",
     "function": {
         "name": "convert_units",
         "description": (
             "Convert one length value between mm, cm, m, km, inch, and ft. "
             "Use this to put lengths in the same unit before arithmetic. "
             "Returns the converted number followed by the target unit. "
             "Only length conversions are supported; use calculator for sums "
             "and other arithmetic."
         ),
         "parameters": {
             "type": "object",
             "properties": {
                 "value": {"type": "number", "description": "The numeric length."},
                 "from_unit": {"type": "string",
                               "enum": ["mm", "cm", "m", "km", "inch", "ft"]},
                 "to_unit": {"type": "string",
                             "enum": ["mm", "cm", "m", "km", "inch", "ft"]}
             },
             "required": ["value", "from_unit", "to_unit"],
             "additionalProperties": False
         }}},
]

MODEL = os.environ.get("AGENT_MODEL", "nvidia/nemotron-3.5-lightning:free")
TASK = ("Read notes.txt and find the total length of the three parts in centimeters. "
        "Show the converted length of each part and the total with units.")


def run(goal: str, max_steps: int = 8, baseline: bool = False):
    client = OpenAI(timeout=45.0, max_retries=0)
    tools = [t for t in TOOLS
             if not baseline or t["function"]["name"] != "convert_units"]
    allowed = {t["function"]["name"] for t in tools}
    print(f"[config] model={MODEL} temperature=0 max_steps={max_steps} "
          f"tools={sorted(allowed)}", flush=True)
    print(f"[task] {goal}", flush=True)
    messages = [{"role": "user", "content": goal}]

    for step in range(max_steps):   # <- this loop is what makes it an agent
        resp = client.chat.completions.create(
            model=MODEL, tools=tools, messages=messages, temperature=0)
        msg = resp.choices[0].message
        messages.append(msg)
        print(f"[step {step + 1}] {msg.content or ''}", flush=True)
        if resp.usage:
            print(f"[usage] input={resp.usage.prompt_tokens} "
                  f"output={resp.usage.completion_tokens}", flush=True)

        if not msg.tool_calls:               # final answer -> stop
            return msg.content or ""

        for call in msg.tool_calls:          # execute tool calls -> observe
            args = call.function.arguments
            try:
                if call.function.name not in allowed:
                    raise ValueError("tool is not available in this run")
                args = json.loads(args)
                out = TOOLS_IMPL[call.function.name](**args)
            except Exception as exc:
                out = f"error: {exc}"
            print(f"  [tool] {call.function.name}({args}) -> {out}", flush=True)
            messages.append({"role": "tool", "tool_call_id": call.id,
                             "content": str(out)})

    return "stopped: max steps exceeded"   # the stop condition is a safety net


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("goal", nargs="?", default=TASK)
    parser.add_argument("--baseline", action="store_true",
                        help="Offer only the original calculator and read_file tools.")
    args = parser.parse_args()
    try:
        print(f"[final] {run(args.goal, baseline=args.baseline)}", flush=True)
    except Exception as exc:
        # Record the failure without dumping authentication/request contents.
        print(f"[failed] {type(exc).__name__} "
              f"status={getattr(exc, 'status_code', None)}", flush=True)
        sys.exit(1)
