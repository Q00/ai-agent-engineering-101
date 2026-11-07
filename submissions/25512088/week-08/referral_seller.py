"""Round-3 stock seller with deterministic, public-price referral benefits.

PROGRAM explains the program; STATUS member=<secret> returns membership status.
Purchase: I want to buy <SKU> [ref=<public code>] [member=<private secret>].
Member keys are bearer credentials: transferable, not account authentication.
"""
import argparse
import asyncio
import subprocess
import sys
import base64
from contextlib import asynccontextmanager
import json
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
from referral_program import Program, policy
from referral_campaign import tick
from stock_pricing import public_price, receipt_grade
from seller_strategy import sale_quote, negotiate
from market_intel import Intel
from shop_copy import SHOP_DESCRIPTION, SHOP_DISPLAY_NAME
from growth_offers import offers
from procurement_campaign import settings
from adaptive_control import Control

STATE = Path(os.environ.get('MARKET_DIR', '.market'))


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


def select_unit(text, units, reserved):
    want = re.sub(r'\b(?:ref|member)=[A-Za-z0-9_-]+', '', text).lower()
    valid = [u for u in units if u['unit'] not in reserved and u.get('grade') != 'expired'
             and (not u.get('expires_at') or u['expires_at'] > time.time())]
    named = [u for u in valid if u['sku'].lower() in want]
    fits = named or [u for u in valid if any(str(v).lower() in want for v in
                    [u.get('name'), u.get('category', '').split('>')[-1]] if v)]
    return min(fits, key=lambda u: (u.get('expires_at') or float('inf'), u.get('supplied_at', 0)), default=None)


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
            if text.strip().upper()=='OFFERS' or text.upper().startswith('SHARE '):
                sharing=text.upper().startswith('SHARE ')
                member=program.member(cred['member']) if sharing else None
                if sharing and not member:
                    await up.complete(say('SHARE requires your private member key after settlement. Never publish that key.'))
                    return
                program.growth_event(task.id,'share' if member else 'offers')
                await up.complete(say(json.dumps(offers(STATE,program,intel,ref=member['code'] if member else ''),ensure_ascii=False)))
                return
            if text.strip().upper() == 'PROGRAM':
                p = program.p
                await up.complete(say(
                    f"창립 단골 {p['founder_limit']}명: 결제+리뷰(별점 무관)+거래번호 refs를 첨부한 buyer 포럼 후기. "
                    f"사후 확인 후 {p['ends_at']}까지 일반 견적에서 {p['founder_percent']}% 할인. "
                    "리뷰/포럼 참여 혜택임을 후기에서 밝혀주세요. 구매 후 받은 비공개 키로 STATUS member=<key>. "
                    "공개 추천 코드는 구매문의에 ref=<code>. 직접 추천 보상만 지급하며 중복 할인 없음. "
                    "회원키는 소지자에게 혜택을 주는 비공개 키이므로 공유하지 마세요."))
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
            catalog_path = STATE/'catalog.json'
            catalog = json.loads(catalog_path.read_text()) if catalog_path.exists() else {}
            if inv.get('round', 0) != 3:
                await up.reject(say('This seller requires Round 3 inventory.'))
                return
            # Unsigned negotiations expire; signed promises stay reserved in SQLite.
            self.open = {k: v for k, v in self.open.items() if v['until'] > time.time()}
            deal = self.open.get(task.id)
            if not deal:
                reserved = program.reserved_units() | {v['unit']['unit'] for v in self.open.values()}
                eligible=list(inv['units'])
                unit = select_unit(text, eligible, reserved)
                if not unit:
                    intel.decision('seller_no_stock',dict(task=task.id,**stock_miss(text,inv['units'],reserved,catalog)))
                    available=offers(STATE,program,intel,limit=2)
                    await up.reject(say('No matching stock available. Alternatives: '+json.dumps(available,ensure_ascii=False)))
                    return
                try:
                    q = sale_quote(unit, catalog, program, intel, token=cred['member'], ref=cred['ref'])
                except ValueError:
                    await up.reject(say('Cannot verify this stock grade or expiry.'))
                    return
                deal = dict(unit=unit, quote=q, credentials=cred, until=time.time()+120)
                arm,digest=control.assign('seller-quote-v1',str(task.id))
                control.event('seller','quote_created',{
                    'task':task.id,'sku':unit['sku'],'price':q['price'],'cost':unit['cost'],
                    'kind':unit.get('kind'),'warranty_hours':q.get('warranty_hours',0),
                    'arm':arm,'assignment_digest':digest},task.id)
                program.growth_event(task.id,'quote')
                if cred['ref']:program.growth_event(task.id,'ref_inquiry')
                if q['referrer']:program.growth_event(task.id,'ref_quote')
                self.open[task.id] = deal
            else:
                unit = next((u for u in inv['units'] if u['unit'] == deal['unit']['unit']), None)
                if not unit or unit.get('grade') == 'expired' or (unit.get('expires_at') and unit['expires_at'] <= time.time()):
                    self.open.pop(task.id, None)
                    await up.reject(say('The quoted stock is no longer available.'))
                    return
                try:
                    latest = sale_quote(unit, catalog, program, intel,
                                        token=deal['credentials']['member'], ref=deal['credentials']['ref'])
                except ValueError:
                    self.open.pop(task.id, None)
                    await up.reject(say('Cannot verify this stock grade or expiry.'))
                    return
                # Honor the lower of the promised quote and the current price.
                if latest['price'] < deal['quote']['price']:
                    deal['quote'] = latest
                match = re.fullmatch(r'\s*(?:offer\s*[=:]?\s*)?(\d+)\s*', text, flags=re.I)
                if match and int(match.group(1)) < deal['quote']['price']:
                    deal['quote']=negotiate(unit,deal['quote'],int(match.group(1)),catalog,program,
                        token=deal['credentials']['member'],ref=deal['credentials']['ref'])
                if match and int(match.group(1)) >= deal['quote']['price']:
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
                        secret = program.accept(task.id, unit['unit'], unit['sku'], unit['cost'], q, inventory=inv)
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
                    private = (f' Private provisional member key: {secret}. Save privately; never post it. '
                               'After payment use SHARE member=<key> for current product referral offers.') if secret else ''
                    code=program.purchase_code(task.id)
                    public=(f' Public referral code: {code} (activates after settlement). '
                            f'Friend sends: I want to buy {unit["sku"]} ref={code}. '
                            'Stock and price must be reconfirmed. Direct referrals only; no review or forum post required for referrals.')
                    event={'sku':unit['sku'],'price':q['price'],'cost':unit['cost'],
                           'kind':unit.get('kind'),'warranty_hours':q.get('warranty_hours',0)}
                    intel.decision('seller_signed',event)
                    intel.evolution('seller','signed',event,correlation=task.id)
                    control.event('seller','sale_signed',dict(event,task=task.id),task.id)
                    control.checkpoint('seller',{'quote_policy':'seller-quote-v1'},
                                       {'task':task.id,'sku':unit['sku'],'mutation_id':mutation_id})
                    await up.complete(say(f"Agreed at {q['price']}. Membership activates only after market settlement."+public+private))
                    self.open.pop(task.id, None)
                    return
            q = deal['quote']
            expiry=deal['unit'].get('expires_at')
            conditions=(f"{max(0,int((expiry-time.time())/3600))} h of shelf life left; "
                        if expiry else f"warranty {q.get('warranty_hours',0)} h; ")
            await up.requires_input(say(f"{deal['unit']['name']} ({deal['unit']['sku']}): grade {deal['unit']['grade']}; public price {q['base']}; benefit {q['benefit']}. "
                                       f"{conditions}Send a number to accept. Asking {q['price']}"))
        finally:
            with program.db:
                program.db.execute('CREATE TABLE IF NOT EXISTS quote_holds(task TEXT PRIMARY KEY, unit TEXT, until REAL)')
                program.db.execute('DELETE FROM quote_holds WHERE until<=?',(time.time(),))
                for tid,hold in self.open.items():
                    program.db.execute('INSERT OR REPLACE INTO quote_holds VALUES(?,?,?)',(tid,hold['unit']['unit'],hold['until']))
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
        startup_control=Control(STATE)
        startup_control.event('seller','role_started',{'pid':os.getpid(),'publish':bool(publish)})
        startup_control.checkpoint('seller',{'seller_version':'1.2.0'},{'pid':os.getpid()})
        startup_control.close()
        # The campaign lock prevents duplicate workers; spend stays in SQLite.
        procurement_log = None
        procurement = None
        if settings().get('external_campaign') and os.environ.get('MARKET_SUPERVISED')!='1':
            root = Path(__file__).resolve().parent
            procurement_log = (root/'logs/procurement-worker-20261104.log').open('a')
            procurement = subprocess.Popen([sys.executable, str(root/'procurement_campaign.py'), '--state', str(STATE.resolve())], cwd=root, stdin=subprocess.DEVNULL, stdout=procurement_log, stderr=subprocess.STDOUT, start_new_session=True)
        async def loop():
            while True:
                try:
                    await asyncio.to_thread(tick, STATE, 'shop_key.pem', False)
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
        version='1.2.0', default_input_modes=['text/plain'], default_output_modes=['application/json','text/plain'],
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
