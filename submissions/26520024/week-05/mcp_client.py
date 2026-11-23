"""Small synchronous HTTP host transport; credentials never enter its trace."""
import json
from pathlib import Path
import secrets
import select
import subprocess
import sys
from urllib.error import HTTPError
from urllib.request import Request, build_opener, ProxyHandler

from market_server import VERSION, META_VERSION, META_CAPS, strict_json


def http_post(url, token, body, headers=None):
    headers = dict(headers or {})
    headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = "Bearer " + token
    req = Request(url, data=json.dumps(body, allow_nan=False).encode(), headers=headers, method="POST")
    opener = build_opener(ProxyHandler({}))
    try:
        response = opener.open(req, timeout=20)
    except HTTPError as exc:
        response = exc
    with response:
        raw = response.read().decode("utf-8")
        return response.status, dict(response.headers), raw


def decode_rpc(raw, content_type):
    if content_type.startswith("application/json"):
        return strict_json(raw)
    if content_type.startswith("text/event-stream"):
        replies = []
        for event in raw.replace("\r\n", "\n").split("\n\n"):
            data = "\n".join(line[5:].lstrip(" ") for line in event.splitlines() if line.startswith("data:"))
            if data:
                obj = strict_json(data)
                if "result" in obj or "error" in obj:
                    replies.append(obj)
        if len(replies) == 1:
            return replies[0]
    raise RuntimeError("Expected one final JSON-RPC response")


class MCPClient:
    def __init__(self, url, token, emit=lambda *a, **kw: None):
        self.url, self.token, self.emit = url, token, emit
        self.sequence = 0

    def request_parts(self, method, params=None):
        self.sequence += 1
        params = dict(params or {})
        params["_meta"] = {META_VERSION: VERSION, META_CAPS: {},
            "io.modelcontextprotocol/clientInfo": {"name": "week05-python-host", "version": "1.0"}}
        body = dict(jsonrpc="2.0", id=self.sequence, method=method, params=params)
        headers = {"Accept": "application/json, text/event-stream", "MCP-Protocol-Version": VERSION,
                   "Mcp-Method": method}
        if method == "tools/call":
            headers["Mcp-Name"] = params["name"]
        return body, headers

    def rpc(self, method, params=None):
        body, headers = self.request_parts(method, params)
        self.emit("http_request", request=body, headers=headers)
        status, response_headers, raw = http_post(self.url + "/mcp", self.token, body, headers)
        self.emit("http_response", request_id=body["id"], status=status, headers=response_headers, raw=raw)
        reply = decode_rpc(raw, response_headers.get("Content-Type", ""))
        if status != 200 or reply.get("id") != body["id"] or "error" in reply:
            raise RuntimeError("MCP request failed: {} {}".format(status, reply))
        result = reply["result"]
        if result.get("resultType") != "complete":
            raise RuntimeError("Unsupported asynchronous result")
        return result

    def call(self, name, arguments):
        return self.rpc("tools/call", dict(name=name, arguments=arguments))


class ServerProcess:
    def __enter__(self):
        self.secret = secrets.token_urlsafe(32)
        self.process = subprocess.Popen([sys.executable, "-u", str(Path(__file__).with_name("market_server.py"))],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            self.process.stdin.write(json.dumps({"admin_secret": self.secret}) + "\n")
            self.process.stdin.flush()
            self.process.stdin.close()
            ready, _, _ = select.select([self.process.stdout], [], [], 15)
            if not ready:
                raise RuntimeError("Market server startup timed out")
            self.url = strict_json(self.process.stdout.readline())["url"]
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def admin(self, operation, **body):
        status, _, raw = http_post(self.url + "/admin/" + operation, self.secret, body)
        if status != 200:
            raise RuntimeError("Admin request failed: {} {}".format(status, raw))
        return strict_json(raw)

    def __exit__(self, *args):
        self.process.terminate()
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=10)
        self.process.stdout.close()
        self.process.stderr.close()

