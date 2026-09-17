"""Work tools, and who is allowed to call them.

One tool per skill, and each tool answers exactly the question that skill's
check will ask. A contractor can therefore verify its own answer inside its
own specialty and nowhere else — which is what a specialist is once agents
have tools, and what Smith's nodes had in hardware: the one with the sensor
measured, everyone else estimated.

The owner table is enforced by the orchestrator, not by the prompt. A call to
a tool you do not own is refused and counted; reaching for one is itself a
signal, so the refusal is logged rather than silently dropped.
"""
import ast
import re
import subprocess
import sys
import tempfile

TIMEOUT_S = 10

OWNER = {"calc": "A", "count_sentences": "B", "run_python": "C"}

DESCRIPTION = {
    "calc": '{"tool": "calc", "args": {"expr": "137 * 249"}}'
            '  -- evaluate an arithmetic expression exactly',
    "count_sentences": '{"tool": "count_sentences", "args": {"text": "..."}}'
                       '  -- sentence count and words per sentence',
    "run_python": '{"tool": "run_python", "args": {"code": "..."}}'
                  '  -- run Python and return its output or error',
}

_ARITHMETIC = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Constant,
               ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod,
               ast.Pow, ast.USub, ast.UAdd)

_SENTENCE_RX = re.compile(r"[.!?]+")


def calc(expr: str = "") -> str:
    """Evaluate an arithmetic expression. Anything else is refused."""
    try:
        tree = ast.parse(str(expr), mode="eval")
    except SyntaxError as err:
        return f"error: {err}"
    if any(not isinstance(node, _ARITHMETIC) for node in ast.walk(tree)):
        return "error: only arithmetic is allowed"
    try:
        return str(eval(compile(tree, "<calc>", "eval"), {"__builtins__": {}}, {}))
    except Exception as err:                      # ZeroDivision, overflow, ...
        return f"error: {err}"


def count_sentences(text: str = "") -> str:
    """Count sentences and the words in each, by the rule the check uses."""
    parts = [p.strip() for p in _SENTENCE_RX.split(str(text)) if p.strip()]
    words = [len(p.split()) for p in parts]
    return f"sentences={len(parts)} words_per_sentence={words}"


def run_python(code: str = "") -> str:
    """Run Python in a subprocess and return its output or its error."""
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as fh:
        fh.write(str(code))
        path = fh.name
    try:
        done = subprocess.run([sys.executable, path], capture_output=True,
                              text=True, timeout=TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return "error: timed out"
    out = (done.stdout or "") + (done.stderr or "")
    return (out.strip() or "(no output)")[:800]


IMPL = {"calc": calc, "count_sentences": count_sentences,
        "run_python": run_python}


def owned_by(name: str) -> list:
    """The tools this contractor may call."""
    return [tool for tool, owner in OWNER.items() if owner == name]


def call(tool: str, args: dict, caller: str):
    """Run a tool for `caller`, or refuse it. Returns (output, refused)."""
    if OWNER.get(tool) != caller:
        return f"refused: {tool!r} is not available to contractor {caller}", True
    try:
        return IMPL[tool](**(args or {})), False
    except TypeError as err:
        return f"error: bad arguments ({err})", False
