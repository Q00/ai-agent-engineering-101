"""Week 07 — harness.py: the one place that calls the model.

Both agents (seller.py, buyer.py) decide every price through ask(). Which harness runs
is chosen by the HARNESS environment variable:

    HARNESS=openrouter (default)  an OpenRouter :free model over its chat-completions API.
                                  Needs OPENROUTER_API_KEY. The key has a $0 credit limit, so
                                  a paid model can never be billed.
    HARNESS=claude                the week-06 harness, unchanged:
                                  claude -p --model haiku --tools "" --setting-sources "" --no-session-persistence
                                  with the prompt on stdin, inside a fresh empty directory.

OPENROUTER_MODELS (comma-separated) overrides the model list. The first model is tried first;
when it is rate limited (429) or has no endpoint, the next one is tried at once. Only when every
model failed does it wait (2 s, then 4 s) and go through the list again.

    python harness.py        # one sample decision, to check the harness works
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import time

import httpx

HARNESS_KIND = os.environ.get("HARNESS", "openrouter")
TIMEOUT = 90                          # seconds for one harness call

# claude -p (week 06)
CMD = ["claude", "-p", "--model", "haiku", "--tools", "", "--setting-sources", "",
       "--no-session-persistence"]

# OpenRouter
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
MODELS = os.environ.get(
    "OPENROUTER_MODELS",
    "minimax/minimax-m3.1-flash:free,google/gemma-4-26b-a4b-it:free,google/gemma-4-31b-it:free",
).split(",")
ROUNDS = 3                            # passes over the whole model list; waits 2, 4 s between passes

HARNESS = "claude -p --model haiku" if HARNESS_KIND == "claude" else f"OpenRouter {MODELS[0]}"


def _first_json(text: str) -> dict:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise RuntimeError(f"no JSON in harness answer: {text.strip()[:200]}")
    return json.loads(match.group())


def _ask_claude(prompt: str) -> str:
    with tempfile.TemporaryDirectory() as empty_dir:
        run = subprocess.run(CMD, input=prompt, capture_output=True, text=True,
                             cwd=empty_dir, timeout=TIMEOUT)
    if run.returncode != 0:
        # claude -p prints some failures (e.g. "Credit balance is too low") on stdout, not stderr
        raise RuntimeError(f"harness exited {run.returncode}: {(run.stderr.strip() or run.stdout.strip())[:200]}")
    return run.stdout


def _ask_openrouter(prompt: str) -> str:
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise RuntimeError("OPENROUTER_API_KEY is not set")
    headers = {"Authorization": f"Bearer {key}", "X-OpenRouter-Title": "seoultech-agentic week-07"}
    errors = []
    for rnd in range(ROUNDS):
        if rnd:
            time.sleep(2 ** rnd)      # every model was busy: wait, then try the list again
        for model in MODELS:
            body = {"model": model.strip(), "messages": [{"role": "user", "content": prompt}], "temperature": 0}
            try:
                r = httpx.post(OPENROUTER_URL, json=body, headers=headers, timeout=TIMEOUT)
            except httpx.HTTPError as e:
                errors.append(f"{model}: {type(e).__name__}")
                continue
            if r.status_code != 200:
                # 429 (busy), 404 (no endpoint), 5xx: no waiting here, the next model is tried at once
                errors.append(f"{model}: HTTP {r.status_code}")
                continue
            data = r.json()
            content = (data.get("choices") or [{}])[0].get("message", {}).get("content")
            if not content:
                errors.append(f"{model}: empty answer {str(data)[:120]}")
                continue
            used = data.get("model", model)
            if used != MODELS[0]:
                print(f"[harness] answered by {used}", file=sys.stderr, flush=True)
            return content
    raise RuntimeError("all OpenRouter models failed: " + "; ".join(errors[-6:]))


def ask(prompt: str) -> dict:
    """Send one prompt to the harness and return the first JSON object in its answer.
    Raises RuntimeError if the harness fails or prints no JSON."""
    text = _ask_claude(prompt) if HARNESS_KIND == "claude" else _ask_openrouter(prompt)
    return _first_json(text)


if __name__ == "__main__":
    sample = (
        "You sell a lamp. Your lowest acceptable price is 100. You asked 130 and the "
        "buyer offered 80. Decide your next move. Reply with one JSON object only: "
        '{"action": "counter" or "accept" or "reject", "price": <whole number>}'
    )
    print("harness:", HARNESS)
    print("prompt :", sample)
    print("answer :", ask(sample))
