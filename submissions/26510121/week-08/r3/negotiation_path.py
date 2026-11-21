"""Exact-task continuation price scenarios, gated by temporal predictive checks."""
from collections import defaultdict
import math
import json
import statistics
import time
from .state import identity

KEY='buyer-negotiation-path:v26'


def bootstrap(journal,now=None):
    """Recover historical task IDs only from an unambiguous logged decision.

    Raw events are immutable. Derived records retain both source event IDs.
    Incomplete contracts and ambiguous joins do not enter the new cohort.
    """
    now=time.time() if now is None else now;cut=max(now-21600,journal.get('market_rules',{}).get('buyer_score_epoch',0))
    with journal.connect() as db:
        decisions={r.get('decision'):r.get('chosen') for raw, in db.execute(
            'SELECT data FROM events WHERE kind=? AND role=? AND ts>=? AND ts<=?',('decision','buyer',cut,now)) for r in [json.loads(raw)]}
        predictions=[dict(json.loads(raw),_event_id=eid,_ts=at) for eid,at,raw in db.execute(
            'SELECT id,ts,data FROM events WHERE kind=? AND role=? AND ts>=? AND ts<=?',('prediction','buyer',cut,now))]
    raw=defaultdict(list)
    for r in journal.events('buyer_counter',10000):
        if r['_ts']>=cut and not r.get('task'):raw[(r['order'],r['shop'],r.get('sku'),r['price'])].append(r)
    quotes=defaultdict(list)
    for r in journal.events('quote_comparison',10000):
        if r['_ts']>=cut:quotes[(r['order'],r['shop'],r['sku'])].append(r)
    matches=defaultdict(list)
    for p in predictions:
        if p['_ts']<cut or p.get('role')!='buyer':continue
        ctx=p.get('context',{});oid=(ctx.get('order') or {}).get('order')
        if ctx.get('operation')!='price' or not ctx.get('task'):continue
        chosen=next((c for c in p.get('candidates',[]) if c.get('id')==decisions.get(p.get('decision'))),{})
        if chosen.get('action',chosen.get('id','').partition(':')[0])!='counter':continue
        key=(oid,ctx.get('shop'),ctx.get('sku'),chosen.get('price'))
        for r in raw.get(key,[]):
            if 0<=r['_ts']-p['_ts']<=3:matches[r['_event_id']].append((r,p,ctx))
    new=[]
    for candidates in matches.values():
        if len(candidates)!=1:continue
        r,p,ctx=candidates[0];qs=[q for q in quotes[(r['order'],r['shop'],r['sku'])] if q['_ts']<=p['_ts']]
        if not qs:continue
        a=max(qs,key=lambda q:q['_ts'])['assessment']
        if a.get('conditional_terms'):continue
        row={k:r.get(k) for k in ('order','shop','sku','ask','price','score_formula')}
        row.update(task=ctx['task'],turn=ctx.get('turn',0),grade=a.get('required_grade'),
            warranty_hours=a.get('required_warranty',0),terms_complete=True,source_counter=r['_event_id'],
            source_prediction=p['_event_id'],observed_at=r['_ts'],derived=True)
        new.append(('buyer_counter_route',row,'counter-derived:'+r['_event_id'],'buyer'))
    if new:journal.add_batch(new,ts=now)
    return len(new)


def group(row):
    return '|'.join((row['kind'],str(row.get('grade')),str(row.get('warranty_hours',0)>=6),str(min(19,int(row['price']/row['ask']*20)))))


def fit(rows):
    groups=defaultdict(list)
    for r in rows:groups[group(r)].append(r)
    base=(sum(r['paid'] for r in rows)+1)/(len(rows)+2)
    out={}
    for key,rs in groups.items():
        positive=[r for r in rs if r['paid']];ratios=sorted(r['final_ratio'] for r in positive)
        out[key]={'n':len(rs),'paid':len(positive),'probability':(len(positive)+4*base)/(len(rs)+4),
            'final_price_ratios':[ratios[round((len(ratios)-1)*q)] for q in (.25,.5,.75)] if ratios else [],
            'seconds':statistics.median(r['seconds'] for r in rs)}
    return {'groups':out,'baseline_probability':base}


def evaluate(rows):
    split=int(len(rows)*.7);train,test=rows[:split],rows[split:];m=fit(train)
    errors=[];base=[];price=[];ask=[];covered=0
    for r in test:
        cell=m['groups'].get(group(r),{});p=cell.get('probability',m['baseline_probability'])
        errors.append((r['paid']-p)**2);base.append((r['paid']-m['baseline_probability'])**2)
        if cell.get('n',0)>=4:covered+=1
        if r['paid'] and cell.get('final_price_ratios'):
            price.append(abs(r['final_ratio']-statistics.median(cell['final_price_ratios'])))
            ask.append(abs(r['final_ratio']-1))
    mse=statistics.mean(errors) if errors else None;b=statistics.mean(base) if base else None
    ready=(len(train)>=30 and len(test)>=15 and covered>=len(test)*.5 and mse is not None and b>0
        and mse<b*.98 and len(price)>=5 and statistics.mean(price)<=statistics.mean(ask))
    return {'mode':'validated_predictive_scenario' if ready else 'shadow','train':len(train),'test':len(test),
        'brier':mse,'baseline_brier':b,'covered_test':covered,'price_test':len(price),
        'price_mae':statistics.mean(price) if price else None,'ask_mae':statistics.mean(ask) if ask else None,
        'temporal_split':.7,'not_causal':True}


def update(journal,config,now=None):
    if not config.get('service_contract_v26_enabled'):return None
    now=time.time() if now is None else now;old=journal.get(KEY,{})
    if 0<=now-old.get('at',0)<60:return old
    cut=max(now-21600,journal.get('market_rules',{}).get('buyer_score_epoch',0))
    catalog=journal.snapshot('catalog.json',{});first={}
    for r in reversed(journal.events('buyer_counter_route',10000)):
        at=r.get('observed_at',r['_ts'])
        if not cut<=at<now-30 or not r.get('terms_complete') or not r.get('task') or r.get('ask',0)<=0:continue
        if r.get('score_formula')!=journal.get('market_rules',{}).get('buyer_saved_formula'):continue
        first.setdefault((r['order'],r['shop'],r['sku'],r['task']),dict(r,at=at))
    purchases={r['order']:r for r in reversed(journal.events('purchase',5000)) if r['_ts']>=cut}
    results={r['order']:r for r in reversed(journal.events('market_result',5000)) if r['_ts']>=cut}
    agreements=defaultdict(list)
    for a in journal.events('agreement',10000):
        if a['_ts']>=cut:agreements[a['order']].append(a)
    attempts=journal.events('attempt',10000);samples=[]
    for key,r in first.items():
        oid,shop,sku,task=key
        if oid not in results:continue
        p=purchases.get(oid);same=bool(p and p.get('shop')==shop and p.get('sku')==sku and
            any(a.get('task')==task and a.get('shop')==shop and a.get('sku')==sku and
                a.get('price')==p['price'] for a in agreements[oid]))
        technical=any(a.get('order')==oid and a.get('shop')==shop and a['_ts']>=r['at'] and
            a.get('kind') in ('ReadTimeout','TimeoutError','ConnectError','HTTPStatusError') for a in attempts)
        if technical and not same:continue  # Uncertain transport is not a price refusal.
        end=p.get('paid_at',p['_ts']) if same else results[oid]['_ts']
        if not 0<=end-r['at']<=180:continue
        samples.append(dict(r,kind=catalog.get(sku,{}).get('kind','goods'),paid=same,
            final_ratio=p['price']/r['ask'] if same else None,seconds=max(1,end-r['at'])))
    samples.sort(key=lambda r:r['at']);summary=evaluate(samples)
    row={'at':now,'expires_at':now+180,'rules_hash':journal.get('market_rules',{}).get('sha256'),
        'model':fit(samples),'summary':summary,'samples':len(samples),
        'join':'order+shop+sku+task+settled price','source':'observed entire policy continuation; not seller acceptance probability'}
    row['id']=identity(row);journal.put(KEY,row)
    journal.add('negotiation_path_prepared',{'at':now,'id':row['id'],'summary':summary,'samples':len(samples)})
    return row


def context(journal,product,grade,warranty,now=None):
    from .decision_view import fact
    now=time.time() if now is None else now
    row=fact(journal,'negotiation-path-v26',lambda:journal.get(KEY,{}))
    if (not -2<=now-row.get('at',-1000)<180 or now>=row.get('expires_at',0) or
            row.get('rules_hash')!=journal.get('market_rules',{}).get('sha256') or
            row.get('summary',{}).get('mode')!='validated_predictive_scenario'):return None
    return {'id':row['id'],'kind':product.get('kind'),'grade':grade,'warranty_hours':warranty,
        'model':row['model'],'summary':row['summary']}


def scenario(model,bid,ask,cap):
    if not model:return None
    key=group(dict(model,price=bid,ask=ask))
    row=model['model']['groups'].get(key,{})
    if row.get('n',0)<8 or not row.get('final_price_ratios'):return None
    prices=[max(bid,math.ceil(ask*r)) for r in row['final_price_ratios']]
    valid=[p for p in prices if p<=cap]
    if not valid:return {'probability':0,'prices':[],'seconds':row['seconds'],'id':model['id']}
    return {'probability':row['probability']*len(valid)/len(prices),'prices':valid,
        'seconds':row['seconds'],'id':model['id'],'selected_policy_not_causal':True}
