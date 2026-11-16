"""Bounded quality alternatives and observed receipt-to-payment evidence.

These are model inputs, not a private buyer policy or an automatic grade ban.
"""
from inventory_observation import timestamp


def diverse_options(units, public_quotes=(), *, limit=12):
    featured={o['unit'] for o in public_quotes}
    chosen=[];seen=set()
    def add(unit):
        if unit['unit'] not in seen and len(chosen)<limit:
            chosen.append(unit);seen.add(unit['unit'])
    for unit in units:
        if unit['unit'] in featured:add(unit)
    # Existing exact SKU/requirements filtering has already run. Keep one
    # available unit of each quality before filling remaining slots by FEFO.
    for grade in ('A','B','C'):
        for unit in units:
            if unit.get('grade')==grade:
                add(unit);break
    for unit in units:add(unit)
    return chosen


def checkout_evidence(attempts, sku, grade, now):
    counts=dict(signed=0,settled=0,checkout_cancelled=0,public_unpaid=0,mature_unsettled_observed=0,pending_unknown=0)
    for a in attempts:
        actual=a.get('settled_grade') or a.get('grade_at_receipt') or a.get('grade')
        at=timestamp(a.get('receipt_at'))
        if a.get('sku')!=sku or actual!=grade or at is None or not now-43200<=at<=now:continue
        counts['signed']+=1
        if a.get('outcome')=='SETTLED':counts['settled']+=1;continue
        if a.get('checkout_cancelled_at'):counts['checkout_cancelled']+=1
        if a.get('outcome')=='CHECKOUT_UNPAID':counts['public_unpaid']+=1
        if at+600<=now:counts['mature_unsettled_observed']+=1
        else:counts['pending_unknown']+=1
    return {**counts,'window_hours':12,'maturity_seconds':600,
            'caution':'Own exact SKU/grade observations. Cancellation is not a known private refusal reason. Mature unpaid is observed absence, not proof of permanent failure; only settled is a paid sale.'}
