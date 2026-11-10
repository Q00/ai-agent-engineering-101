"""The one door to a model: OpenAI's Responses API.

ask() returns "" when there is no key, or the call fails or is slow, so every
caller needs a rule to fall back on. Three models, placed by how often and how much
a decision matters (prices per million tokens, input/output, OpenAI's pricing page of 11/2):

    MODEL         gpt-6-luna     $0.10/$0.50  trading modules: a buyer's or shop's sentence
                                              the code cannot read (many calls, small, within 20 s)
    MODEL_MID     gpt-6.1-terra  $1/$5        the seller and buyer analyses, every 3 hours
    MODEL_STRONG  gpt-6.1-sol    $2/$10       the integrated analysis that changes the rules,
                                              every 3 hours (12 until 11/4 night)

The Responses API, not Chat Completions: GPT-6.1 Sol calls tools only there, and GPT-6 Luna
calls tools in Chat Completions only without reasoning (the models' pages of 11/2). Reasoning
effort per model: none for the trading model (speed), low for the role analyses, medium for
the integrated analysis. chat() still takes and gives Chat Completions-shaped messages, so the
tool loop (run_tools) and the tests stay as they are; the translation is _items() and chat().

decide() is the fourth door, the Decisions API (POST /v1/decisions, gpt-6-luna only, GA since
October 2026): it returns typed answers, here the probability that a statement is true, about ten
times faster than a Responses call, and bills no output. The trading modules use it where a
judgement rests on words the code cannot read: our buyer's odds that a shop delivers as described
(its reviews and forum posts), and our shop's odds that a buyer walks away if countered.

    OPENAI_API_KEY      the key (on the server from ~/market/openai.env, never in the repo)
    OPENAI_MODEL, OPENAI_MODEL_MID, OPENAI_MODEL_STRONG   override the three names
    OPENAI_EFFORT, OPENAI_EFFORT_MID, OPENAI_EFFORT_STRONG override their reasoning effort
    HARNESS_FAKE_REPLY  tests only: every ask() returns this text and calls nothing
    HARNESS_FAKE_DECISION  tests only: every decide() question gets this probability

What every call costs is counted here, in the one place all calls pass (_post): the calls, the tokens the API reports
(input, cached input, output, reasoning) and the characters sent, summed per API and model. A process writes its
sums as ONE line of records/api_usage.jsonl every USAGE_EVERY seconds and when it exits (11/10, the user's call: the
bill had grown to about $5 a day and nothing said where), not a line per call: the seller and the buyer make
thousands of calls a day. A line says which program made the calls (seller.py, market_buyer.py, analysis.py).
"""
import atexit
import json
import os
import re
import sys
import time

import httpx

MODEL = os.environ.get("OPENAI_MODEL", "gpt-6-luna")
MODEL_MID = os.environ.get("OPENAI_MODEL_MID", "gpt-6.1-terra")
MODEL_STRONG = os.environ.get("OPENAI_MODEL_STRONG", "gpt-6.1-sol")
EFFORT = {MODEL: os.environ.get("OPENAI_EFFORT", "none"), MODEL_MID: os.environ.get("OPENAI_EFFORT_MID", "low"),
          MODEL_STRONG: os.environ.get("OPENAI_EFFORT_STRONG", "medium")}
URL = "https://api.openai.com/v1/responses"
DECIDE_URL = "https://api.openai.com/v1/decisions"
DECIDE_MODEL = "gpt-6-luna"              # the only model the Decisions API takes (11/2)


USAGE_EVERY = 600        # seconds between a process's usage lines
USAGE_KEYS = ("calls", "failed", "input", "cached", "output", "reasoning", "chars")
_usage = {}              # "api:model" -> {USAGE_KEYS}: this process's calls since its last line
_since = [time.time()]


def _post(body: dict, timeout: float, url: str = URL) -> dict:
    api = "decisions" if url == DECIDE_URL else "responses"
    try:
        r = httpx.post(url, headers={"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}"}, json=body, timeout=timeout)
        r.raise_for_status()
        data = r.json()
    except Exception:
        _count(api, body, None)
        raise
    _count(api, body, data)
    return data


def _count(api: str, body: dict, data: dict | None) -> None:
    """Add one call to this process's sums (data None: it failed); never raises, a count is not worth a lost call."""
    try:
        u = _usage.setdefault(f"{api}:{body.get('model')}", dict.fromkeys(USAGE_KEYS, 0))
        use = (data or {}).get("usage") or {}
        u["calls"] += 1
        u["failed"] += data is None
        u["chars"] += len(json.dumps(body, ensure_ascii=False))
        u["input"] += int(use.get("input_tokens") or 0)
        u["cached"] += int((use.get("input_tokens_details") or {}).get("cached_tokens") or 0)
        u["output"] += int(use.get("output_tokens") or 0)
        u["reasoning"] += int((use.get("output_tokens_details") or {}).get("reasoning_tokens") or 0)
        if time.time() - _since[0] >= USAGE_EVERY:
            flush_usage()
    except Exception:                                            # noqa: BLE001
        pass


def flush_usage() -> None:
    """This process's sums since its last line, as one line of records/api_usage.jsonl (archived after 48 h like the
    other records); nothing when it made no call."""
    if not _usage:
        return
    try:
        import common                      # here, not at the top: the door itself needs nothing of the trading side
        common.record("api_usage", proc=os.path.basename(sys.argv[0] or "?"), since=int(_since[0]), usage=_usage)
    except Exception as e:                                       # noqa: BLE001
        print(f"[harness] usage line not written: {type(e).__name__}", flush=True)
    _usage.clear()
    _since[0] = time.time()


atexit.register(flush_usage)


def decide(note: str, evidence, questions: list, timeout: float = 10.0) -> list | None:
    """Probabilities, in order, that each statement in `questions` (instruction strings) is true of
    `evidence` (a JSON-able object, sent as data with `note` saying what it is); None when there is
    no key, or the call fails. A None or a missing answer means: decide by the rules alone."""
    if os.environ.get("HARNESS_FAKE_DECISION") is not None:
        return [float(os.environ["HARNESS_FAKE_DECISION"])] * len(questions)
    if not os.environ.get("OPENAI_API_KEY") or not questions:
        return None
    body = {"model": DECIDE_MODEL,
            "input": [{"role": "user", "content": [{"type": "input_text", "text": note},
                                                   {"type": "input_json", "data": evidence}]}],
            "questions": [{"type": "predicate", "name": f"q{i}", "instructions": q} for i, q in enumerate(questions)]}
    try:
        answers = _post(body, timeout, DECIDE_URL).get("answers") or []
    except Exception as e:                                       # noqa: BLE001
        print(f"[harness] decisions: {type(e).__name__}: {str(e)[:200]}; deciding by the rules", flush=True)
        return None
    by_name = {a.get("name"): a.get("probability") for a in answers}
    return [by_name.get(f"q{i}") if isinstance(by_name.get(f"q{i}"), (int, float)) else None for i in range(len(questions))]


def _body(model: str, items) -> dict:
    body = {"model": model, "input": items}
    if model in EFFORT:
        body["reasoning"] = {"effort": EFFORT[model]}
    return body


def _text(data: dict) -> str:
    return "".join(c.get("text", "") for o in data.get("output") or [] if o.get("type") == "message"
                   for c in o.get("content") or [] if c.get("type") == "output_text").strip()


def ask(prompt: str, model: str | None = None, timeout: float = 20.0, system: str | None = None) -> str:
    """system: the fixed instructions, sent as a system message ahead of the prompt, so words another
    agent wrote (in the prompt) rank below them."""
    if os.environ.get("HARNESS_FAKE_REPLY") is not None:
        return os.environ["HARNESS_FAKE_REPLY"]
    if not os.environ.get("OPENAI_API_KEY"):
        return ""
    items = [{"role": "system", "content": system}, {"role": "user", "content": prompt}] if system else prompt
    try:
        return _text(_post(_body(model or MODEL, items), timeout))
    except Exception as e:                                       # noqa: BLE001
        print(f"[harness] {type(e).__name__}; falling back to rules", flush=True)
        return ""


def ask_json(prompt: str, model: str | None = None, system: str | None = None) -> dict | None:
    """The first {...} object in the answer, or None."""
    return first_json(ask(prompt, model, system=system))


def first_json(text: str | None) -> dict | None:
    m = re.search(r"\{.*\}", text or "", re.S)
    try:
        return json.loads(m.group()) if m else None
    except ValueError:
        return None


def _items(messages: list, flat: bool = False) -> list:
    """Chat Completions-shaped messages as Responses API input items. An assistant turn the API gave
    goes back as the API gave it (its reasoning and function calls). flat: no tools on this call
    (the budget is spent), so earlier calls and their answers go in as plain text."""
    out = []
    for m in messages:
        if m["role"] == "tool":
            out.append({"role": "user", "content": f"(tool answer) {m['content']}"} if flat else
                       {"type": "function_call_output", "call_id": m["tool_call_id"], "output": m["content"]})
        elif m["role"] == "assistant":
            if flat or not m.get("items"):
                calls = [f"(called {c['function']['name']} {c['function'].get('arguments') or '{}'})" for c in m.get("tool_calls") or []]
                text = " ".join([m.get("content") or ""] + calls).strip()
                if flat:
                    out.append({"role": "assistant", "content": text})
                else:
                    if m.get("content"):
                        out.append({"role": "assistant", "content": m["content"]})
                    out += [{"type": "function_call", "call_id": c["id"], "name": c["function"]["name"],
                             "arguments": c["function"].get("arguments") or "{}"} for c in m.get("tool_calls") or []]
            else:
                out += m["items"]
        else:
            out.append({"role": m["role"], "content": m["content"]})
    return out


def chat(messages: list, model: str, tools: list | None = None, timeout: float = 90.0) -> dict | None:
    """One turn with function tools; the assistant message in Chat Completions shape (content,
    tool_calls) plus the API's own output items, or None."""
    if not os.environ.get("OPENAI_API_KEY"):
        return None
    body = _body(model, _items(messages, flat=not tools))
    if tools:
        body.update(tools=[{"type": "function", "name": t["function"]["name"], "description": t["function"].get("description", ""),
                            "parameters": t["function"].get("parameters") or {"type": "object", "properties": {}},
                            "strict": False} for t in tools], parallel_tool_calls=False)
    try:
        data = _post(body, timeout)
    except Exception as e:                                       # noqa: BLE001
        print(f"[harness] {model}: {type(e).__name__}: {str(e)[:200]}", flush=True)
        return None
    calls = [o for o in data.get("output") or [] if o.get("type") == "function_call"]
    return {"content": _text(data), "items": data.get("output") or [],
            "tool_calls": [{"id": c["call_id"], "type": "function",
                            "function": {"name": c["name"], "arguments": c.get("arguments") or "{}"}} for c in calls]}


def run_tools(messages: list, model: str, tools: list, run, max_calls: int, chat_fn=None) -> str:
    """Let the model call tools at most max_calls times, then take its final text. The code counts
    the calls and runs the tools; `run(name, args) -> text` is the only way a tool is executed."""
    chat_fn = chat_fn or chat
    calls = 0
    while True:
        msg = chat_fn(messages, model, tools if calls < max_calls else None)
        if not msg:
            return ""
        wanted = msg.get("tool_calls") or []
        if not wanted or calls >= max_calls:
            return msg.get("content") or ""
        messages.append({"role": "assistant", "content": msg.get("content"), "tool_calls": wanted,
                         **({"items": msg["items"]} if msg.get("items") else {})})
        for call in wanted:
            calls += 1
            try:
                args = json.loads(call["function"].get("arguments") or "{}")
            except ValueError:
                args = {}
            messages.append({"role": "tool", "tool_call_id": call["id"],
                             "content": run(call["function"]["name"], args) if calls <= max_calls
                             else json.dumps({"error": "tool budget spent"})})
