"""Week 07 -- the shop on the PC: shop_core served through the a2a SDK.

Everything that decides a price is in shop_core.py, shared with the Cloudflare
shop (worker/). This file is what only the PC has: Claude Code as the harness
(harness.py), the key file the kit created (shop_key.pem), the repriced asks
on disk (asks.json, re-read when it changes), the journal file, and the A2A
server around it -- the card, the JSON-RPC routes, the per-task state and the
INPUT_REQUIRED / COMPLETED / REJECTED transitions. The market test and the
trade test start this file.

Run:
    python seller.py --port 9999
"""
import argparse
import asyncio
import json
from pathlib import Path

import uvicorn
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from starlette.applications import Starlette

import catalog
import harness
import market_anchor
import negotiation_memory
import shop_core
from a2a.helpers import get_message_text, new_data_part, new_task_from_user_message, new_text_part
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill

HERE = Path(__file__).resolve().parent
NAME = shop_core.NAME


def load_key(path: Path = HERE / "shop_key.pem"):
    """The kit's key, never a fresh one while it exists: the market verifies
    sig_seller with the public key registered at join."""
    if path.exists():
        return serialization.load_pem_private_key(path.read_bytes(), password=None)
    key = Ed25519PrivateKey.generate()
    path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                       serialization.NoEncryption()))
    print("[seller] created shop_key.pem (do not commit it)", flush=True)
    return key


# Challenge 1: reprice.py reads this shop's own trades and reviews from the
# market ledger and writes asks.json. Re-read whenever it changes, so a reprice
# takes effect without restarting the kit (which would change the tunnel URL).
_ASKS_PATH = HERE / "asks.json"
_asks = {"mtime": None, "asks": {}}


def repriced(item_id: str) -> int | None:
    try:
        mtime = _ASKS_PATH.stat().st_mtime
    except OSError:
        return None
    if mtime != _asks["mtime"]:
        try:
            asks = json.loads(_ASKS_PATH.read_text(encoding="utf-8")).get("asks", {})
        except Exception:                               # noqa: BLE001  (half-written file -> keep the old asks)
            return _asks["asks"].get(item_id)
        _asks.update(mtime=mtime, asks=asks)
        print(f"[seller] asks.json loaded: {asks}", flush=True)
    a = _asks["asks"].get(item_id)
    return int(a) if isinstance(a, (int, float)) else None


async def ask_claude(prompt: str) -> str:
    return await asyncio.to_thread(harness.ask, prompt)


async def market_mean(item_id: str):
    return await asyncio.to_thread(market_anchor.mean, item_id)


class Seller(AgentExecutor):
    def __init__(self, shop: shop_core.Shop):
        self.shop = shop
        # task id -> the deal so far. Per task, because the trade test runs
        # seven negotiations through one shop and a single "current ask" would
        # leak between them.
        self.open: dict[str, dict] = {}

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        for tid in self.shop.stale(self.open):
            self.shop.walked(self.open.pop(tid))
        task = context.current_task
        if task is None:
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)
        up = TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id)

        reply, deal = await self.shop.handle(task.id, get_message_text(context.message) or "",
                                             self.open.get(task.id))
        if deal is None:
            self.open.pop(task.id, None)
        else:
            self.open[task.id] = deal

        if reply["state"] == "input-required":
            await up.requires_input(up.new_agent_message(parts=[new_text_part(reply["text"])],
                                                         metadata={"ask": int(reply["ask"])}))
        elif reply["state"] == "completed":
            await up.add_artifact(parts=[new_data_part(reply["receipt"])], name="receipt")
            await up.complete(up.new_agent_message(parts=[new_text_part(reply["text"])]))
        else:
            await up.reject(up.new_agent_message(parts=[new_text_part(reply["text"])]))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        deal = self.open.pop(task.id, None)
        if deal:
            self.shop.walked(deal)
        await TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id).cancel()


def build(port: int, name: str = NAME, public_url: str | None = None) -> Starlette:
    """The shop. `public_url` is the address the card advertises, which is not
    always the one the server listens on: a client reads the card and then
    talks to `supportedInterfaces[0].url`, so behind a tunnel or on a host the
    card has to name the address the other side can actually reach."""
    url = (public_url.rstrip("/") + "/") if public_url else f"http://127.0.0.1:{port}/"
    catalog.configure()
    description, skill = shop_core.card_text(name)
    card = AgentCard(
        name=name, description=description, version="1.1.0",
        default_input_modes=["text/plain"], default_output_modes=["application/json", "text/plain"],
        capabilities=AgentCapabilities(streaming=True),
        supported_interfaces=[AgentInterface(protocol_binding="JSONRPC", url=url, protocol_version="1.0")],
        skills=[AgentSkill(id="negotiate", name="Find an item and negotiate a purchase", description=skill,
                           tags=["shopping", "negotiation", "furniture"] + catalog.categories(),
                           examples=["I want to buy a lamp", "something wooden to keep about 40 books", "90"])],
    )
    shop = shop_core.Shop(ask_claude, negotiation_memory.file_journal(), key=load_key(), name=name,
                          asks=repriced, market_mean=market_mean)
    handler = DefaultRequestHandler(agent_executor=Seller(shop), task_store=InMemoryTaskStore(),
                                    agent_card=card)
    app = Starlette(routes=create_agent_card_routes(card) + create_jsonrpc_routes(handler, "/"))
    app.state.card_url = url
    return app


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9999)
    ap.add_argument("--name", default=NAME)
    ap.add_argument("--host", default="127.0.0.1",
                    help="address to listen on; 0.0.0.0 to accept other machines")
    ap.add_argument("--public-url", dest="public_url",
                    help="address the Agent Card advertises, when it differs from the "
                         "listening one (a LAN IP, a tunnel, a deployed host)")
    args = ap.parse_args()
    app = build(args.port, args.name, args.public_url)
    print(f"[seller] listening on {args.host}:{args.port}, "
          f"card advertises {app.state.card_url}", flush=True)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
