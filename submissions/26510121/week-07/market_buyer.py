"""Market buyer: personal evidence, cautious reviews and bounded exploration."""
import argparse
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import time
import sqlite3

import httpx

from negotiation.a2a_client import A2AClient, ProtocolError
from negotiation.buying import negotiate
from negotiation.config import ROOT, load_settings
from negotiation.harness import HarnessError, ModelHarness
from negotiation.market import choose_shop, observation_count, read_memory, read_shops, remember, review_trade, read_review_cautions
from negotiation.receipts import verify_receipt
from negotiation.runlog import RunLog
from negotiation.experience import Experience, POLICY
from negotiation.ranking import select_buyer, unit_draw
from urllib.parse import urlsplit


class BudgetClient(A2AClient):
    def __init__(self, log, budget, **kwargs):
        super().__init__(log, **kwargs)
        self.budget, self.turns = budget, 0
        self.last_task_id = None

    async def send(self, text, task_id=None):
        # Final boundary before the HTTP request, in addition to schema validation.
        if task_id is not None and re.fullmatch(r"[+-]?\d+", text):
            price = int(text)
            if not 0 < price <= self.budget:
                raise ProtocolError("outgoing_price_outside_budget")
            self.turns += 1
        result = await super().send(text, task_id)
        if isinstance(result,dict):
            self.last_task_id = result.get('id', self.last_task_id)
        return result


class InformedHarness:
    def __init__(self, harness, evidence):
        self.harness, self.evidence = harness, evidence

    async def buyer(self, context, observation):
        # Public text remains data; it cannot replace trusted item/budget fields.
        return await self.harness.buyer(context, {**observation, "market_context_untrusted": self.evidence,
                                                'host_policy':self.evidence.get('policy_context', {})})


async def checkout_available(shop, experience, log, *, transport=None):
    """Read capabilities before sending any binding offer; never starts a checkout."""
    try:
        async with httpx.AsyncClient(timeout=5, trust_env=False, follow_redirects=False,transport=transport) as client:
            response=await client.get(shop['url'].rstrip('/')+'/.well-known/acp.json')
            response.raise_for_status();data=response.json()
        endpoint=urlsplit(data['endpoint']);base=urlsplit(shop['url'])
        valid=(data.get('protocol')=='acp' and endpoint.scheme in {'http','https'}
               and endpoint.hostname==base.hostname and not endpoint.username and not endpoint.password
               and 'checkout' in data.get('capabilities',{}).get('services',[])
               and 'ap2-mandate' in data.get('capabilities',{}).get('payment_methods',[])
               and {'create_checkout_session','complete_checkout_session'} <= {t.get('name') for t in data.get('tools',[])})
    except (httpx.HTTPError,ValueError,KeyError,TypeError,AttributeError):
        valid=False
    if experience:
        try:experience.capability(shop['name'],shop.get('pubkey'),valid)
        except (OSError,sqlite3.Error,ValueError):log.record('long_term_memory_unavailable',operation='capability')
    log.record('acp_preflight',shop=shop['name'],available=valid)
    return valid


def validate_market_receipt(receipt, shop, memory, registry_path, *, required):
    if any(r.get("task_id") == receipt.get("task") and r.get("outcome") == "purchased" for r in memory):
        raise ProtocolError("receipt_task_reused")
    registry = json.loads(Path(registry_path).read_text(encoding="utf-8")) if registry_path else {}
    registered = registry.get(shop["url"].rstrip("/"))
    if not registered or registered.get("name") != shop["name"]:
        if required:
            raise ProtocolError("receipt_registered_key_unavailable")
        return None
    if not receipt.get("sig_seller") and not required:
        return None
    if not verify_receipt(receipt, registered["pubkey"]):
        raise ProtocolError("receipt_signature_invalid")
    return True


async def run(args):
    settings = load_settings()
    cycle_started=time.monotonic()
    folder = ROOT / "logs" / "market-buyer" / datetime.now(timezone.utc).strftime("run-%Y%m%dT%H%M%S.%fZ")
    log = RunLog(folder / "buyer-events.jsonl", settings.api_key)
    shops = read_shops(args.shops, exclude_name=args.self_name)
    registry_path=os.environ.get('MARKET_REGISTRY_KEYS')
    registry=json.loads(Path(registry_path).read_text(encoding='utf-8')) if registry_path else {}
    for candidate in shops:
        identity=registry.get(candidate['url'].rstrip('/'),{})
        candidate['pubkey']=identity.get('pubkey') if identity.get('name')==candidate['name'] else None
    cautions = read_review_cautions(ROOT / '.market' / 'review-cautions.json')
    for candidate in shops:
        candidate['review_concentration_caution'] = candidate['name'] in cautions
    memory = read_memory(args.memory, ROOT / "memory" / "market-verification.jsonl")
    round_number = int(os.environ.get('MARKET_ROUND', '0'))
    require_verified = round_number >= 2
    experience, history = None, {}
    live=os.environ.get('MARKET_LIVE')=='1'
    order_id=os.environ.get('MARKET_ORDER_ID')
    experiment_id=order_id or folder.name
    selection_arm='saved-rate-selection' if live and unit_draw(experiment_id,'buyer-control')>=0.10 else 'v1-selection-control'
    try:
        if live:
            experience = Experience()
            history = experience.buyer_evidence(args.item,args.budget,{s['name']:s['pubkey'] for s in shops},round_number=round_number)
    except (OSError, sqlite3.Error, ValueError, TypeError) as failure:
        log.record('long_term_memory_unavailable', code=type(failure).__name__)
    candidates=[s for s in shops if not (live and require_verified) or s.get('pubkey')]
    for attempt in range(2):
        candidates=[s for s in candidates if not live or history.get(s['name'],{}).get('acp_available') is not False]
        if not candidates:
            log.record('no_ready_shop',reason='temporary capability evidence')
            print(json.dumps({'shop':'','receipt':None,'stars':3,'review':'No checkout-ready shop available; no purchase attempted.'}))
            return 0
        try:
            if selection_arm=='saved-rate-selection':
                shop,reason,evidence=select_buyer(candidates,history,args.item,args.budget,experiment_id,
                                                audit_name=os.environ.get('MARKET_AUDIT_SHOP'))
            else:
                control_memory=[r for r in memory if not live or r.get('pubkey')==next((s.get('pubkey') for s in candidates if s['name']==r['name']),None) and r.get('pubkey')]
                shop,reason,evidence=choose_shop(candidates,control_memory,args.item,args.budget,
                                                require_verified=require_verified,require_settled=round_number>=3,
                                                audit_name=os.environ.get('MARKET_AUDIT_SHOP'))
                evidence['policy_context']={'version':POLICY,'arm':selection_arm,'objective':'common negotiation; selector-only control'}
                evidence['personal_long_term_statistics']=history.get(shop['name'],{})
        except ValueError:
            log.record('no_ready_shop',reason='temporary integrity or transport evidence')
            print(json.dumps({'shop':'','receipt':None,'stars':3,'review':'No currently reachable, verifiable shop available; no purchase attempted.'}))
            return 0
        if round_number<3 or history.get(shop['name'],{}).get('acp_available') is True:
            break
        if await checkout_available(shop,experience,log):break
        candidates=[s for s in candidates if s['name']!=shop['name']]
    else:
        log.record('no_ready_shop',reason='two bounded ACP reads failed')
        print(json.dumps({'shop':'','receipt':None,'stars':3,'review':'Checkout discovery unavailable; no binding offer sent.'}))
        return 0
    safe = lambda value: value.replace(settings.api_key, "[REDACTED]") if settings.api_key else value
    for avoided in evidence["avoided_for_integrity"]:
        print(safe(f"[avoid] {avoided['name']}: {avoided['reason']}"), flush=True)
    print(safe(f"[choose] {shop['name']} {shop['url']}: {reason}"), flush=True)
    log.record("shop_selected", name=shop["name"], url=shop["url"], item=args.item,
               budget=args.budget, reason=reason, evidence=evidence)
    client = BudgetClient(log, args.budget)
    started, result, error, signature_verified = time.monotonic(), {}, None, None
    local_failure = False
    try:
        async with asyncio.timeout(240):
            result = await negotiate(client, InformedHarness(ModelHarness(folder), evidence),
                                     shop["url"], args.item, args.budget, log)
            if result.get("outcome") == "purchased":
                signature_verified = validate_market_receipt(result["receipt"], shop, memory,
                    os.environ.get("MARKET_REGISTRY_KEYS"), required=require_verified)
    except HarnessError as failure:
        error, local_failure = str(failure), True
    except ProtocolError as failure:
        error = str(failure)
    except TimeoutError:
        error = "buyer_deadline_exceeded"
    except httpx.HTTPError as failure:
        error = "a2a_network_error"
        response = getattr(failure, "response", None)
        log.record("transport_failure", kind=type(failure).__name__,
                   http_status=response.status_code if response is not None else None)
    except Exception:
        error = "unexpected_market_buyer_error"
    finally:
        await client.close()
    stars, review = review_trade(result, error, args.budget, client.turns,
                                 reveal_budget=round_number < 3, local_failure=local_failure)
    receipt = result.get("receipt") if not error and result.get("outcome") == "purchased" else None
    outcome = ('local_failure' if local_failure else "integrity_error" if error and stars == 1 else "unavailable" if error
               else "purchased" if receipt is not None else "no_deal")
    record = remember(args.memory, shop=shop, item=args.item, budget=args.budget, outcome=outcome,
                      price=receipt["price"] if receipt is not None else None,
                      seconds=time.monotonic() - cycle_started, turns=client.turns, task_id=result.get("task_id") or client.last_task_id,
                      sequence=observation_count(memory) + 1, signature_verified=signature_verified,
                      order_id=order_id,error_code=error,policy_arm=selection_arm,round_number=round_number)
    if experience is not None:
        try:
            experience.buyer_observation(record, round_number=round_number)
        except (OSError, sqlite3.Error, ValueError, TypeError) as failure:
            log.record('long_term_memory_unavailable', code=type(failure).__name__)
    log.record("market_result", memory=record, error=error, stars=stars, review=review, policy=POLICY, market_round=round_number)
    print(safe(json.dumps({"shop": shop["url"], "receipt": receipt,
                          "stars": stars, "review": review}, ensure_ascii=False)), flush=True)
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shops", required=True, type=Path)
    parser.add_argument("--item", required=True)
    parser.add_argument("--budget", required=True, type=int)
    parser.add_argument("--self-name", default="seochanit")
    parser.add_argument("--memory", type=Path,
                        default=Path(os.environ.get("MARKET_BUYER_MEMORY", ROOT / "memory" / "market-trades.jsonl")))
    args = parser.parse_args()
    if not args.item.strip() or args.budget < 0:
        parser.error("Nonempty item and nonnegative budget required")
    if not args.memory.resolve().is_relative_to(ROOT):
        parser.error("Memory must stay inside this submission")
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
