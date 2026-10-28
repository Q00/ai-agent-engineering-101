"""Authenticated Streamable HTTP market, with a separate local runner API.

Run: MARKET_ADMIN_TOKEN=<random secret> python market_server.py --port 8015
The administrator creates negotiations; a party bearer token can use only MCP.
"""

from __future__ import annotations

import argparse
from contextvars import ContextVar
import ipaddress
import json
import os
import secrets
from typing import Annotated

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field
from starlette.requests import Request
from starlette.responses import JSONResponse
import uvicorn

from market import Market, MarketError, Principal


REQUEST_GRANT: ContextVar[tuple[Principal, int | None] | None] = ContextVar("market_grant", default=None)
StrictPrice = Annotated[int, Field(strict=True, ge=0)]
READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False,
                            idempotent_hint=True, open_world_hint=False)
MOVE = ToolAnnotations(read_only_hint=False, destructive_hint=False,
                       idempotent_hint=False, open_world_hint=False)


class BearerBoundary:
    """Authorize every MCP/admin HTTP request before SDK parsing or tool work.

    Count actual tools/call requests here so malformed tool arguments still count
    toward host cost, even when the SDK rejects them before invoking a function.
    """

    def __init__(self, app, market: Market, admin_token: str):
        self.app, self.market, self.admin_token = app, market, admin_token

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path = scope.get("path", "")
        is_mcp = path.rstrip("/") == "/mcp"
        is_admin = path.startswith("/admin/")
        if not (is_mcp or is_admin):
            return await self.app(scope, receive, send)
        headers = [value.decode("latin-1") for name, value in scope["headers"]
                   if name.lower() == b"authorization"]
        authorization = headers[0].split() if len(headers) == 1 else []
        bearer = (authorization[1] if len(authorization) == 2
                  and authorization[0].lower() == "bearer" else "")
        principal = self.market.authenticate(bearer)
        if is_admin:
            client = scope.get("client")
            try:
                local = bool(client and ipaddress.ip_address(client[0]).is_loopback)
            except ValueError:
                local = False
            authorized = local and bool(bearer) and secrets.compare_digest(
                bearer.encode("utf-8"), self.admin_token.encode("utf-8"))
        else:
            authorized = principal is not None
        if not authorized:
            return await JSONResponse(
                {"error": "a valid bearer token is required"}, status_code=401,
                headers={"WWW-Authenticate": 'Bearer realm="negotiation-market"'},
            )(scope, receive, send)
        if is_admin:
            return await self.app(scope, receive, send)

        parts = []
        size = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body = message.get("body", b"")
            size += len(body)
            if size > 65536:
                return await JSONResponse({"error": "request too large"}, status_code=413)(scope, receive, send)
            parts.append(body)
            if not message.get("more_body", False):
                break
        request_body = b"".join(parts)
        call_index = None
        try:
            payload = json.loads(request_body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            payload = None
        if isinstance(payload, dict) and payload.get("method") == "tools/call":
            params = payload.get("params")
            params = params if isinstance(params, dict) else {}
            call_index = self.market.begin_call(principal, params.get("name", ""), params.get("arguments", {}))
        context_token = REQUEST_GRANT.set((principal, call_index))
        replayed = False
        response_parts = []

        async def replay_receive():
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": request_body, "more_body": False}
            return await receive()

        async def capture_send(message):
            if message["type"] == "http.response.body":
                response_parts.append(message.get("body", b""))
            await send(message)

        try:
            await self.app(scope, replay_receive, capture_send)
        finally:
            REQUEST_GRANT.reset(context_token)
            if call_index is not None:
                # Executed functions have already completed their audit event.
                # This fallback captures schema/unknown-tool/transport errors.
                raw = b"".join(response_parts).decode("utf-8", errors="replace")
                self.market.complete_unhandled(principal, call_index, raw or "request ended before tool execution")


def create_app(admin_token: str, market: Market | None = None):
    if not isinstance(admin_token, str) or not admin_token:
        raise ValueError("MARKET_ADMIN_TOKEN must be a nonempty secret")
    market = market or Market()
    mcp = MCPServer("negotiation-market", version="1.0")

    def invoke(name: str, args: dict) -> dict:
        grant = REQUEST_GRANT.get()
        if grant is None or grant[1] is None:
            raise ToolError("missing authenticated tool request")
        principal, call_index = grant
        try:
            return market.execute(principal, name, args, call_index)
        except MarketError as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool(annotations=READ_ONLY)
    async def get_negotiation(negotiation_id: str) -> dict:
        """Read this negotiation's item, your role, current turn, status, and moves."""
        return invoke("get_negotiation", {"negotiation_id": negotiation_id})

    @mcp.tool(annotations=MOVE)
    async def propose(negotiation_id: str, price: StrictPrice) -> dict:
        """Propose a nonnegative whole-number price. A successful proposal ends your turn."""
        return invoke("propose", {"negotiation_id": negotiation_id, "price": price})

    @mcp.tool(annotations=MOVE)
    async def accept_proposal(negotiation_id: str) -> dict:
        """Accept the other party's last proposed price and close with a deal."""
        return invoke("accept_proposal", {"negotiation_id": negotiation_id})

    @mcp.tool(annotations=MOVE)
    async def reject_proposal(negotiation_id: str) -> dict:
        """Decline the proposal and continue negotiating. Ends your turn."""
        return invoke("reject_proposal", {"negotiation_id": negotiation_id})

    @mcp.tool(annotations=MOVE)
    async def refuse(negotiation_id: str) -> dict:
        """Leave this negotiation and close it with no deal."""
        return invoke("refuse", {"negotiation_id": negotiation_id})

    @mcp.custom_route("/health", methods=["GET"])
    async def health(request: Request):
        return JSONResponse({"status": "ok"})

    @mcp.custom_route("/admin/negotiations", methods=["POST"])
    async def create_negotiation(request: Request):
        try:
            payload = await request.json()
            if not isinstance(payload, dict):
                raise MarketError("body must be an object")
            result = market.create(payload.get("scenario"), payload.get("condition"), payload.get("max_turns", 8))
            return JSONResponse(result, status_code=201)
        except (ValueError, TypeError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    @mcp.custom_route("/admin/negotiations/{negotiation_id}", methods=["GET"])
    async def snapshot(request: Request):
        try:
            return JSONResponse(market.snapshot(request.path_params["negotiation_id"]))
        except MarketError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)

    @mcp.custom_route("/admin/negotiations/{negotiation_id}/finish_turn", methods=["POST"])
    async def finish_turn(request: Request):
        try:
            payload = await request.json()
            if not isinstance(payload, dict):
                raise MarketError("body must be an object")
            return JSONResponse(market.finish_turn(request.path_params["negotiation_id"], payload.get("turn_index")))
        except (ValueError, TypeError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    app = mcp.streamable_http_app(json_response=True, stateless_http=True, host="127.0.0.1")
    return BearerBoundary(app, market, admin_token)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8015)
    args = parser.parse_args()
    admin_token = os.environ.get("MARKET_ADMIN_TOKEN", "")
    if not admin_token:
        parser.error("set MARKET_ADMIN_TOKEN to a random secret before starting the server")
    uvicorn.run(create_app(admin_token), host="127.0.0.1", port=args.port,
                log_level="warning", access_log=False)
