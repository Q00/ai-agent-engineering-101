"""A2A SDK server with model-driven negotiation and code-enforced reserves."""
import argparse
import asyncio
import base64
import json
from pathlib import Path
import time

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import uvicorn
from starlette.applications import Starlette
from a2a.helpers import get_message_text, new_data_part, new_task_from_user_message, new_text_part
from a2a.server.agent_execution import AgentExecutor
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill

from core import Catalog, SELLER_PROMPT, decide, emit, last_price, validate_seller
from rps_promotion import new_promotion, parse_move, is_game_request, resolve, invitation

ROOT = Path(__file__).resolve().parent
RECEIPT_FIELDS = ('shop', 'item', 'price', 'task')
MARKET_SELLER_PROMPT = SELLER_PROMPT + '''
Market pricing policy: opening asks must not exceed the supplied per-product opening_ceiling.
Prefer a modest 5-10 percent markup, rather than an aggressive initial price.
For an incoming offer at or above the reserve, prefer accepting and closing an honest deal.
Never raise an asking price within a task. Honor any offer meeting your previous ask.
If the first counteroffer is below reserve, promptly quote the reserve as your final minimum.
If the buyer remains below that clearly stated minimum, reject courteously; do not pretend a deal occurred.
Never change a reserve, add fees, promise an impossible discount, request a favorable review,
condition a price on a review, or discuss attacking another shop. Your receipt must match the accepted offer.
The optional RPS promotion is handled by code before your decision. Honor its current asking price.
Never invent game outcomes, rerolls, or an additional discount below reserve. Participation is never required.
The harness renders your action, not your summary, to the buyer. Do not claim explanations were sent
unless the supplied recent messages actually contain them.'''


def canon(data):
    return json.dumps(data, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                      allow_nan=False).encode('utf-8')


def load_signing_key(key_path=ROOT / 'shop_key.pem'):
    key = serialization.load_pem_private_key(Path(key_path).read_bytes(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError('The existing shop key must be Ed25519; it will not be replaced')
    return key


def sign_receipt(receipt, key_path=ROOT / 'shop_key.pem', *, key=None):
    if set(receipt) != set(RECEIPT_FIELDS):
        raise ValueError('Receipt must contain exactly shop, item, price, task')
    if type(receipt['price']) is not int or receipt['price'] < 0:
        raise ValueError('Sign only a nonnegative integer credit price')
    if any(not isinstance(receipt[k], str) or not receipt[k] for k in ('shop', 'item', 'task')):
        raise ValueError('Receipt identity fields must be nonempty strings')
    key = key if key is not None else load_signing_key(key_path)
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError('Ed25519 key required')
    body = {k: receipt[k] for k in RECEIPT_FIELDS}
    return dict(body, sig_seller=base64.b64encode(key.sign(canon(body))).decode('ascii'))


def opening_ceiling(reserve):
    return reserve * 110 // 100


def validate_market_seller(action, candidates, selected, offer, previous_ask):
    action = validate_seller(action, candidates, selected, offer)
    if previous_ask is not None and offer is not None and offer >= previous_ask and action['action'] != 'accept':
        raise ValueError('Honor the previous asking price by accepting this offer')
    if action['action'] == 'ask':
        product = next(p for p in candidates if p['id'] == action['item_id'])
        ceiling = opening_ceiling(product['reserve'])
        if previous_ask is not None:
            ceiling = min(ceiling, previous_ask)
        if action['price'] > ceiling:
            raise ValueError('Ask must not exceed the opening ceiling or increase during negotiation')
        if offer is not None and action['price'] <= offer:
            raise ValueError('Accept the actual offer instead of countering at or below it')
    return action


def ask_message(product, price):
    if price == product['reserve']:
        return '{}; final minimum price {}'.format(product['name'], price)
    return '{}; asking {}'.format(product['name'], price)


class Seller(AgentExecutor):
    def __init__(self, catalog, name, decision=decide, signing_key=None):
        self.catalog, self.name, self.decision = catalog, name, decision
        self.signing_key = signing_key if signing_key is not None else load_signing_key()
        if not isinstance(self.signing_key, Ed25519PrivateKey):
            raise ValueError('Ed25519 key required before starting the shop')
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
            if previous and is_game_request(text):
                promotion = previous.get('promotion')
                move = parse_move(text)
                if promotion is None:
                    reply = 'No discount game is available on this quote.'
                elif promotion['used']:
                    reply = 'This task already used its optional game. Proof: ' + json.dumps(promotion['result'], sort_keys=True)
                elif move is None:
                    reply = 'Optional game syntax: RPS rock, RPS paper, or RPS scissors. Numeric negotiation is also available.'
                else:
                    updated, result = resolve(promotion, move, previous['ask'])
                    previous['promotion'] = dict(updated, result=result)
                    previous['ask'] = result['price']
                    reply = 'Optional game result (buyer perspective): {}. Proof: {}.'.format(
                        result['outcome'], json.dumps(result, sort_keys=True))
                    previous['memory'] = ('Optional game completed; current quote is %s. No purchase confirmed.' % previous['ask'])
                    emit('seller_promotion_played', task_id=task.id, shop=self.name, result=result)
                reply += ' No purchase is made by playing; send a numeric offer to continue. Current asking {}'.format(previous['ask'])
                previous['recent'] = (previous['recent'] + [dict(buyer=text[:500], action='promotion',
                                                               price=previous['ask'], sent_text=reply)])[-2:]
                await up.requires_input(say(reply))
                return
            selected = previous['item_id'] if previous else None
            candidates = [self.catalog.items[selected]] if selected else self.catalog.search(text)
            offer = last_price(text) if selected else None
            state = dict(task_id=task.id, buyer_text=text[:3000], incoming_offer=offer,
                         candidates=candidates, item_id=selected,
                         previous_ask=previous['ask'] if previous else None,
                         rounds=previous['rounds'] if previous else 0,
                         memory=previous['memory'] if previous else '',
                         recent=previous['recent'] if previous else [],
                         pricing_policy=dict(opening_ceiling={p['id']: opening_ceiling(p['reserve']) for p in candidates},
                                             maximum_markup_percent=10, never_raise_ask=True))
            emit('seller_retrieval', task_id=task.id, shop=self.name,
                 catalog_size=len(self.catalog.items), candidate_ids=[p['id'] for p in candidates],
                 candidate_bytes=len(json.dumps(candidates).encode()))
            try:
                action = await asyncio.to_thread(
                    self.decision, MARKET_SELLER_PROMPT, state,
                    lambda a: validate_market_seller(a, candidates, selected, offer, state['previous_ask']),
                    'seller', time.monotonic() + 150, dict(task_id=task.id, shop=self.name))
                validate_market_seller(action, candidates, selected, offer, state['previous_ask'])
                signed_receipt = None
                if action['action'] == 'accept':
                    product = self.catalog.items[selected]
                    signed_receipt = sign_receipt(dict(shop=self.name, item=product['name'],
                                                       price=action['price'], task=task.id), key=self.signing_key)
            except Exception as exc:
                emit('seller_error', task_id=task.id, error=str(exc), kind=type(exc).__name__)
                await up.failed(say('Unable to issue a valid purchase; no receipt was issued.'))
                self.states[task.id] = dict(closed=True)
                return
            reply = None
            promotion = previous.get('promotion') if previous else None
            if action['action'] == 'ask':
                product = self.catalog.items[action['item_id']]
                reply = ask_message(product, action['price'])
                if previous is None and action['price'] > product['reserve']:
                    promotion = new_promotion(task.id, product['reserve'])
                    reply += ' ' + invitation(promotion) + ' Current asking {}'.format(action['price'])
                    emit('seller_promotion_offered', task_id=task.id, shop=self.name,
                         commitment=promotion['commitment'], current_ask=action['price'])
            record = dict(item_id=action['item_id'], ask=action['price'],
                          promotion=promotion,
                          rounds=state['rounds'] + 1, memory=action['summary'],
                          recent=(state['recent'] + [dict(buyer=text[:500], action=action['action'],
                                                       price=action['price'], sent_text=reply)])[-2:],
                          closed=action['action'] != 'ask')
            self.states[task.id] = record
            emit('seller_action', task_id=task.id, shop=self.name, action=action,
                 incoming_offer=offer)
            if action['action'] == 'ask':
                await up.requires_input(say(reply))
            elif action['action'] == 'accept':
                await up.add_artifact(parts=[new_data_part(signed_receipt)], name='receipt')
                await up.complete(say('Purchase completed at {} credits. Signed receipt attached; no added fees. '
                                      'Honest feedback is welcome.'.format(action['price'])))
                emit('seller_receipt', task_id=task.id, shop=self.name, receipt=signed_receipt)
            else:
                await up.reject(say('No mutually acceptable price; no purchase was completed.'))

    async def cancel(self, context, event_queue):
        task = context.current_task
        async with self.locks.setdefault(task.id, asyncio.Lock()):
            self.states[task.id] = dict(closed=True)
            emit('seller_cancel', task_id=task.id, shop=self.name)
            await TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id).cancel()


def build(port, shop_id=None, decision=decide, signing_key=None):
    products = json.loads((ROOT / 'catalog.json').read_text())
    name = 'Household Catalog'
    description = 'Household goods with transparent whole-credit prices, no added fees, and signed receipts.'
    tags = ['shopping', 'negotiation']
    skill = ('Describe an item, then exchange integer offers in credits. '
             'Standard opening-price caps: lamp 110, chair 198, bookshelf 286. '
             'An optional RPS rock/paper/scissors message after selection can discount a quote to its minimum. '
             'Losing or drawing never raises the quote; numeric negotiation does not require a game. '
             'A signed receipt records exactly the agreed price; no added fees.')
    if shop_id:
        shop = next(p for p in json.loads((ROOT / 'shops.json').read_text()) if p['id'] == shop_id)
        products = [dict(p) for p in products if p['category'] == shop['category']]
        for product in products:
            if product['id'] == shop['category']:
                product['reserve'] = shop['reserve']
        name, description, skill = shop['name'], shop['description'], shop['description']
        tags.append(shop['category'])
    url = 'http://127.0.0.1:{}/'.format(port)
    card = AgentCard(name=name, description=description, version='1.2.0',
                     default_input_modes=['text/plain'], default_output_modes=['application/json', 'text/plain'],
                     capabilities=AgentCapabilities(streaming=False),
                     supported_interfaces=[AgentInterface(protocol_binding='JSONRPC', url=url, protocol_version='1.0')],
                     skills=[AgentSkill(id='negotiate', name='Negotiate a purchase', description=skill,
                                        tags=tags, examples=['I want to buy a lamp', '100'])])
    handler = DefaultRequestHandler(agent_executor=Seller(Catalog(products), name, decision, signing_key),
                                    task_store=InMemoryTaskStore(), agent_card=card)
    return Starlette(routes=create_agent_card_routes(card) + create_jsonrpc_routes(handler, '/'))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, required=True)
    parser.add_argument('--shop-id')
    args = parser.parse_args()
    uvicorn.run(build(args.port, args.shop_id), host='127.0.0.1', port=args.port, log_level='warning')
