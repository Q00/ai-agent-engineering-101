"""Work tools, and who is allowed to call them.

One tool per skill, and each tool answers exactly the question that skill's
check will ask, so a contractor that calls the right one can know its answer
is right instead of guessing.

Every contractor holds every tool. Restricting them was tried and rejected:
the probe in `logs/probe-tools.txt` shows the work here is easy enough that a
contractor succeeds outside its specialty anyway, so a permission table buys a
difference the tasks do not support. What differs between contractors is the
persona and, the open question this stage measures, whether a contractor
bothers to check before it answers.

The table stays and is still enforced by the orchestrator rather than by the
prompt, so narrowing it again is one line, and any call outside it is refused
and counted instead of silently dropped.
"""
import ast
import re
import subprocess
import sys
import tempfile

TIMEOUT_S = 10

NAMES = ("calc", "count_sentences", "run_python")
ALLOWED = {who: list(NAMES) for who in "ABC"}

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
    return ALLOWED.get(name, [])


def call(tool: str, args: dict, caller: str):
    """Run a tool for `caller`, or refuse it. Returns (output, refused)."""
    if tool not in ALLOWED.get(caller, []):
        return f"refused: {tool!r} is not available to contractor {caller}", True
    try:
        return IMPL[tool](**(args or {})), False
    except TypeError as err:
        return f"error: bad arguments ({err})", False
