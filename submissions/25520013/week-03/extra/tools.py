"""Work tools, and who is allowed to call them.

One tool per skill, and each tool runs exactly the acceptance rule that skill
is checked by: `calc` is the arithmetic `verify._exact` compares, the sentence
counter is the regex `verify._sentences` splits on, `run_tests` is the assert
run `verify._asserts` performs. A contractor holding one can therefore know its
answer is right inside its own specialty, and has to guess everywhere else.

The model is the same for all three, so this table is the only thing that makes
them differ at all. Without it the probes in `logs/` show every contractor
passing every skill, the record fills with straight wins, and the award rule
has nothing to rank on. It is the 1980 premise put back: the node with the
sensor could measure, the others estimated.

`run_tests` returns a verdict and not an output on purpose. An arbitrary Python
runner would let the code contractor evaluate `48317 * 7629` and do the
arithmetic contractor's job, which is the asymmetry leaking; a pass or a fail
cannot be read as a number.

The table is enforced by the orchestrator, not by the prompt. A call to a tool
you do not hold is refused and counted, so reaching outside your specialty is
visible rather than silently dropped.
"""
import ast
import re

import verify


ALLOWED = {"A": ["calc"], "B": ["count_sentences"], "C": ["run_tests"]}

# Tools that need the work item's committed checks injected by the caller.
NEEDS_SPECS = frozenset({"run_tests"})

DESCRIPTION = {
    "calc": '{"tool": "calc", "args": {"expr": "137 * 249"}}'
            '  -- evaluate an arithmetic expression exactly',
    "count_sentences": '{"tool": "count_sentences", "args": {"text": "..."}}'
                       '  -- sentence count and words per sentence',
    "run_tests": '{"tool": "run_tests", "args": {"code": "..."}}'
                 '  -- run this work item\'s hidden tests, returns PASS or FAIL',
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


def run_tests(code: str = "", specs=()) -> str:
    """Run this work item's committed asserts against the code.

    A verdict, never an output: the asserts themselves are never shown and the
    result is one bit, so this cannot be turned into a general interpreter.
    """
    checks = [spec for spec in specs if spec.get("type") == "pytest"]
    if not checks:
        return "no tests are attached to this work item"
    return ("PASS" if all(verify.check(spec, str(code)) for spec in checks)
            else "FAIL")


IMPL = {"calc": calc, "count_sentences": count_sentences,
        "run_tests": run_tests}


def owned_by(name: str) -> list:
    """The tools this contractor may call."""
    return ALLOWED.get(name, [])


def call(tool: str, args: dict, caller: str, specs=()):
    """Run a tool for `caller`, or refuse it. Returns (output, refused)."""
    if tool not in ALLOWED.get(caller, []):
        return f"refused: {tool!r} is not available to contractor {caller}", True
    kwargs = dict(args or {})
    if tool in NEEDS_SPECS:
        kwargs["specs"] = specs
    try:
        return IMPL[tool](**kwargs), False
    except TypeError as err:
        return f"error: bad arguments ({err})", False
