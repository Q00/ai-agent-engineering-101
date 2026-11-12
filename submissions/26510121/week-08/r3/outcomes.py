"""One outcome view: exact market unit facts supersede provisional name matching."""
import math
from .state import identity


def latest(journal, limit=10000):
    result={}
    for row in journal.events('outcome',limit):
        oid=row.get('order');old=result.get(oid)
        if old is None or row.get('canonical') and not old.get('canonical') or (
                not old.get('canonical') and row.get('broken') and not old.get('broken')):
            result[oid]=row
    return result


def confirmed_refund(event):
    value=event.get('refunded')
    if type(value) not in (int,float):value=event.get('amount')
    return value if type(value) in (int,float) and math.isfinite(value) and value>=0 else None


def reconcile(journal,purchases,now):
    """Append revisions, never rewrite raw outcomes or assume a promised refund."""
    # A shared short cache bounds the extra feed decoding on the order path.
    facts=journal.get('outcome-unit-facts:v1',{})
    if not 0<=now-facts.get('at',-1000)<30:
        broken={};refunds={}
        for row in journal.events('public_broken',20000):
            if row.get('unit') and 0<=now-row['_ts']<=172800:broken.setdefault(row['unit'],row)
        for row in journal.events('public_refund',20000):
            unit=row.get('unit');oid=row.get('order');amount=confirmed_refund(row)
            if amount is not None and 0<=now-row['_ts']<=172800:
                if unit:refunds.setdefault('unit:'+unit,row)
                if oid:refunds.setdefault('order:'+oid,row)
        facts={'at':now,'broken':broken,'refunds':refunds}
        journal.put('outcome-unit-facts:v1',facts)
    from .buyer_scoring import formula,LEGACY,same_formula
    version=formula(journal)
    old=latest(journal);feed=journal.get('public_feed_caught_up_at',0)
    fresh=0<=now-feed<=180
    pending=[]
    for item in purchases:
        if item.get('kind')!='goods' or not item.get('unit'):continue
        age=now-item.get('paid_at',now)
        if not 0<=age<=172800:continue
        unit=item['unit'];oid=item['order'];broken=facts['broken'].get(unit)
        if broken and broken.get('order') not in (None,oid):continue
        refund=facts['refunds'].get('unit:'+unit) or facts['refunds'].get('order:'+oid)
        if refund and refund.get('unit') not in (None,unit):refund=None
        if broken:
            amount=confirmed_refund(refund or broken)
            hours=broken.get('hours_after_sale')
            if type(hours) in (int,float) and not 0<=hours<=6:continue
            coverage=item.get('warranty_hours') or 0
            covered=type(hours) in (int,float) and type(coverage) in (int,float) and 0<=hours<=coverage and coverage>0
            status=('confirmed' if amount is not None else 'expected_by_warranty_rule' if covered else 'not_covered')
            amount=min(item['price'],amount) if amount is not None else 0
            row={'order':oid,'unit':unit,'broken':True,'covered':covered,'canonical':True,
                 'matured':age>=21600,'refund_status':status,'refunded':amount,
                 'surplus':-item['price']+amount,'source':'market_unit_ledger',
                 'saved_contribution':0 if amount==item['price'] else -1}
        elif age>=22200 and fresh and feed>=item['paid_at']+22200 and not old.get(oid,{}).get('broken'):
            row={'order':oid,'unit':unit,'broken':False,'canonical':True,'matured':True,
                 'refund_status':'not_needed','refunded':0,'surplus':item['value']-item['price'],
                 'source':'six_hour_unit_feed_complete',
                 'saved_contribution':item.get('initial_saved_contribution',1-item['price']/max(1,item['value']))
                    if version==LEGACY and same_formula(item,version) else None,
                 'saved_lower_scenario':item.get('initial_saved_lower_scenario') if same_formula(item,version) else None,
                 'score_exact':version==LEGACY and same_formula(item,version)}
        else:continue
        row['score_formula']=version
        if broken:row['score_exact']=True
        previous=old.get(oid,{})
        # Sparse bookkeeping changes must not produce a new event every poll.
        if all(previous.get(k)==v for k,v in row.items()):continue
        pending.append(('outcome',row,'canonical-outcome:'+oid+':'+identity(row),'buyer'))
    journal.add_batch(pending,ts=now)
    return len(pending)
