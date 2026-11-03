"""Week 06 challenge 1 -- a seller with a large catalog (thousands of items).

The buyer names no item, only a free-text description ("something wooden to
keep about 40 books, not taller than my desk"). Pasting the whole catalog
into every prompt would be slow and expensive, so this file narrows
candidates with a SQLite FTS5 full-text search BEFORE ever calling the
harness, and only ever sends the harness a handful of candidate rows -- never
the whole catalog.

Reuses seller.py's per-turn negotiation functions (ask_harness_open,
ask_harness_turn) unchanged; the only new part here is picking WHICH item's
reserve those functions should negotiate around.

    python catalog_seller.py --port 9997 --catalog catalog.json
"""
import argparse
import json
import re
import sqlite3

import uvicorn
from starlette.applications import Starlette

from a2a.helpers import get_message_text, new_data_part, new_task_from_user_message, new_text_part
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill

from harness import call_harness
from seller import MAX_ROUNDS, ask_harness_open, ask_harness_turn

SHOP_NAME = "Harness Catalog Shop"
CANDIDATES_K = 8


class Catalog:
    def __init__(self, path: str):
        rows = json.load(open(path, encoding="utf-8"))
        self.by_name = {r["name"]: r for r in rows}
        self.con = sqlite3.connect(":memory:")
        self.con.execute("CREATE VIRTUAL TABLE catalog USING fts5(name, description)")
        self.con.executemany(
            "INSERT INTO catalog VALUES (?, ?)", [(r["name"], r["description"]) for r in rows])

    def search(self, query: str, k: int = CANDIDATES_K) -> list[dict]:
        terms = re.findall(r"[a-zA-Z]+", query.lower())
        if not terms:
            return []
        match = " OR ".join(terms)
        cur = self.con.execute(
            "SELECT name FROM catalog WHERE catalog MATCH ? ORDER BY rank LIMIT ?", (match, k))
        return [self.by_name[row[0]] for row in cur.fetchall()]


def ask_harness_pick(description: str, candidates: list[dict]) -> str | None:
    listing = "\n".join(f"  - {c['name']}: {c['description']}" for c in candidates)
    prompt = (
        "You are a furniture shop assistant. A buyer described what they want:\n"
        f'  "{description}"\n\n'
        f"Here are the closest catalog matches (found by search, not the whole catalog):\n{listing}\n\n"
        "Pick the ONE item name that best matches what the buyer described.\n"
        "Respond with EXACTLY one line:\nPICK <exact item name>\n\n"
        "If none of these are a real match, respond with exactly:\nNONE"
    )
    reply = call_harness(prompt)
    m = re.search(r"PICK\s+(.+)", reply)
    if m:
        name = m.group(1).strip()
        if any(c["name"] == name for c in candidates):
            return name
    return None


class CatalogSeller(AgentExecutor):
    def __init__(self, catalog: Catalog):
        self.catalog = catalog
        self.open = {}   # task id -> {"item", "reserve", "ask", "rounds", "history"}

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if task is None:
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)
        up = TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id)
        say = lambda text: up.new_agent_message(parts=[new_text_part(text)])
        text = get_message_text(context.message) or ""
        deal = self.open.get(task.id)

        if deal is None:  # first message: a free-text description, not an item name
            candidates = self.catalog.search(text)
            if not candidates:
                await up.reject(say("Could not find anything matching that description."))
                return
            picked = ask_harness_pick(text, candidates)
            item = self.catalog.by_name.get(picked) if picked else candidates[0]  # guard: fall back to top FTS hit
            reserve = item["reserve"]
            ask, raw = ask_harness_open(item["name"], reserve)
            self.open[task.id] = {
                "item": item["name"], "reserve": reserve, "ask": ask, "rounds": 0,
                "history": [f"[you] {item['name']}: asking {ask}. Send your offer as a number."],
            }
            await up.requires_input(say(f"{item['name']}: asking {ask}. Send your offer as a number."))
            return

        m = re.search(r"\d+", text)
        if m is None:
            await up.requires_input(say("Send your offer as a number."))
            return
        offer, reserve, item = int(m.group()), deal["reserve"], deal["item"]
        deal["history"].append(f"[buyer] {offer}")
        deal["rounds"] += 1

        action, price, raw = ask_harness_turn(item, reserve, deal["history"], offer, deal["ask"])

        # guard: never ask below reserve, never accept below reserve, however the harness answers
        if action == "ACCEPT" and offer >= reserve:
            decision = "ACCEPT"
        elif deal["rounds"] >= MAX_ROUNDS and offer >= reserve:
            decision = "ACCEPT"
        elif action == "REJECT" and deal["rounds"] >= 2:
            decision = "REJECT"
        else:
            decision = "COUNTER"
            new_ask = price if (action in ("COUNTER", "ACCEPT") and price is not None) else deal["ask"]
            deal["ask"] = max(reserve, new_ask, offer + 1 if offer < reserve else reserve)

        if decision == "ACCEPT":
            receipt = {"seller": SHOP_NAME, "item": item, "price": offer}
            await up.add_artifact(parts=[new_data_part(receipt)], name="receipt")
            await up.complete(say(f"Deal: {item} at {offer}."))
            del self.open[task.id]
        elif decision == "REJECT":
            await up.reject(say(f"No deal at {offer}."))
            del self.open[task.id]
        else:
            deal["history"].append(f"[you] {offer} is too low. Counteroffer: {deal['ask']}.")
            await up.requires_input(say(f"{offer} is too low. Counteroffer: {deal['ask']}."))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        self.open.pop(task.id, None)
        await TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id).cancel()


def build(port: int, catalog_path: str):
    catalog = Catalog(catalog_path)
    url = f"http://127.0.0.1:{port}/"
    card = AgentCard(
        name=SHOP_NAME,
        description="Sells thousands of furniture items. Describe what you want; the shop figures out which item.",
        version="1.0.0",
        default_input_modes=["text/plain"],
        default_output_modes=["application/json", "text/plain"],
        capabilities=AgentCapabilities(streaming=True),
        supported_interfaces=[AgentInterface(protocol_binding="JSONRPC", url=url, protocol_version="1.0")],
        skills=[AgentSkill(
            id="negotiate",
            name="Negotiate a purchase",
            description="Describe what you're looking for in free text, then send a price.",
            tags=["shopping", "negotiation", "catalog"],
            examples=["something wooden to keep about 40 books, not taller than my desk", "90"],
        )],
    )
    handler = DefaultRequestHandler(agent_executor=CatalogSeller(catalog), task_store=InMemoryTaskStore(), agent_card=card)
    return Starlette(routes=create_agent_card_routes(card) + create_jsonrpc_routes(handler, "/"))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--catalog", default="catalog.json")
    args = ap.parse_args()
    uvicorn.run(build(args.port, args.catalog), host="127.0.0.1", port=args.port, log_level="warning")
