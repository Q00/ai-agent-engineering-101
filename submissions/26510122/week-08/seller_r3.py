"""A2A shop: calculation-tool evidence, compact memory, model-owned decisions."""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import time

import uvicorn
from cryptography.hazmat.primitives import serialization
from starlette.applications import Starlette
from google.protobuf.json_format import MessageToDict

from a2a.helpers import get_message_text, new_data_part, new_task_from_user_message, new_text_part
from a2a.server.agent_execution import AgentExecutor
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import TaskUpdater
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill

from harness import decide_json, trace, MODEL
from market_memory import (RECEIPT_EVENTS, compact_context, events, record,
                           sync_settlements, trade_attempts)
from newsvendor_tool import price_guidance, quote_candidates
from inventory_observation import requested_skus
from context_compaction import MAX_TURNS
import buyer_identity
import counterparty_memory
from newsvendor_tool import GRADE_ANCHORS
from seller_state import SellerState, SQLiteTaskStore, ResumingContextBuilder
import seller_quality
import seller_requirements
import seller_warranty
from seller_promotion import active_event, discount_quote
import seller_featured
from capital_policy import operating_policy
from market_scoring import VERSION
import decision_audit as audit


NAME = "outseop-26510122"
SELLER_DESCRIPTION = (
    "Brief quotes help save tokens. Negotiable prices; counteroffers welcome. "
    "Clear prices and checkout certificates. Stocked products. "
    "New goods quotes include a 6 h market warranty in the total price. "
    "Numeric offers; signed receipts on agreement."
)
MARKET_DIR = Path(os.environ.get("MARKET_DIR", ".market"))
RESERVE = {"lamp": 100, "chair": 180, "bookshelf": 260}  # pre-Round-3 compatibility
HOLD_SECONDS = 600
FIRST_QUOTE_IDLE_SECONDS = 90
NEGOTIATION_IDLE_SECONDS = 300
NEGOTIATION_MAX_SECONDS = 900
QUOTE_SPEED_TRIAL = Path('state/seller-quote-speed-trial.json')
QUOTE_SPEED_SKUS = frozenset({'G75f11c19', 'Gc2f7811b', 'Gbf98024f', 'G59e3e5ff'})
QUOTE_SPEED_CONTROL_SECONDS = 10


def speed_trial_quote(task_id, requested, choices, public_quotes, requirements, now, path=QUOTE_SPEED_TRIAL):
    if len(requested) != 1 or requested[0] not in QUOTE_SPEED_SKUS:
        return None
    try:
        trial = json.loads(path.read_text())
        start, end = trial['starts_at'], trial['ends_at']
        prices = trial['prices'][requested[0]]
        if not isinstance(prices, dict) or not 0 < end - start <= 3 * 3600 or not start <= now < end:
            return None
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if (active_event(now=now) or {}).get('discount_bps'):
        return None
    featured = {offer['unit'] for offer in public_quotes}
    for unit in choices:
        if unit['sku'] != requested[0] or unit['kind'] != 'goods' or unit['unit'] in featured:
            continue
        price = prices.get(unit['grade'])
        if type(price) is not int or not 0 < price <= 2 * unit['list_price']:
            continue
        floor = seller_warranty.economics(unit, now=now + QUOTE_SPEED_CONTROL_SECONDS,
                                         warranty_hours=seller_warranty.hours(unit))['price_floor']
        budget = requirements.get('budget_max')
        if price >= floor and (budget is None or price <= budget):
            digest = hashlib.blake2b(task_id.encode(), digest_size=1, person=b'quote-speed-v1').digest()
            return ('instant' if digest[0] % 2 else 'control', unit, price)
    return None


def canon(body: dict) -> bytes:
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def sign(receipt: dict) -> dict:
    key = serialization.load_pem_private_key(Path("shop_key.pem").read_bytes(), password=None)
    body = {k: receipt[k] for k in ("shop", "item", "price", "task")}
    return dict(receipt, sig_seller=base64.b64encode(key.sign(canon(body))).decode())


def read_inventory() -> dict:
    try:
        return json.loads((MARKET_DIR / "inventory.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def find_units(text: str, taken: set[str], inventory: dict, *, limit: int | None = 12) -> list[dict]:
    """Only currently available units; exact SKU requests win over broad categories."""
    want = text.lower()
    if inventory.get("round", 0) < 3:
        item = next((name for name in RESERVE if re.search(rf"\b{name}\b", want)), None)
        if item is None:
            return []
        reserve = RESERVE[item]
        return [{"sku": item, "name": item, "category": "pre-round-3",
                 "list_price": round(reserve * 1.3), "cost": reserve, "grade": "A",
                 "kind": "goods", "unit": item, "expires_at": None, "supplied_at": 0}]
    units = inventory.get("units", [])
    def fits(u: dict) -> bool:
        terms = (u.get("sku", "").lower(), u.get("name", "").lower(),
                 (u.get("category") or "").split(">")[-1].lower())
        return any(term and term in want for term in terms)

    matches = [u for u in units if u.get("grade") in ("A", "B", "C")
               and u.get("unit") not in taken and fits(u)]
    named = [u for u in matches if u["sku"].lower() in want]
    if named:
        matches = named
    matches.sort(key=lambda u: (u.get("expires_at") or float("inf"), u.get("supplied_at") or 0))
    return matches[:limit]


def decision_schema(actions: list[str]) -> dict:
    return {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": actions},
            "price": {"type": ["integer", "null"]},
            "unit_id": {"type": ["string", "null"]},
            "reason": {"type": "string"},
        },
        "required": ["action", "price", "unit_id", "reason"],
        "additionalProperties": False,
    }


class Seller(AgentExecutor):
    def __init__(self, state=None) -> None:
        self.state = state or SellerState()
        self.state.import_receipt_holds(events(kinds=RECEIPT_EVENTS), HOLD_SECONDS)
        self.open: dict[str, dict] = self.state.restore_deals()
        self.holds: dict[str, float] = {}  # signed receipts awaiting checkout/settlement
        self.identity_messages: dict[str,float] = {}
        self.task_locks = {}

    def expire_negotiations(self, now=None):
        now=time.monotonic() if now is None else now
        for task_id,deal in list(self.open.items()):
            last=deal.get('last_activity',now)
            opened=deal.get('opened_monotonic',last)
            waiting = deal.get('waiting_for_first_reply', False)
            idle = FIRST_QUOTE_IDLE_SECONDS if waiting else NEGOTIATION_IDLE_SECONDS
            if now-last>=idle or now-opened>=NEGOTIATION_MAX_SECONDS:
                del self.open[task_id]
                self.state.close_deal(task_id)
                record('negotiation_expired',task_id=task_id,sku=deal['unit']['sku'],unit=deal['unit']['unit'],
                       waiting_for_first_reply=waiting,
                       role='seller',episode_id=f'seller:{task_id}',decision_id=deal.get('last_decision_id'),
                       reason='negotiation reservation timed out; payment outcome unknown')

    def refresh_settlements(self):
        try:
            imported = sync_settlements(on_settled=self.state.mark_settled)
            if imported:
                trace('settlements_imported', count=imported)
        except (OSError, sqlite3.Error) as error:
            trace('settlement_sync_error', error=type(error).__name__)

    def available(self, text: str, *, limit: int | None = 12) -> list[dict]:
        self.expire_negotiations()
        inventory = read_inventory()
        stock = {u.get("unit") for u in inventory.get("units", [])}
        now = time.monotonic()
        leases = self.state.active_leases()
        for unit, _, kind, until in leases:
            if kind == 'receipt':
                self.holds[unit] = now + max(0, until-time.time())
        self.holds = {unit: until for unit, until in self.holds.items()
                      if until > now and (not stock or unit in stock)}
        taken = {d["unit"]["unit"] for d in self.open.values()} | set(self.holds) | {r[0] for r in leases}
        settled = self.state.settled_units()
        available = []
        for unit in find_units(text, taken, inventory, limit=None):
            if unit['unit'] in settled:
                record('settled_unit_blocked', sku=unit['sku'], unit=unit['unit'], stage='opening')
            else:
                available.append(unit)
        return available[:limit]

    async def choose(self, prompt: dict, actions: list[str], fallback: dict) -> dict:
        audit.begin(prompt['phase'], policy_version=prompt.get('market_objective',{}).get('meta_version') or 'operator_default')
        source='model'
        try:
            answer = await asyncio.to_thread(decide_json, "seller", prompt, decision_schema(actions))
        except Exception as error:
            trace("model_fallback", phase=prompt["phase"], error=type(error).__name__,
                  reason=str(error)[-400:])
            answer = dict(fallback, reason=f"model unavailable: {type(error).__name__}")
            source='fallback'
        audit.proposed({'action':answer['action'],'price':answer.get('price'),
                        'unit':answer.get('unit_id')},source=source,reason=answer.get('reason'))
        record("model_choice", task_id=prompt["task_id"], phase=prompt["phase"],
               action=answer["action"], price=answer.get("price"),
               unit=answer.get("unit_id"), reason=answer.get("reason"),
               fallback=answer["reason"].startswith("model unavailable:"))
        return answer

    async def execute(self, context, event_queue) -> None:
        key = context.current_task.id if context.current_task else context.message.message_id
        lock = self.task_locks.setdefault(key, asyncio.Lock())
        async with lock:
            with audit.scope('seller',record,score_version=VERSION,model=MODEL):
                try:
                    await self._execute(context, event_queue)
                except Exception as error:
                    audit.executed('unknown',status='failed',reason=type(error).__name__)
                    raise

    async def _execute(self, context, event_queue) -> None:
        task = context.current_task
        new_task = task is None
        if task is None:
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)
        up = TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id)
        audit.bind(task_id=task.id,episode_id=f'seller:{task.id}')
        def say(message):
            record('seller_message',task_id=task.id,text=message)
            return up.new_agent_message(parts=[new_text_part(message)])
        async def reject(message, reason):
            await up.reject(say(message))
            if reason != 'model_reject':audit.guard(reason)
            audit.executed('reject',status='emitted',reason=reason)
        message = get_message_text(context.message) or ""
        record('buyer_message',task_id=task.id,text=message)
        self.refresh_settlements()
        self.expire_negotiations()
        deal = self.open.get(task.id)
        if not new_task and deal is None:
            receipt = self.state.pending_receipt(task.id)
            if receipt is not None:
                current = read_inventory()
                unit = next((u for u in current.get('units', []) if u.get('unit') == receipt['unit']), None)
                saved = self.state.requirements_for(task.id) or seller_requirements.initial('')
                if current.get('round') == 3 and (unit is None or seller_requirements.violations(unit, saved, time.time(), warranty_hours=receipt.get('warranty_hours',0))):
                    await up.reject(say('The saved receipt unit no longer meets the purchase conditions.'))
                    return
                await up.add_artifact(parts=[new_data_part(receipt)], name='receipt')
                await up.complete(say('Previously agreed receipt; price and unit unchanged.'))
                return
            await up.reject(say('This negotiation is no longer active. Please start a new purchase request.'))
            return
        if deal is not None:
            deal['last_activity'] = time.monotonic()
            deal['last_activity_at'] = time.time()
            deal['waiting_for_first_reply'] = False
            deal['requirements'] = seller_requirements.update(deal.get('requirements', seller_requirements.initial('')), message)
            if not self.state.save_deal(task.id, deal, create=False):
                self.open.pop(task.id, None)
                await up.reject(say('That reservation is no longer active. Please start a new request.'))
                return

        if deal is None:
            try:
                catalog = json.loads((MARKET_DIR / 'catalog.json').read_text(encoding='utf-8'))
                if not isinstance(catalog, dict):
                    catalog = {}
            except (OSError, ValueError):
                catalog = {}
            requested = requested_skus(message, catalog)
            public_quotes=seller_featured.active(self.state.path,time.time())
            public_request=seller_featured.requested(message,public_quotes)
            if public_request and not public_request.get('unavailable') and not requested:requested=[public_request['sku']]
            quote_started = time.monotonic()
            record('request_seen', task_id=task.id, requested_skus=requested,
                   forum_offer_id=public_request.get('id') if public_request else None)
            if public_request and (public_request.get('unavailable') or public_request['sku'] not in requested):
                await up.reject(say('That public quote code is expired, unavailable or conflicts with the requested SKU. Please ask for a new quote.'))
                return
            lookup = requested[0] if len(requested) == 1 else message
            choices = self.available(public_request['sku'] if public_request else lookup, limit=None)
            if public_request:choices=[u for u in choices if u['unit']==public_request['unit']]
            else:choices.sort(key=lambda u:not any(o['unit']==u['unit'] for o in public_quotes))
            if not choices:
                record("no_stock", task_id=task.id, request=message[:160], requested_skus=requested)
                await up.reject(say("Sorry, no matching unit is in stock."))
                return
            requirements = seller_requirements.initial(message)
            checked_at = time.time()
            matching = [u for u in choices if not seller_requirements.violations(
                u, requirements, checked_at, warranty_hours=seller_warranty.hours(u))]
            if not matching:
                shortfall = seller_requirements.expiry_shortfall(choices, requested, requirements, checked_at)
                record('requirements_unmet', task_id=task.id, requirements=requirements,
                       requested_skus=requested, expiry_shortfall=shortfall)
                await up.reject(say('No available unit meets the requested expiry, grade or warranty.'))
                return
            choices = matching[:12]
            metadata=context.metadata or MessageToDict(context.message.metadata)
            proofs=counterparty_memory.load_cache().get('proofs',[])
            # The independent worker refreshes this cache. Public-network
            # latency and backlog must not block a customer's opening quote.
            identity=buyer_identity.identify(metadata,recipient=NAME,message_id=context.message.message_id,
                                             task_id=None if new_task else task.id,message=message,proofs=proofs)
            now=time.time()
            self.identity_messages={mid:at for mid,at in self.identity_messages.items() if now-at<300}
            if identity['verified']:
                if context.message.message_id in self.identity_messages:
                    identity={**identity,'status':'replayed_identity_message','verified':False}
                else:
                    if len(self.identity_messages)>=512:self.identity_messages.pop(next(iter(self.identity_messages)))
                    self.identity_messages[context.message.message_id]=now
            record('buyer_identity_observed',task_id=task.id,**identity)
            trial = None if public_request else speed_trial_quote(
                task.id, requested, choices, public_quotes, requirements, now)
            if trial:
                arm, trial_unit, middle = trial
                profiles_by_unit = {}
                answer = {'action': 'ask', 'price': middle, 'unit_id': trial_unit['unit']}
                audit.begin('opening', policy_version='quote-speed-v1')
                audit.proposed({'action': 'ask', 'price': middle, 'unit': trial_unit['unit']}, source='fixed_quote')
                record('quote_speed_trial', task_id=task.id, arm=arm, sku=trial_unit['sku'],
                       grade=trial_unit['grade'], unit=trial_unit['unit'], planned_price=middle)
            else:
                tools = [quote_candidates(u,warranty_hours=seller_warranty.hours(u)) for u in choices]
                for unit,tool in zip(choices,tools):
                    tool['public_quote'] = next(({k:o[k] for k in ('id','price','valid_until','grade')}
                                                for o in public_quotes if o['unit']==unit['unit']),None)
                keys=list(dict.fromkeys((u['sku'],u['grade']) for u in choices))
                per_key=min(3600,12000//len(keys)-64)
                attempts = trade_attempts({sku for sku, _ in keys})
                memories = {f'{sku}:{grade}': compact_context(sku, grade, max_bytes=per_key, attempts=attempts)
                            for sku, grade in keys}
                profile_history = events(kinds=counterparty_memory.PROFILE_EVENTS) if identity['verified'] else ()
                for tool in tools:
                    tool['memory_key']=f"{tool['sku']}:{tool['grade']}"
                profiles={}
                profiles_by_unit={}
                for unit,tool in zip(choices,tools):
                    tool['price_guidance'] = price_guidance(
                        unit, now=now, warranty_hours=seller_warranty.hours(unit), attempts=attempts)
                    remaining=(unit['expires_at']-now)/3600 if unit.get('expires_at') is not None else None
                    bucket='unknown' if remaining is None else 'near_expiry' if remaining<=3 else 'not_near_expiry'
                    key=f"{unit['sku']}:{unit['grade']}:{bucket}"
                    if key not in profiles:
                        profiles[key] = counterparty_memory.profile(
                            identity, unit, history=profile_history, attempts=attempts, now=now)
                    profiles_by_unit[unit['unit']]=profiles[key]
                    tool['buyer_evidence_key']=key
                    if profiles[key]['eligible_for_price_probe']:
                        tool['buyer_price_candidates']=profiles[key]['price_candidates']
                del attempts, profile_history
                prompt = {
                    "phase": "opening", "task_id": task.id, "buyer_message": message[:500],
                    'purchase_requirements': requirements,
                    'warranty_policy':seller_warranty.POLICY,
                    "objective": "Choose one available physical unit and an opening price. "
                                 "Use price_guidance: distinguish successful opening quotes from final paid prices. "
                                 "A paid median is a negotiation outcome, not an instruction to start there and discount again. "
                                 "Compare the historical opening reference, sale profit and settlement chance; "
                                 "A median below the profit floor signals a cost or demand gap. Use the other candidates "
                                 "and market memory when comparable sales are sparse. "
                                 "you may choose another positive price when you explain why. "
                                 "A signed receipt is not a settled sale. Buyer-specific higher quotes are optional "
                                 "experiments ONLY for eligible, market-linked signed identities. Use actual calm "
                                 "high-price settlements under comparable conditions, not a permanent buyer label "
                                 "or an assumed private budget. Unverified/unknown buyers use ordinary evidence. "
                                 "Respect failed or unresolved price probes; never reprice an already agreed receipt.",
                    "unit_options": tools, "memory_by_sku_grade": memories,
                    'buyer_identity':identity,'buyer_price_evidence':profiles,
                    'market_objective':operating_policy(time.time()),
                }
                prompt['objective'] += (' Calculate the ordinary opening quote without anticipating promotions; '
                                        'A public_quote is a binding price ceiling for its physical unit. Prefer a compatible '
                                        'featured unit when useful to the buyer. An explicit quote code narrows the unit choices. '
                                        'a separate tool applies any campaign afterward. Never inflate a base price to offset a discount. '
                                        'Prefer repeatable positive-margin turnover, settled revenue and honest quality over '
                                        'maximizing one markup. Unsold inventory retains acquisition-cost Book credit at round end. '
                                        'Compare selling with holding after storage, spoilage and refund risk; credit is not a cash buyback. '
                                        'Buying inventory does not itself increase Book. Ranking weights are percentiles.')
                middle = tools[0]["candidates"][len(tools[0]["candidates"]) // 2]["price"]
                answer = await self.choose(prompt, ["ask", "reject"],
                                           {"action": "ask", "price": middle, "unit_id": choices[0]["unit"]})
            if answer["action"] == "reject":
                record("rejected", task_id=task.id, reason=answer["reason"])
                await reject("No suitable offer for this request.",'model_reject')
                return
            unit = next((u for u in choices if u["unit"] == answer.get("unit_id")), choices[0])
            if unit['unit'] != answer.get('unit_id'):
                audit.guard('unavailable_model_unit',proposed_unit=answer.get('unit_id'),effective_unit=unit['unit'])
            current = read_inventory()
            if current.get("round", 0) >= 3:
                current_unit = next((u for u in current.get("units", [])
                                     if u.get("unit") == unit["unit"] and u.get("grade") != "expired"), None)
                already_taken = ({d["unit"]["unit"] for d in self.open.values()}
                                 | set(self.holds))
                if (current_unit is None or unit["unit"] in already_taken
                        or any(current_unit.get(k) != unit.get(k)
                               for k in ("grade", "cost", "expires_at"))):
                    record("stale_inventory", task_id=task.id, unit=unit["unit"])
                    await reject("That unit is no longer available. Please try again.",'stale_inventory')
                    return
                unit = current_unit
            warranty_hours = seller_warranty.hours(unit)
            warranty = seller_warranty.economics(unit,now=time.time(),warranty_hours=warranty_hours)
            errors = seller_requirements.violations(unit, requirements, time.time(),warranty_hours=warranty_hours)
            if errors:
                record('requirements_unmet', task_id=task.id, errors=errors)
                await reject('That unit no longer meets the requested conditions. Please start a new request.','requirements_unmet')
                return
            sale_policy = seller_warranty.selling_floor(unit,now=time.time(),warranty_hours=warranty_hours)
            required_floor = sale_policy['price_floor']
            proposed = answer.get("price")
            maximum = max(1, 2 * int(unit["list_price"]))
            if required_floor > maximum:
                await reject('No viable covered quote is available for this unit.','warranty_floor_above_maximum')
                return
            ask = min(maximum, max(1, int(proposed))) if type(proposed) is int else middle
            if ask != proposed:
                audit.guard('price_range_or_type',proposed_price=proposed,effective_price=ask)
            if ask < required_floor:
                audit.guard('selling_price_floor',price_floor=required_floor,policy=sale_policy)
            ask = max(ask,required_floor)
            if ask != proposed or unit["unit"] != answer.get("unit_id"):
                trace("decision_guard", phase="opening", proposed_price=proposed,
                      effective_price=ask, proposed_unit=answer.get("unit_id"),
                      effective_unit=unit["unit"])
            try:promotion=seller_featured.apply(unit,ask,public_quotes,time.time())
            except ValueError:
                await reject('That public quote no longer has a viable unit. Please request another quote.','public_quote_unavailable')
                return
            if public_request and promotion is None:
                await reject('That public quote expired before a quote could be issued. Please request a new quote.','public_quote_expired')
                return
            if promotion is None:promotion = discount_quote(unit, ask,minimum_price=required_floor)
            if promotion:
                if promotion['price'] < required_floor:
                    await reject('That public quote no longer has a viable unit. Please request a fresh quote.','promotion_below_selling_floor')
                    return
                if promotion['price'] > maximum:
                    await reject('No suitable price for this unit.','promotion_above_maximum')
                    return
                ask = promotion['price']
                audit.guard('opening_promotion',ordinary_quote=promotion['ordinary_quote'],effective_price=ask)
                record('promotion_applied', task_id=task.id, sku=unit['sku'], unit=unit['unit'], **promotion)
            quote_wait = (max(0, QUOTE_SPEED_CONTROL_SECONDS - (time.monotonic() - quote_started))
                          if trial and arm == 'control' else 0)
            self.open[task.id] = deal = {
                "unit": unit, "ask": ask, "history": [], "opened_at": time.time(),
                'opened_monotonic':time.monotonic(),'last_activity':time.monotonic() + quote_wait,
                'waiting_for_first_reply':True,
                'buyer_identity':identity,'buyer_price_evidence':profiles_by_unit.get(unit['unit'], {}),
                'last_activity_at':time.time() + quote_wait, 'requirements':requirements,
                'warranty_hours':warranty_hours,'warranty_price_floor':warranty['price_floor'],
                'selling_price_floor':required_floor,
                'last_decision_id':audit.links().get('decision_id'),
            }
            if promotion: deal['promotion'] = promotion
            if not self.state.save_deal(task.id, deal, idle=FIRST_QUOTE_IDLE_SECONDS):
                del self.open[task.id]
                await reject('That unit was reserved by another purchase. Please try again.','reservation_conflict')
                return
            if quote_wait:
                try:
                    await asyncio.sleep(quote_wait)
                except asyncio.CancelledError:
                    self.open.pop(task.id, None)
                    self.state.close_deal(task.id)
                    raise
            record("opening", task_id=task.id, sku=unit["sku"], unit=unit["unit"],
                   category=unit.get("category"), grade=unit["grade"], kind=unit.get("kind"),
                   cost=unit["cost"], list_price=unit["list_price"], ask=ask,
                   expires_at=unit.get("expires_at"),warranty_hours=warranty_hours,warranty_economics=warranty,
                   score_version=VERSION,score_metric='book')
            evidence=profiles_by_unit.get(unit['unit'], {})
            ordinary=unit['list_price']*GRADE_ANCHORS[unit['grade']]
            if evidence.get('eligible_for_price_probe') and ask>ordinary*1.025:
                remaining=(unit['expires_at']-now)/3600 if unit.get('expires_at') is not None else None
                record('buyer_price_probe',task_id=task.id,buyer_name=identity['buyer_name'],sku=unit['sku'],
                       grade=unit['grade'],price=ask,baseline_anchor=ordinary,
                       expiry_bucket='unknown' if remaining is None else 'near_expiry' if remaining<=3 else 'not_near_expiry',
                       evidence_count=evidence['calm_high_paid'])
            trace("seller_open", task_id=task.id, sku=unit["sku"], unit=unit["unit"],
                  grade=unit["grade"], cost=unit["cost"], ask=ask)
            note = ''
            if promotion:
                if promotion.get('kind')=='featured':
                    note=f" Public quote {promotion['offer_id']} honored: ceiling {promotion['public_price_ceiling']}; asking {ask}. Negotiable."
                else:
                    label = 'Opening quote discount included' if promotion['saving'] else 'Minimum selling price; no discount available'
                    note = (f" {label}: {promotion['ordinary_quote']} -> {ask}; saving {promotion['saving']}. "
                            'Negotiable; no extra automatic discount.')
            quality = seller_quality.quality(unit, MARKET_DIR, time.time(), HOLD_SECONDS)
            record('quality_disclosed',task_id=task.id,sku=unit['sku'],unit=unit['unit'],**quality)
            coverage = f' Warranty {warranty_hours} h included in this total price; covered breakage receives a full market refund.' if warranty_hours else ''
            await up.requires_input(say(f"{unit['name']}: asking {ask}. {seller_quality.description(unit,quality)}.{note}{coverage} Short replies save tokens; send a number."))
            audit.executed('ask',status='emitted',price=ask,unit=unit['unit'])
            return

        offer = seller_requirements.offer(message)
        unit = deal["unit"]
        current = read_inventory()
        if current.get("round", 0) >= 3:
            fresh = next((u for u in current.get("units", [])
                          if u.get("unit") == unit["unit"] and u.get("grade") != "expired"), None)
            if fresh is None:
                record("stale_inventory", task_id=task.id, unit=unit["unit"])
                await up.reject(say("That unit is no longer available."))
                del self.open[task.id]
                self.state.close_deal(task.id)
                return
            unit = deal["unit"] = fresh
        warranty_hours = deal.get('warranty_hours',0)  # legacy negotiations keep their agreed terms
        warranty_floor = deal.get('warranty_price_floor',0)
        errors = seller_requirements.violations(unit, deal['requirements'], time.time(),warranty_hours=warranty_hours)
        if errors:
            record('requirements_unmet', task_id=task.id, errors=errors)
            self.state.close_deal(task.id)
            del self.open[task.id]
            await up.reject(say('This unit cannot meet your current requirements: '+ '; '.join(errors)+'. Please start a new request.'))
            return
        if offer is None:
            self.state.save_deal(task.id, deal, create=False)
            await up.requires_input(say(f"Conditions saved. Please send one clear price offer. Current ask: {deal['ask']}"))
            return
        record("offer", task_id=task.id, sku=unit["sku"], unit=unit["unit"], price=offer,
               grade=unit['grade'],cost=unit['cost'],expires_at=unit.get('expires_at'))
        deal["history"].append({"buyer_offer": offer, "seller_ask": deal["ask"]})
        self.state.save_deal(task.id, deal, create=False)
        attempts = trade_attempts({unit['sku']})
        memory = compact_context(unit["sku"], unit["grade"], attempts=attempts)
        guidance = price_guidance(unit, warranty_hours=warranty_hours, attempts=attempts, offered_price=offer)
        prompt = {
            "phase": "follow_up", "task_id": task.id,
            "unit": quote_candidates(unit,warranty_hours=warranty_hours), "current_ask": deal["ask"],
            "buyer_offer": offer, "price_guidance": guidance, "history": deal["history"][-MAX_TURNS:], "memory": memory,
            'buyer_price_evidence':deal.get('buyer_price_evidence'),
            'purchase_requirements':deal['requirements'], 'buyer_message':message[:500],
            'warranty_policy':seller_warranty.POLICY,'agreed_warranty_hours':warranty_hours,
            'warranty_price_floor':warranty_floor,
            'selling_policy':seller_warranty.selling_floor(unit,now=time.time(),warranty_hours=warranty_hours,agreed_warranty_floor=warranty_floor),
            'market_objective':operating_policy(time.time()),
            "objective": "Accept, counter or reject. Use the tool's estimated profit at the offered price and "
                         "comparable paid evidence. If near-expiry food is sold below the profit floor, treat it as "
                         "limiting a loss. Consider purchase cost, grade, "
                         "food expiry and goods storage, but do not treat purchase cost "
                         "as an absolute floor for food already near expiry. "
                         "A receipt counts only if checkout later settles. The buyer profile is a hypothesis; "
                         "a new low offer is evidence against the tested price. Do not escalate mechanically.",
        }
        prompt['objective'] += (' Value Book, settled revenue and honest buyer satisfaction. '
                                'Compare sale proceeds after warranty risk with retained inventory Book credit, storage and spoilage. '
                                'Do not force below-cost liquidation solely because the round is ending. '
                                'Never relax the buyer\'s quality, expiry or signature requirements to create volume.')
        if deal.get('promotion'):
            prompt['opening_promotion'] = deal['promotion']
            prompt['objective'] += (' The campaign price ceiling is already applied. Never raise the ask above '
                                    'that discounted quote or accept below its price_floor. Do not stack discounts. '
                                    'Receipt price must exactly equal the buyer\'s acknowledged offer.')
        fallback_action = "accept" if offer >= int(unit["cost"]) else "counter"
        audit.bind(parent_decision_id=deal.get('last_decision_id'))
        answer = await self.choose(prompt, ["accept", "counter", "reject"],
                                   {"action": fallback_action, "price": deal["ask"],
                                    "unit_id": unit["unit"]})
        deal['last_decision_id']=audit.links().get('decision_id')
        if answer.get('unit_id') != unit['unit']:
            audit.guard('unit_fixed_to_reservation',proposed_unit=answer.get('unit_id'),effective_unit=unit['unit'])
        current = read_inventory()
        if current.get("round", 0) >= 3:
            fresh = next((u for u in current.get("units", [])
                          if u.get("unit") == unit["unit"] and u.get("grade") != "expired"), None)
            if (fresh is None or any(fresh.get(k) != unit.get(k)
                                     for k in ("grade", "cost", "expires_at"))):
                record("stale_inventory", task_id=task.id, unit=unit["unit"])
                await reject("That unit changed during negotiation. Please try again.",'stale_inventory')
                del self.open[task.id]
                self.state.close_deal(task.id)
                return
            unit = fresh
        errors = seller_requirements.violations(unit, deal['requirements'], time.time(),warranty_hours=warranty_hours)
        if errors:
            record('requirements_unmet', task_id=task.id, errors=errors)
            self.state.close_deal(task.id)
            del self.open[task.id]
            await reject('The unit no longer meets your saved purchase conditions. Please start a new request.','requirements_unmet')
            return
        action = answer["action"]
        promotion = deal.get('promotion')
        sale_policy = seller_warranty.selling_floor(unit,now=time.time(),warranty_hours=warranty_hours,agreed_warranty_floor=warranty_floor)
        required_floor = sale_policy['price_floor']
        if required_floor > max(1,2*int(unit['list_price'])):
            self.state.close_deal(task.id)
            self.open.pop(task.id,None)
            await reject('No viable quote is available for this unit.','selling_floor_above_maximum')
            return
        if promotion and required_floor > min(deal['ask'],promotion.get('public_price_ceiling',deal['ask'])):
            self.state.close_deal(task.id)
            self.open.pop(task.id,None)
            await reject('That quote no longer has a viable unit. Please request a fresh quote.','promotion_below_selling_floor')
            return
        if action == 'accept' and offer < required_floor:
            audit.guard('selling_price_floor',buyer_offer=offer,price_floor=required_floor,policy=sale_policy)
            record('selling_price_guard',task_id=task.id,unit=unit['unit'],sku=unit['sku'],buyer_offer=offer,**sale_policy)
            action='counter'
            answer={**answer,'price':required_floor}

        if action == 'accept' and offer < warranty_floor:
            audit.guard('warranty_price_floor',buyer_offer=offer,price_floor=warranty_floor)
            action = 'counter'
            answer = {**answer,'price':warranty_floor}
            record('warranty_price_guard',task_id=task.id,buyer_offer=offer,price_floor=warranty_floor)
        if promotion and action == 'accept' and (offer < promotion['price_floor'] or offer > deal['ask']):
            audit.guard('promotion_accept_guard',buyer_offer=offer,price_floor=promotion['price_floor'],current_ask=deal['ask'])
            # A lower price must be offered and acknowledged, never silently signed
            # at a price different from the buyer's final numeric message.
            action = 'counter'
            answer = {**answer, 'price': min(deal['ask'],max(promotion['price_floor'],offer))}
            record('promotion_accept_guard',task_id=task.id,buyer_offer=offer,counter=answer['price'])
        if action == "accept" and offer > 0:
            self.refresh_settlements()
            quality = seller_quality.quality(unit, MARKET_DIR, time.time(), HOLD_SECONDS)
            terms = {"shop":NAME,"item":unit["sku"],"price":offer,"task":task.id,"unit":unit["unit"]}
            if warranty_hours:terms['warranty_hours']=warranty_hours
            if quality["claim"] is not None:
                terms["claim"] = quality["claim"]
            receipt = sign(terms)
            if not self.state.commit_receipt(task.id, receipt, HOLD_SECONDS):
                self.open.pop(task.id, None)
                if unit['unit'] in self.state.settled_units():
                    record('settled_unit_blocked', task_id=task.id, sku=unit['sku'], unit=unit['unit'], stage='receipt')
                    await reject('That unit has already sold. Please start a new request.', 'unit_already_settled')
                else:
                    await reject('That unit reservation expired. Please start a new request.', 'reservation_expired')
                return
            self.holds[unit['unit']] = time.monotonic() + HOLD_SECONDS
            await up.add_artifact(parts=[new_data_part(receipt)], name="receipt")
            record("receipt_signed", task_id=task.id, sku=unit["sku"], unit=unit["unit"],
                   grade=quality['grade'], claim=quality['claim'], claim_reason=quality['reason'],
                   price=offer,cost=unit['cost'],expires_at=unit.get('expires_at'),supplied_at=unit.get('supplied_at'),
                   warranty_hours=warranty_hours,warranty_economics=seller_warranty.economics(unit,offer,warranty_hours=warranty_hours),
                   score_version=VERSION,score_metric='book',public_offer_id=promotion.get('offer_id') if promotion else None)
            self.holds[unit["unit"]] = time.monotonic() + HOLD_SECONDS
            coverage = f' Includes {warranty_hours} h market warranty.' if warranty_hours else ''
            await up.complete(say(f"Deal: {unit['name']} at {offer}.{coverage}"))
            audit.executed('accept',status='emitted',price=offer,unit=unit['unit'],receipt_status='pending_settlement')
            trace("seller_receipt", task_id=task.id, unit=unit["unit"], price=offer,
                  status="pending settlement")
            del self.open[task.id]
            self.state.close_deal(task.id)
            return
        if action == "reject":
            record("rejected", task_id=task.id, sku=unit["sku"], unit=unit["unit"],
                   price=offer, reason=answer["reason"])
            await reject("No deal.",'model_reject')
            del self.open[task.id]
            self.state.close_deal(task.id)
            return
        proposed = answer.get("price")
        maximum = max(1, 2 * int(unit["list_price"]))
        ask = min(maximum, max(1, int(proposed))) if type(proposed) is int else deal["ask"]
        if ask != proposed:
            audit.guard('price_range_or_type',proposed_price=proposed,effective_price=ask)
        before_floor=ask
        if warranty_floor:ask = max(warranty_floor,min(deal['ask'],ask))
        if ask != before_floor:
            audit.guard('warranty_floor_or_current_ask',before=before_floor,effective_price=ask)
        if promotion:
            before_promotion=ask
            ask = min(deal['ask'], max(promotion['price_floor'],ask))
            if ask != before_promotion:
                audit.guard('promotion_counter_bounds',before=before_promotion,effective_price=ask)
        before_cost_floor=ask
        ask=max(ask,required_floor)
        if ask != before_cost_floor:
            audit.guard('selling_price_floor',proposed_price=before_cost_floor,effective_price=ask,policy=sale_policy)
        if ask != proposed:
            trace("decision_guard", phase="follow_up", proposed_price=proposed, effective_price=ask)
        deal["ask"] = ask
        if not self.state.save_deal(task.id, deal, create=False):
            self.open.pop(task.id, None)
            audit.guard('reservation_expired')
            audit.executed('counter',status='blocked',price=ask,unit=unit['unit'],reason='reservation_expired')
            return
        record("counter", task_id=task.id, sku=unit["sku"], unit=unit["unit"],
               buyer_offer=offer, ask=ask)
        trace("seller_counter", task_id=task.id, sku=unit["sku"], offer=offer, ask=ask)
        await up.requires_input(say(f"Counteroffer: {ask}."))
        audit.executed('counter',status='emitted',price=ask,unit=unit['unit'])

    async def cancel(self, context, event_queue) -> None:
        task = context.current_task
        if task is None:
            return
        deal = self.open.pop(task.id, None)
        self.state.close_deal(task.id)
        record("cancelled", task_id=task.id, sku=deal["unit"]["sku"] if deal else None,
               role='seller',episode_id=f'seller:{task.id}',decision_id=deal.get('last_decision_id') if deal else None)
        await TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id).cancel()


def build(port: int) -> Starlette:
    url = f"http://127.0.0.1:{port}/"
    card = AgentCard(
        name=NAME,
        description=SELLER_DESCRIPTION,
        version="1.0.0",
        default_input_modes=["text/plain"],
        default_output_modes=["application/json", "text/plain"],
        capabilities=AgentCapabilities(streaming=False),
        supported_interfaces=[AgentInterface(protocol_binding="JSONRPC", url=url, protocol_version="1.0")],
        skills=[AgentSkill(id="negotiate", name="Negotiate a stocked purchase",
                           description="Ask for a stocked product by SKU or name, then send a numeric offer. "
                                       "We keep replies brief.",
                           tags=["shopping", "negotiation", "signed-receipt"],
                           examples=["I want to buy G00001", "52000"])],
    )
    state = SellerState()
    task_store = SQLiteTaskStore(state)
    handler = DefaultRequestHandler(agent_executor=Seller(state), task_store=task_store, agent_card=card,
                                    request_context_builder=ResumingContextBuilder(task_store=task_store))
    return Starlette(routes=create_agent_card_routes(card) + create_jsonrpc_routes(handler, "/"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=9301)
    args = parser.parse_args()
    uvicorn.run(build(args.port), host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
