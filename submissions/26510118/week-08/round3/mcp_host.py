"""The analysis side's MCP host: starts the data room (data_room.py) for one analysis and lends its
tools to harness.run_tools.

MCP over stdio as week 5 has it: the host runs the server program and speaks JSON-RPC 2.0 to it,
one message per line, on its standard input and output. The handshake is initialize, then the
notifications/initialized notice; then tools/list, tools/call, resources/list and resources/read.
Lines that answer nothing we asked (the server's notices) are skipped. The server's own log goes to
analysis/data_room.err. When the room cannot start (open_room), the analysis goes on without tools
and says so, rather than stopping.
"""
import contextlib
import json
import os
import queue
import subprocess
import sys
import threading
import time

import common
import tools

PROTOCOL = "2025-06-18"
TIMEOUT = 30             # seconds for one answer


class DataRoom:
    def __init__(self, role: str):
        self.role, self.proc, self.n, self.tools = role, None, 0, []
        self.lines = queue.Queue()

    def __enter__(self):
        folder = common.MARKET_DIR / "analysis"
        folder.mkdir(parents=True, exist_ok=True)
        err = open(folder / "data_room.err", "a", encoding="utf-8")
        self.proc = subprocess.Popen([sys.executable, str(common.HERE / "data_room.py"), "--as", self.role],
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=err, cwd=common.HERE,
                                     env=dict(os.environ, PYTHONUTF8="1"), text=True, encoding="utf-8", bufsize=1)
        err.close()
        threading.Thread(target=self._read, daemon=True).start()
        try:
            self.server = self.request("initialize", {"protocolVersion": PROTOCOL, "capabilities": {},
                                                      "clientInfo": {"name": "kiro729-analysis", "version": "1"}})
            self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})
            self.tools = self.request("tools/list", {}).get("tools") or []
        except Exception:
            self.__exit__(None, None, None)
            raise
        return self

    def __exit__(self, *exc):
        if self.proc:
            with contextlib.suppress(OSError):
                self.proc.stdin.close()                      # end of input: the server exits
            try:
                self.proc.wait(5)
            except subprocess.TimeoutExpired:
                self.proc.kill()

    def _read(self):
        for line in self.proc.stdout:
            self.lines.put(line)
        self.lines.put(None)

    def _send(self, msg: dict) -> None:
        self.proc.stdin.write(json.dumps(msg, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()

    def request(self, method: str, params: dict) -> dict:
        self.n += 1
        self._send({"jsonrpc": "2.0", "id": self.n, "method": method, "params": params})
        deadline = time.time() + TIMEOUT
        while True:
            line = self.lines.get(timeout=max(0.1, deadline - time.time()))      # queue.Empty when it is late
            if line is None:
                raise RuntimeError("the data room closed")
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if msg.get("id") != self.n:
                continue
            if "error" in msg:
                raise RuntimeError(f"{method}: {msg['error'].get('message')}")
            return msg.get("result") or {}

    def schemas(self) -> list:
        """The room's tools as Chat Completions function tools."""
        return [{"type": "function", "function": {"name": t["name"], "description": t.get("description", ""),
                                                  "parameters": t.get("inputSchema") or {"type": "object", "properties": {}}}}
                for t in self.tools]

    def run(self, name: str, args: dict) -> str:
        """One tools/call; the text it answers, or an error the model can read."""
        try:
            r = self.request("tools/call", {"name": name, "arguments": args or {}})
        except (RuntimeError, queue.Empty, OSError) as e:
            return json.dumps({"error": f"the data room did not answer: {type(e).__name__} {e}"})
        text = "".join(c.get("text", "") for c in r.get("content") or [] if c.get("type") == "text")
        return json.dumps({"error": text[:500]}) if r.get("isError") else text[:tools.LIMIT]

    def resources(self) -> list:
        return self.request("resources/list", {}).get("resources") or []

    def read(self, uri: str) -> str:
        got = self.request("resources/read", {"uri": uri}).get("contents") or [{}]
        return got[0].get("text", "")


@contextlib.contextmanager
def open_room(role: str):
    """The data room for one analysis, or None when it cannot start."""
    room = DataRoom(role)
    try:
        room.__enter__()
    except Exception as e:                                       # noqa: BLE001  (no tools is better than no analysis)
        print(f"[analysis] data room unavailable for {role} ({type(e).__name__}: {e}); going on without tools", flush=True)
        yield None
        return
    try:
        yield room
    finally:
        room.__exit__(None, None, None)
