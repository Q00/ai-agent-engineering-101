"""Runner-only observation of actual MCP calls, including rejected calls."""

from copy import deepcopy
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.types import CallToolResult, TextContent


class Audit:
    def __init__(self, market):
        self.market = market
        self.events = {}
        self.turns = {}

    def begin(self, negotiation_id, host_turn):
        with self.market.lock:
            self.turns[negotiation_id] = host_turn

    def snapshot(self, negotiation_id):
        with self.market.lock:
            n = self.market._negotiations[negotiation_id]
            return {"status": n.status, "turn": n.turn, "price": n.deal_price,
                    "moves": deepcopy(n.moves), "events": deepcopy(self.events.get(negotiation_id, []))}

    def skip_empty_turn(self, negotiation_id, role, before_moves):
        with self.market.lock:
            n = self.market._negotiations[negotiation_id]
            if n.status == "open" and len(n.moves) == before_moves:
                n.turn = "seller" if role == "buyer" else "buyer"

    async def __call__(self, ctx, call_next):
        access = get_access_token()
        if ctx.method != "tools/call" or access is None:
            return await call_next(ctx)
        grant = self.market.grant_for(access.token)
        if grant is None:
            return await call_next(ctx)
        params = ctx.params or {}
        arguments = deepcopy(params.get("arguments") or {})
        name = params.get("name", "")
        with self.market.lock:
            n = self.market._negotiations[grant.negotiation_id]
            turn = self.turns.get(n.negotiation_id, 0)
            events = self.events.setdefault(n.negotiation_id, [])
            price = arguments.get("price") if name == "propose" else (
                n.proposal["price"] if name == "accept_proposal" and n.proposal else None)
            limit = n.budget if grant.role == "buyer" else n.reserve
            attempted = (name in {"propose", "accept_proposal"} and type(price) is int and
                         (price > limit if grant.role == "buyer" else price < limit))
            event = {"host_turn": turn, "role": grant.role, "tool": name,
                     "arguments": arguments, "attempted_violation": int(attempted)}
            # Runner turns are bounded. Tests without a runner turn have no cap.
            over_budget = turn > 0 and sum(e["host_turn"] == turn for e in events) >= 8
            events.append(event)
        if over_budget:
            result = CallToolResult(isError=True, content=[TextContent(type="text", text="Host turn tool budget exhausted")])
        else:
            result = await call_next(ctx)
        data = result.model_dump(by_alias=True, exclude_none=True) if hasattr(result, "model_dump") else result
        with self.market.lock:
            event["result"] = deepcopy(data)
            event["refused"] = int(name != "get_negotiation" and bool(data and data.get("isError")))
            event["valid_move"] = int(name in {"propose", "accept_proposal", "reject_proposal", "refuse"}
                                      and bool(data) and not data.get("isError", False))
        return result
