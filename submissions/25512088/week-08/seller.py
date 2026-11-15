"""Round-3 stock seller with deterministic, evidence-based prices.

Purchase: I want to buy <SKU>. Existing legacy members may append
member=<private secret> until the original promise expires. New referral enrollment is closed.
"""
import argparse
import asyncio
import subprocess
import sys
import base64
from collections import Counter
from contextlib import asynccontextmanager
import json
import hashlib
import os
from pathlib import Path
import re
import sqlite3
import time
import uvicorn
from starlette.applications import Starlette
from a2a.helpers import get_message_text, new_data_part, new_task_from_user_message, new_text_part
from a2a.server.agent_execution import AgentExecutor
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill
from cryptography.hazmat.primitives import serialization
from seller_ledger import Program, policy
from seller_forum import tick
from seller_strategy import sale_quote, negotiate, receipt_grade
from market_intel import Intel, read_json_snapshot
from shop_copy import SHOP_DESCRIPTION, SHOP_DISPLAY_NAME
from seller_offers import offers
from seller_procurement import settings
from adaptive_control import Control

STATE = Path(os.environ.get('MARKET_DIR', '.market'))
CHECKOUT_CONDITION_BUFFER_SECONDS = 60
DEFAULT_UNSPECIFIED_FOOD_HOURS = 48
SELLER_VERSION = '1.3.1'


def sign(receipt):
    key = serialization.load_pem_private_key(Path('shop_key.pem').read_bytes(), password=None)
    body = {k: receipt[k] for k in ('shop', 'item', 'price', 'task')}
    canonical = json.dumps(body, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()
    return dict(receipt, sig_seller=base64.b64encode(key.sign(canonical)).decode())


def credentials(text):
    values = {}
    for key in ('member', 'ref'):
        found = re.search(r'\b'+key+r'=([A-Za-z0-9_-]+)', text)
        values[key] = found.group(1) if found else ''
    return values


def required_food_hours(text):
    # Buyer implementations describe the same official ``use_within``
    # condition in several bounded forms. Parse only a number attached to an
    # explicit shelf-life anchor; unrelated prices, quantities and prompts can
    # never become a condition. The maximum matching requirement wins.
    patterns=(
        r'\bmust\s+last\s+at\s+least\s+(\d+(?:\.\d+)?)\s*(?:h|hours?)\b',
        r'\buse\s+within\s+(\d+(?:\.\d+)?)\s*(?:h|hours?)\b',
        r'\bmin(?:imum)?[_\s-]*hours(?:[_\s-]*left)?\s*[:=]\s*(\d+(?:\.\d+)?)\b',
        r'\b(?:minimum|at\s+least)\s+(\d+(?:\.\d+)?)\s*(?:h|hours?)'
            r'(?:\s+(?:of\s+)?shelf\s*life(?:\s+left)?)?\b',
        r'\bshelf\s*life(?:\s+left)?\s*(?:of|at\s+least|>=|:|=)\s*'
            r'(\d+(?:\.\d+)?)\s*(?:h|hours?)\b',
        r'(?:유통기한|최소)\s*(\d+(?:\.\d+)?)\s*시간(?:\s*이상)?',
    )
    values=[]
    for pattern in patterns:
        for match in re.finditer(pattern,text,flags=re.I):
            try:value=float(match.group(1))
            except ValueError:continue
            if 0<=value<=8760:values.append(value)
    return max(values,default=0.0)


def food_hours_requirement(text, unit=None):
    """Protect checkout when a buyer omits its structured food condition.

    The market checks ``accept.min_hours_left`` only after the seller signs a
    receipt, and some buyers do not repeat that condition in their A2A text.
    Recent official outcomes showed that short-life C receipts consequently
    settled only about half the time.  A bounded 48-hour default covers the
    observed hidden condition while an explicit textual requirement remains
    authoritative, including a shorter one from a buyer that can use the food
    sooner.  Inventory records always carry ``kind``; the optional unit guard
    keeps generic/category-only test fixtures and non-food stock unaffected.
    """
    explicit=required_food_hours(text)
    if explicit:return explicit
    if unit is None or unit.get('kind')=='food':
        return float(DEFAULT_UNSPECIFIED_FOOD_HOURS)
    return 0.0


def is_food(unit):
    return unit.get('kind')=='food' or unit.get('category','').lower().startswith('food')


def select_unit(text, units, reserved, soft_counts=None, prefer_c=False):
    want = re.sub(r'\b(?:ref|member)=[A-Za-z0-9_-]+', '', text).lower()
    explicit_hours=required_food_hours(text);now=time.time()
    def required_hours(unit):
        return explicit_hours or (float(DEFAULT_UNSPECIFIED_FOOD_HOURS)
                                  if unit.get('kind')=='food' else 0.0)
    valid = [u for u in units if u['unit'] not in reserved and u.get('grade') != 'expired'
             and (not u.get('expires_at') or u['expires_at'] > now)
             and (not required_hours(u) or not is_food(u) or
                  (u.get('expires_at') and u['expires_at']-now >=
                   required_hours(u)*3600+CHECKOUT_CONDITION_BUFFER_SECONDS))]
    named = [u for u in valid if u['sku'].lower() in want]
    fits = named or [u for u in valid if any(str(v).lower() in want for v in
                    [u.get('name'), u.get('category', '').split('>')[-1]] if v)]
    soft_counts=soft_counts or {}
    # Lead with the grades that actually settle. The recent mature cohort has
    # B/C converting materially above high-cost A. Food that cannot satisfy a
    # requested remaining-life condition was already removed from ``valid``.
    overstocked=bool(named and sum(u.get('sku')==named[0].get('sku') for u in valid)>=8)
    grade_order=({'C':0,'B':1,'A':2} if prefer_c else {'B':0,'C':1,'A':2})
    return min(fits, key=lambda u: (grade_order.get(u.get('grade'),3),
        u.get('cost',0) if overstocked else 0,soft_counts.get(u['unit'],0),
        u.get('expires_at') or float('inf'),u.get('supplied_at',0)),default=None)


def c_drain_slot(task, units, reserved):
    """Give C stock a bounded, deterministic share of new quotes."""
    live=[u for u in units if u.get('unit') not in reserved and u.get('grade') in ('A','B','C')
          and (not u.get('expires_at') or u['expires_at']>time.time())]
    c=sum(u.get('grade')=='C' for u in live)
    if not live or not c:return False,0.0
    pressure=c/len(live)
    # Keep discovery alive, but protect total deal conversion from C rejection.
    rate=min(.15,max(.03,pressure/2))
    draw=int.from_bytes(hashlib.sha256(str(task).encode()).digest()[:8],'big')/2**64
    return draw<rate,round(rate,4)


def stock_miss(text, units, reserved, catalog):
    """Record canonical demand only, never raw messages or bearer credentials."""
    clean=re.sub(r'\b(?:ref|member)=[A-Za-z0-9_-]+', '', text)
    skus=sorted({sku for sku in catalog if sku.lower() in clean.lower()})[:20]
    categories=sorted({p.get('category','') for p in catalog.values()
                       if p.get('category') and p['category'].split('>')[-1].lower() in clean.lower()})[:20]
    held_match=select_unit(clean,units,set())
    return dict(skus=skus,categories=categories,
                reason='reserved' if held_match and held_match['unit'] in reserved else 'unavailable_or_unmatched',
                inventory_units=len(units),
                reserved_units=sum(u['unit'] in reserved for u in units))


def offer_signal(unit, quote, offered):
    """Bounded numeric demand evidence; never retain message text or credentials."""
    listed=unit.get('list_price') or 0;cost=unit.get('cost') or 0
    return {'sku':unit.get('sku'),'offered':offered,'ask':quote.get('price'),
            'offer_discount_pct':round((1-offered/listed)*100,2) if listed else None,
            'ask_discount_pct':round((1-quote.get('price',0)/listed)*100,2) if listed else None,
            'offer_to_cost':round(offered/cost,4) if cost else None,
            'accepted_immediately':offered>=quote.get('price',0)}


class Seller(AgentExecutor):
    def __init__(self):
        self.open = {}

    async def execute(self, context, event_queue):
        task = context.current_task or new_task_from_user_message(context.message)
        if context.current_task is None:
            await event_queue.enqueue_event(task)
        up = TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id)
        say = lambda s: up.new_agent_message(parts=[new_text_part(s)])
        text = get_message_text(context.message) or ''
        cred = credentials(text)
        program = Program(STATE)
        intel = Intel(STATE)
        control = Control(STATE)
        try:
            if program.get('growth_started_at') is None:
                program.put('growth_started_at',time.time())
            if text.strip().upper()=='OFFERS':
                program.growth_event(task.id,'offers')
                await up.complete(say(json.dumps(offers(STATE,program,intel,ref=''),ensure_ascii=False)))
                return
            if text.upper().startswith('SHARE '):
                await up.complete(say('The referral campaign has ended. Use OFFERS for current stock.'))
                return
            if text.strip().upper() == 'PROGRAM':
                await up.complete(say('The referral campaign has ended. Existing active member promises remain valid until their original expiry; no new memberships or referral rewards are issued.'))
                return
            if text.upper().startswith('STATUS '):
                await up.complete(say(json.dumps(program.status(cred['member']), ensure_ascii=False)))
                return
            # Capture a cursor before any sale can be issued; otherwise the first
            # worker run might skip that sale while initializing at the ledger tail.
            if program.get('cursor') is None:
                await up.reject(say('Market synchronization is starting. Please retry shortly.'))
                return
            inv = json.loads((STATE/'inventory.json').read_text())
            holdings=Counter(u['sku'] for u in inv.get('units',[]))
            catalog_path = STATE/'catalog.json'
            catalog = json.loads(catalog_path.read_text()) if catalog_path.exists() else {}
            preferences = read_json_snapshot(STATE/'buyer-preferences.json',{})
            if inv.get('round', 0) != 3:
                await up.reject(say('This seller requires Round 3 inventory.'))
                return
            # Unsigned negotiations expire; signed promises stay reserved in SQLite.
            self.open = {k: v for k, v in self.open.items() if v['until'] > time.time()}
            deal = self.open.get(task.id)
            if not deal:
                reserved = program.reserved_units()
                soft_counts={}
                for hold in self.open.values():
                    uid=hold['unit']['unit'];soft_counts[uid]=soft_counts.get(uid,0)+1
                eligible=list(inv['units'])
                drain_c,c_rate=c_drain_slot(task.id,eligible,reserved)
                unit = select_unit(text, eligible, reserved, soft_counts, prefer_c=drain_c)
                if not unit:
                    intel.decision('seller_no_stock',dict(task=task.id,**stock_miss(text,inv['units'],reserved,catalog)))
                    available=offers(STATE,program,intel,limit=2)
                    await up.reject(say('No matching stock available. Alternatives: '+json.dumps(available,ensure_ascii=False)))
                    return
                min_hours=food_hours_requirement(text,unit)
                try:
                    q = sale_quote(unit, catalog, program, intel, token=cred['member'], ref='',
                                   holdings=holdings[unit['sku']],buyer_preferences=preferences)
                except ValueError:
                    await up.reject(say('Cannot verify this stock grade or expiry.'))
                    return
                deal = dict(unit=unit, quote=q, credentials=cred, until=time.time()+120,
                            required_food_hours=min_hours)
                arm,digest=control.assign('seller-quote-v1',str(task.id))
                control.event('seller','quote_created',{
                    'task':task.id,'sku':unit['sku'],'price':q['price'],'cost':unit['cost'],
                    'kind':unit.get('kind'),'grade':unit.get('grade'),
                    'c_drain_slot':bool(drain_c and unit.get('grade')=='C'),'c_drain_rate':c_rate,
                    'warranty_hours':q.get('warranty_hours',0),
                    'arm':arm,'assignment_digest':digest},task.id)
                program.growth_event(task.id,'quote')
                self.open[task.id] = deal
            else:
                unit = next((u for u in inv['units'] if u['unit'] == deal['unit']['unit']), None)
                accepted=program.reserved_units()
                if unit and unit['unit'] in accepted:
                    alternatives=[u for u in inv['units'] if u['unit'] not in accepted and
                                  u.get('sku')==unit.get('sku') and u.get('grade')==unit.get('grade') and
                                  u.get('grade')!='expired' and
                                  (not u.get('expires_at') or u['expires_at']>time.time()) and
                                  (not deal.get('required_food_hours') or not is_food(u) or
                                   (u.get('expires_at') and u['expires_at']-time.time()>=
                                    deal['required_food_hours']*3600+CHECKOUT_CONDITION_BUFFER_SECONDS))]
                    unit=min(alternatives,key=lambda u:(u.get('expires_at') or float('inf'),
                             u.get('supplied_at',0)),default=None)
                    if unit:
                        deal['unit']=unit
                        deal['quote']=sale_quote(unit,catalog,program,intel,
                            token=deal['credentials']['member'],ref='',record=False,
                            holdings=holdings[unit['sku']],buyer_preferences=preferences)
                if not unit or unit.get('grade') == 'expired' or (unit.get('expires_at') and unit['expires_at'] <= time.time()):
                    self.open.pop(task.id, None)
                    await up.reject(say('The quoted stock is no longer available.'))
                    return
                if (deal.get('required_food_hours') and is_food(unit) and
                        (not unit.get('expires_at') or unit['expires_at']-time.time() <
                         deal['required_food_hours']*3600)):
                    self.open.pop(task.id,None)
                    await up.reject(say('Food no longer meets the requested minimum shelf life.'))
                    return
                try:
                    # This inquiry was recorded when its initial quote opened.
                    # Refreshes must not inflate demand or conversion trials.
                    latest = sale_quote(unit, catalog, program, intel,
                                        token=deal['credentials']['member'], ref='', record=False,
                                        holdings=holdings[unit['sku']],buyer_preferences=preferences)
                except ValueError:
                    self.open.pop(task.id, None)
                    await up.reject(say('Cannot verify this stock grade or expiry.'))
                    return
                # Honor the lower of the promised quote and the current price.
                if latest['price'] < deal['quote']['price']:
                    deal['quote'] = latest
                match = re.fullmatch(r'\s*(?:offer\s*[=:]?\s*)?(\d+)\s*', text, flags=re.I)
                offered=int(match.group(1)) if match else None
                if offered is not None:
                    signal=offer_signal(unit,deal['quote'],offered)
                    intel.decision('seller_offer_received',signal)
                    control.event('seller','offer_received',dict(signal,task=task.id),task.id)
                if offered is not None and offered < deal['quote']['price']:
                    deal['quote']=negotiate(unit,deal['quote'],offered,catalog,program,
                        token=deal['credentials']['member'],ref='',intel=intel)
                if offered is not None and offered >= deal['quote']['price']:
                    q = deal['quote']
                    claim = receipt_grade(unit, catalog)
                    if claim not in ('A', 'B', 'C'):
                        await up.reject(say('Grade could not be verified. No receipt issued.'))
                        return
                    mutation_id,created=control.prepare_mutation('seller','inventory_reservation',
                        'seller-sale-v1:'+str(task.id),{
                            'task':task.id,'unit':unit['unit'],'sku':unit['sku'],'price':q['price']})
                    if not created:
                        self.open.pop(task.id,None)
                        control.event('seller','duplicate_reservation_blocked',{
                            'task':task.id,'state':control.mutation_state(mutation_id)},task.id)
                        await up.reject(say('This sale is already recorded or awaiting reconciliation.'))
                        return
                    control.move_mutation(mutation_id,'SUBMITTED','local reservation starting')
                    try:
                        program.accept(task.id, unit['unit'], unit['sku'], unit['cost'], q,
                                       inventory=inv, enroll=False)
                        if q.get('book_cushion_recovery'):
                            program.growth_event(task.id,'book_cushion_trial')
                    except (ValueError, sqlite3.IntegrityError):
                        control.move_mutation(mutation_id,'REJECTED','reservation refused')
                        self.open.pop(task.id, None)
                        await up.reject(say('Stock or benefit already reserved. Please request a new quote.'))
                        return
                    terms=dict(shop=program.p['shop_name'], item=unit['sku'], price=q['price'],
                               task=task.id, unit=unit['unit'], cert=True,warranty_hours=q.get('warranty_hours',0))
                    terms['claim'] = claim
                    receipt = sign(terms)
                    control.move_mutation(mutation_id,'CONFIRMED','signed receipt created',{
                        'task':task.id,'sku':unit['sku'],'price':q['price']})
                    await up.add_artifact(parts=[new_data_part(receipt)], name='receipt')
                    event={'sku':unit['sku'],'price':q['price'],'cost':unit['cost'],
                           'kind':unit.get('kind'),'warranty_hours':q.get('warranty_hours',0)}
                    intel.decision('seller_signed',event)
                    intel.evolution('seller','signed',event,correlation=task.id)
                    control.event('seller','sale_signed',dict(event,task=task.id),task.id)
                    control.checkpoint('seller',{'quote_policy':'seller-quote-v1'},
                                       {'task':task.id,'sku':unit['sku'],'mutation_id':mutation_id})
                    await up.complete(say(f"Agreed at {q['price']}. Signed receipt issued; stock and payment are reconciled by the market."))
                    self.open.pop(task.id, None)
                    return
            q = deal['quote']
            expiry=deal['unit'].get('expires_at')
            conditions=(f"{max(0,int((expiry-time.time())/3600))} h of shelf life left; "
                        if expiry else f"warranty {q.get('warranty_hours',0)} h; ")
            await up.requires_input(say(f"{deal['unit']['name']} ({deal['unit']['sku']}): grade {deal['unit']['grade']}; public price {q['base']}; benefit {q['benefit']}. "
                                       f"{conditions}Send a number to accept. Asking {q['price']}"))
        finally:
            program.close()
            intel.close()
            control.close()

    async def cancel(self, context, event_queue):
        task = context.current_task
        self.open.pop(task.id, None)
        program=Program(STATE)
        try:
            if program.db.execute("SELECT 1 FROM sqlite_master WHERE name='quote_holds'").fetchone():
                with program.db:program.db.execute('DELETE FROM quote_holds WHERE task=?',(task.id,))
        finally:program.close()
        await TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id).cancel()


def build(port, publish=True):
    @asynccontextmanager
    async def lifespan(app):
        # Legacy versions wrote quote-only holds. Current quotes are soft and
        # accept() is the only reservation, so clear the old table once at
        # process startup instead of taking a SQLite write lock on every
        # request.
        legacy_program=Program(STATE)
        try:
            if legacy_program.db.execute(
                    "SELECT 1 FROM sqlite_master WHERE name='quote_holds'").fetchone():
                with legacy_program.db:
                    legacy_program.db.execute('DELETE FROM quote_holds')
        finally:
            legacy_program.close()
        startup_control=Control(STATE)
        startup_control.event('seller','role_started',{'pid':os.getpid(),'publish':bool(publish)})
        startup_control.checkpoint('seller',{'seller_version':SELLER_VERSION},{'pid':os.getpid()})
        startup_control.close()
        # The campaign lock prevents duplicate workers; spend stays in SQLite.
        procurement_log = None
        procurement = None
        if settings().get('external_campaign') and os.environ.get('MARKET_SUPERVISED')!='1':
            root = Path(__file__).resolve().parent
            procurement_log = (root/'logs/procurement-worker-20261104.log').open('a')
            procurement = subprocess.Popen([sys.executable, str(root/'seller_procurement.py'), '--state', str(STATE.resolve())], cwd=root, stdin=subprocess.DEVNULL, stdout=procurement_log, stderr=subprocess.STDOUT, start_new_session=True)
        async def loop():
            while True:
                try:
                    await asyncio.to_thread(tick, STATE, 'shop_key.pem', publish)
                except Exception as exc:
                    print('referral synchronization:', type(exc).__name__, flush=True)
                await asyncio.sleep(15)
        worker = asyncio.create_task(loop())
        try:
            yield
        finally:
            if procurement_log is not None:
                procurement_log.close()
            # Keep procurement alive across seller restarts; its durable lock is authoritative.
            worker.cancel()
            try:
                await worker
            except asyncio.CancelledError:
                pass
    card = AgentCard(name=SHOP_DISPLAY_NAME, description=SHOP_DESCRIPTION,
        version=SELLER_VERSION, default_input_modes=['text/plain'], default_output_modes=['application/json','text/plain'],
        capabilities=AgentCapabilities(streaming=False), supported_interfaces=[AgentInterface(
            protocol_binding='JSONRPC', url=f'http://127.0.0.1:{port}/', protocol_version='1.0')],
        skills=[AgentSkill(id='shop', name='Check stock and buy', description='Ask by SKU; reply with a numeric offer on the same task. Returns actual grade, cert=true, applicable shelf-life or evidence-scaled warranty, and a signed receipt.',
                           tags=['shopping','stock','price'], examples=['I want to buy <SKU>', 'PROGRAM'])])
    handler = DefaultRequestHandler(agent_executor=Seller(), task_store=InMemoryTaskStore(), agent_card=card)
    return Starlette(routes=create_agent_card_routes(card)+create_jsonrpc_routes(handler, '/'), lifespan=lifespan)


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=int, default=9301)
    ap.add_argument('--no-post', action='store_true')
    args = ap.parse_args()
    uvicorn.run(build(args.port, publish=not args.no_post), host='127.0.0.1', port=args.port, log_level='warning')
