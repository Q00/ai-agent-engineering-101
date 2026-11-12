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
when it is rate limited (429) or has no endpoint, the next one is tried at once.

Free models come and go: on 2026-10-24 minimax-m3.1-flash:free was gone (404) and both gemma models were
busy (429) for hours, so 82% of the buyer's orders ended without a deal. So when every listed model fails,
the harness reads OpenRouter's current :free list (/api/v1/models, cached for an hour), tries up to four
unlisted chat models, and remembers the one that answered in .market/openrouter_free.json; later calls try
that model first. Only after that does it wait (2 s, then 4 s) and go through the list again.

    python harness.py        # one sample decision, to check the harness works
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import time

from pathlib import Path

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
    # 2026-10-24: measured with a JSON offer prompt; minimax-m3.1-flash:free (first until then) returned 404
    "nvidia/nemotron-3-ultra-550b-a55b:free,recursive/flint-v1.0:free,nvidia/nemotron-3.5-lightning:free,"
    "google/gemma-4-31b-it:free,google/gemma-4-26b-a4b-it:free",
).split(",")
MODELS_URL = "https://openrouter.ai/api/v1/models"
CACHE = Path(__file__).parent / ".market" / "openrouter_free.json"   # .market/ is gitignored
NOT_CHAT = re.compile(r"safety|guard|code|laguna|omni|-vl|vision|embed|reasoning|preview|\b[0-9.]+b:free$", re.I)
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


def _cache():
    try:
        return json.loads(CACHE.read_text())
    except Exception:
        return {}


def _remember(**kv):
    try:
        CACHE.parent.mkdir(exist_ok=True)
        CACHE.write_text(json.dumps({**_cache(), **kv}))
    except OSError:
        pass


def _free_models():
    """OpenRouter's current :free chat models (cached for an hour)."""
    c = _cache()
    if c.get("free") and time.time() - c.get("free_at", 0) < 3600:
        return c["free"]
    try:
        ids = [m["id"] for m in httpx.get(MODELS_URL, timeout=20).json()["data"] if m["id"].endswith(":free")]
    except Exception:
        return c.get("free", [])
    free = [m for m in ids if not NOT_CHAT.search(m)]
    _remember(free=free, free_at=time.time())
    return free


def _try(models, prompt, headers, errors):
    for model in models:
        body = {"model": model.strip(), "messages": [{"role": "user", "content": prompt}], "temperature": 0}
        try:
            r = httpx.post(OPENROUTER_URL, json=body, headers=headers, timeout=TIMEOUT)
        except httpx.HTTPError as e:
            errors.append(f"{model}: {type(e).__name__}")
            continue
        if r.status_code != 200:
            # 429 (busy), 404 (no endpoint), 403 (not allowed), 5xx: the next model is tried at once
            errors.append(f"{model}: HTTP {r.status_code}")
            continue
        data = r.json()
        content = (data.get("choices") or [{}])[0].get("message", {}).get("content")
        if not content:
            errors.append(f"{model}: empty answer {str(data)[:120]}")
            continue
        return model, data.get("model", model), content
    return None


def _ask_openrouter(prompt: str) -> str:
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise RuntimeError("OPENROUTER_API_KEY is not set")
    headers = {"Authorization": f"Bearer {key}", "X-OpenRouter-Title": "seoultech-agentic week-07"}
    errors = []
    found = _cache().get("found")                      # a model the refresh found earlier
    first = ([found] if found and found not in MODELS else []) + MODELS
    for rnd in range(ROUNDS):
        if rnd:
            time.sleep(2 ** rnd)      # every model was busy: wait, then try the list again
        hit = _try(first, prompt, headers, errors)
        if not hit and rnd == 0:      # every listed model failed: look at today's free list
            fresh = [m for m in _free_models() if m not in first][:4]
            if fresh:
                print(f"[harness] listed models failed; trying OpenRouter's current free models {fresh}",
                      file=sys.stderr, flush=True)
            hit = _try(fresh, prompt, headers, errors)
            if hit:
                _remember(found=hit[0], found_at=time.time())
        if hit:
            model, used, content = hit
            if model != MODELS[0]:
                print(f"[harness] answered by {used}", file=sys.stderr, flush=True)
            return content
    if found:
        _remember(found=None)          # it stopped answering too
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
