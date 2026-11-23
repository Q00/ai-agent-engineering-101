"""The harness: Claude Code in non-interactive mode. Prompt on stdin, one JSON object back."""
import json
import os
import shlex
import subprocess
import tempfile
import time

CMD = os.environ.get("HARNESS_CMD",
                     'claude -p --model haiku --tools "" --setting-sources "" --no-session-persistence')
TIMEOUT = 90


def extract_json(text):
    """The first {...} in the text that parses as JSON, or None."""
    start = text.find("{")
    while start != -1:
        depth = 0
        for i in range(start, len(text)):
            depth += {"{": 1, "}": -1}.get(text[i], 0)
            if depth == 0:
                try:
                    return json.loads(text[start:i + 1])
                except json.JSONDecodeError:
                    break
        start = text.find("{", start + 1)
    return None


def ask(prompt, log=print, tries=2):
    """Run the harness in an empty directory so it reads no project instructions."""
    for attempt in range(1, tries + 1):
        t0 = time.time()
        with tempfile.TemporaryDirectory() as empty:
            try:
                answer = subprocess.run(shlex.split(CMD), input=prompt, capture_output=True, text=True,
                                        cwd=empty, timeout=TIMEOUT).stdout.strip()
            except subprocess.TimeoutExpired:
                answer = ""
        log(f"  [harness {time.time() - t0:.1f}s] {answer!r}")
        obj = extract_json(answer)
        if isinstance(obj, dict):
            return obj
        log(f"  [harness] no JSON object in the answer (try {attempt}/{tries})")
    return None
