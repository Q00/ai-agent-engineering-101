"""Week 06 challenge 1 — catalog_seller.py: the large-catalog shop as an A2A v1.0 server (parts 5-6).

    python catalog_seller.py --port 9200

The buyer describes what it wants instead of naming an item. The first message (and any answer
to a clarifying question) goes through:
  part 2  harness reads the request into a JSON query          (catalog_search.parse_request)
  part 3  code filters 4,500 items, keeps a diverse top five   (catalog_search.search)
  part 4  curator harness call picks one or asks one question  (catalog_curator.curate)
  part 5  code locks the picked item and its reserve to the task
  part 6  negotiation as in seller.py, on that item only        (HarnessSeller.decide_open / decide_turn)

seller.py is untouched (it is what passed the trade test); this class inherits its harness calls
and reserve guards. The negotiator never sees the catalog or the other candidates.

v4 (challenge 2, design/challenge-2-buyer.md part A): one program, many shops.
    python catalog_seller.py --port 9301 --name "Lamp Corner" --categories lamp --markup 0.9
  --categories  sell only these categories; a request for anything else is rejected at once
  --markup      every reserve (and so every ask) x this, rounded to 5
  --name        the card name; the card description lists what the shop sells
"""
import argparse
import asyncio
import re

import uvicorn
from starlette.applications import Starlette

from a2a.helpers import get_message_text, new_data_part, new_task_from_user_message, new_text_part
from a2a.server.agent_execution import RequestContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill

from seller import HarnessSeller, MAX_ROUNDS, log
from catalog_search import load_catalog, parse_request, search, is_vague, applied_conditions, CAPACITY_FIELD
from catalog_curator import curate
from make_catalog import r5

NAME = "Harness Furniture Store (25620024)"
ALL_CATEGORIES = list(CAPACITY_FIELD)
MAX_QUESTIONS = 2   # clarifying questions before the shop picks the closest item anyway


def short_spec(item):
    """Item description for the buyer: the id, material, capacity and size. Numbers come BEFORE the
    ask in every reply, so the ask stays the last number in the message."""
    cap_field = CAPACITY_FIELD[item["category"]]
    cap = f", {cap_field} {item[cap_field]}" if cap_field in item else ""
    return f"{item['name']} [{item['id']}] ({item['material']}{cap}, W{item['width_cm']} D{item['depth_cm']} H{item['height_cm']} cm)"


class CatalogSeller(HarnessSeller):
    def __init__(self, name=NAME, categories=None, markup=1.0):
        super().__init__()
        self.name = name
        self.categories = categories or ALL_CATEGORIES
        self.catalog = [dict(it, reserve=r5(it["reserve"] * markup), list_price=r5(it["list_price"] * markup))
                        for it in load_catalog() if it["category"] in self.categories]
        self.pending = {}   # task id -> {"request": str, "questions": int} while the item is not chosen yet

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if task is None:
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)
        up = TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id)
        say = lambda text: up.new_agent_message(parts=[new_text_part(text)])
        text = get_message_text(context.message) or ""
        tid = task.id[:8]

        if task.id in self.open:                       # part 6: the item is locked, negotiate
            await self.negotiate(task, up, say, text, tid)
            return

        pending = self.pending.get(task.id, {"request": "", "questions": 0})
        request = (pending["request"] + " " + text).strip()   # an answer extends the original request
        log(f"[{tid}] request: {request!r}")
        result = await asyncio.to_thread(self.find_item, request, tid, pending["questions"] >= MAX_QUESTIONS)

        if "question" in result:                       # part 4 asked: one clarifying question
            if result.get("none"):
                await up.reject(say(f"Sorry, nothing in our catalog matches that. We sell: {', '.join(self.categories)}."))
                log(f"[{tid}] REJECT no match after {pending['questions']} question(s)")
                self.pending.pop(task.id, None)
                return
            self.pending[task.id] = {"request": request, "questions": pending["questions"] + 1}
            await up.requires_input(say(result["question"]))
            log(f"[{tid}] QUESTION {result['question']!r}")
            return

        item = result["pick"]                          # part 5: lock the item and its reserve
        self.pending.pop(task.id, None)
        ask_price = await self.decide_open(item["name"], item["reserve"], tid)
        reason = result.get("reason", "").strip()
        reply = (f"I can offer the {short_spec(item)}. {reason} "
                 f"Price: asking {ask_price}. Send your offer as a number.")
        self.open[task.id] = {"item": item["name"], "item_id": item["id"], "reserve": item["reserve"],
                              "ask": ask_price, "rounds": 0,
                              "history": [f"buyer: {request}", f"seller: {reply}"]}
        log(f"[{tid}] LOCKED {item['id']} (reserve {item['reserve']}), opening ask {ask_price}")
        await up.requires_input(say(reply))

    def find_item(self, request, tid, must_pick):
        """Parts 2-4. Returns {"pick", "reason"} or {"question"} (with "none" when nothing matches)."""
        q = parse_request(request)
        top, n, relaxed = search(self.catalog, q)
        vague = is_vague(q, n)
        applied = applied_conditions(q, relaxed)
        log(f"[{tid}] query {q}")
        log(f"[{tid}] search: {n} matches" + (f", relaxed {relaxed}" if relaxed else "") + (", VAGUE" if vague else "")
            + f" | top: {', '.join(it['id'] for it in top)}")
        if q.get("category") and q["category"] not in self.categories:   # v4: not sold in this shop (checked before
            # the empty search below, which would otherwise ask a lamp shop's buyer about the sofa)
            log(f"[{tid}] GUARD {q['category']} is not sold here ({', '.join(self.categories)}): rejecting")
            return {"question": "", "none": True}
        if not top:
            if q.get("category") and not must_pick:    # v3: a kind we sell -> ask before giving up (c07)
                log(f"[{tid}] GUARD no match for a {q['category']}: asking instead of rejecting")
                return {"question": f"I couldn't find a {q['category']} like that. Which matters most to you: "
                                    f"the size, the material, or the style?"}
            return {"question": "", "none": True}
        if q.get("category") is None:                  # v2: no furniture category -> never pick an item
            if must_pick:                              # e.g. "a refrigerator": not something this shop sells
                log(f"[{tid}] GUARD no furniture category after {MAX_QUESTIONS} questions: not picking anything")
                return {"question": "", "none": True}
            log(f"[{tid}] GUARD no furniture category: asking which kind of furniture")
            return {"question": f"Which kind of furniture are you looking for? We sell: {', '.join(self.categories)}."}
        if must_pick:                                  # questions used up: closest item, said plainly
            log(f"[{tid}] GUARD {MAX_QUESTIONS} questions asked already: picking the top-ranked {top[0]['id']}")
            return {"pick": top[0], "reason": "This is the closest match to everything you told me."}
        result = curate(request, top, vague, applied)
        if result.get("note"):
            log(f"[{tid}] {result['note']}")
        if "pick" in result:
            log(f"[{tid}] curator picked {result['pick']['id']}: {result.get('reason', '')}")
        return result

    async def negotiate(self, task, up, say, text, tid):
        """Same flow as seller.py's offer handling, on the locked item."""
        deal = self.open[task.id]
        nums = re.findall(r"\d+", text)
        if not nums:
            await up.requires_input(say(f"Send your offer as a number. Current ask: {deal['ask']}."))
            return
        offer = int(nums[0])
        deal["rounds"] += 1
        deal["history"].append(f"buyer: {text}")
        if offer >= deal["ask"]:
            action, price = "accept", offer
            log(f"[{tid}] offer {offer} >= current ask {deal['ask']}: accept without asking the harness")
        else:
            action, price = await self.decide_turn(deal, offer, tid)
        if action == "accept":
            receipt = {"seller": self.name, "item": deal["item"], "item_id": deal["item_id"], "price": offer}
            await up.add_artifact(parts=[new_data_part(receipt)], name="receipt")
            await up.complete(say(f"Deal: {deal['item']} [{deal['item_id']}] at {offer}."))
            log(f"[{tid}] DEAL {deal['item_id']} at {offer} (reserve {deal['reserve']})")
            del self.open[task.id]
            return
        if action == "reject" or deal["rounds"] >= MAX_ROUNDS:
            await up.reject(say("No deal at that price."))
            log(f"[{tid}] REJECT after {deal['rounds']} offers (last offer {offer})")
            del self.open[task.id]
            return
        deal["ask"] = price
        reply = f"{offer} is too low. Counteroffer: {price}."
        deal["history"].append(f"seller: {reply}")
        await up.requires_input(say(reply))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        self.pending.pop(context.current_task.id, None)
        await super().cancel(context, event_queue)


def build(port, name=NAME, categories=None, markup=1.0):
    executor = CatalogSeller(name, categories, markup)
    sells = ", ".join(executor.categories)
    card = AgentCard(
        name=name,
        description=f"A furniture shop with {len(executor.catalog):,} items. Sells: {sells}. "
                    "Describe what you need; the shop picks an item and negotiates.",
        version="1.0.0",
        default_input_modes=["text/plain"],
        default_output_modes=["application/json", "text/plain"],
        capabilities=AgentCapabilities(streaming=True),
        supported_interfaces=[AgentInterface(protocol_binding="JSONRPC", url=f"http://127.0.0.1:{port}/",
                                             protocol_version="1.0")],
        skills=[AgentSkill(
            id="find-and-negotiate",
            name="Find furniture and negotiate a price",
            description=f"Sells {sells}. Describe the furniture you need in your own words (size, use, material, "
                        "room). The shop may ask one question, then offers one item with an asking price; reply with "
                        "offers as numbers.",
            tags=["shopping", "furniture", "negotiation", "catalog"] + executor.categories,
            examples=["something wooden to keep about 40 books, not taller than my desk", "a light so I can read in bed", "120"],
        )],
    )
    handler = DefaultRequestHandler(agent_executor=executor, task_store=InMemoryTaskStore(), agent_card=card)
    return Starlette(routes=create_agent_card_routes(card) + create_jsonrpc_routes(handler, "/"))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9200)
    ap.add_argument("--name", default=NAME)
    ap.add_argument("--categories", default="", help="comma-separated, e.g. lamp,desk (default: all)")
    ap.add_argument("--markup", type=float, default=1.0)
    a = ap.parse_args()
    cats = [c.strip() for c in a.categories.split(",") if c.strip()] or None
    bad = [c for c in cats or [] if c not in ALL_CATEGORIES]
    if bad:
        ap.error(f"unknown categories {bad}; choose from {ALL_CATEGORIES}")
    log(f"{a.name} on http://127.0.0.1:{a.port}  (categories: {a.categories or 'all'}, markup {a.markup}; "
        f"harness: claude -p --model haiku)")
    uvicorn.run(build(a.port, a.name, cats, a.markup), host="127.0.0.1", port=a.port, log_level="warning")
