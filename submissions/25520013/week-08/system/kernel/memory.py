"""Client for the mem0 daemon (memd.server) over a unix socket (human code, not editable by the model).

One JSON request per connection, newline-terminated, one JSON reply. Every call has a total time budget and never
raises: search gives [] and add gives False on any failure (no socket, timeout, bad reply), so a dead daemon only
means "no memories". Stdlib only.
"""

import json
import os
import socket
import time

from kernel import common as c

MAX_TOP_K = 20
MAX_REPLY = 1_000_000


def sock_path(root=None):
    """Socket path: env R3A2_MEM_SOCK, else <root>/a2/mem0.sock."""
    return os.environ.get("R3A2_MEM_SOCK") or os.path.join(
        c.data_root(root), "a2/mem0.sock"
    )


def bound_top_k(v):
    """top_k from memory.json clamped to 0..MAX_TOP_K; anything that is not an int counts as 0."""
    if not isinstance(v, int) or isinstance(v, bool):
        return 0
    return max(0, min(MAX_TOP_K, v))


def _call(sock, req, timeout):
    """Send one request, return the reply dict or None. Never raises."""
    deadline = time.monotonic() + timeout
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            s.connect(sock)
            s.sendall(json.dumps(req, ensure_ascii=False).encode("utf-8") + b"\n")
            buf = b""
            while not buf.endswith(b"\n"):
                left = deadline - time.monotonic()
                if left <= 0 or len(buf) > MAX_REPLY:
                    return None
                s.settimeout(left)
                chunk = s.recv(65536)
                if not chunk:
                    break
                buf += chunk
        obj = json.loads(buf.decode("utf-8"))
    except (OSError, ValueError, TypeError, RecursionError):
        return None
    return obj if isinstance(obj, dict) else None


def search(sock, agent, query, top_k, timeout=1.0):
    """-> list of memory strings, best first; [] on any failure or when top_k is 0."""
    k = bound_top_k(top_k)
    if not k or not sock:
        return []
    r = _call(
        sock, {"op": "search", "agent": agent, "query": query, "top_k": k}, timeout
    )
    res = (r or {}).get("results")
    if not isinstance(res, list):
        return []
    return [
        x["memory"]
        for x in res
        if isinstance(x, dict) and isinstance(x.get("memory"), str)
    ][:k]


def add(sock, agent, text, infer=True, metadata=None, timeout=2.0):
    """Queue text for the agent's memory. True when the daemon accepted it."""
    if not sock:
        return False
    req = {
        "op": "add",
        "agent": agent,
        "text": text,
        "infer": bool(infer),
        "metadata": metadata or {},
    }
    r = _call(sock, req, timeout)
    return bool(r and r.get("ok"))


def stats(sock, timeout=0.5):
    """Daemon counters as a dict, or None when it does not answer."""
    if not sock:
        return None
    r = _call(sock, {"op": "stats"}, timeout)
    return r if r and r.get("ok") else None
