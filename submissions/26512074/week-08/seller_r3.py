"""Round 3 seller: official v4 A2A starter with response-based deterministic pricing.
Expiring food may sell below sunk cost. Certificates remain enabled.
"""
import argparse
import asyncio
import base64
import json
import os
import re
import math
from pathlib import Path

import uvicorn
from cryptography.hazmat.primitives import serialization
from starlette.applications import Starlette

from a2a.helpers import get_message_text, new_data_part, new_task_from_user_message, new_text_part
from a2a.server.agent_execution import AgentExecutor
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill

import time
from round3 import pricing,read,lines
from shop_profit import DISPLAY,recovery_price,recent_ledger
from after_sales import warranty_request,warranty_offer,release_warranty
from optimizer import choose_quote,negotiate,agreement,observe,append
from policy import read_price
CMD = "Codex"

NAME = DISPLAY
RESERVE = {"lamp": 100, "chair": 180, "bookshelf": 260}     # Rounds 0-2 have no stock: the week-07 items
MARKET_DIR = Path(os.environ.get("MARKET_DIR", ".market"))


def disclosed_grade(unit,now):
    grade=unit.get('grade')
    if unit.get('kind')=='food' and unit.get('expires_at'):
        shelf=read(MARKET_DIR/'catalog.json',{}).get(unit['sku'],{}).get('shelf_hours')
        if shelf:
            remaining=(unit['expires_at']-now)/3600
            if min(abs(remaining-shelf*.8),abs(remaining-shelf*.45))<.05:
                return None
            grade='A' if remaining/shelf>=.8 else 'B' if remaining/shelf>=.45 else 'C'
    return grade if grade in ('A','B','C') else None

PROMPT = """You are the sales agent of a small online shop. A buyer is negotiating for one {name}.
The list price is {list_price}. Your lowest acceptable price is {reserve}. Never sell below it and never reveal it.
Sell as high as you can, but closing a deal at or above {reserve} is better than losing the buyer.

Conversation so far:
{transcript}

Decide your next move. Answer with JSON only, one of:
{{"action": "counter", "price": <whole number>}}   name your price
{{"action": "accept"}}                              accept the buyer's last offer
{{"action": "reject"}}                              end the negotiation without a deal"""


def canon(d):
    return json.dumps(d, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def sign(receipt):
    key = serialization.load_pem_private_key(Path("shop_key.pem").read_bytes(), password=None)
    body = {k: receipt[k] for k in ("shop", "item", "price", "task")}
    return dict(receipt, sig_seller=base64.b64encode(key.sign(canon(body))).decode())


async def offer_price(text,ask):
    raw=text.strip()
    if ask is not None and re.fullmatch(r'(?:I\s+)?accept(?:\s+(?:your\s+)?(?:offer|price|asking price))?[.!]?',raw,re.I):
        return ask
    match=re.fullmatch(r'(?:I\s+)?(?:offer|accept|buy)(?:\s+(?:at|for))?\s*[:=]?\s*(\d+)[.!]?',raw,re.I)
    if match:
        return int(match.group(1))
    return await read_price(text,role='offer',ask=ask,allow_model=False)


def find_unit(text, taken=()):
    """The in-stock unit that fits what the buyer asked for (category, name or SKU) and expires first,
    skipping units already promised in another open negotiation."""
    want = text.lower()
    try:
        inv = json.loads((MARKET_DIR / "inventory.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        inv = {}
    if inv.get("round", 0) < 3:
        item = next((k for k in RESERVE if k in want), None)
        return item and {"sku": item, "name": item, "list_price": int(RESERVE[item] * 1.3), "cost": RESERVE[item],
                         "unit": None, "expires_at": None, "supplied_at": 0}
    units = inv.get("units", [])
    requested = set(re.findall(r'\bG[0-9a-f]{8}\b', text, re.I))
    requested = {s.lower() for s in requested}
    lifetime = re.search(r'(?:at least|last|within|need)\s+(\d+(?:\.\d+)?)\s*(?:hours?|h)\b', want)
    min_hours = float(lifetime.group(1)) if lifetime else 0
    fit = [u for u in units if u["grade"] != "expired" and (u.get("expires_at") or float("inf")) > time.time() and u["unit"] not in taken and
           (u.get('expires_at') is None or u['expires_at']-time.time() >= min_hours*3600) and
           ((u['sku'].lower() in requested) if requested else
            (any(part and part.lower() in want for part in (u.get('category') or '').split('>'))
             or bool(u.get('name')) and u['name'].lower() in want or u['sku'].lower() in want))]
    named = [u for u in fit if u["sku"].lower() in want]
    fit = named or fit                                   # a SKU the buyer named comes first
    grades=set(re.findall(r'\bgrade\s*[:=]?\s*([abc])\b|\b([abc])\s*등급',want,re.I))
    requested_grades={part.upper() for pair in grades for part in pair if part}
    if requested_grades:fit=[u for u in fit if u.get('grade') in requested_grades]
    from strategy_policy import load as quote_policy,database as quote_database
    qp=quote_policy(MARKET_DIR)
    if qp.get('inquiry_price_episodes_enabled') and requested and fit:
        epoch=int(time.time()//qp.get('quote_episode_seconds',180))
        with quote_database(MARKET_DIR) as c:preferred={r['unit'] for r in c.execute('select unit from price_sessions where epoch=?',(epoch,))}
        same_offer=[u for u in fit if u['unit'] in preferred]
        if same_offer:fit=same_offer
    # A stale expensive A unit must not hide an affordable B/C unit of the
    # requested SKU. The actual chosen grade is still certified and disclosed.
    old_goods=[u for u in fit if u.get('kind')=='goods' and time.time()-u.get('supplied_at',time.time())>=21600]
    if requested and old_goods:
        return min(old_goods,key=lambda u:(u['cost'],u['supplied_at']))
    return min(fit, key=lambda u: (u["expires_at"] or float("inf"), u["supplied_at"]), default=None)


class Seller(AgentExecutor):
    def __init__(self):
        self.open = {}  # task id -> {"unit", "ask", "offer", "lines"}
        self.promised = set()
        self.promised_at = {}
        self.last_observation = 0

    def release_stale(self,now):
        for identity,deal in list(self.open.items()):
            if now-deal.get('seen_at',now)>180:
                if deal.get('unit',{}).get('unit'):
                    release_warranty(MARKET_DIR,deal['unit']['unit'])
                del self.open[identity]
        for unit,at in list(self.promised_at.items()):
            if now-at>900:
                self.promised.discard(unit)
                del self.promised_at[unit]

    async def execute(self, context, event_queue):
        task = context.current_task
        if task is None:
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)
        up = TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id)
        say = lambda text: up.new_agent_message(parts=[new_text_part(text)])
        text = get_message_text(context.message) or ""
        from buyer_access import blocked_buyer
        messages=[text]+[get_message_text(m) or '' for m in list(getattr(task,'history',()) or ())[:8]]
        denied=blocked_buyer(messages,getattr(context.message,'metadata',None))
        from strategy_policy import load as access_policy,database as access_database
        if access_policy(MARKET_DIR).get('verified_buyer_access_enabled'):
            from review_policy import identity
            buyer,confidence=identity(MARKET_DIR,messages,getattr(context.message,'metadata',None))
            combined='\n'.join(messages).casefold()
            generic='i want to buy' in combined and 'that is more than i want to pay. i can do' in combined
            with access_database(MARKET_DIR,True) as c:
                c.execute('insert or replace into quote_context values(?,?,?)',(task.id,buyer,confidence))
                if denied or generic:c.execute('insert or replace into access_audit values(?,?,?,?,?)',(task.id,time.time(),'anonymous_refusal_template' if denied and 'anonymous refusal template' in denied else 'explicit_blocked_claim' if denied else 'old_generic_pattern', 'deny' if denied else 'allow',confidence))
        if denied:
            previous=self.open.pop(task.id,None)
            if previous and previous['unit'].get('unit'):release_warranty(MARKET_DIR,previous['unit']['unit'])
            print(json.dumps({'event':'buyer_access_refused','buyer':denied,'task':task.id}),flush=True)
            await up.reject(say('This shop declines new transactions with this buyer. No sale is authorized.'))
            return
        self.release_stale(time.time())
        deal = self.open.get(task.id)

        if deal is None:
            unit = find_unit(text, self.promised | {d["unit"]["unit"] for d in self.open.values() if d["unit"]["unit"]})
            if unit is None:
                missing=set(re.findall(r'\bG[0-9a-f]{8}\b',text,re.I))
                if missing and 'quote only' not in text.lower():
                    from strategy_policy import database
                    with database(MARKET_DIR,True) as c:
                        c.executemany('insert or ignore into unfilled_requests values(?,?,?)',[(task.id,sku,time.time()) for sku in missing])
                print(json.dumps({'event':'seller_unavailable','task':task.id,
                                  'requested_skus':re.findall(r'\bG[0-9a-f]{8}\b',text,re.I)}),flush=True)
                await up.reject(say("Sorry, we have none of that in stock."))
                return
            from profit_strategy import settings
            from after_sales import without_warranty
            requested=(unit.get('kind')=='goods' and warranty_request(text) and 'state price' not in text.lower()
                       and not without_warranty(text))
            from buyer_compare import quoted_warranty
            requested_hours=quoted_warranty(text)
            from strategy_policy import active as flow_active,load as flow_policy
            warranty_grades=flow_policy(MARKET_DIR).get('warranty_grades',['A']) if flow_active(MARKET_DIR) else ['A']
            if requested and unit.get('grade') not in warranty_grades:
                explicit=requested_hours>0 or bool(re.search(r'(?:require|need|want)\b.{0,30}\bwarranty\b|보증.{0,10}필수',text,re.I))
                if explicit:
                    await up.reject(say('Warranty is available for grade A goods only. B/C goods are sold without warranty; request a new no-warranty quote or an available grade A item.'))
                    return
                requested=False  # Asking about available terms is not requesting coverage.
            if not requested_hours and not without_warranty(text):requested_hours=6
            if requested and requested_hours not in (0,3,6,12,24,48,72):
                await up.reject(say('Choose warranty hours: 0, 3, 6, 12, 24 or 48.'))
                return
            automatic=(unit.get('kind')=='goods' and unit.get('grade')=='A'
                       and not without_warranty(text))
            deal = self.open[task.id] = {"unit": unit, "ask": None, "offer": None, "lines": [f"buyer: {text}"], 'seen_at':time.time(),
                                       'warranty_hours':requested_hours if requested else settings()['default_warranty_hours'] if automatic else 0,
                                       'automatic_warranty':bool(automatic and not requested)}
        else:
            deal["lines"].append(f"buyer: {text}")
            if deal['unit'].get('kind')=='goods' and warranty_request(text):
                await up.requires_input(say('To change warranty terms, request a new quote with warranty 0, 3, 6, 12, 24 or 48 hours. Your existing quote remains unchanged.'))
                return
            deal["previous_offer"] = deal["offer"]
            deal["offer"] = await offer_price(text,deal["ask"])
        deal['response_wait_seconds']=max(.1,time.time()-deal.get('seen_at',time.time()))
        deal['seen_at']=time.time()

        u = deal["unit"]
        from strategy_policy import active as strategy_active,warranty_rate
        integrated=strategy_active(MARKET_DIR)
        if integrated:u=dict(u,grade=disclosed_grade(u,time.time()))
        inventory=read(MARKET_DIR/'inventory.json',{})
        ledger=recent_ledger(MARKET_DIR/'ledger.jsonl',time.time())
        if time.time()-self.last_observation>30:
            observe(MARKET_DIR,inventory,ledger,time.time())
            self.last_observation=time.time()
            # Historical experiment records remain available; no review guidance.
        price_plan = recovery_price(u,inventory,ledger,time.time())
        if price_plan is None:
            if u.get('unit'):
                release_warranty(MARKET_DIR,u['unit'])
            self.open.pop(task.id, None)
            await up.reject(say("That unit expired; please request a fresh quote."))
            return
        inquiry=any(marker in deal['lines'][0].lower() for marker in ('quote only','no purchase authorization','견적만'))
        metadata=getattr(context.message,'metadata',None) or {}
        if isinstance(metadata,dict) and metadata.get('buyer'):
            price_plan['peer']=str(metadata['buyer'])[:128]
        if deal.get('warranty_hours') and deal['ask'] is None and not integrated:
            from warranty_risk import statistics,cohort
            from premium import WARRANTY_FEES
            observed_risk=cohort(statistics(MARKET_DIR,time.time()),u['sku'],u.get('grade'),deal['warranty_hours'])
            price_plan['estimated_refund_rate']=observed_risk['refund_rate']
            price_plan['warranty_fee']=WARRANTY_FEES.get(deal['warranty_hours'],0)
        price_plan['warranty_hours']=deal.get('warranty_hours',0) or 0
        price_plan=choose_quote(MARKET_DIR,task.id,u,inventory,price_plan,time.time(),inquiry=inquiry)
        evidence=price_plan.get('pricing_evidence',{})
        if price_plan['ask']<price_plan['floor'] or price_plan['ask']<=0:
            self.open.pop(task.id,None)
            await up.reject(say('A profitable quote is not currently supported for this unit. Please ask for another product.'))
            return
        from premium import WARRANTY_FEES,WARRANTY_MENU
        fee=WARRANTY_FEES.get(deal.get('warranty_hours',0),0)
        if integrated and deal.get('warranty_hours'):
            risk=warranty_rate(MARKET_DIR,u['sku'],u.get('grade'),deal['warranty_hours'])
            fee=risk/(1-risk)
        if price_plan.get('locked_total') is not None:
            fee=max(0,price_plan['locked_total']/max(1,price_plan['ask'])-1)
        uninsured_ask=price_plan['ask']
        from profit_strategy import settings
        high_value=not integrated and u.get('kind')=='goods' and u['list_price']>=settings()['pilot_min_list_price']
        if high_value:
            from optimizer import db
            from premium import terms as premium_terms
            with db(MARKET_DIR) as connection:
                connection.execute('create table if not exists premium_terms(task text primary key,sku text,hours real,fee real)')
                old_terms=connection.execute('select hours,fee from premium_terms where task=?',(task.id,)).fetchone()
            if deal['ask'] is None or old_terms:
                bundle=premium_terms(MARKET_DIR,task.id,u,deal.get('warranty_hours',0),flexible=deal.get('automatic_warranty',False))
                deal['warranty_hours']=bundle['hours'];fee=bundle['fee']
                with db(MARKET_DIR) as connection:
                    connection.execute("update trials set strategy='premium-offensive-v1' where task=? and strategy!='verified-resale-100pct'",(task.id,))
        if deal.get('warranty_hours'):
            hours=deal['warranty_hours']
            cash=read(MARKET_DIR/'inventory.json',{}).get('balance',0)
            funded=warranty_offer(MARKET_DIR,u,math.ceil(price_plan['ask']*(1+fee)),cash,time.time(),hours=hours)
            if not funded and deal.get('automatic_warranty') and deal['ask'] is None:
                deal['warranty_hours']=0
                fee=0
                if high_value:
                    with db(MARKET_DIR) as connection:
                        connection.execute('update premium_terms set hours=0,fee=0 where task=?',(task.id,))
        if deal.get('warranty_hours'):
            hours=deal['warranty_hours']
            price_plan['ask']=math.ceil(price_plan['ask']*(1+fee))
            price_plan['floor']=math.ceil(price_plan['floor']*(1+fee))
            from warranty_risk import locked_price_floor
            risk_floor=0 if integrated else locked_price_floor(MARKET_DIR,task.id,u,hours,time.time(),create=deal['ask'] is None)
            price_plan['floor']=max(price_plan['floor'],risk_floor)
            if risk_floor:price_plan['ask']=max(price_plan['ask'],price_plan['floor'])
            if price_plan['floor']>price_plan['ask']:
                self.open.pop(task.id,None)
                await up.reject(say('The requested warranty cannot be priced profitably for this unit.'))
                return
            if not warranty_offer(MARKET_DIR,u,price_plan['ask'],read(MARKET_DIR/'inventory.json',{}).get('balance',0),time.time(),hours=hours):
                release_warranty(MARKET_DIR,u['unit'])
                self.open.pop(task.id,None)
                await up.reject(say('The requested warranty cannot currently be funded. No purchase is authorized.'))
                return
        if price_plan.get('locked_total') is not None:
            price_plan['ask']=price_plan['locked_total'];price_plan['floor']=min(price_plan['floor'],price_plan['ask'])
        epoch=evidence.get('offer_epoch')
        if integrated and epoch is not None:
            from price_learning import lock_episode_terms
            terms=lock_episode_terms(MARKET_DIR,u,epoch,deal.get('warranty_hours',0),price_plan['ask'],price_plan['floor'])
            price_plan['ask']=terms['total'];price_plan['floor']=terms['floor']
        from liquidity_pricing import apply as liquidity_plan
        if not integrated:
            price_plan=liquidity_plan(MARKET_DIR,task.id,u,inventory,price_plan,
                                     deal.get('warranty_hours',0),deal['ask'] is None,time.time())
        if deal['ask'] is None:
            from bundle_offer import discount
            bundle_floor=price_plan['floor']
            if high_value and not price_plan.get('liquidity'):
                bundle_floor=max(bundle_floor,math.ceil(u['cost']*(1+settings()['premium_min_margin'])),
                                 math.ceil((price_plan.get('market_median') or 0)*settings()['premium_market_floor']))
            bundle={'rate':0} if integrated else discount(text,u['sku'],price_plan['ask'],min(price_plan['ask'],bundle_floor),ledger,time.time())
            if bundle['rate']:
                price_plan['ask']=bundle['price']
                deal['bundle_discount']=bundle
        from extreme_quotes import terms as experiment_terms,is_extreme,negotiation as extreme_negotiation
        experimental=None if integrated else experiment_terms(MARKET_DIR,task.id,dict(u,grade=disclosed_grade(u,time.time())),price_plan['ask'],
            deal.get('warranty_hours',0),deal['ask'] is None,
            lambda quote:not deal.get('warranty_hours') or warranty_offer(MARKET_DIR,u,quote,
                inventory.get('balance',0),time.time(),hours=deal['warranty_hours']))
        extreme=is_extreme(experimental)
        if integrated:extreme=False
        if extreme:
            price_plan['ask']=experimental['effective_quote']
            from optimizer import db
            with db(MARKET_DIR) as connection:
                connection.execute('update trials set strategy=? where task=?',(experimental.get('experiment','pricing_x50_v1'),task.id))
            if deal.get('warranty_hours') and not warranty_offer(MARKET_DIR,u,price_plan['ask'],
                    inventory.get('balance',0),time.time(),hours=deal['warranty_hours']):
                release_warranty(MARKET_DIR,u['unit'])
                self.open.pop(task.id,None)
                await up.reject(say('The existing warranty cannot currently be funded. No sale is authorized.'))
                return
        reserve = price_plan["floor"]
        if integrated and deal['ask'] is None:
            from strategy_feedback import finalize_quote
            finalize_quote(MARKET_DIR,task.id,price_plan['ask'],deal.get('warranty_hours',0))
        move=negotiate(MARKET_DIR,task.id,deal,price_plan,time.time())
        if extreme and not (experimental['price_arm']=='x2' and (move or {}).get('action')=='reject'):
            move=extreme_negotiation(experimental,deal.get('offer'),sum(s.startswith('buyer:') for s in deal['lines'])-1)
        append(MARKET_DIR/'optimization/negotiations.jsonl',
               {'at':time.time(),'task':task.id,'sku':u['sku'],'move':move,'model_calls':0})
        action = (move or {}).get("action")
        if action == "accept" and (deal["offer"] is None or deal["offer"] < reserve):
            print(f"[guard] accept of {deal['offer']} below cost {reserve} -> counter", flush=True)
            action, move = "counter", {"price": deal["ask"] or price_plan["ask"]}
        if action == "accept":
            price = deal["offer"]
            agreement(MARKET_DIR,task.id,price)
            receipt = {"shop": NAME, "item": u["sku"], "price": price, "task": task.id}
            if u["unit"]:
                receipt["unit"] = u["unit"]
            receipt["cert"] = True
            grade=disclosed_grade(u,time.time())
            if grade:
                receipt['claim']=grade
            if deal.get('warranty_hours'):
                if not warranty_offer(MARKET_DIR,u,price,read(MARKET_DIR/'inventory.json',{}).get('balance',0),time.time(),hours=deal['warranty_hours']):
                    if price>(deal.get('ask') or price):
                        from active_offer import mark_infeasible
                        mark_infeasible(MARKET_DIR,task.id,'overbid_infeasible_warranty_capacity')
                    release_warranty(MARKET_DIR,u['unit'])
                    self.open.pop(task.id,None)
                    await up.reject(say('Warranty reserve is unavailable; no sale completed.'))
                    return
                receipt['warranty_hours']=deal['warranty_hours']
            receipt = sign(receipt)
            if deal.get('warranty_hours'):
                release_warranty(MARKET_DIR,u['unit'],issued=True,price=price,now=time.time())
            if u["unit"]:
                self.promised.add(u["unit"])
                self.promised_at[u['unit']]=time.time()
            await up.add_artifact(parts=[new_data_part(receipt)], name="receipt")
            await up.complete(say(f"Deal: {u['name']} at {price}."))
            print(json.dumps({'event':'seller_agreed','task':task.id,'sku':u['sku'],'price':price}),flush=True)
            del self.open[task.id]
            return
        if action == "reject":
            if u.get('unit'):
                release_warranty(MARKET_DIR,u['unit'])
            await up.reject(say("No deal."))
            del self.open[task.id]
            return
        price = (move or {}).get("price")
        if isinstance(price, bool):
            price = None
        if not isinstance(price, (int, float)) or not math.isfinite(price):
            price = deal["ask"] or price_plan["ask"]      # unparsed answer: repeat the last ask
        if price < reserve:                             # the harness may forget the cost; the code does not
            print(f"[guard] ask {price} below cost {reserve} -> {reserve}", flush=True)
            price = reserve
        price=max(reserve,min(price,deal['ask'] or price_plan['ask']))
        initial_quote=deal['ask'] is None
        deal["ask"] = int(price)
        reply = (f"{u['name']}: asking {deal['ask']}. Send your offer as a number." if len(deal["lines"]) == 1
                 else f"Counteroffer: {deal['ask']}.")
        if not deal.get('alternative_shown') and (deal.get('offer') is not None and deal['offer']<reserve
                or re.search(r'expensive|budget|cheaper|비싸|예산|저렴',text,re.I)):
            from turnover import alternative
            other=alternative(u,inventory,self.promised|{d['unit']['unit'] for d in self.open.values() if d['unit'].get('unit')},time.time())
            if other:
                reply+=f" Lower-priced alternative: {other['sku']} {other['name'][:40]}. Request a separate quote for that SKU; this quote remains for {u['sku']}."
                deal['alternative_shown']=True
        grade=disclosed_grade(u,time.time())
        if grade:
            reply+=f' SKU {u["sku"]}, grade {grade}; market certificate at checkout.'
        if initial_quote and epoch is not None:
            remaining=max(0,(epoch+1)*access_policy(MARKET_DIR).get('quote_episode_seconds',180)-time.time())
            reply+=f' This available-unit comparison offer lasts {remaining:.0f} seconds; this task retains its agreed terms.'
        if u.get('expires_at') is not None:
            reply+=f" {max(0,(u['expires_at']-time.time())/3600):.3f} hours of shelf life left."
        if deal.get('warranty_hours'):
            reply+=f' Includes a {deal["warranty_hours"]:g}-hour warranty: market-enforced full refund if the goods break during warranty; the quote includes the warranty surcharge.'
        elif u.get('kind')=='goods' and u.get('grade')=='A':
            reply+=' Optional warranties can be requested in a new quote.'
        elif u.get('kind')=='goods' and integrated:
            reply+=' Optional grade-specific, funded warranty is available in a new quote; no fee is added without agreement.'
        elif u.get('kind')=='goods':
            reply+=' Grade B/C goods are sold without warranty.'
        if u.get('kind')=='goods' and u.get('grade')=='A' and len(deal['lines'])==1:
            if deal.get('warranty_hours') and not extreme:
                reply+=' A separate no-warranty quote is available; its total is confirmed in that quote.'
            if integrated:
                reply+=' Warranty options: 0, 3, 6, 12, 24, 48 or 72 hours. A new quote states the total price. Breakage risk is assessed over the market six-hour failure window.'
            else:reply+=' Warranty options (base price surcharge): 0h +0%, 3h +8%, 6h +15%, 12h +22%, 24h +35%, 48h +55%. Choose in a new quote; funding approval required.'
        if len(deal['lines'])==1 and not integrated:
            from bundle_offer import PROMO_EN
            reply+=' '+PROMO_EN
            if deal.get('bundle_discount'):
                reply+=f" Applied receipt tier: {deal['bundle_discount']['rate']:.0%}, minimum-price limited."
        from purchase_guidance import quote_suffix
        reply+=quote_suffix(MARKET_DIR,task.id,dict(u,grade=grade),deal['ask'],deal.get('warranty_hours',0),initial_quote)
        deal["lines"].append(f"you: {reply}")
        await up.requires_input(say(reply))

    async def cancel(self, context, event_queue):
        task = context.current_task
        deal=self.open.pop(task.id, None)
        if deal and deal['unit'].get('unit'):
            release_warranty(MARKET_DIR,deal['unit']['unit'])
        await TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id).cancel()


def build(port):
    url = f"http://127.0.0.1:{port}/"
    card = AgentCard(
        name=NAME,
        description="Goods and food from current stock. SKU, grade, total price and optional funded warranty are disclosed in quotes. ACP/AP2 checkout with signed receipts.",
        version="2.0.0",
        default_input_modes=["text/plain"],
        default_output_modes=["application/json", "text/plain"],
        capabilities=AgentCapabilities(streaming=False),
        supported_interfaces=[AgentInterface(protocol_binding="JSONRPC", url=url, protocol_version="1.0")],
        skills=[AgentSkill(id="negotiate", name="Negotiate a purchase",
                           description="SKU stock quotes, grade disclosure, price negotiation, optional warranty and ACP/AP2 settlement with signed receipts.",
                           tags=["shopping", "negotiation"], examples=["I want to buy 종가 알뜰맘 포기김치 10kg (G00001)", "52000"])],
    )
    agent,store=Seller(),InMemoryTaskStore()
    agent.store=store
    from seller_handoff import restore
    restored=restore(agent,store,MARKET_DIR)
    if restored:print(json.dumps({'event':'seller_handoff_restored','tasks':restored}),flush=True)
    from strategy_policy import active,load
    if active(MARKET_DIR):print(json.dumps({'event':'strategy_seller_loaded','version':load(MARKET_DIR)['version'],'pid':os.getpid(),'model_calls':0}),flush=True)
    handler = DefaultRequestHandler(agent_executor=agent, task_store=store, agent_card=card)
    return Starlette(routes=create_agent_card_routes(card) + create_jsonrpc_routes(handler, "/"))


def port_in_use(port):
    import socket,errno
    with socket.socket(socket.AF_INET,socket.SOCK_STREAM) as sock:
        try:sock.bind(('127.0.0.1',port))
        except OSError as exc:
            if exc.errno in (errno.EADDRINUSE,errno.EACCES) or getattr(exc,'winerror',None) in (10048,10013):return True
            raise
    return False


def run_single_seller(port):
    """Let the existing kit watchdog attach without creating a second server."""
    MARKET_DIR.mkdir(parents=True,exist_ok=True)
    with (MARKET_DIR/f'seller-port-{port}.lock').open('a+b') as lock:
        lock.seek(0)
        if lock.read(1)==b'':lock.write(b'0');lock.flush()
        announced=False
        while True:
            lock.seek(0)
            try:
                if os.name=='nt':
                    import msvcrt
                    msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
                else:
                    import fcntl
                    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
                break
            except OSError:
                if not announced:
                    print(json.dumps({'event':'seller_waiting_for_existing_owner','port':port,'pid':os.getpid()}),flush=True);announced=True
                time.sleep(2)
        if port_in_use(port):
            print(json.dumps({'event':'seller_waiting_for_existing_listener','port':port,'pid':os.getpid()}),flush=True)
            while port_in_use(port):time.sleep(2)
        uvicorn.run(build(port),host='127.0.0.1',port=port,log_level='warning')


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9301)
    port = ap.parse_args().port
    run_single_seller(port)
