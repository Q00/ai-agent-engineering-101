"""Six-hour goods coverage; refund scenarios are assumptions, not market odds."""
from datetime import datetime
import math
import time

HOURS = 6
REFUND_SHARE = .10
POLICY = ('New goods quotes include a 6 h market warranty in the total price. '
          'A covered breakage refunds the full paid price under market rules. '
          'Food has no goods warranty. Honor the hours saved for this deal; '
          'never promise longer coverage or add an undisclosed fee.')
DISCLOSURE = ('공산품 새 견적에는 결제 후 6시간 보증이 총액에 포함됩니다. '
              '보증 기간 내 고장은 시장 규칙에 따라 전액 환불됩니다. 식품은 보증 대상이 아닙니다.')


def hours(unit):
    return HOURS if unit.get('kind') == 'goods' else 0


def economics(unit, price=None, *, now=None, warranty_hours=0):
    now = time.time() if now is None else now
    cost = int(unit['cost'])
    holding = cost*.01*max(0., now-float(unit.get('supplied_at') or now))/86400 if unit.get('kind') == 'goods' else 0.
    covered = unit.get('kind') == 'goods' and warranty_hours > 0
    share = REFUND_SHARE if covered else 0.
    result = {'warranty_hours':warranty_hours if covered else 0,
              'refund_share_scenario':share, 'risk_source':'operator stress scenario; true breakage probability is unpublished',
              'estimated_accrued_holding':round(holding,4),
              'price_floor':math.ceil((cost+holding)/(1-share))+1 if covered else 0}
    if price is not None:
        result['refund_scenarios'] = [{'refund_share':p, 'margin_after_refunds_and_accrued_holding':round(price*(1-p)-cost-holding,2)}
                                      for p in ((0.,.05,.10,.20) if covered else (0.,))]
        result['refund_allowance'] = round(price*share,2)
    return result


def _time(value):
    if type(value) in (int,float):return value
    try:return datetime.fromisoformat(str(value).replace('Z','+00:00')).timestamp()
    except (ValueError,TypeError):return 0.


def exposure(history, now):
    """Reserve a scenario share only for distinct paid, still-covered sales.

    A signature alone is not exposure. Keeping refunded trades reserved until
    expiry is conservative when the local settlement feed lacks refund events.
    """
    receipts = {(e.get('task_id'),e.get('unit')):e for e in history if e.get('event')=='receipt_signed'}
    paid = {e['ledger']:e for e in history if e.get('event')=='settled' and e.get('ledger') is not None}
    total = count = 0
    for sale in paid.values():
        receipt = receipts.get((sale.get('task_id'),sale.get('unit')), {})
        duration = receipt.get('warranty_hours',0)
        at = _time(sale.get('at'));price = sale.get('price',0)
        if (type(duration) in (int,float) and 0<duration<=72 and at>0 and at<=now<at+duration*3600
                and type(price) is int and price>0):
            total += price;count += 1
    return {'covered_sales':count,'covered_paid_amount':total,'refund_share_scenario':REFUND_SHARE,
            'cash_reserve':math.ceil(total*REFUND_SHARE),
            'source':'paid ledger matches within warranty; scenario reserve, not an observed failure rate'}


NEAR_EXPIRY_HOURS = 3

def selling_floor(unit, *, now=None, warranty_hours=0, agreed_warranty_floor=0):
    """Only finite, unexpired food within three hours may be liquidated at a loss."""
    now=time.time() if now is None else now
    cost=unit.get('cost')
    if type(cost) is not int or cost<=0:raise ValueError('verified positive acquisition cost required')
    expiry=unit.get('expires_at')
    remaining=(expiry-now)/3600 if type(expiry) in (int,float) and math.isfinite(expiry) else None
    near=unit.get('kind')=='food' and remaining is not None and 0<remaining<=NEAR_EXPIRY_HOURS
    economics_view=economics(unit,now=now,warranty_hours=warranty_hours)
    acquisition_floor=0 if near else math.ceil(cost+economics_view['estimated_accrued_holding'])+1
    return {'price_floor':max(acquisition_floor,economics_view['price_floor'],agreed_warranty_floor),
            'acquisition_cost':cost,'near_expiry_loss_allowed':near,
            'hours_left':remaining,'near_expiry_hours':NEAR_EXPIRY_HOURS,
            'reason':'near_expiry_food_model_may_limit_loss' if near else 'protect_acquisition_cost_and_accrued_holding'}


def quote_floor(unit, *, now=None, warranty_hours=0, agreed_warranty_floor=0, seconds=120):
    """Carry a short goods-storage allowance in new quotes, never weaken live floors."""
    now=time.time() if now is None else now
    future=now+max(0,seconds) if unit.get('kind')=='goods' else now
    return max(selling_floor(unit,now=t,warranty_hours=warranty_hours,
                           agreed_warranty_floor=agreed_warranty_floor)['price_floor']
               for t in (now,future))
