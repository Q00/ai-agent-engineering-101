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

_NUMBER_RX = re.compile(r"-?\d{1,3}(?:,\d{3})+(?:\.\d+)?|-?\d+(?:\.\d+)?")
_FENCE_RX = re.compile(r"```(?:python)?\s*(.*?)```", re.S)
_SENTENCE_RX = re.compile(r"[.!?]+")


def check(spec: dict, answer: str) -> bool:
    """Return whether `answer` satisfies `spec`. Unknown types fail closed."""
    kind = spec.get("type")
    if kind == "exact":
        return _exact(spec["expect"], answer)
    if kind == "rule":
        return _sentences(spec, answer)
    if kind == "pytest":
        return _asserts(spec["asserts"], answer)
    return False


def _exact(expect: str, answer: str) -> bool:
    """The last number in the reply must equal the expected one.

    The element text tells the contractor to return only the number, so taking
    the last number is a lenient reading of a strict instruction: "the answer
    is 34113" passes, "34113 in 2 steps" does not.

    Thousand separators are part of the number. Reading "262,245,905" as a
    trailing 905 marks a correct answer wrong, which would put a defect of this
    checker into the record the manager then learns from.
    """
    found = _NUMBER_RX.findall(answer or "")
    if not found:
        return False
    try:
        return float(found[-1].replace(",", "")) == float(expect)
    except ValueError:
        return False


def _sentences(spec: dict, answer: str) -> bool:
    """Exactly `sentences` sentences, each at most `max_words` words, and
    `total_words` words in all when that is given.

    An exact count with a word ceiling, not a loose upper bound: a constraint
    a model clears by accident records nothing about the contractor that met
    it. Sentences are split on terminating punctuation, words on whitespace.
    """
    parts = [p.strip() for p in _SENTENCE_RX.split(answer or "") if p.strip()]
    if len(parts) != spec["sentences"]:
        return False
    limit = spec.get("max_words")
    if limit is not None and any(len(p.split()) > limit for p in parts):
        return False
    total = spec.get("total_words")
    return total is None or sum(len(p.split()) for p in parts) == total


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
        done = subprocess.run(
            [sys.executable, path], capture_output=True, text=True, timeout=TIMEOUT_S
        )
    except subprocess.TimeoutExpired:
        return False
    return done.returncode == 0
