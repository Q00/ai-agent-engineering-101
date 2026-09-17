"""Machine verification of contractor work. No model is involved anywhere.

The rules come from the `verify` field of each element in `tasks_ext.json`,
committed before any run. A contractor never sees them: it only sees the
element text, which states the format the rule will check.

Verification is not a quality judgement. A graceful one-sentence summary and a
clumsy one both pass `max_sentences: 1`. What is being recorded is whether a
contractor met a stated, checkable constraint — which is all the trajectory
needs, and all that can be established without a judge.
"""
import re
import subprocess
import sys
import tempfile

TIMEOUT_S = 10

_NUMBER_RX = re.compile(r"-?\d+(?:\.\d+)?")
_FENCE_RX = re.compile(r"```(?:python)?\s*(.*?)```", re.S)
_SENTENCE_RX = re.compile(r"[.!?]+")


def check(spec: dict, answer: str) -> bool:
    """Return whether `answer` satisfies `spec`. Unknown types fail closed."""
    kind = spec.get("type")
    if kind == "exact":
        return _exact(spec["expect"], answer)
    if kind == "rule":
        return _sentences(spec["max_sentences"], answer)
    if kind == "pytest":
        return _asserts(spec["asserts"], answer)
    return False


def _exact(expect: str, answer: str) -> bool:
    """The last number in the reply must equal the expected one.

    The element text tells the contractor to return only the number, so taking
    the last number is a lenient reading of a strict instruction: "the answer
    is 34113" passes, "34113 in 2 steps" does not.
    """
    found = _NUMBER_RX.findall(answer or "")
    if not found:
        return False
    try:
        return float(found[-1]) == float(expect)
    except ValueError:
        return False


def _sentences(limit: int, answer: str) -> bool:
    """Count sentences by terminating punctuation; empty answers fail."""
    parts = [p for p in _SENTENCE_RX.split(answer or "") if p.strip()]
    return 1 <= len(parts) <= limit


def _code(answer: str) -> str:
    """Take the first fenced block if the reply has one, else the whole reply."""
    match = _FENCE_RX.search(answer or "")
    return match.group(1) if match else (answer or "")


def _asserts(asserts: list, answer: str) -> bool:
    """Run the contractor's code plus the pre-committed asserts in a subprocess.

    A subprocess with a timeout, not `exec` in this process: the code is model
    output and a syntax error, an infinite loop, or a stray `exit()` must not
    take the run down with it.
    """
    script = _code(answer) + "\n" + "\n".join(f"assert {a}" for a in asserts)
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as fh:
        fh.write(script)
        path = fh.name
    try:
        done = subprocess.run([sys.executable, path], capture_output=True,
                              text=True, timeout=TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return False
    return done.returncode == 0
