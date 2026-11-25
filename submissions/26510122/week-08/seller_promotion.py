"""One opening-quote discount; agreed receipts are never repriced."""
import json
import math
from pathlib import Path
import time

EVENT = Path('state/seller-opening-event.json')


def active_event(path=EVENT, now=None):
    now = time.time() if now is None else now
    try: event = json.loads(path.read_text())
    except (OSError, ValueError): return None
    start, end = event.get('starts_at'), event.get('ends_at')
    if (type(start) not in (int,float) or type(end) not in (int,float)
            or not 0 < end-start <= 72*3600 or not start <= now < end): return None
    rate = event.get('discount_bps', 0)
    begins = event.get('discount_starts_at', end)
    if type(rate) is not int or not 0 <= rate <= 300: rate = 0
    if type(begins) not in (int,float) or not start <= begins <= now: rate = 0
    return {'id':event.get('id'), 'starts_at':start, 'ends_at':end, 'discount_bps':rate,
            'discount_starts_at':begins if rate else None,
            'description':'오픈 안내. 활성 할인은 첫 제시가에만 적용, 최저판매가 한도. 이후 네고 가능. 중복 적용 없음.'}


def discount_quote(unit, ordinary_price, *, path=EVENT, now=None, minimum_price=0):
    now = time.time() if now is None else now
    event = active_event(path, now)
    if not event or not event['discount_bps']: return None
    cost = int(unit['cost'])
    age_days = max(0., now-float(unit.get('supplied_at') or now))/86400
    holding = cost*.01*age_days if unit.get('kind') == 'goods' else 0.
    floor = max(minimum_price,math.ceil(cost+holding)+1)
    baseline = max(int(ordinary_price),floor)
    # Round the saving down: the effective reduction never exceeds the advertised maximum.
    saving = baseline*event['discount_bps']//10000
    price = max(floor,baseline-saving)
    return {'event_id':event['id'], 'discount_bps':event['discount_bps'],
            'ordinary_quote':baseline, 'model_quote':ordinary_price, 'price_floor':floor,
            'estimated_accrued_holding':round(holding,4), 'price':price, 'saving':baseline-price,
            'quoted_at':now, 'campaign_ends_at':event['ends_at']}
