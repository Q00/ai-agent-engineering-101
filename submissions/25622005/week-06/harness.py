"""Call the harness (Codex CLI) with a prompt on stdin and get one JSON object back.

    python harness.py 'Reply with exactly {"ok": true}'   # manual check: one call

The harness runs in an empty temporary directory so it does not pick up any
project's instruction files. Only the last message is read (from the -o file).
"""
import json
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

CODEX = ["codex", "exec", "--skip-git-repo-check", "--ephemeral", "-s", "read-only", "--color", "never"]
TIMEOUT = 120  # seconds for one harness call


def log(line):
    print(f"[harness] {line}", file=sys.stderr, flush=True)


def extract_json(text):
    """Last {...} object in the text that parses as JSON, or None."""
    for m in reversed(re.findall(r"\{[^{}]*\}", text or "")):
        try:
            return json.loads(m)
        except json.JSONDecodeError:
            continue
    return None


def ask(prompt, retries=1):
    """Run the harness once (plus `retries` more if the answer has no JSON). Returns a dict or None."""
    for attempt in range(retries + 1):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "answer.txt"
            t0 = time.time()
            try:
                p = subprocess.run(CODEX + ["-C", d, "-o", str(out), "-"], input=prompt,
                                   text=True, capture_output=True, timeout=TIMEOUT)
            except subprocess.TimeoutExpired:
                log(f"timeout after {TIMEOUT}s (attempt {attempt + 1})")
                continue
            text = out.read_text(encoding="utf-8") if out.exists() else ""
            log(f"{time.time() - t0:.1f}s rc={p.returncode} answer={text.strip()[:200]!r}")
            if p.returncode != 0:
                log(f"stderr={p.stderr.strip()[-300:]!r}")
            j = extract_json(text)
            if isinstance(j, dict):
                return j
            log(f"no JSON in answer (attempt {attempt + 1})")
    return None


if __name__ == "__main__":
    print(ask(" ".join(sys.argv[1:]) or 'Reply with exactly {"ok": true}'))
