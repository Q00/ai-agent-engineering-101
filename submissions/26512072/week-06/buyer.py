"""The buyer: an A2A v1.0 client whose offers come from the harness.

  python -u buyer.py --url http://127.0.0.1:9999 --item lamp --budget 120

Reads the seller's Agent Card, sends every request with the A2A-Version: 1.0 header,
continues the negotiation on the task's taskId, and stops when the task ends.
The harness decides every offer and when to walk away (CancelTask). The code only
enforces the budget: no offer above it ever leaves this process.
"""
import argparse
import asyncio
import re
import sys
import uuid

import httpx

import harness

HEADERS = {"A2A-Version": "1.0"}
MAX_ROUNDS = 8

SYSTEM = ("You are a shopping agent that buys items for a person. The budget is a hard limit: "
          "never offer more than it. Answer with exactly one JSON object and nothing else.")
STEP = ("You want to buy: {item}. Your budget (the most you may pay) is {budget}.\n"
        "The seller's asks so far: {asks}. Your offers so far: {offers}.\n"
        'The seller now wrote: "{text}" (current ask: {ask}).\n'
        "This is round {round}. The seller closes the deal as soon as you offer a price it accepts, so only "
        "offer prices you are willing to pay. Start low and move up step by step.\n"
        "Decide: offer a price (at most your budget), accept the current ask (only if it is within your "
        "budget), or walk away if the seller clearly will not come down to your budget.\n"
        'JSON: {{"action": "offer" | "accept" | "walk", "price": <your offer if action is offer>}}')


def log(msg):
    print(msg, flush=True)


def last_number(text):
    nums = re.findall(r"\d+(?:\.\d+)?", text or "")
    return int(float(nums[-1])) if nums else None


class A2AClient:
    """Minimal A2A v1.0 JSON-RPC client: card, SendMessage, CancelTask."""

    def __init__(self, base):
        self.base = base.rstrip("/")
        self.http = httpx.AsyncClient(headers=HEADERS, timeout=200)
        self.rpc_url = None

    async def card(self):
        card = (await self.http.get(self.base + "/.well-known/agent-card.json")).json()
        iface = (card.get("supportedInterfaces") or [{}])[0]
        self.rpc_url = iface.get("url") or self.base + "/"
        return card

    async def _rpc(self, method, params):
        body = {"jsonrpc": "2.0", "id": uuid.uuid4().hex[:8], "method": method, "params": params}
        r = (await self.http.post(self.rpc_url, json=body)).json()
        if "error" in r:
            raise RuntimeError(f"{method} error {r['error'].get('code')}: {r['error'].get('message')}")
        return r["result"].get("task", r["result"])

    async def send(self, text, task_id=None):
        msg = {"role": "ROLE_USER", "messageId": uuid.uuid4().hex, "parts": [{"text": text}]}
        if task_id:
            msg["taskId"] = task_id
        return await self._rpc("SendMessage", {"message": msg})

    async def cancel(self, task_id):
        return await self._rpc("CancelTask", {"id": task_id})

    async def close(self):
        await self.http.aclose()


def state(t):
    return (t or {}).get("status", {}).get("state", "")


def text_of(t):
    return " ".join(p.get("text", "") for p in (t or {}).get("status", {}).get("message", {}).get("parts", []))


def receipt(t):
    for a in (t or {}).get("artifacts", []):
        for p in a.get("parts", []):
            if isinstance(p.get("data"), dict) and "price" in p["data"]:
                return p["data"]
    return None


async def negotiate(client, request, budget, item_label=None, logf=log, tag="buyer", task=None):
    """One negotiation with one seller. Returns {"status": deal|walked|rejected, "price", "asks", "offers"}.
    `task` continues a negotiation that was already opened (challenge 2 opens it to get a quote)."""
    item_label = item_label or request
    asks, offers = [], []
    t = task
    if t is None:
        t = await client.send(f"I want to buy {request}")
        logf(f"[{tag}] -> 'I want to buy {request}'  <- {state(t)} | {text_of(t)}")
    tid = t.get("id")
    for rnd in range(1, MAX_ROUNDS + 1):
        st = state(t)
        if st == "TASK_STATE_COMPLETED":
            r = receipt(t)
            logf(f"[{tag}] DEAL {r}")
            return {"status": "deal", "price": (r or {}).get("price"), "receipt": r, "asks": asks, "offers": offers}
        if st != "TASK_STATE_INPUT_REQUIRED":
            logf(f"[{tag}] task ended: {st}")
            return {"status": "rejected", "price": None, "asks": asks, "offers": offers}
        ask = last_number(text_of(t))
        if ask is not None:
            asks.append(ask)
        d = await harness.decide(SYSTEM, STEP.format(item=item_label, budget=budget, asks=asks[:-1], offers=offers,
                                                     text=text_of(t), ask=ask, round=rnd), logf, f"{tag} harness")
        action = str(d.get("action", "offer")).lower()
        if action == "walk":
            await client.cancel(tid)
            logf(f"[{tag}] walk away -> CancelTask")
            return {"status": "walked", "price": None, "asks": asks, "offers": offers}
        if action == "accept" and ask is not None:
            offer = ask
        else:
            offer = d.get("price") if isinstance(d.get("price"), (int, float)) else (offers[-1] if offers else budget // 2)
            offer = int(offer)
        if ask is not None and offer > ask:
            offer = ask                       # never offer more than the seller asks
        if offer > budget:                    # code guard: the budget is never exceeded
            logf(f"[{tag}] [guard] offer {offer} is above the budget {budget}: offer {budget} instead")
            offer = budget
        if offers and offer <= offers[-1] and offer == budget:
            await client.cancel(tid)          # already offered the whole budget once: nothing left to give
            logf(f"[{tag}] budget {budget} already offered and refused -> CancelTask")
            return {"status": "walked", "price": None, "asks": asks, "offers": offers}
        offers.append(offer)
        t = await client.send(str(offer), tid)
        logf(f"[{tag}] -> {offer}  <- {state(t)} | {text_of(t)}")
    await client.cancel(tid)
    logf(f"[{tag}] {MAX_ROUNDS} rounds without a deal -> CancelTask")
    return {"status": "walked", "price": None, "asks": asks, "offers": offers}


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--item", required=True)
    ap.add_argument("--budget", type=int, required=True)
    args = ap.parse_args()
    log(f"[buyer] {args.item}, budget {args.budget}, seller {args.url}, harness: {harness.NAME}")
    client = A2AClient(args.url)
    try:
        card = await client.card()
        log(f"[buyer] card: {card.get('name')} | {card.get('skills', [{}])[0].get('description')}")
        res = await negotiate(client, f"a {args.item}", args.budget, args.item)
        log(f"[buyer] result: {res['status']} price {res.get('price')}")
    finally:
        await client.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:  # report and exit; the trade test reads the seller's record
        log(f"[buyer] error: {type(e).__name__}: {e}")
        sys.exit(1)
