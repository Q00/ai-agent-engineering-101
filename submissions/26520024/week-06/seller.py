"""A2A SDK server with model-driven negotiation and code-enforced reserves."""
import argparse
import asyncio
import json
from pathlib import Path
import time

import uvicorn
from starlette.applications import Starlette
from a2a.helpers import get_message_text, new_data_part, new_task_from_user_message, new_text_part
from a2a.server.agent_execution import AgentExecutor
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill

from core import Catalog, SELLER_PROMPT, decide, emit, last_price, validate_seller

ROOT = Path(__file__).resolve().parent


class Seller(AgentExecutor):
    def __init__(self, catalog, name, decision=decide):
        self.catalog, self.name, self.decision = catalog, name, decision
        self.states, self.locks = {}, {}

    async def execute(self, context, event_queue):
        task = context.current_task
        if task is None:
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)
        lock = self.locks.setdefault(task.id, asyncio.Lock())
        async with lock:
            up = TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id)
            say = lambda text: up.new_agent_message(parts=[new_text_part(text)])
            previous = self.states.get(task.id)
            if previous and previous['closed']:
                raise RuntimeError('Task already closed')
            text = get_message_text(context.message) or ''
            selected = previous['item_id'] if previous else None
            candidates = [self.catalog.items[selected]] if selected else self.catalog.search(text)
            offer = last_price(text) if selected else None
            state = dict(task_id=task.id, buyer_text=text[:3000], incoming_offer=offer,
                         candidates=candidates, item_id=selected,
                         previous_ask=previous['ask'] if previous else None,
                         rounds=previous['rounds'] if previous else 0,
                         memory=previous['memory'] if previous else '',
                         recent=previous['recent'] if previous else [])
            emit('seller_retrieval', task_id=task.id, shop=self.name,
                 catalog_size=len(self.catalog.items), candidate_ids=[p['id'] for p in candidates],
                 candidate_bytes=len(json.dumps(candidates).encode()))
            try:
                action = await asyncio.to_thread(
                    self.decision, SELLER_PROMPT, state,
                    lambda a: validate_seller(a, candidates, selected, offer),
                    'seller', time.monotonic() + 150, dict(task_id=task.id, shop=self.name))
            except Exception as exc:
                emit('seller_error', task_id=task.id, error=str(exc), kind=type(exc).__name__)
                await up.fail(say('Decision service failed; no purchase was made.'))
                self.states[task.id] = dict(closed=True)
                return
            record = dict(item_id=action['item_id'], ask=action['price'],
                          rounds=state['rounds'] + 1, memory=action['summary'],
                          recent=(state['recent'] + [dict(buyer=text[:500], action=action['action'],
                                                       price=action['price'])])[-2:],
                          closed=action['action'] != 'ask')
            self.states[task.id] = record
            emit('seller_action', task_id=task.id, shop=self.name, action=action,
                 incoming_offer=offer)
            if action['action'] == 'ask':
                product = self.catalog.items[action['item_id']]
                await up.requires_input(say('{}; asking {}'.format(product['name'], action['price'])))
            elif action['action'] == 'accept':
                product = self.catalog.items[selected]
                result = dict(seller=self.name, item=product['name'], item_id=selected, price=action['price'])
                await up.add_artifact(parts=[new_data_part(result)], name='receipt')
                await up.complete(say('Purchase completed.'))
            else:
                await up.reject(say('No mutually acceptable deal.'))

    async def cancel(self, context, event_queue):
        task = context.current_task
        async with self.locks.setdefault(task.id, asyncio.Lock()):
            self.states[task.id] = dict(closed=True)
            emit('seller_cancel', task_id=task.id, shop=self.name)
            await TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id).cancel()


def build(port, shop_id=None, decision=decide):
    products = json.loads((ROOT / 'catalog.json').read_text())
    name, description = 'Household Catalog', 'Negotiates household goods from a searchable catalog.'
    tags = ['shopping', 'negotiation']
    skill = 'Describe an item, then exchange integer offers in credits. A receipt confirms a purchase.'
    if shop_id:
        shop = next(p for p in json.loads((ROOT / 'shops.json').read_text()) if p['id'] == shop_id)
        products = [dict(p) for p in products if p['category'] == shop['category']]
        for product in products:
            if product['id'] == shop['category']:
                product['reserve'] = shop['reserve']
        name, description, skill = shop['name'], shop['description'], shop['description']
        tags.append(shop['category'])
    url = 'http://127.0.0.1:{}/'.format(port)
    card = AgentCard(name=name, description=description, version='1.0.0',
                     default_input_modes=['text/plain'], default_output_modes=['application/json', 'text/plain'],
                     capabilities=AgentCapabilities(streaming=False),
                     supported_interfaces=[AgentInterface(protocol_binding='JSONRPC', url=url, protocol_version='1.0')],
                     skills=[AgentSkill(id='negotiate', name='Negotiate a purchase', description=skill,
                                        tags=tags, examples=['I want to buy a lamp', '100'])])
    handler = DefaultRequestHandler(agent_executor=Seller(Catalog(products), name, decision),
                                    task_store=InMemoryTaskStore(), agent_card=card)
    return Starlette(routes=create_agent_card_routes(card) + create_jsonrpc_routes(handler, '/'))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, required=True)
    parser.add_argument('--shop-id')
    args = parser.parse_args()
    uvicorn.run(build(args.port, args.shop_id), host='127.0.0.1', port=args.port, log_level='warning')
