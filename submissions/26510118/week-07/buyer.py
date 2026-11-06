"""Week 06 -- the buyer, an A2A v1.0 client whose offers come from a harness.

This is what the `a2a` CLI did by hand in the lab, written out:

    a2a card get <url>              ->  GET /.well-known/agent-card.json
    a2a send -a <url> "I want ..."  ->  POST SendMessage
    a2a send --task-id <t> 80       ->  POST SendMessage with taskId

and the two things the CLI was quietly doing for us: the `A2A-Version: 1.0`
header on every POST, and carrying the task id from one message to the next.
Without the header the server reads the request as 0.3 and answers -32009.

The negotiation itself -- the budget guards, the harness's offers, the
receipt check -- is buyer_core.py, shared with market_buyer.py and with the
Cloudflare buyer. This file is the PC's A2A client and the command line the
kit runs in Round 0 and the week-06 trade test runs.

Run:
    python buyer.py http://127.0.0.1:9999 lamp 120
"""
import asyncio
import os
import sys
import uuid

import httpx

import buyer_core
import harness

# The trade test's numbers by default; trading with a classmate's shop across
# a tunnel is slower, so BUYER_HTTP_TIMEOUT relaxes it without touching the test.
TIMEOUT = int(os.environ.get("BUYER_HTTP_TIMEOUT", "60"))
log = buyer_core.log


class A2A:
    """An A2A 1.0 JSON-RPC client over httpx: the card, then SendMessage and
    CancelTask to the card's JSON-RPC interface."""

    def __init__(self):
        self.http = httpx.AsyncClient(timeout=TIMEOUT)
        self.url = None

    async def open(self, base: str) -> dict:
        card = (await self.http.get(base.rstrip("/") + "/.well-known/agent-card.json")).json()
        ifaces = card.get("supportedInterfaces") or [{}]
        # Take the first interface this client can actually speak rather than
        # simply the first one listed: a shop that offers HTTP+JSON only would
        # otherwise be sent JSON-RPC bodies and answer nothing useful.
        iface = next((i for i in ifaces if i.get("protocolBinding") == "JSONRPC"), None)
        if iface is None:
            iface = ifaces[0]
            log(f"[buyer] this shop offers {[i.get('protocolBinding') for i in ifaces]} and not JSONRPC; "
                f"trying {iface.get('protocolBinding')} anyway")
        self.url = iface.get("url") or base
        log(f"[buyer] card: {card.get('name')!r} via {iface.get('protocolBinding')} {self.url}")
        log(f"[buyer] skills: {[s.get('id') for s in card.get('skills') or []]}")
        return card

    async def rpc(self, method: str, params: dict):
        body = {"jsonrpc": "2.0", "id": str(uuid.uuid4())[:8], "method": method, "params": params}
        # The header is the whole reason this client talks to a 1.0 server at
        # all. Every POST carries it, including CancelTask.
        r = (await self.http.post(self.url, json=body, headers={"A2A-Version": "1.0"})).json()
        if "error" in r:
            log(f"[buyer] <- error {r['error'].get('code')} {str(r['error'].get('message'))[:120]}")
            return None
        return r["result"].get("task", r["result"])

    async def send(self, text: str, task_id: str | None = None):
        msg = {"role": "ROLE_USER", "messageId": str(uuid.uuid4()), "parts": [{"text": text}]}
        if task_id:
            msg["taskId"] = task_id
        log(f"[buyer] -> {text!r}" + (f" taskId={task_id[:8]}" if task_id else ""))
        t = await self.rpc("SendMessage", {"message": msg})
        if t:
            log(f"[buyer] <- {buyer_core.state(t)} | {buyer_core.message_text(t)[:120]}")
        return t

    async def cancel(self, task_id: str):
        try:
            await self.rpc("CancelTask", {"name": f"tasks/{task_id}", "id": task_id})
        except Exception as e:                                   # noqa: BLE001
            log(f"[buyer] cancel failed, stopping anyway: {e}")

    async def close(self):
        await self.http.aclose()


async def ask_claude(prompt: str) -> str:
    return await asyncio.to_thread(harness.ask, prompt)


async def amain(base: str, item: str, budget: int) -> int:
    log(f"[buyer] shopping for {item}, budget {budget}, at {base}")
    client = A2A()
    try:
        r = await buyer_core.trade(client, ask_claude, base, item, budget)
    finally:
        await client.close()
    if r["deal"] and r["problem"]:
        log(f"[buyer] the shop closed but its receipt is wrong: {r['problem']}")
    elif r["deal"]:
        log(f"[buyer] bought {item} at {r['price']}")
    else:
        log(f"[buyer] no deal" + (f" ({r['error']})" if r["error"] else ""))
    log(harness.stats())
    return 0


def main() -> int:
    if len(sys.argv) != 4:
        print(__doc__)
        return 2
    return asyncio.run(amain(sys.argv[1], sys.argv[2], int(sys.argv[3])))


if __name__ == "__main__":
    sys.exit(main())
