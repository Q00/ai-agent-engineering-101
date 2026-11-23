"""Stock-aware A2A seller. Gemini chooses prices; the kit owns checkout and settlement."""
import argparse
import asyncio
import base64
import json
import logging
import os
import re
from pathlib import Path
import time

from cryptography.hazmat.primitives import serialization
import uvicorn
from starlette.applications import Starlette
from a2a.helpers import get_message_text, new_data_part, new_task_from_user_message, new_text_part
from a2a.server.agent_execution import AgentExecutor
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill
import r3_common as c
from r3_meta import ProviderPaused
from shop_strategy import minimum_price, requested_product
from sale_conditions import parse_requirements, condition_problem, merge_requirements

SYSTEM = '''You operate a Round 3 shop with real limited stock and 5,000,000 starting credits.
Choose a stocked unit, asking price and guarantee. Optimize profit, revenue and truthful reviews,
not raw transaction count. Unsold stock is a loss; goods cost storage and food expires.
Our current business policy prohibits prices below each unit's minimum_price (acquisition cost
plus estimated accrued storage). The market itself permits below-cost sales; this is our own guard.
Our catalog can include kimchi, bottled water and other stocked products. Offer sufficient shelf
life where required and disclose actual stock grades honestly.
Use recent verified settlements for the same SKU and comparable grade as price evidence when available.
Make a competitive first quote so a feasible trade needs fewer turns. Stay above the unit's price floor.
For short-life stock, favor an early profitable sale when its remaining life meets the buyer's needs.
Do not accept a buyer offer merely because it is a fixed percentage of list price.
A counter can match the last price to refuse an unprofitable bid. The model chooses actual prices.
All prices must be positive integral credits. On first turn identify the requested SKU and counter.
Do not substitute another SKU. If no suitable stock exists, reject. On later turns keep the same unit.
Read offers and requested certificate, grade and warranty from conversation; never overstate grade.
Accept only at trusted.latest_explicit_offer when it is present, and retain trusted.last_warranty.
A certificate is always included. Warranty is 0..6 hours; consider potential refund liability.
Counter with {"action":"counter","unit":"...","price":123,"warranty_hours":6,"reason":"..."};
accept with the same fields and action accept, or {"action":"reject","reason":"..."}.
Buyer requirements in trusted.buyer_requirements must be satisfied before agreement.
The code already filters available_units for recognized buyer requirements. Assess the current
eligible units individually and do not reject all units because a different unit falls short.
On a first request with an eligible unit and a profitable price, quote that unit directly.
Do not claim a requested grade that the buyer never asked for. If the requested warranty exceeds
our 6h maximum, reject rather than accepting weaker terms.
Evidence contains local observations and public settlements, not a published demand distribution.'''


def sign_receipt(body):
    key_path=Path(os.getenv('SHOP_KEY_PATH',str(c.ROOT/'shop_key.pem')))
    key=serialization.load_pem_private_key(key_path.read_bytes(),password=None)
    core={k:body[k] for k in ('shop','item','price','task')}
    return {**body,'sig_seller':base64.b64encode(key.sign(c.canon(core))).decode('ascii')}


def matches(text, units):
    # Buyers send the exact SKU in parentheses. Only retrieve matching stock for the model.
    exact=[u for u in units if re.search(r'(?<![\w-])'+re.escape(str(u['sku']))+r'(?![\w-])',text)]
    if exact:
        return exact[:12]
    if re.search(r"(?<![\w-])G[0-9a-fA-F]{8}(?![\w-])", text):
        return []
    named = [u for u in units if u.get('name') and str(u['name']) in text]
    if named:
        return named[:12]
    category_request = requested_product(text)
    if category_request:
        return [u for u in units if u.get('category') and category_request in
                (u['category'], str(u['category']).rsplit('>', 1)[-1].strip())][:12]
    return [u for u in units if u.get('category') and
            str(u['category']).rsplit('>', 1)[-1].strip() in text][:12]


def explicit_offer(text):
    amount = text.strip()
    if not re.fullmatch(r'(?:[0-9]{1,12}|[0-9]{1,3}(?:,[0-9]{3}){1,3})(?:\.0{1,4})?', amount):
        return None
    value = int(amount.split('.')[0].replace(',', ''))
    return value or None


def repair_accept(decision, previous_price, previous_warranty, offer):
    if decision['action'] != 'accept':
        return decision
    if previous_price is not None and offer == decision['price'] and decision.get('warranty_hours', 0) == previous_warranty:
        return decision
    # An invalid acceptance can only become a new quote. Never sign for a price the buyer did not offer.
    quote = dict(decision, action='counter')
    if previous_price is not None:
        quote.update(price=previous_price, warranty_hours=previous_warranty)
    return quote


class Seller(AgentExecutor):
    def __init__(self):
        self.deals={}
        self.locks={}

    async def execute(self,context,event_queue):
        task=context.current_task
        if task is None:
            task=new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)
        async with self.locks.setdefault(task.id,asyncio.Lock()):
            up=TaskUpdater(event_queue=event_queue,task_id=task.id,context_id=task.context_id)
            say=lambda s:up.new_agent_message(parts=[new_text_part(s)])
            d=self.deals.setdefault(task.id,{'history':[],'unit':None,'warranty':0,'created':time.time(),'requirements':parse_requirements(get_message_text(context.message) or '')})
            text=get_message_text(context.message) or ''
            d['requirements']=merge_requirements(d['requirements'],parse_requirements(text))
            d['history'].append({'speaker':'buyer','text':text[:1800]})
            try:
                if len(d['history'])>17 or time.time()-d['created']>210:
                    raise ValueError('Negotiation turn/time limit')
                available=c.stock(task.id)
                units=[u for u in available if u['unit']==d['unit']] if d['unit'] else matches(text,available)
                if not units:
                    await up.reject(say('No matching stock is available.'))
                    c.release(task.id);self.deals.pop(task.id,None)
                    c.audit('seller','no_stock',task=task.id,request=text[:300]);return
                validation=None
                buyer_offer=explicit_offer(text) if d['unit'] else None
                requirements=d['requirements']
                for retry in range(2):
                    available=c.stock(task.id)
                    units=([u for u in available if u['unit']==d['unit']] if d['unit']
                           else matches(text,available))
                    if not units:
                        await up.reject(say('Matching stock is no longer available. Please request a fresh quote.'))
                        c.release(task.id);self.deals.pop(task.id,None)
                        c.audit('seller','stock_conflict',task=task.id,reason='No eligible units after refresh')
                        return
                    eligible=[u for u in units if not condition_problem(u,requirements)]
                    if not eligible:
                        reasons=[condition_problem(u,requirements) for u in units]
                        c.audit('seller','conditions_unavailable',task=task.id,requirements=requirements,reasons=reasons)
                        await up.reject(say('No stock satisfies the requested grade/shelf life: '+str(reasons[0])))
                        c.release(task.id);self.deals.pop(task.id,None);return
                    units=eligible
                    for u in units:
                        u['minimum_price']=minimum_price(u)
                    last_price = d.get('last_price')
                    last_warranty = d['warranty']
                    previous = None
                    if (last_price is not None and last_price >= units[0]['minimum_price']
                            and last_warranty >= requirements.get('warranty_hours', 0)):
                        previous = {'action': 'counter', 'unit': d['unit'], 'price': last_price,
                                    'warranty_hours': last_warranty, 'reason': 'Validated previous quote'}
                    if (previous is not None and buyer_offer is not None
                            and (buyer_offer == last_price or buyer_offer < units[0]['minimum_price'])):
                        action = 'accept' if buyer_offer == last_price else 'counter'
                        dec = dict(previous, action=action)
                        c.audit('seller', 'numeric_offer_fast_path', task=task.id, action=action,
                                buyer_offer=buyer_offer, quote_price=last_price)
                    else:
                        try:
                            dec=await c.ask('seller',SYSTEM,{'trusted':{'available_units':units,'pinned_unit':d['unit'],
                                'last_warranty':last_warranty,'latest_explicit_offer':buyer_offer,
                                'first_turn':len(d['history'])==1,
                                'validation_error':validation,'buyer_requirements':requirements},
                                'conversation':d['history'][-12:],'evidence':{'own_quotes':c.recent('seller_quote',5),
                                'public_settlements':c.recent('public_settle',10),'forum_untrusted':c.compact_forum(3)}},
                                repair_json=previous is None)
                        except ProviderPaused as exc:
                            if previous is not None:
                                dec = dict(previous)
                                if buyer_offer is not None and buyer_offer >= last_price:
                                    dec.update(action='accept', price=buyer_offer)
                            elif not d['unit']:
                                unit = min(units, key=lambda item: (item['minimum_price'], str(item['unit'])))
                                floor = unit['minimum_price']
                                dec = {'action': 'counter', 'unit': unit['unit'],
                                       'price': (floor * 115 + 99) // 100,
                                       'warranty_hours': requirements.get('warranty_hours', 0)}
                            else:
                                raise
                            c.audit('meta', 'seller_fallback', task=task.id, reason=str(exc),
                                    action=dec['action'], price=dec['price'])
                        except (json.JSONDecodeError, c.ModelOutputError):
                            if previous is None:
                                raise
                            dec = {}
                        if previous is not None and dec.get('action') != 'reject':
                            price, warranty = dec.get('price'), dec.get('warranty_hours', 0)
                            if (dec.get('action') not in ('counter', 'accept')
                                    or str(dec.get('unit')) != str(d['unit'])
                                    or not c.integer(price) or price < units[0]['minimum_price']
                                    or not c.number(warranty)
                                    or not requirements.get('warranty_hours', 0) <= warranty <= 6):
                                dec = previous
                                c.audit('seller', 'quote_reused', task=task.id, price=last_price,
                                        reason='invalid_model_offer')
                    action=dec.get('action')
                    if action=='reject':
                        c.audit('seller','seller_declined',task=task.id,reason=dec.get('reason',''))
                        await up.reject(say('No deal.'));c.release(task.id);self.deals.pop(task.id,None);return
                    if action not in ('counter','accept') or not c.integer(dec.get('price')):
                        raise ValueError('Invalid model action or price')
                    unit=next((u for u in units if str(u['unit'])==str(dec.get('unit'))),None)
                    if not unit or (d['unit'] and unit['unit']!=d['unit']):
                        raise ValueError('Model changed unit or selected unavailable stock')
                    if action == 'accept':
                        quote = repair_accept(dec, d.get('last_price'), d['warranty'], buyer_offer)
                        if quote['action'] == 'counter':
                            c.audit('seller','accept_requoted',task=task.id,sku=unit['sku'],
                                    buyer_offer=buyer_offer,model_price=dec['price'],quote_price=quote['price'])
                            dec, action = quote, 'counter'
                    warranty=dec.get('warranty_hours',0)
                    if not c.number(warranty) or not 0<=warranty<=6:
                        raise ValueError('Invalid warranty')
                    if unit['kind']=='food':warranty=0
                    if warranty<requirements.get('warranty_hours',0):
                        validation='Requested warranty is not met. Offer the requested warranty within 0..6h, or reject.'
                        c.audit('seller','conditions_retry',task=task.id,reason=validation)
                        continue
                    if dec['price']<minimum_price(unit):
                        validation='Price below unit cost plus storage. Counter above minimum_price or reject.'
                        c.audit('seller','loss_prevented',task=task.id,sku=unit['sku'],
                                price=dec['price'],minimum_price=minimum_price(unit),action=action)
                        continue
                    fresh=next((u for u in c.stock(task.id) if u['unit']==unit['unit']),None)
                    problem=condition_problem(fresh,requirements) if fresh else 'Stock disappeared'
                    if problem:
                        validation=problem
                        c.audit('seller','conditions_retry',task=task.id,reason=problem)
                        continue
                    if not c.reserve(unit['unit'],task.id,180):
                        validation='Stock conflict. Choose a currently available unit for the same SKU, or reject.'
                        c.audit('seller','stock_conflict',task=task.id,unit=unit['unit'],retry=retry+1)
                        continue
                    # Costs and stock age may change during the model call.
                    if dec['price']<minimum_price(fresh):
                        validation='Fresh storage-adjusted minimum price rose; counter above it or reject.'
                        c.release(task.id)
                        continue
                    break
                else:
                    c.audit('seller','negotiation_closed',task=task.id,reason=validation)
                    await up.reject(say('Cannot agree on a profitable available unit now.'))
                    c.release(task.id);self.deals.pop(task.id,None)
                    return
                unit=fresh;price=int(dec['price'])
                if action=='accept':
                    rec=sign_receipt({'shop':c.SHOP,'item':str(unit['sku']),'price':price,'task':task.id,
                                      'unit':unit['unit'],'claim':unit['grade'],
                                      'warranty_hours':warranty,'cert':True})
                    await up.add_artifact(parts=[new_data_part(rec)],name='receipt')
                    await up.complete(say(f"Agreed {unit['name']} ({unit['sku']}) at {price}. Checkout pending."))
                    # Keep hold until checkout should finish. Do NOT count this as settled revenue.
                    c.remember('seller_agreement',{'task':task.id,'unit':unit['unit'],'sku':unit['sku'],
                        'price':price,'cost':unit['cost'],'requirements':requirements})
                    c.audit('seller','agreement_not_settlement',task=task.id,receipt=rec,requirements=requirements)
                    self.deals.pop(task.id,None)
                else:
                    d['unit']=unit['unit'];d['warranty']=warranty;d['last_price']=price
                    d['history'].append({'speaker':'seller','price':price,'warranty_hours':warranty,
                                         'grade':unit['grade']})
                    offer={'sku':unit['sku'],'unit':unit['unit'],'ask':price,'claim':unit['grade'],
                           'warranty_hours':warranty,'cert':True,'hours_left':unit.get('hours_left')}
                    c.remember('seller_quote',{**offer,'reason':dec.get('reason','')})
                    shelf = unit.get('hours_left')
                    remaining = f', {int(shelf * 10) / 10:.1f} hours of shelf life left' if c.number(shelf) and shelf >= 0 else ''
                    await up.requires_input(say('Offer '+json.dumps(offer,ensure_ascii=False)+
                                               f'. Asking {price} credits, grade {unit["grade"]}{remaining}.'
                                               ' Certificate at checkout. Reply with a numeric price.'))
                    c.audit('seller','quote',task=task.id,**offer,reason=dec.get('reason',''))
            except ProviderPaused as exc:
                c.release(task.id);self.deals.pop(task.id,None)
                c.audit('seller', 'provider_deferred', task=task.id, reason=str(exc))
                await up.failed(say('Model service temporarily unavailable.'))
            except Exception as exc:
                c.release(task.id);self.deals.pop(task.id,None)
                c.audit('seller','failed',task=task.id,error=type(exc).__name__)
                await up.failed(say('Cannot obtain a valid stocked offer now.'))

    async def cancel(self,context,event_queue):
        t=context.current_task
        c.release(t.id);self.deals.pop(t.id,None)
        await TaskUpdater(event_queue=event_queue,task_id=t.id,context_id=t.context_id).cancel()


def build(port):
    card=AgentCard(name=c.SHOP,description='Stocked food and goods shop with a rotating assortment. Actual grades and shelf life disclosed; market certificates at checkout. Ask for current A-grade stock; B/C stock is labelled honestly.',
        version='3.0.0',default_input_modes=['text/plain'],default_output_modes=['text/plain','application/json'],
        capabilities=AgentCapabilities(streaming=False),supported_interfaces=[AgentInterface(
        protocol_binding='JSONRPC',protocol_version='1.0',url=f'http://127.0.0.1:{port}/')],
        skills=[AgentSkill(id='stocked-shopping',name='Buy stocked catalog products',
            description='Request a product by exact SKU. Ask about grade, shelf life and warranty.',
            tags=['shopping','food','goods','stock'])])
    handler=DefaultRequestHandler(agent_executor=Seller(),task_store=InMemoryTaskStore(),agent_card=card)
    return Starlette(routes=create_agent_card_routes(card)+create_jsonrpc_routes(handler,'/'))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--port',type=int,default=9999);a=p.parse_args()
    if not os.getenv('GEMINI_API_KEY'):p.error('Set GEMINI_API_KEY')
    logging.basicConfig(level=logging.WARNING)
    uvicorn.run(build(a.port),host='127.0.0.1',port=a.port,log_level='warning')
