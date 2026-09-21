"""Week 01 — first agent with THREE tools (Anthropic API version).

Tools: calculator, read_file, clock  (clock is the new one).
Requires: pip install anthropic, and ANTHROPIC_API_KEY in the environment.

Settings (all via environment variables; none are secrets except the key):
  ANTHROPIC_API_KEY  required. Never written to this repo.
  AGENT_MODEL        model id. default: claude-sonnet-4-5 (the lab default)
  CLOCK_DESC         "full" (default) or "terse". Swaps the clock tool's
                     description so two runs can compare how the wording
                     changes tool selection. See TOOLS.md.
  DROP_CLOCK         if set to any value, the clock tool is NOT sent to the
                     model (2-tool baseline for comparison). The code still
                     defines 3 tools; only the request omits one.

Run:
  python first_agent.py                       # default goal (see bottom)
  python first_agent.py "your goal here"
  python first_agent.py 2>&1 | tee logs/run-$(date +%m%d-%H%M).txt
"""
import os
import sys
import ast
import operator
from datetime import datetime
from zoneinfo import ZoneInfo

import anthropic

MODEL = os.environ.get("AGENT_MODEL", "claude-sonnet-4-5")

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


# ---- tool 3 (NEW): clock ----
def clock(timezone: str = "Asia/Seoul") -> str:
    """Return the current date/time in the given IANA timezone."""
    try:
        now = datetime.now(ZoneInfo(timezone))
    except Exception:
        return (f"error: unknown timezone '{timezone}'. "
                "Use an IANA name such as Asia/Seoul or UTC.")
    return now.strftime("%Y-%m-%d %H:%M:%S %Z (%A)")


TOOLS_IMPL = {"calculator": calculator, "read_file": read_file, "clock": clock}

# Two candidate descriptions for the new tool. "full" is the one I defend in
# TOOLS.md; "terse" is the first draft, kept so the difference is measurable.
CLOCK_DESCRIPTIONS = {
    "terse": "Get the current time.",
    "full": (
        "Return the current date and time (YYYY-MM-DD HH:MM:SS, timezone, "
        "weekday). Call this whenever the task depends on today's date, the "
        "current time, or how much time remains until/since some date. "
        "Do not guess the date from memory; you do not know today's date "
        "without this tool. Default timezone is Asia/Seoul."
    ),
}
CLOCK_DESC = os.environ.get("CLOCK_DESC", "full")

# ---- tool schemas handed to the model (the description IS the interface) ----
TOOLS = [
    {"name": "calculator",
     "description": "Evaluate an arithmetic expression.",
     "input_schema": {"type": "object",
                      "properties": {"expression": {"type": "string"}},
                      "required": ["expression"]}},
    {"name": "read_file",
     "description": "Read a text file in the working directory.",
     "input_schema": {"type": "object",
                      "properties": {"path": {"type": "string"}},
                      "required": ["path"]}},
    {"name": "clock",
     "description": CLOCK_DESCRIPTIONS[CLOCK_DESC],
     "input_schema": {"type": "object",
                      "properties": {"timezone": {
                          "type": "string",
                          "description": "IANA timezone, e.g. Asia/Seoul, UTC. "
                                         "Optional; defaults to Asia/Seoul."}},
                      "required": []}},
]


def run(goal: str, max_steps: int = 8):
    client = anthropic.Anthropic()  # uses ANTHROPIC_API_KEY
    tools = TOOLS if not os.environ.get("DROP_CLOCK") else \
        [t for t in TOOLS if t["name"] != "clock"]

    print(f"[config] model={MODEL} clock_desc={CLOCK_DESC} "
          f"tools={[t['name'] for t in tools]}")
    print(f"[goal] {goal}")
    messages = [{"role": "user", "content": goal}]

    for step in range(max_steps):   # <- this loop is what makes it an agent
        resp = client.messages.create(
            model=MODEL, max_tokens=1024,
            tools=tools, messages=messages)
        messages.append({"role": "assistant", "content": resp.content})
        print(f"[step {step + 1}] stop_reason={resp.stop_reason} "
              f"in={resp.usage.input_tokens} out={resp.usage.output_tokens}")
        for b in resp.content:               # show the model's interim text
            if b.type == "text" and b.text.strip():
                print(f"  [text] {b.text.strip()}")

        if resp.stop_reason != "tool_use":   # final answer -> stop
            return "".join(b.text for b in resp.content if b.type == "text")

        results = []
        for block in resp.content:           # execute tool calls -> observe
            if block.type == "tool_use":
                try:
                    out = TOOLS_IMPL[block.name](**block.input)
                    is_error = False
                except Exception as e:       # feed the error back, don't crash
                    out, is_error = f"error: {type(e).__name__}: {e}", True
                print(f"  [tool] {block.name}({block.input}) -> {out}")
                results.append({"type": "tool_result", "is_error": is_error,
                                "tool_use_id": block.id, "content": str(out)})
        messages.append({"role": "user", "content": results})

    return "stopped: max steps exceeded"   # the stop condition is a safety net


DEFAULT_GOAL = (
    "Read notes.txt. It is an expense memo with a reimbursement deadline. "
    "Report: (1) the total amount spent, (2) how much is still unreimbursed, "
    "and (3) how many days are left until the deadline as of today."
)

if __name__ == "__main__":
    goal = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_GOAL
    print("[answer]", run(goal))
