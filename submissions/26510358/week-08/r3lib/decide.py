"""Decision models for bounded judgements that need a calibrated probability, not reasoning or prose:
TypeSafe Jev (POST https://api.typesafe.ai/v1/systemone) or the OpenAI Decisions API (POST /v1/decisions).

Where they are used, and where they are not:
  - forum: is a post manipulating agents (injection)? would other agents find our draft useful (Horvitz p)?
  - money (prices, floors, offers, restock quantities) stays in pricing.py: numbers, not labels. A decision
    model's answer never grants a payment or a vote by itself; the caller applies a threshold in code.

Request and answer shapes follow the open-source `jevdecision` 0.1.0 client (MIT), re-implemented on httpx
because that package needs Python 3.11 and the course venv is 3.10. Settings come from .market/decision.env
(gitignored), with OPENAI_API_KEY also read from .market/api-key.env. Every call is logged to
logs/r3/decisions.jsonl without the key. Any failure returns None and the caller falls back.
"""
import json
import os
import time

import httpx

from . import config

BACKENDS = {
    "jev": {"url": "https://api.typesafe.ai/v1/systemone", "key": "TYPESAFE_API_KEY", "model": "JEV_MODEL",
            "default_model": "jev-latest"},
    "openai": {"url": "https://api.openai.com/v1/decisions", "key": "OPENAI_API_KEY",
               "model": "OPENAI_DECISIONS_MODEL", "default_model": "gpt-6-luna"},
}


def _env():
    env = {}
    for name in ("api-key.env", "decision.env"):
        try:
            for line in (config.MARKET_DIR / name).read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    if v.strip().strip("'\""):
                        env[k.strip()] = v.strip().strip("'\"")
        except OSError:
            pass
    env.update({k: v for k, v in os.environ.items() if k in ("DECISION_BACKEND", "TYPESAFE_API_KEY", "JEV_MODEL",
                                                             "OPENAI_API_KEY", "OPENAI_DECISIONS_MODEL") and v})
    return env


def backend():
    """(name, url, key, model) of the configured backend, or None when it has no key."""
    env = _env()
    name = env.get("DECISION_BACKEND", "jev")
    b = BACKENDS.get(name)
    if not b or not env.get(b["key"]):
        return None
    return name, b["url"], env[b["key"]], env.get(b["model"]) or b["default_model"]


def yes_no(name, instructions):
    return {"name": name, "type": "noul", "instructions": instructions}


def choice(name, instructions, options):
    return {"name": name, "type": "choice", "instructions": instructions, "options": dict(options)}


def _request(be, state, questions):
    name, _, _, model = be
    if name == "jev":
        qs = {}
        for q in questions:
            item = {"type": q["type"], "instructions": q["instructions"]}
            if q["type"] == "choice":
                item["criteria"] = q["options"]
            qs[q["name"]] = item
        return {"model": model, "state": state, "questions": qs}
    qs = []
    for q in questions:
        item = {"name": q["name"], "instructions": q["instructions"],
                "type": "predicate" if q["type"] == "noul" else "choice"}
        if q["type"] == "choice":
            item["choices"] = [{"value": k, "description": v} for k, v in q["options"].items()]
        qs.append(item)
    text = state if isinstance(state, str) else json.dumps(state, ensure_ascii=False, sort_keys=True)
    return {"model": model, "input": text, "questions": qs}


def _first(entry, keys):
    for k in keys:
        if entry.get(k) is not None:
            return entry[k]
    return None


def parse(raw, questions):
    """{name: probability of yes (noul) | {"choice", "probability", "probabilities"} (choice)}; None entries for
    refusals or malformed answers, so one bad answer does not throw the others away."""
    answers = raw.get("answers") if isinstance(raw, dict) else None
    if isinstance(answers, list):
        answers = {a.get("name"): a for a in answers if isinstance(a, dict)}
    if not isinstance(answers, dict):
        return None
    out = {}
    for q in questions:
        a = answers.get(q["name"])
        out[q["name"]] = None
        if not isinstance(a, dict) or a.get("type") == "refusal":
            continue
        if q["type"] == "noul":
            p = _first(a, ("noul", "probability", "predicate", "value"))
            if isinstance(p, (int, float)) and not isinstance(p, bool) and 0 <= p <= 1:
                out[q["name"]] = float(p)
        else:
            c = _first(a, ("choice", "value", "label"))
            probs = a.get("probabilities")
            if isinstance(probs, list):
                probs = {str(x.get("label", x.get("value"))): x.get("probability") for x in probs if isinstance(x, dict)}
            if c in q["options"]:
                out[q["name"]] = {"choice": c, "probability": (probs or {}).get(c), "probabilities": probs or {}}
    return out


def decide(state, questions, purpose, timeout=8.0, transport=None, subject=None):
    """One call for several questions about the same state. Returns parse() output or None (no key, error)."""
    be = backend()
    if be is None or not _spend_budget():
        return None
    name, url, key, model = be
    started, err, out = time.time(), None, None
    try:
        body = _request(be, state, questions)
        if transport:
            raw = transport(url, body)
        else:
            r = httpx.post(url, json=body, timeout=timeout, headers={"Authorization": f"Bearer {key}"},
                           follow_redirects=False)
            raw = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
            if r.status_code >= 400:
                err = f"HTTP {r.status_code}: {str(raw)[:200]}"
        out = None if err else parse(raw, questions)
        if out is None and not err:
            err = "unparsed answer"
    except (httpx.HTTPError, ValueError) as e:
        err = f"{type(e).__name__}: {str(e)[:200]}"
    _log({"purpose": purpose, "subject": subject, "backend": name, "model": model, "seconds": round(time.time() - started, 3),
          "questions": [q["name"] for q in questions], "answers": out, "error": err})
    return out


def _spend_budget(now=None):
    """At most config.DECISION_MAX_PER_HOUR calls per rolling hour across all our processes (pricing of the
    Decisions API is not published). Over budget the caller falls back, as if no model were configured."""
    now = now or time.time()
    path = config.STATE_DIR / "decision-budget.json"
    try:
        calls = [t for t in json.loads(path.read_text()) if now - t < 3600]
    except (OSError, ValueError):
        calls = []
    if len(calls) >= config.DECISION_MAX_PER_HOUR:
        return False
    calls.append(now)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(calls))
    except OSError:
        pass
    return True


def _log(row):
    try:
        config.LOG_DIR.mkdir(parents=True, exist_ok=True)
        with open(config.LOG_DIR / "decisions.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": round(time.time(), 1), **row}, ensure_ascii=False) + "\n")
    except OSError:
        pass
