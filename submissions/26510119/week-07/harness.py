"""The harness: one prompt in, one JSON object back.

HARNESS=claude (default): Claude Code in non-interactive mode, prompt on stdin, run in an empty directory.
HARNESS=openrouter: one chat completion on OpenRouter (OPENAI_API_KEY, OPENAI_BASE_URL, HARNESS_MODEL).
"""
import json
import os
import shlex
import subprocess
import tempfile
import time

BACKEND = os.environ.get("HARNESS", "claude")
MODEL = os.environ.get("HARNESS_MODEL", "nvidia/nemotron-3-super-120b-a12b")
if BACKEND == "openrouter":
    CMD = f"openrouter {MODEL} (reasoning off, temperature 0.7)"
else:
    CMD = os.environ.get("HARNESS_CMD",
                         'claude -p --model haiku --tools "" --setting-sources "" --no-session-persistence')
TIMEOUT = 90

_client = None


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


def _claude(prompt):
    with tempfile.TemporaryDirectory() as empty:
        try:
            return subprocess.run(shlex.split(CMD), input=prompt, capture_output=True, text=True,
                                  cwd=empty, timeout=TIMEOUT).stdout.strip()
        except subprocess.TimeoutExpired:
            return ""


def _openrouter(prompt, retries=5):
    global _client
    if _client is None:
        from openai import OpenAI
        _client = OpenAI(base_url=os.environ.get("OPENAI_BASE_URL", "https://openrouter.ai/api/v1"),
                         api_key=os.environ.get("OPENAI_API_KEY"), timeout=60)
    for attempt in range(retries):
        try:
            resp = _client.chat.completions.create(
                model=MODEL, messages=[{"role": "user", "content": prompt}], temperature=0.7, max_tokens=300,
                extra_body={"reasoning": {"enabled": False}})
            if resp.choices:
                return (resp.choices[0].message.content or "").strip()
            err = "empty choices"
        except Exception as e:
            err = str(e)
            if not any(s in err for s in ("429", "500", "502", "503", "504", "timed out", "Connection")):
                return ""
        time.sleep(2 ** attempt)
    return ""


def ask(prompt, log=print, tries=2):
    """Ask the harness; retry once if the answer has no JSON object."""
    for attempt in range(1, tries + 1):
        t0 = time.time()
        answer = _openrouter(prompt) if BACKEND == "openrouter" else _claude(prompt)
        log(f"  [harness {time.time() - t0:.1f}s] {answer!r}")
        obj = extract_json(answer)
        if isinstance(obj, dict):
            return obj
        log(f"  [harness] no JSON object in the answer (try {attempt}/{tries})")
    return None
