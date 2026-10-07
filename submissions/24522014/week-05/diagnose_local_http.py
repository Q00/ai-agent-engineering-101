"""Compare ASGI, Uvicorn socket writes, and received bytes without logging tokens."""

import asyncio
import json
import secrets
import socket

import uvicorn
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route
from uvicorn.protocols.http.h11_impl import H11Protocol

from market_server import create_app


class RecordingTransport:
    def __init__(self, transport, protocol):
        self.transport, self.protocol = transport, protocol

    def __getattr__(self, name):
        return getattr(self.transport, name)

    def write(self, data):
        path = (self.protocol.scope or {}).get("path", "")
        if data.startswith(b"HTTP/"):
            print(f"[server write {path}] {data.decode('latin-1').replace(chr(13) + chr(10), ' | ')}", flush=True)
        elif path in ("/control", "/rpc", "/mcp") and data:
            # None of these diagnostic responses contains issued tokens.
            print(f"[server body {path}] bytes={len(data)} prefix={data[:24]!r}", flush=True)
        self.transport.write(data)


class RecordingH11(H11Protocol):
    def connection_made(self, transport):
        super().connection_made(RecordingTransport(transport, self))


async def request(port, path, token=None, protocol="HTTP/1.1", modern=True):
    body = json.dumps({"jsonrpc": "2.0", "id": 99, "method": "tools/list", "params": {
        "_meta": {"io.modelcontextprotocol/protocolVersion": "2026-07-28",
                  "io.modelcontextprotocol/clientCapabilities": {}}
    }}).encode()
    headers = {
        "Host": f"127.0.0.1:{port}", "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream", "Mcp-Method": "tools/list",
        "Mcp-Protocol-Version": "2026-07-28", "Content-Length": str(len(body)),
        "Connection": "close",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if not modern:
        del headers["Mcp-Method"]
        del headers["Mcp-Protocol-Version"]
        body = b""
        headers["Content-Length"] = "0"
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        data = f"POST {path} {protocol}\r\n" + "".join(f"{k}: {v}\r\n" for k, v in headers.items()) + "\r\n"
        writer.write(data.encode() + body)
        await writer.drain()
        async with asyncio.timeout(10):
            head = await reader.readuntil(b"\r\n\r\n")
            received_body = await reader.read()
        print(f"[client headers {path} {protocol}] {head.decode('latin-1').replace(chr(13) + chr(10), ' | ')}", flush=True)
        print(f"[client body {path}] bytes={len(received_body)} prefix={received_body[:24]!r}", flush=True)
    finally:
        writer.close()
        await writer.wait_closed()


async def main():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    app = create_app(secrets.token_urlsafe(32), f"http://127.0.0.1:{port}")
    episode = app.state.market.create("diagnostic item")

    async def control(_request):
        return JSONResponse({"ok": True})

    async def rpc(_request):
        return JSONResponse({"jsonrpc": "2.0", "id": 99, "result": {"ok": True}})

    async def streaming(_request):
        async def body():
            yield b'{"ok":'
            yield b'true}'
        return StreamingResponse(body(), media_type="application/json")

    app.routes.insert(0, Route("/control", control, methods=["POST"]))
    app.routes.insert(0, Route("/rpc", rpc, methods=["POST"]))
    app.routes.insert(0, Route("/streaming", streaming, methods=["POST"]))
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port,
                                          http=RecordingH11, access_log=False))
    task = asyncio.create_task(server.serve())
    try:
        async with asyncio.timeout(15):
            while not server.started:
                if task.done():
                    await task
                    raise RuntimeError("Server failed to start")
                await asyncio.sleep(0.05)
        await request(port, "/control")
        await request(port, "/control", modern=False)
        await request(port, "/streaming", modern=False)
        await request(port, "/streaming")
        await request(port, "/rpc")
        await request(port, "/mcp")
        await request(port, "/mcp", episode["tokens"]["buyer"])
        await request(port, "/mcp", episode["tokens"]["buyer"], "HTTP/1.0")
    finally:
        server.should_exit = True
        await task


if __name__ == "__main__":
    asyncio.run(main())
