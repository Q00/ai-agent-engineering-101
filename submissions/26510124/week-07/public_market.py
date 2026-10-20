"""Run the official kit with JSON whitespace heartbeats for slow RPC replies.

Serveo returned empty HTTP 502s after about five seconds without response headers.
The JSON-RPC payload is unchanged; leading whitespace keeps the HTTP response alive.
Use the same arguments as scripts/market_kit.py. Shared course files stay untouched.
"""
import asyncio
import importlib.util
import json
from pathlib import Path

import httpx
from starlette.responses import StreamingResponse

KIT_PATH = Path(__file__).resolve().parents[3] / "scripts" / "market_kit.py"
spec = importlib.util.spec_from_file_location("course_market_kit", KIT_PATH)
kit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(kit)


async def json_heartbeat(fetch, request_id, interval=1):
    """Stream whitespace, then exactly one JSON response; cancel on disconnect."""
    pending = asyncio.create_task(fetch())
    try:
        yield b" \n"
        while not pending.done():
            done, _ = await asyncio.wait({pending}, timeout=interval)
            if not done:
                yield b" \n"
        try:
            content = pending.result()
            # Never pass an upstream HTML error page or empty body to a JSON client.
            reply = json.loads(content)
            if not isinstance(reply, dict) or reply.get("jsonrpc") != "2.0":
                raise ValueError("upstream did not return a JSON-RPC object")
        except Exception as exc:
            kit.log(f"[public proxy] upstream failure: {type(exc).__name__}: {exc}")
            content = json.dumps({
                "jsonrpc": "2.0", "id": request_id,
                "error": {"code": -32603, "message": "Seller response unavailable"},
            }).encode()
        yield content
    finally:
        if not pending.done():
            pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)


class PublicProxy(kit.Proxy):
    async def handle(self, req):
        path = req.path_params["path"]
        if req.method != "POST" or not path.startswith("a2a/"):
            return await super().handle(req)
        body = await req.body()
        try:
            rpc = json.loads(body)
        except ValueError:
            return await super().handle(req)
        # SSE streaming methods retain the original kit behavior.
        if not isinstance(rpc, dict) or rpc.get("method") not in {
            "SendMessage", "GetTask", "CancelTask",
        }:
            return await super().handle(req)
        url = self.ifaces.get(path.split("/", 1)[1])
        if url is None:
            return await super().handle(req)
        if req.url.query:
            url += "?" + req.url.query
        headers = {k: v for k, v in req.headers.items() if k.lower() in {
            "content-type", "a2a-version", "authorization", "accept",
        }}

        async def fetch():
            async with httpx.AsyncClient(timeout=240) as client:
                response = await client.request("POST", url, content=body, headers=headers)
            self.observe(response.content)
            return response.content

        # HTTP headers must go out before model inference finishes. Any subsequent
        # failure is represented by a JSON-RPC error (HTTP status is already 200).
        return StreamingResponse(
            json_heartbeat(fetch, rpc.get("id")), media_type="application/json",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )


if __name__ == "__main__":
    kit.Proxy = PublicProxy
    kit.main()
