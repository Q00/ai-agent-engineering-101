"""Long-running mem0 daemon for the fast memory tier (not kernel code; this one may import mem0).

    uv run --group memory python -m memd.server --root <data root> [--sock <path>]

Protocol (kernel/memory.py is the client): unix socket, one JSON request per connection, newline-terminated, one
JSON reply.
    {"op": "search", "agent", "query", "top_k"} -> {"ok": true, "results": [{"memory", "score"}]}
    {"op": "add", "agent", "text", "infer", "metadata"} -> {"ok": true, "queued": n}   (acknowledged on enqueue)
    {"op": "stats"} -> {"ok": true, "counts", "queue", "uptime_s", "last_add_ts"}

Each agent (seller, buyer, operator) is a mem0 agent_id. Adds go through one worker queue, so an infer add (one
gpt-6.1-terra call, about 7 s) never blocks a search; searches only embed the query and read the vector store,
which is guarded by a lock that is held for vector-store and embedder calls only, never across the LLM call.
Every op is logged to the trajectory (kind "memory"): op, agent, latency, result count; no query or input text,
only the masked extracted facts of an add.
"""

import argparse
import json
import os
import queue
import socketserver
import threading
import time

from kernel import common as c
from kernel.trajectory import Trajectory

AGENTS = ("seller", "buyer", "operator")
MAX_TOP_K = 20
MAX_REQUEST = 200_000
MAX_QUEUE = 1000
MAX_FACT_CHARS = 200
EMBED_MODEL = "google/embeddinggemma-300m"
EMBED_DIMS = 768


class _Locked:
    """Proxy that runs every method of the wrapped object under one lock."""

    def __init__(self, obj, lock):
        self._obj, self._lock = obj, lock

    def __getattr__(self, name):
        v = getattr(self._obj, name)
        if not callable(v):
            return v

        def call(*a, **kw):
            with self._lock:
                return v(*a, **kw)

        return call


def build_memory(store_dir, terra):
    """mem0 Memory on a local embedder and qdrant, with fact extraction by gpt-6.1-terra."""
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("MEM0_TELEMETRY", "False")
    from mem0 import Memory
    from mem0.llms.base import LLMBase

    class TerraLLM(LLMBase):
        def __init__(self, model):
            super().__init__()
            self.terra = model

        def generate_response(
            self, messages, tools=None, tool_choice="auto", response_format=None, **kw
        ):
            prompt = "\n\n".join(
                f"[{m.get('role', '')}]\n{m.get('content', '')}" for m in messages
            )
            if response_format:
                prompt += "\n\n[format]\nJSON 객체 하나만 출력"
            return self.terra.complete(prompt, agent="memory", timeout_s=90, effort="low")

    os.makedirs(store_dir, exist_ok=True)
    cfg = {
        "embedder": {
            "provider": "huggingface",
            "config": {"model": EMBED_MODEL, "embedding_dims": EMBED_DIMS},
        },
        "vector_store": {
            "provider": "qdrant",
            "config": {
                "path": os.path.join(store_dir, "qdrant"),
                "collection_name": "r3a2",
                "embedding_model_dims": EMBED_DIMS,
                "on_disk": True,
            },
        },
        "llm": {
            "provider": "openai",
            "config": {"api_key": "unused", "model": "unused"},
        },
        "history_db_path": os.path.join(store_dir, "history.db"),
    }
    m = Memory.from_config(cfg)
    m.llm = TerraLLM(terra)
    lock = threading.RLock()
    m.vector_store = _Locked(m.vector_store, lock)
    m.embedding_model = _Locked(m.embedding_model, lock)
    return m


class MemService:
    """Protocol handling and the add worker. mem is a mem0 Memory (or a fake with the same add/search)."""

    def __init__(self, mem, traj, secrets=()):
        self.mem, self.traj, self.secrets = mem, traj, tuple(s for s in secrets if s)
        self.q = queue.Queue(maxsize=MAX_QUEUE)
        self.counts = {"search": 0, "add": 0, "add_done": 0, "error": 0}
        self.started, self.last_add_ts = time.time(), None
        self._count_lock = threading.Lock()

    def _bump(self, k):
        with self._count_lock:
            self.counts[k] += 1

    def _log(self, op, agent, t0, status, **fields):
        self.traj.append(
            agent,
            "memory",
            op=op,
            status=status,
            latency_s=round(time.monotonic() - t0, 3),
            **fields,
        )

    def handle(self, req):
        if not isinstance(req, dict):
            return {"ok": False, "error": "request must be a JSON object"}
        op, agent = req.get("op"), req.get("agent")
        if op == "stats":
            return {
                "ok": True,
                "counts": dict(self.counts),
                "queue": self.q.qsize(),
                "uptime_s": round(time.time() - self.started),
                "last_add_ts": self.last_add_ts,
            }
        if agent not in AGENTS:
            return {"ok": False, "error": f"agent must be one of {AGENTS}"}
        if op == "search":
            return self._search(agent, req)
        if op == "add":
            return self._enqueue(agent, req)
        return {"ok": False, "error": "op must be search, add or stats"}

    def _search(self, agent, req):
        t0 = time.monotonic()
        k = req.get("top_k")
        k = (
            max(1, min(MAX_TOP_K, k))
            if isinstance(k, int) and not isinstance(k, bool)
            else 5
        )
        query = req.get("query")
        if not isinstance(query, str) or not query.strip():
            return {"ok": False, "error": "query must be a non-empty string"}
        self._bump("search")
        try:
            r = self.mem.search(query, filters={"agent_id": agent}, top_k=k)
        except Exception as e:  # noqa: BLE001 - a daemon must answer, not die
            self._bump("error")
            self._log("search", agent, t0, "error", error=type(e).__name__)
            return {"ok": False, "error": type(e).__name__}
        res = [
            {
                "memory": c.mask(x.get("memory", ""), self.secrets),
                "score": x.get("score"),
            }
            for x in (r or {}).get("results") or []
            if isinstance(x, dict)
        ][:k]
        self._log("search", agent, t0, "ok", top_k=k, n=len(res))
        return {"ok": True, "results": res}

    def _enqueue(self, agent, req):
        text = req.get("text")
        if not isinstance(text, str) or not text.strip():
            return {"ok": False, "error": "text must be a non-empty string"}
        meta = req.get("metadata") if isinstance(req.get("metadata"), dict) else {}
        item = (agent, c.mask(text, self.secrets), bool(req.get("infer", True)), meta)
        try:
            self.q.put_nowait(item)
        except queue.Full:
            return {"ok": False, "error": "queue full"}
        self._bump("add")
        return {"ok": True, "queued": self.q.qsize()}

    def work_one(self, block=True, timeout=None):
        """Run one queued add. Returns False when the queue was empty."""
        try:
            agent, text, infer, meta = self.q.get(block=block, timeout=timeout)
        except queue.Empty:
            return False
        t0 = time.monotonic()
        try:
            r = self.mem.add(text, agent_id=agent, infer=infer, metadata=meta)
            facts = [
                c.mask(str(x.get("memory", "")), self.secrets)[:MAX_FACT_CHARS]
                for x in (r or {}).get("results") or []
                if isinstance(x, dict)
            ]
            self._bump("add_done")
            self.last_add_ts = round(time.time(), 3)
            self._log(
                "add",
                agent,
                t0,
                "ok",
                infer=infer,
                input_chars=len(text),
                n=len(facts),
                facts=facts,
            )
        except Exception as e:  # noqa: BLE001 - one bad add must not stop the worker
            self._bump("error")
            self._log("add", agent, t0, "error", infer=infer, error=type(e).__name__)
        finally:
            self.q.task_done()
        return True

    def worker(self):
        while True:
            self.work_one()


class _Handler(socketserver.StreamRequestHandler):
    def handle(self):
        line = self.rfile.readline(MAX_REQUEST)
        try:
            reply = self.server.service.handle(json.loads(line.decode("utf-8")))
        except (ValueError, RecursionError):
            reply = {"ok": False, "error": "bad JSON"}
        try:
            self.wfile.write(json.dumps(reply, ensure_ascii=False).encode("utf-8") + b"\n")
        except BrokenPipeError:
            pass


class Server(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True
    request_queue_size = 128  # default 5 refused concurrent searches (검증 11-06)

    def __init__(self, sock_path, service):
        if os.path.exists(sock_path):
            os.unlink(sock_path)
        os.makedirs(os.path.dirname(sock_path) or ".", exist_ok=True)
        old = os.umask(0o177)
        try:
            super().__init__(sock_path, _Handler)
        finally:
            os.umask(old)
        os.chmod(sock_path, 0o600)
        self.service = service


def serve(sock_path, service):
    """Start the add worker and serve until killed."""
    threading.Thread(target=service.worker, daemon=True).start()
    with Server(sock_path, service) as srv:
        srv.serve_forever()


def main(argv=None):
    from kernel.llm import Terra
    from kernel.replay import CLI

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=None)
    ap.add_argument("--sock", default=None)
    ap.add_argument("--store", default=None, help="default <root>/a2/mem0")
    ap.add_argument("--cli", default=CLI)
    a = ap.parse_args(argv)
    root = c.data_root(a.root)
    login = c.owner_login(root)
    secrets = (login,) if login else ()
    traj = Trajectory(os.path.join(root, "a2/trajectory.jsonl"), secrets=secrets)
    t0 = time.monotonic()
    mem = build_memory(
        a.store or os.path.join(root, "a2/mem0"),
        Terra(traj, cli=a.cli, secrets=secrets),
    )
    sock = a.sock or os.path.join(root, "a2/mem0.sock")
    traj.append(
        "operator",
        "memory",
        op="start",
        status="ok",
        latency_s=round(time.monotonic() - t0, 3),
    )
    serve(sock, MemService(mem, traj, secrets))


if __name__ == "__main__":
    main()
