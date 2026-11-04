"""Calling a department: over the internal A2A server when it runs, in-process when it does not.

call(sender, to, skill, args) seals a signed envelope, sends it to http://127.0.0.1:<port>/<to>/, checks the signed
receipt against the request and the result, and returns the result. Any transport problem (server not started,
timeout) falls back to running the same skill in-process, so a dead server never stops restocking. A rejection or a
receipt that does not check is an error, not a fallback: a forged answer must not be used.
Every call is logged to logs/r3/org-calls.jsonl with how it went (a2a or local) and how long it took.
"""
import json
import os
import time

import httpx

from .. import config
from ..a2a import A2AError, Client                  # the same A2A client the buyer uses
from . import envelope

TIMEOUT_S = 30
_CLIENTS = {}


def log_call(row):
    try:
        config.LOG_DIR.mkdir(parents=True, exist_ok=True)
        with open(config.LOG_DIR / "org-calls.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": round(time.time(), 2), **row}, ensure_ascii=False) + "\n")
    except OSError:
        pass


def server_port():
    """The port of a live server, or None."""
    try:
        info = json.loads((envelope.org_dir() / "server.json").read_text())
        os.kill(int(info["pid"]), 0)
        return int(info["port"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _client(to, port):
    key = (to, port)
    if key not in _CLIENTS:
        _CLIENTS[key] = Client(f"http://127.0.0.1:{port}/{to}", timeout=TIMEOUT_S)
    return _CLIENTS[key]


def _over_a2a(sender, to, skill, args, port):
    env = envelope.seal(sender, to, skill, args)
    task = _client(to, port).send(json.dumps(env, ensure_ascii=False))
    state = (task.get("status") or {}).get("state")
    if state != "TASK_STATE_COMPLETED":
        msg = " ".join(p.get("text", "") for p in ((task.get("status") or {}).get("message") or {}).get("parts") or [])
        raise envelope.Rejected(f"{to} answered {state}: {msg[:200]}")
    text = next((p.get("text") for a in task.get("artifacts") or [] for p in a.get("parts") or [] if p.get("text")), None)
    out = json.loads(text or "{}")
    envelope.check_receipt(out.get("receipt"), env, out.get("result"))
    return out["result"], out["receipt"]["req"][:12]


def call(sender, to, skill, args=None, mem=None):
    from ..memory import Memory
    from . import departments
    t0 = time.time()
    port = server_port()
    if port:
        try:
            result, req = _over_a2a(sender, to, skill, args or {}, port)
            log_call({"from": sender, "to": to, "skill": skill, "via": "a2a", "req": req,
                      "ms": round((time.time() - t0) * 1000)})
            return result
        except (httpx.HTTPError, A2AError, OSError) as e:      # transport: fall back below
            _CLIENTS.pop((to, port), None)
            log_call({"from": sender, "to": to, "skill": skill, "fallback": f"{type(e).__name__}: {str(e)[:120]}"})
    mem = mem or Memory()
    result = departments.run(to, skill, args or {}, mem,
                             lambda t, s, a: call(to, t, s, a, mem=mem))
    log_call({"from": sender, "to": to, "skill": skill, "via": "local", "ms": round((time.time() - t0) * 1000)})
    return result
