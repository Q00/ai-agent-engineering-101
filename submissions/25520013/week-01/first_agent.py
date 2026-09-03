"""Week 01 — multi-currency to KRW converter agent (OpenAI-compatible API, works with OpenRouter).

Three tools: calculator, read_file, get_exchange_rate (ECB rates via api.frankfurter.dev, no key needed).
Requires: pip install openai, and in the environment:
  OPENAI_API_KEY   your key (an OpenRouter key works)
  OPENAI_BASE_URL  optional; set to https://openrouter.ai/api/v1 for OpenRouter
  AGENT_MODEL      optional; defaults to z-ai/glm-5.2:free via OpenRouter. Other OpenRouter free
                   models use e.g. AGENT_MODEL=meta-llama/llama-3.3-70b-instruct:free
"""
import os
import sys
import ast
import json
import operator
import re
import urllib.error
import urllib.request

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


# ---- tool 3: get_exchange_rate (fixed host, KRW only, errors as strings) ----
_FX_URL = "https://api.frankfurter.dev/v1/{date}?base={base}&symbols=KRW"


def get_exchange_rate(base: str, date: str = "latest") -> str:
    """Return the ECB reference rate from `base` currency to KRW on `date`."""
    base = base.strip().upper()
    if not re.fullmatch(r"[A-Z]{3}", base):
        return "error: base must be a 3-letter currency code like USD"
    if date != "latest" and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        return "error: date must be YYYY-MM-DD or 'latest'"
    try:
        req = urllib.request.Request(   # the default urllib agent gets a 403
            _FX_URL.format(date=date, base=base),
            headers={"User-Agent": "week01-agent"})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read().decode("utf-8"))
        return (f"1 {base} = {data['rates']['KRW']} KRW "
                f"(ECB reference rate, {data['date']})")
    except (urllib.error.URLError, KeyError, ValueError) as e:
        return f"error: rate lookup failed for {base} on {date} ({e})"


TOOLS_IMPL = {"calculator": calculator, "read_file": read_file,
              "get_exchange_rate": get_exchange_rate}

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
         "name": "get_exchange_rate",
         "description": "Look up the official daily exchange rate from a "
                        "currency to Korean won (KRW). Give a 3-letter ISO "
                        "currency code such as USD, EUR or JPY, and a date "
                        "(YYYY-MM-DD) to get that day's rate, or omit the "
                        "date for the latest published rate. Call it once "
                        "per currency.",
         "parameters": {"type": "object",
                        "properties": {
                            "base": {"type": "string",
                                     "description": "3-letter ISO currency "
                                                    "code, e.g. USD"},
                            "date": {"type": "string",
                                     "description": "YYYY-MM-DD; omit for "
                                                    "the latest published "
                                                    "rate"}},
                        "required": ["base"]}}},
]

MODEL = os.environ.get("AGENT_MODEL", "z-ai/glm-5.2:free")


def run(goal: str, max_steps: int = 8):
    client = OpenAI()  # uses OPENAI_API_KEY and OPENAI_BASE_URL
    messages = [{"role": "user", "content": goal}]

    for step in range(max_steps):   # <- this loop is what makes it an agent
        resp = client.chat.completions.create(
            model=MODEL, tools=TOOLS, messages=messages)
        msg = resp.choices[0].message
        messages.append(msg)

        if not msg.tool_calls:               # final answer -> stop
            return msg.content or ""

        for call in msg.tool_calls:          # execute tool calls -> observe
            args = json.loads(call.function.arguments)
            out = TOOLS_IMPL[call.function.name](**args)
            print(f"  [tool] {call.function.name}({args}) -> {out}")
            messages.append({"role": "tool", "tool_call_id": call.id,
                             "content": str(out)})

    return "stopped: max steps exceeded"   # the stop condition is a safety net


if __name__ == "__main__":
    goal = sys.argv[1] if len(sys.argv) > 1 else \
        "Read fees.txt and report the total registration cost in KRW, " \
        "using the exchange rates on the date written in the file."
    print(run(goal))
