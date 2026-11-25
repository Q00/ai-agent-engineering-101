"""Local deterministic reviews based on the kit's actual settlement callback."""
import argparse
import json
import math
from r3_clock import clock as time

from r3_buyer import sanitized_order
from r3_common import load_catalog, load_policy, money as checked_money, read_json, require_live, state_dir
from r3_memory import Memory
from wire import market_url


def _money(value):
    try:
        return checked_money(value)
    except (ValueError, TypeError):
        return None


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def validate_deal(deal):
    if not isinstance(deal, dict):
        raise ValueError('Deal must be an object')
    order, receipt, settled = (deal.get(k) for k in ('order', 'receipt', 'settled'))
    if not all(isinstance(v, dict) for v in (order, receipt, settled)):
        raise ValueError('A review requires order, receipt, and actual settlement')
    ledger = settled.get('ledger')
    if type(ledger) is not int or ledger < 0:
        raise ValueError('Settlement requires a numeric ledger ID')
    if type(order.get('order')) not in (str, int) or not str(order['order']).strip():
        raise ValueError('Settlement requires an order ID')
    market_url(settled.get('shop', ''))
    sku = settled.get('sku')
    signed_item = receipt.get('item')
    if (not isinstance(sku, str) or not sku or sku not in order.get('acceptable', []) or
            not isinstance(signed_item, str) or signed_item not in (order.get('item'), sku)):
        raise ValueError('Settled SKU does not match receipt and order')
    price, budget = _money(receipt.get('price')), _money(order.get('budget'))
    if price is None or budget is None or price > budget:
        raise ValueError('Invalid settled price or fixed budget')
    if 'price' in settled and _money(settled['price']) != price:
        raise ValueError('Settlement price differs from receipt')
    if 'order' in settled and str(settled['order']) != str(order['order']):
        raise ValueError('Settlement belongs to another order')
    if not isinstance(settled.get('unit'), str) or not settled['unit']:
        raise ValueError('Settlement requires a delivered unit')
    if receipt.get('unit') is not None and receipt['unit'] != settled['unit']:
        raise ValueError('Settlement unit differs from receipt')
    if not isinstance(settled.get('name'), str) or not settled['name']:
        raise ValueError('Settlement requires an item name')
    if not isinstance(receipt.get('task'), str) or not receipt['task'] or not receipt.get('sig_seller'):
        raise ValueError('Missing negotiated receipt evidence')
    if settled.get('claim_honest') is not None and type(settled['claim_honest']) is not bool:
        raise ValueError('Invalid claim honesty evidence')
    return order, receipt, settled


def review_result(order, receipt, settled, product, policy, now):
    price, grade = _money(receipt['price']), settled.get('grade')
    stars = {'A': 5, 'B': 4, 'C': 3, 'expired': 1}.get(grade, 3)
    parts = ['Settled %s at %s credits' % (settled['sku'], price)]
    if grade in ('A', 'B', 'C', 'expired'):
        parts.append('market grade %s' % grade)
    else:
        parts.append('grade unavailable')
    list_price = _money(product.get('list_price'))
    if list_price:
        ratio = price / list_price
        parts.append('price %.2fx catalog list price' % ratio)
        if ratio > 1.2:
            stars -= 1
        elif ratio <= 0.8:
            stars += 1
    value = settled.get('value')
    unusable = False
    if _finite(value):
        parts.append('market-reported usable value %g credits' % value)
        if value == 0:
            unusable = True
        elif price > value * 2:
            stars -= 2
        elif price > value * 1.25:
            stars -= 1
    if order.get('kind', product.get('kind')) == 'food':
        expires = settled.get('expires_at')
        horizon = order.get('use_within_hours', 0)
        if _finite(expires) and _finite(horizon):
            hours = (expires - now) / 3600
            parts.append('%.2f h shelf life remaining at review' % max(0, hours))
            if hours < horizon:
                parts.append('shorter than the order use horizon')
                stars = min(stars, 2)
            if hours <= 0:
                unusable = True
        else:
            parts.append('remaining shelf life unavailable')
            stars = min(stars, 3)
    else:
        warranty = settled.get('warranty_hours')
        if _finite(warranty):
            parts.append('settled warranty %g h' % warranty)
            if warranty < policy.get('warranty_hours', 0):
                stars = min(stars, 2)
    honesty = settled.get('claim_honest')
    if honesty is False:
        parts.append('market settlement reports an inaccurate quality claim')
        stars = min(stars, 2)
    elif honesty is True:
        parts.append('market settlement reports a consistent quality claim')
    if unusable or grade == 'expired':
        stars = 1
    return dict(stars=max(1, min(5, stars)), review='; '.join(parts) + '.')


def _check_payment(md, order, receipt, settled):
    path = md / 'payments.jsonl'
    if not path.exists():
        return
    found = False
    for line in path.read_text(encoding='utf-8').splitlines():
        try:
            payment = json.loads(line)
        except ValueError:
            continue
        if not isinstance(payment, dict) or str(payment.get('order')) != str(order['order']):
            continue
        if payment.get('ledger') != settled['ledger']:
            continue
        if (payment.get('paid') is not True or payment.get('shop', '').rstrip('/') != settled['shop'].rstrip('/') or
                payment.get('item') != receipt['item'] or _money(payment.get('price')) != _money(receipt['price'])):
            raise ValueError('Payment evidence disagrees with settlement')
        found = True
    if not found:
        raise ValueError('No matching paid ledger entry in payment evidence')


def run(deal_path, market_dir=None, now=None):
    md = state_dir(market_dir)
    order, receipt, settled = validate_deal(read_json(deal_path))
    _check_payment(md, order, receipt, settled)
    memory = Memory(md)
    memory.reconcile()
    key = str(settled['ledger'])
    previous_event = memory.get('settlement', key)
    previous = previous_event['data'] if previous_event is not None else None
    if previous is not None:
        if (str(previous.get('order')) != str(order['order']) or previous.get('sku') != settled['sku'] or
                previous.get('shop_url', '').rstrip('/') != settled['shop'].rstrip('/') or
                previous.get('unit') != settled['unit']):
            raise ValueError('Ledger ID conflicts with previous settlement')
        if isinstance(previous.get('review'), dict):
            feedback = previous['review']
            memory.close()
            print(json.dumps(feedback, ensure_ascii=False, allow_nan=False), flush=True)
            return feedback
    observed_at = time.time() if now is None else now
    if not _finite(observed_at):
        raise ValueError('Invalid observation time')
    product = load_catalog(md).get(settled['sku'], {})
    feedback = review_result(order, receipt, settled, product, load_policy(md), observed_at)
    shops = read_json(md / 'shops.json', [])
    shop_name = next((s['name'] for s in shops if isinstance(s, dict) and isinstance(s.get('name'), str) and
                      s.get('url', '').rstrip('/') == settled['shop'].rstrip('/')), settled['shop']) if isinstance(shops, list) else settled['shop']
    memory.record('settlement', key, dict(order=order['order'], shop=shop_name,
        shop_url=settled['shop'], sku=settled['sku'], name=settled['name'], unit=settled['unit'],
        price=_money(receipt['price']), settled=settled, receipt=receipt,
        order_details=sanitized_order(order), review=feedback,
        settled_at=observed_at, observed_at=observed_at))
    memory.close()
    print(json.dumps(feedback, ensure_ascii=False, allow_nan=False), flush=True)
    return feedback


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--deal', required=True)
    args = parser.parse_args()
    require_live()
    run(args.deal)


if __name__ == '__main__':
    main()
