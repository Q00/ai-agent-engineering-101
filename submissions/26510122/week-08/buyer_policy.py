"""Buyer-side value and checkout guards for Round 3.

The discount ratios are initial hypotheses, not published demand or breakage
rates. Settlement logs can support revisions after Round 3 opens.
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
import json
import math
import os
from pathlib import Path
from statistics import median_low, median
import time
from urllib.request import urlopen

from context_compaction import epoch
from market_scoring import saved_at_price, BUYER_RANKING_VERSION


FOOD_MAX_SHARE = 0.85
GOODS_MAX_SHARE = 0.75
CHECKOUT_BUFFER_SECONDS = 120
SAVED_BOARD = 'https://market.ourolabs.ai/api/board?round=3'
SAVED_CACHE = Path('state/buyer-saved-reference.json')
SAVED_REFRESH_SECONDS = 300
SAVED_MAX_AGE_SECONDS = 900


def shelf_minimum(order: dict) -> float:
    """Exact mandate horizon at checkout; no proportional private requirement."""
    required = float(order.get("use_within_hours") or 0)
    if not math.isfinite(required) or required<0: raise ValueError('invalid use_within_hours')
    return required


def quote_shelf_minimum(order: dict) -> float:
    """Request two minutes of transaction headroom, independently of shelf length."""
    return round(shelf_minimum(order)+CHECKOUT_BUFFER_SECONDS/3600,6)


def price_cap(order: dict, product: dict) -> int:
    """Protect the private mandate and a provisional positive normalized saving."""
    if not isinstance(product, dict) or type(product.get("list_price")) is not int:
        return 0
    list_price = product["list_price"]
    budget = order.get("budget")
    if type(budget) is not int or budget <= 0 or list_price <= 0:
        return 0
    kind = order.get("kind")
    if product.get("kind") != kind:
        return 0
    if kind == "food":
        shelf = product.get("shelf_hours")
        if not isinstance(shelf, (int, float)) or shelf < shelf_minimum(order):
            return 0
        ratio = FOOD_MAX_SHARE
    elif kind == "goods":
        ratio = GOODS_MAX_SHARE
    else:
        return 0
    return min(budget, math.floor(list_price * ratio))


def paid_price_samples(order, history, now=None, *, by_shop=False):
    """Recent usable, locally verified exact-SKU payments for an issued order."""
    now = time.time() if now is None else now
    acceptable = set(order.get('acceptable') or ())
    policy = acceptance(order)
    minimum = policy.get('min_hours_left')
    by_sku = defaultdict(dict)
    for e in history:
        if (e.get('event') != 'settled' or e.get('sku') not in acceptable or e.get('ledger') is None
                or type(e.get('price')) is not int or e['price'] <= 0 or e.get('claim_honest') is False):
            continue
        at = e.get('at')
        at = at if type(at) in (int, float) else epoch(at)
        if at is None or not 0 <= now - at <= 72 * 3600:
            continue
        if minimum is not None:
            remaining = e.get('hours_left_at_recording')
            if (type(remaining) not in (int, float) or not math.isfinite(remaining)
                    or remaining <= 0 or remaining < minimum):
                continue
        elif e.get('grade') not in policy.get('grades', ()):
            continue
        if by_shop and not e.get('shop_name'):
            continue
        key = (e['shop_name'], e['sku']) if by_shop else e['sku']
        by_sku[key][e['ledger']] = e['price']
    return {sku: tuple(sorted(prices.values())) for sku, prices in by_sku.items()}


def opening_offer_range(cap, guidance=None):
    if guidance and guidance.get('opening_offer_range'):
        return guidance['opening_offer_range']
    return [max(1, math.floor(cap * .7)), max(1, math.floor(cap * .85))]


def market_saved_reference(name, *, path=SAVED_CACHE, now=None):
    """Read a bounded, cached copy of the official buyer Saved value."""
    now = time.time() if now is None else now
    try:
        cache = json.loads(path.read_text())
        if (not isinstance(cache, dict) or cache.get('name') != name
                or cache.get('ranking_version') != BUYER_RANKING_VERSION):
            cache = {}
    except (OSError, ValueError):
        cache = {}
    checked = cache.get('checked_at')
    if type(checked) not in (int, float) or not 0 <= now - checked < SAVED_REFRESH_SECONDS:
        cache.update(name=name, checked_at=now, ranking_version=BUYER_RANKING_VERSION)
        try:
            with urlopen(SAVED_BOARD, timeout=2) as response:
                board = json.load(response)
            if board.get('round') != 3:
                raise ValueError('unexpected market round')
            row = next(x for x in board['buyers'] if x['name'] == name and x.get('deals', 0) > 0)
            saved = row['saved']
            if type(saved) not in (int, float) or not -1 <= saved < 1:
                raise ValueError('invalid market Saved')
            cache.update(saved=saved, observed_at=now, deals=row.get('deals'))
        except (OSError, ValueError, TypeError, KeyError, AttributeError, StopIteration):
            pass
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name(f'.{path.name}.{os.getpid()}.tmp')
            temporary.write_text(json.dumps(cache))
            temporary.replace(path)
        except OSError:
            pass
    seen = cache.get('observed_at')
    saved = cache.get('saved')
    if (type(seen) in (int, float) and 0 <= now - seen <= SAVED_MAX_AGE_SECONDS
            and type(saved) in (int, float) and -1 <= saved < 1):
        return {'saved': saved, 'observed_at': seen, **({'deals':cache['deals']} if type(cache.get('deals')) is int else {})}
    return None


def saved_price_reference(list_price, baseline, *, cost_reference=None):
    """Soft price for improving displayed Saved under the given unit-cost scenario."""
    if not baseline or type(list_price) is not int or list_price <= 0:
        return None
    cost = (cost_reference or {}).get('unit_cost')
    if (type(cost) not in (int, float) or not math.isfinite(cost)
            or not 0 <= cost < list_price):
        return None
    saved = baseline.get('saved')
    if type(saved) not in (int, float) or not math.isfinite(saved) or not -1 <= saved < 1:
        return None
    cutoff = Decimal(list_price) - Decimal(str(saved)) * (Decimal(list_price) - Decimal(str(cost)))
    whole_price = math.ceil(cutoff) - 1
    return {'current_market_saved': saved, 'observed_at': baseline['observed_at'],
            'max_price_to_improve_if_usable': whole_price if whole_price > 0 else None,
            'cost_reference': cost_reference, 'ranking_version': BUYER_RANKING_VERSION,
            'official_score_status':baseline.get('official_score_status','reported_snapshot'),
            'note': 'Conditional on this cost and usable settlement; displayed Saved is rounded. '
                    'This is a soft negotiation reference, not an exact aggregate-score guarantee.'}


def purchase_price_guidance(cap, prices, *, list_price=None, saved_baseline=None, cost_reference=None):
    """Suggest a market-supported range while retaining the mandate ceiling."""
    baseline = opening_offer_range(cap)
    result = {'paid_sample_72h': len(prices), 'median_paid': median_low(prices) if prices else None,
              'suggested_maximum': None, 'opening_offer_range': baseline, 'cost_reference': cost_reference}
    saved = saved_price_reference(list_price, saved_baseline, cost_reference=cost_reference)
    if saved is not None:
        result['saved_reference'] = saved
    if len(prices) < 3:
        return result
    median = result['median_paid']
    if type(list_price) is int and list_price > 0:
        contribution = saved_at_price(list_price, median, (cost_reference or {}).get('unit_cost'))
        if contribution is not None:
            result['saved_if_usable_at_reference'] = contribution
    upper_quartile = prices[math.ceil(.75 * len(prices)) - 1]
    high = min(baseline[1], median)
    low = min(baseline[0], max(1, math.floor(median * .92)))
    result.update(suggested_maximum=min(cap, upper_quartile), opening_offer_range=[low, high])
    return result


def routing_profiles(history, order, *, now=None):
    """Recent comparable completed shop/SKU orders, with conservative smoothing.

    Unsigned receipts and unresolved checkouts are not paid. These are
    price/condition-dependent observations, not honesty labels or blacklists.
    """
    now=time.time() if now is None else now
    profiles={}
    for name, rows in getattr(history,'by_name',{}).items():
        episodes={}
        for e in rows:
            at=epoch(e.get('at'))
            if at is None or not 0<=now-at<=6*3600 or not e.get('order') or not e.get('sku'):
                continue
            requirement=history.opportunities.get(e['order'],{})
            if order.get('kind')=='food':
                # A refusal on a much harder shelf horizon is not evidence
                # against serving this order. Unknown old requirements stay out.
                if (requirement.get('order_kind')!='food'
                        or requirement.get('use_within_hours')!=order.get('use_within_hours')):
                    continue
            key=(e['order'],e['sku'])
            item=episodes.setdefault(key,{'outcome':None,'at':at,'duration':None,'unusable':False})
            item['at']=max(item['at'],at)
            if e['event']=='negotiation':
                if item['outcome']!='paid' and e.get('result') in ('no_receipt','invalid_receipt','shop_error'):
                    item['outcome']='failed'
                elif item['outcome']!='paid' and e.get('result')=='receipt_pending':
                    item['outcome']=None  # unresolved new receipt is not a completed failure
                duration=e.get('duration_seconds')
                start=history.selection_starts.get(e['order'],{})
                if type(duration) not in (int,float) and start.get('shop_name')==name and start.get('sku')==e['sku']:
                    begin=epoch(start.get('at'))
                    duration=at-begin if begin is not None else None
                if type(duration) in (int,float) and math.isfinite(duration) and 0<=duration<=600:
                    item['duration']=duration
            elif e['event']=='payment':
                if e.get('paid') is True and e.get('ledger') is not None:
                    item['outcome']='paid'
                elif e.get('paid') is False and item['outcome']!='paid':
                    item['outcome']='failed'
            elif e['event']=='settled' and e.get('ledger') is not None:
                item['outcome']='paid'
                item['unusable']=type(e.get('value')) in (int,float) and e['value']<=0
        by_sku=defaultdict(list)
        for (_,sku),item in episodes.items():
            if item['outcome'] is not None:
                by_sku[sku].append(item)
        for sku,items in by_sku.items():
            paid=sum(e['outcome']=='paid' for e in items)
            unusable=sum(e['unusable'] for e in items)
            durations=[e['duration'] for e in items if e['duration'] is not None]
            # A neutral 3/5 prior and three one-minute observations avoid
            # treating one lucky payment or failure as a permanent rule.
            seconds=(sum(min(180,max(5,t)) for t in durations)+3*60)/(len(durations)+3)
            profiles[(name,sku)]={'completed_orders_6h':len(items),'paid_orders_6h':paid,
                'unusable_at_settlement_6h':unusable,
                'smoothed_paid_probability':round((paid+3)/(len(items)+5),4),
                'smoothed_unusable_probability':round(unusable/(len(items)+5),4),
                'observed_duration_count':len(durations),
                'median_observed_seconds':round(median(durations),1) if durations else None,
                'smoothed_attempt_seconds':round(seconds,1)}
    return profiles


def routing_evidence(profile, saving, quality=None):
    evidence=dict(profile or {'completed_orders_6h':0,'paid_orders_6h':0,
        'unusable_at_settlement_6h':0,'smoothed_paid_probability':.6,
        'smoothed_unusable_probability':0.,'observed_duration_count':0,
        'median_observed_seconds':None,'smoothed_attempt_seconds':60.})
    contribution=saving if type(saving) in (int,float) and math.isfinite(saving) else 0.
    expectation=(evidence['smoothed_paid_probability']*contribution
                 - evidence['smoothed_unusable_probability']*(1+contribution))
    from buyer_breakage import contribution_debit
    debit=contribution_debit(quality,contribution)
    expectation-=evidence['smoothed_paid_probability']*debit
    if quality:
        evidence['post_sale_breakage']=quality
        evidence['post_sale_contribution_debit']=round(debit,6)
    evidence['estimated_saved_per_attempt']=round(expectation,6)
    evidence['estimated_saved_per_minute']=round(
        expectation*60/max(5,evidence['smoothed_attempt_seconds']),6)
    return evidence


def acceptance(order: dict, receipt: dict | None = None) -> dict:
    """The kit checks these against the seller's signed checkout before paying."""
    policy: dict = {"cert": True}
    if order.get("kind") == "food":
        policy["min_hours_left"] = shelf_minimum(order)
    else:
        policy["grades"] = ["A", "B"]
        if isinstance(receipt, dict) and isinstance(receipt.get("warranty_hours"), (int, float)) \
                and receipt["warranty_hours"] >= 6:
            policy["warranty_hours"] = 6
    return policy


def receipt_problem(receipt: dict | None, *, selected_sku: str, order: dict,
                    final_offer: int, task_id: str | None, cap: int) -> str | None:
    """Local checks before handing the receipt to the kit's signature and AP2 checks."""
    if not isinstance(receipt, dict):
        return "no receipt"
    if receipt.get("item") != selected_sku or selected_sku not in order.get("acceptable", []):
        return "receipt SKU differs from the selected acceptable SKU"
    price = receipt.get("price")
    if type(price) is not int or price <= 0 or price != final_offer or price > cap:
        return "receipt price differs from the final offer or price cap"
    if not task_id or receipt.get("task") != task_id:
        return "receipt task ID differs from the negotiation"
    if not receipt.get("sig_seller"):
        return "seller signature missing"
    return None
