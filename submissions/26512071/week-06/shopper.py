"""Challenge 2: a harness-driven buyer with a shopping list, one total budget, and a
directory of shops.

    python shops.py &                     # 20 shops, writes directory.json
    python shopper.py --budget 1500 "a wooden bookcase for about 40 books" "an office chair" "a floor lamp"

A2A is code, as in buyer.py: Agent Cards, the A2A-Version header, taskId, CancelTask.
The harness makes the decisions:
  1. plan: from one line per Agent Card (name and the kinds in its skill tags), pick up
     to 3 shops for each item, best first, and the request to send them;
  2. each move: offer or walk, for one negotiation at a time.

State lives in code, in a ledger with one row per item (shop, last ask, my offer,
status, shops already tried). A move prompt shows the whole ledger as a short table
plus the last few messages of the negotiation being decided, never the transcripts of
the others, so the prompt grows with the number of items, not with the conversation.

All items are negotiated at once, but each item with one shop at a time, so it cannot
be bought twice. Budget is reserved in code: an offer may be at most
budget - spent - (my standing offers in the other open negotiations), because the shop
may accept any standing offer.
"""
import argparse
import asyncio
import json
import re
import sys
import tempfile
import uuid
from pathlib import Path

import httpx

HERE = Path(__file__).parent
HARNESS = ["claude", "-p", "--model", "sonnet", "--tools", "", "--setting-sources", "", "--no-session-persistence"]
WORKDIR = tempfile.mkdtemp(prefix="shopper-harness-")  # empty dir: no project instructions
HEADERS = {"A2A-Version": "1.0"}
MAX_SHOPS, MAX_OFFERS, RECENT = 3, 6, 4

PLAN = """You are shopping for these items with one total budget of {budget}:
{items}

Shops in the directory (id: name | what it sells):
{shops}

For each item pick up to {max_shops} shops that sell that kind of thing, best first,
and write the request to send them. The request says what kind of thing it is and only
the sizes, amounts or materials the shopping list itself asks for; do not add features
the list does not mention (shops reject requests they cannot match) or prices.
Answer with one JSON object and nothing else:
{{"plan": [{{"item": <item number>, "shops": ["S1", ...], "request": "<text>"}}, ...]}}
"""

MOVE = """You are buying several items with one total budget of {budget}. Spent so far: {spent}.
Every open offer can be accepted by its shop, so offers are reserved against the budget.

Ledger, one row per item:
{ledger}

Now decide for item {n} ({item}) at {shop}. Latest messages there:
{recent}

You may offer at most {cap} here (budget minus spent minus your standing offers elsewhere).
Leave enough for the items still open. A lower price is better; if this shop will not come
down far enough, walk away and the next shop on the list is tried ({next}).
Answer with one JSON object and nothing else:
{{"action": "offer" | "walk", "price": <integer>, "reason": "<one short sentence>"}}
"""


def log(*a):
    print(*a, flush=True)


async def harness(prompt):
    proc = await asyncio.create_subprocess_exec(
        *HARNESS, cwd=WORKDIR,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    out, _ = await asyncio.wait_for(proc.communicate(prompt.encode()), timeout=150)
    return out.decode()


async def ask_json(tag, prompt, valid):
    for attempt in range(2):
        raw = await harness(prompt)
        log(f"[{tag}] harness: {raw.strip()}")
        m = re.search(r"\{.*\}", raw, re.S)
        try:
            d = json.loads(m.group())
            if isinstance(d, dict) and valid(d):
                return d
        except (AttributeError, TypeError, ValueError):
            pass
        log(f"[{tag}] unusable answer, attempt {attempt + 1}")
    return None


class Shop:
    def __init__(self, sid, card):
        self.id, self.name = sid, card["name"]
        self.url = next(i["url"] for i in card["supportedInterfaces"] if i["protocolBinding"] == "JSONRPC")
        tags = [t for s in card.get("skills", []) for t in s.get("tags", [])]
        self.sells = ", ".join(t for t in tags if t not in ("shopping", "negotiation"))

    async def rpc(self, client, method, params):
        body = {"jsonrpc": "2.0", "id": str(uuid.uuid4())[:8], "method": method, "params": params}
        r = (await client.post(self.url, json=body, headers=HEADERS, timeout=200)).json()
        if "error" in r:
            raise RuntimeError(f"{method} error: {r['error']}")
        return r["result"].get("task", r["result"])

    async def send(self, client, text, task_id=None):
        msg = {"role": "ROLE_USER", "messageId": str(uuid.uuid4()), "parts": [{"text": text}]}
        if task_id:
            msg["taskId"] = task_id
        t = await self.rpc(client, "SendMessage", {"message": msg})
        status = t.get("status", {})
        reply = " ".join(p.get("text", "") for p in status.get("message", {}).get("parts", []))
        return t, status.get("state"), reply


class Shopper:
    def __init__(self, items, budget, shops):
        self.budget, self.shops = budget, shops
        self.rows = [{"n": i + 1, "item": it, "queue": [], "request": it, "shop": None, "task": None,
                      "ask": None, "offer": None, "status": "waiting", "price": None, "tried": [], "deals": 0}
                     for i, it in enumerate(items)]

    def spent(self):
        return sum(r["price"] for r in self.rows if r["price"] is not None)

    def cap(self, row):
        others = sum(r["offer"] or 0 for r in self.rows if r is not row and r["status"] == "open")
        return self.budget - self.spent() - others

    def ledger(self):
        lines = []
        for r in self.rows:
            if r["status"] == "bought":
                state = f"BOUGHT at {r['price']} from {r['shop'].id}"
            elif r["status"] == "open":
                state = f"open at {r['shop'].id}: ask {r['ask']}, my offer {r['offer'] or '-'}"
            else:
                state = r["status"]
            tried = f"; tried {', '.join(r['tried'])}" if r["tried"] else ""
            lines.append(f"{r['n']}. {r['item']} | {state}{tried}")
        return "\n".join(lines)

    async def plan(self):
        items = "\n".join(f"{r['n']}. {r['item']}" for r in self.rows)
        shops = "\n".join(f"{s.id}: {s.name} | {s.sells}" for s in self.shops.values())
        d = await ask_json("plan", PLAN.format(budget=self.budget, items=items, shops=shops, max_shops=MAX_SHOPS),
                           lambda d: isinstance(d.get("plan"), list))
        for p in (d or {}).get("plan", []):
            row = next((r for r in self.rows if r["n"] == p.get("item")), None)
            if row:
                row["queue"] = [self.shops[s] for s in p.get("shops", []) if s in self.shops][:MAX_SHOPS]
                row["request"] = p.get("request") or row["item"]
        for r in self.rows:
            log(f"[plan] {r['n']}. {r['item']} -> {[s.id for s in r['queue']]} \"{r['request']}\"")

    async def buy(self, client, row):
        tag = f"item {row['n']}"
        for shop in row["queue"]:
            row.update(shop=shop, task=None, ask=None, offer=None, status="open")
            t, state, reply = await shop.send(client, row["request"])
            row["task"] = t["id"]
            log(f"[{tag}] {shop.id} <- {state} | {reply}")
            recent = [f"You: {row['request']}", f"Shop: {reply}"]
            for _ in range(MAX_OFFERS):
                if state != "TASK_STATE_INPUT_REQUIRED":
                    break
                nums = re.findall(r"\d+", reply)
                row["ask"] = int(nums[-1]) if nums else row["ask"]
                rest = [s.id for s in row["queue"][row["queue"].index(shop) + 1:]] or ["none left"]
                d = await ask_json(tag, MOVE.format(
                    budget=self.budget, spent=self.spent(), ledger=self.ledger(), n=row["n"], item=row["item"],
                    shop=f"{shop.id} {shop.name}", recent="\n".join(recent[-RECENT:]), cap=self.cap(row),
                    next=", ".join(rest)), lambda d: d.get("action") in ("offer", "walk"))
                cap = self.cap(row)  # recomputed: other negotiations moved while the harness thought
                if d is None or d["action"] == "walk" or cap <= 0:
                    log(f"[{tag}] walk from {shop.id} (cap {cap})")
                    await shop.rpc(client, "CancelTask", {"id": row["task"]})
                    state = "walked"
                    break
                price = int(d.get("price") or 0)
                if price > cap:
                    log(f"[guard] {tag} offer {price} above cap {cap} -> {cap}")
                    price = cap
                row["offer"] = price  # reserved before the await, so other moves see it
                t, state, reply = await shop.send(client, str(price), row["task"])
                log(f"[{tag}] {shop.id} -> {price} <- {state} | {reply}")
                recent += [f"You offer {price}.", f"Shop: {reply}"]
            else:
                if state == "TASK_STATE_INPUT_REQUIRED":
                    await shop.rpc(client, "CancelTask", {"id": row["task"]})
                    state = "walked"
            receipt = next((p["data"] for a in t.get("artifacts", []) for p in a.get("parts", []) if p.get("data")), None)
            if state == "TASK_STATE_COMPLETED" and receipt:
                row.update(status="bought", price=receipt["price"], offer=None, receipt=receipt)
                row["deals"] += 1
                log(f"[{tag}] BOUGHT {receipt.get('item')} from {shop.id} at {receipt['price']}")
                return
            short = {"walked": "walked", "TASK_STATE_REJECTED": "rejected"}.get(state, state)
            row["tried"].append(f"{shop.id} {short}" + (f" at ask {row['ask']}" if row["ask"] else ""))
            row.update(offer=None)
        row["status"] = "failed"
        log(f"[{tag}] FAILED after {[s.id for s in row['queue']]}")


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("items", nargs="+")
    ap.add_argument("--budget", type=int, required=True)
    ap.add_argument("--directory", default=str(HERE / "directory.json"))
    args = ap.parse_args()

    async with httpx.AsyncClient() as client:
        shops = {}
        for i, url in enumerate(json.loads(Path(args.directory).read_text()), 1):
            card = (await client.get(url.rstrip("/") + "/.well-known/agent-card.json", headers=HEADERS)).json()
            shops[f"S{i}"] = Shop(f"S{i}", card)
        log(f"[shopper] {len(shops)} cards, {len(args.items)} items, budget {args.budget}")
        s = Shopper(args.items, args.budget, shops)
        await s.plan()
        await asyncio.gather(*(s.buy(client, r) for r in s.rows))

    log("\n[ledger]\n" + s.ledger())
    total = s.spent()
    checks = {
        "every item bought": all(r["status"] == "bought" for r in s.rows),
        f"total {total} within budget {args.budget}": total <= args.budget,
        "no item bought twice": all(r["deals"] <= 1 for r in s.rows),
    }
    for name, ok in checks.items():
        log(f"{'PASS' if ok else 'FAIL'}  {name}")
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
