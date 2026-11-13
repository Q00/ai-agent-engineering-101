"""Asks the harness for one decision and returns a JSON answer that matches a schema.

The seller and the buyer both decide through ask(). Two backends:

- HARNESS=claude (default, used by the trade test): Claude Code, `claude -p --model haiku`.
  Every call runs in a fresh empty directory so Claude Code does not pick up this
  repository's CLAUDE.md, and passes --json-schema so the answer comes back as
  structured output. Needs a `claude` login on this machine.
- HARNESS=openrouter (for the deployed seller, where no `claude` login exists):
  openai/gpt-6-luna on OpenRouter with OPENROUTER_API_KEY from the environment,
  pinned to the OpenAI endpoint without fallbacks, require_parameters so an
  unsupported parameter fails instead of being dropped, reasoning effort low, no
  temperature, and response_format = strict json_schema on every request. The body
  is read back as sent and its relevant fields are logged; the key never is.

Either way the answer is validated again here (fields, types, enums) before use.
"""
import json
import os
import subprocess
import sys
import tempfile
import time

import httpx

BACKEND = os.environ.get("HARNESS", "claude")
MODEL = "haiku"
BASE_CMD = ["claude", "-p", "--model", MODEL, "--tools", "", "--setting-sources", "",
            "--no-session-persistence", "--output-format", "json"]
OR_URL = "https://openrouter.ai/api/v1/chat/completions"
OR_MODEL = os.environ.get("OR_MODEL", "openai/gpt-6-luna")
OR_PROVIDER = os.environ.get("OR_PROVIDER", "openai")
OR_REASONING = os.environ.get("OR_REASONING", "low")
OR_MAX_CALLS_PER_HOUR = int(os.environ.get("OR_MAX_CALLS_PER_HOUR", "300"))   # a public seller spends real credit
TIMEOUT = 90
RETRIES = 2
_or_calls = []   # start times of OpenRouter calls in the last hour


class HarnessError(RuntimeError):
    pass


def log(tag, msg):
    print(f"[{time.strftime('%H:%M:%S')}] {tag} {msg}", file=sys.stderr, flush=True)


def validate(answer, schema):
    """Local check of the structured output: required fields, types, enums."""
    if not isinstance(answer, dict):
        raise HarnessError(f"answer is not an object: {answer!r}")
    props = schema.get("properties", {})
    for key in schema.get("required", []):
        if key not in answer:
            raise HarnessError(f"missing field {key!r} in {answer!r}")
    for key, value in answer.items():
        spec = props.get(key)
        if spec is None:
            raise HarnessError(f"unexpected field {key!r}")
        kind = spec.get("type")
        if kind == "integer" and not (isinstance(value, int) and not isinstance(value, bool)):
            raise HarnessError(f"{key}={value!r} is not an integer")
        if kind == "string" and not isinstance(value, str):
            raise HarnessError(f"{key}={value!r} is not a string")
        if "enum" in spec and value not in spec["enum"]:
            raise HarnessError(f"{key}={value!r} not in {spec['enum']}")
    return answer


def ask(prompt, schema, system, tag="harness"):
    """One decision. Returns the validated structured output as a dict."""
    if BACKEND == "openrouter":
        return _ask_openrouter(prompt, schema, system, tag)
    if BACKEND != "claude":
        raise HarnessError(f"unknown HARNESS={BACKEND!r}")
    return _ask_claude(prompt, schema, system, tag)


def _log_wire(tag):
    def hook(request):
        """The body as it leaves the client. The Authorization header is never read."""
        try:
            body = json.loads(request.content or b"{}")
        except ValueError:
            body = {}
        rf = body.get("response_format") or {}
        log(tag, f"wire model={body.get('model')} response_format={rf.get('type')}"
                 f"/strict={rf.get('json_schema', {}).get('strict')} provider={body.get('provider')} "
                 f"reasoning={body.get('reasoning')} temperature_sent={'temperature' in body}")
    return hook


def _ask_openrouter(prompt, schema, system, tag):
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise HarnessError("HARNESS=openrouter needs OPENROUTER_API_KEY in the environment")
    now = time.time()
    _or_calls[:] = [t for t in _or_calls if now - t < 3600]
    if len(_or_calls) >= OR_MAX_CALLS_PER_HOUR:
        raise HarnessError(f"call cap reached: {OR_MAX_CALLS_PER_HOUR} OpenRouter calls in the last hour")
    _or_calls.append(now)
    body = {
        "model": OR_MODEL,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        "response_format": {"type": "json_schema",
                            "json_schema": {"name": "decision", "strict": True, "schema": schema}},
        "reasoning": {"effort": OR_REASONING},
        "provider": {"only": [OR_PROVIDER], "allow_fallbacks": False, "require_parameters": True},
    }
    last = None
    with httpx.Client(timeout=TIMEOUT, event_hooks={"request": [_log_wire(tag)]}) as http:
        for attempt in range(1, RETRIES + 1):
            started = time.time()
            try:
                r = http.post(OR_URL, json=body, headers={"Authorization": f"Bearer {key}"})
            except httpx.HTTPError as e:
                last = f"{type(e).__name__}: {e}"
                log(tag, f"attempt {attempt}: {last}")
                continue
            if r.status_code != 200:
                last = f"HTTP {r.status_code}: {r.text[:300]}"
                log(tag, f"attempt {attempt}: {last}")
                if r.status_code < 500 and r.status_code != 429:
                    break          # a 4xx (unsupported parameter, bad key) will not fix itself
                time.sleep(3 * attempt)
                continue
            try:
                content = r.json()["choices"][0]["message"]["content"]
                answer = validate(json.loads(content), schema)
            except (KeyError, IndexError, TypeError, ValueError, HarnessError) as e:
                last = f"invalid answer: {e}"
                log(tag, f"attempt {attempt}: {last}")
                continue
            log(tag, f"openrouter {OR_MODEL} response_format=json_schema(strict) {time.time() - started:.1f}s "
                     f"-> {json.dumps(answer, ensure_ascii=False)}")
            return answer
    raise HarnessError(f"no valid answer after {RETRIES} attempts: {last}")


def _ask_claude(prompt, schema, system, tag):
    cmd = BASE_CMD + ["--system-prompt", system, "--json-schema", json.dumps(schema)]
    last = None
    for attempt in range(1, RETRIES + 1):
        started = time.time()
        with tempfile.TemporaryDirectory(prefix="harness-") as empty:
            try:
                proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True,
                                      cwd=empty, timeout=TIMEOUT)
            except subprocess.TimeoutExpired:
                last = f"timeout after {TIMEOUT}s"
                log(tag, f"attempt {attempt}: {last}")
                continue
        took = time.time() - started
        try:
            out = json.loads(proc.stdout)
        except json.JSONDecodeError:
            last = f"exit {proc.returncode}, stdout not JSON: {proc.stdout[:200]!r} {proc.stderr[:200]!r}"
            log(tag, f"attempt {attempt}: {last}")
            continue
        if out.get("is_error"):
            last = f"harness error: {out.get('result')!r}"
            log(tag, f"attempt {attempt}: {last}")
            continue
        try:
            answer = validate(out.get("structured_output"), schema)
        except HarnessError as e:
            last = str(e)
            log(tag, f"attempt {attempt}: invalid answer: {last}")
            continue
        log(tag, f"claude -p --model {MODEL} --json-schema(set) {took:.1f}s -> {json.dumps(answer, ensure_ascii=False)}")
        return answer
    raise HarnessError(f"no valid answer after {RETRIES} attempts: {last}")


if __name__ == "__main__":
    demo = {"type": "object", "additionalProperties": False, "required": ["action", "price"],
            "properties": {"action": {"type": "string", "enum": ["counter", "accept", "reject"]},
                           "price": {"type": "integer"}}}
    print(ask("Lamp, reserve 100. The buyer offered 80. What do you do?", demo, "You are a shop owner."))
