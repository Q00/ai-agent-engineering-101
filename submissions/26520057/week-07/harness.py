"""One call to the harness (Claude Code, haiku): prompt in on stdin, answer out as text.

    echo "your prompt" | python harness.py

No decisions here: what goes into the prompt and how the answer is read belong to the seller and the buyer.
"""
import subprocess
import sys
import tempfile

CMD = ["claude", "-p", "--model", "haiku", "--tools", "", "--setting-sources", "", "--no-session-persistence"]


def ask(prompt, timeout=120):
    with tempfile.TemporaryDirectory() as empty:  # empty dir: the harness picks up no project instructions
        r = subprocess.run(CMD, input=prompt, capture_output=True, text=True, cwd=empty, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(f"harness exit {r.returncode}: {r.stderr.strip()[:300]}")
    return r.stdout.strip()


if __name__ == "__main__":
    print(ask(sys.stdin.read()))
