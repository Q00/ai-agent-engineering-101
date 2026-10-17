"""Week 06 — harness.py: the one place that calls the harness (Claude Code, haiku).

Both agents (seller.py, buyer.py) decide every price through ask(). It runs

    claude -p --model haiku --tools "" --setting-sources "" --no-session-persistence

with the prompt on stdin, inside a fresh empty directory so the harness does not
read this repository's instructions, and returns the JSON object the harness
printed. No tools, no saved sessions: the harness only reads the prompt and answers.

    python harness.py        # one sample decision, to check the harness works
"""
import json
import re
import subprocess
import tempfile

CMD = ["claude", "-p", "--model", "haiku", "--tools", "", "--setting-sources", "",
       "--no-session-persistence"]
HARNESS = "claude -p --model haiku"   # what agent.json and the report name
TIMEOUT = 90                          # seconds for one harness call


def ask(prompt: str) -> dict:
    """Send one prompt to the harness and return the first JSON object in its answer.
    Raises RuntimeError if the harness fails or prints no JSON."""
    with tempfile.TemporaryDirectory() as empty_dir:
        run = subprocess.run(CMD, input=prompt, capture_output=True, text=True,
                             cwd=empty_dir, timeout=TIMEOUT)
    if run.returncode != 0:
        raise RuntimeError(f"harness exited {run.returncode}: {run.stderr.strip()[:200]}")
    match = re.search(r"\{.*\}", run.stdout, re.DOTALL)
    if not match:
        raise RuntimeError(f"no JSON in harness answer: {run.stdout.strip()[:200]}")
    return json.loads(match.group())


if __name__ == "__main__":
    sample = (
        "You sell a lamp. Your lowest acceptable price is 100. You asked 130 and the "
        "buyer offered 80. Decide your next move. Reply with one JSON object only: "
        '{"action": "counter" or "accept" or "reject", "price": <whole number>}'
    )
    print("prompt :", sample)
    print("answer :", ask(sample))
