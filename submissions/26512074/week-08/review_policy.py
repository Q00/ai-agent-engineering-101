"""Account-mean marginal Stars; anonymous buyers never get invented high ratings."""
import time
from pathlib import Path
from optimizer import read
from strategy_policy import database


def identity(md,texts,metadata):
    import re,json
    if not isinstance(metadata,dict) and hasattr(metadata,'DESCRIPTOR'):
        from google.protobuf.json_format import MessageToDict
        metadata=MessageToDict(metadata)
    metadata=metadata if isinstance(metadata,dict) else {}
    claimed=metadata.get('buyer',metadata.get('buyer_id',metadata.get('buyerId')))
    if not claimed:
        claimed=next((m[1] for text in texts if (m:=re.search(r'\bbuyer\s*[:=]\s*([\w.-]+)',text,re.I))),None)
    order=metadata.get('order',metadata.get('order_id'))
    if isinstance(order,dict):order=order.get('id',order.get('order'))
    if not isinstance(order,str):order=None
    if not isinstance(claimed,str):claimed=None
    if claimed and order:
        with database(md) as c:
            rows=c.execute("select payload from market_events where kind='order' and ts>? and json_extract(payload,'$.data.order')=?",(time.time()-3600,order)).fetchall()
        for row in rows:
            d=json.loads(row[0])['data']
            if str(d.get('buyer','')).casefold()==str(claimed).casefold() and d.get('expires',0)>time.time():
                return str(d['buyer']),'official_order_record_matched_claim'
    return None,'unverified_claim' if claimed else 'anonymous'


def marginal(md,buyer,grade):
    if not buyer:return 0.
    from patch36 import document
    state=document(Path(md)/'review-account-model.json')
    if not 0<=time.time()-state.get('at',0)<900:return 0.
    accounts=state.get('accounts',{});n=len(accounts)
    if not n:return 0.
    global_mean=state.get('observed_account_mean')
    if global_mean is None:return 0.
    g=state.get('grade_reviews',{}).get(grade,{})
    expected=(g.get('mean',global_mean)*g.get('n',0)+global_mean*4)/(g.get('n',0)+4)
    old=accounts.get(buyer)
    if old:expected=(old['mean']*old['n']+4*expected)/(old['n']+4)
    gain=((old['mean']*old['n']+expected)/(old['n']+1)-old['mean'])/n if old else (expected-global_mean)/(n+1)
    coverage=state.get('coverage_fraction')
    return gain*(coverage if coverage is not None else 1)


def context_value(md,task,grade):
    with database(md) as c:r=c.execute('select buyer from quote_context where task=?',(task,)).fetchone()
    return marginal(md,r['buyer'] if r else None,grade)
