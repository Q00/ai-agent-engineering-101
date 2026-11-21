"""Route by observed terms, recent technical reliability and own settled history."""
import time
from .decision_view import history
from .buyer_scoring import contribution,terms_delta,score_inputs


def context(journal,now=None):
    now=time.time() if now is None else now
    since=max(now-900,journal.get('buyer_revision_epoch',0));errors={}
    for e in history(journal,'attempt',1500):
        if not since<=e['_ts']<=now:continue
        if e.get('kind') not in ('rfq_failed','ReadTimeout','HTTPStatusError','ConnectError') and e.get('reason')!='ambiguous_ask':continue
        shop=e.get('shop')
        if shop:errors[shop]=errors.get(shop,0)+1
    paid={(p.get('order'),p.get('shop')) for p in history(journal,'purchase',2000)
          if 0<=now-p.get('paid_at',p['_ts'])<=1800}
    proofs={}
    for e in history(journal,'agreement',3000):
        if (e.get('order'),e.get('shop')) in paid:
            rows=proofs.setdefault(e['shop'],[])
            if e.get('task') and e['task'] not in rows and len(rows)<3:rows.append(e['task'])
    return {'errors':errors,'paid_task_refs':proofs}


def adjust(candidate,order,journal,config,route_context):
    from .market_memory import buyer_assessment
    observed=candidate['evidence']['public_sku_prices'];shop=candidate['shop']
    terms=[r for r in observed.get('seller_prices',[]) if r['shop']==shop and r['n']>=3 and r['grade'] in ('A','B','C')]
    options=[]
    for term in terms:
        price=term['median']
        assessment=buyer_assessment(dict(candidate['product'],sku=candidate['sku']),order,term,journal,config,shop=shop)
        q,covered=assessment['failure'],assessment['covered_failure']
        completion=.7 if price<=assessment['cap'] else .1
        options.append((completion*contribution(price,candidate['product']['list_price'],q,covered,
                       **score_inputs(assessment['saved_state'])),term,assessment,completion))
    failures=route_context['errors'].get(shop,0)
    reliability=1/(1+.35*min(8,failures))
    if options:
        score,term,assessment,completion=max(options,key=lambda r:r[0])
        candidate['reference_price']=int(term['median'])
        candidate['score']=max(0,score)*reliability
        candidate['expected_delta']=terms_delta(assessment['saved_state'],term['median'],
            candidate['product']['list_price'],assessment['failure'],assessment['covered_failure'],completion*reliability)
        candidate['evidence']['seller_term_prior']=dict(term,assessment=assessment,historical_not_live=True)
    else:
        candidate['score']*=reliability
        candidate['expected_delta']={k:v*reliability for k,v in candidate['expected_delta'].items()}
    candidate['paid_task_refs']=route_context['paid_task_refs'].get(shop,[])
    candidate['evidence']['route_reliability']={'recent_technical_failures':failures,'factor':reliability,
                                              'window_seconds':900,'not_a_price_rejection_model':True}
    candidate['evidence']['public_sku_prices']={k:v for k,v in observed.items() if k!='seller_prices'}
    if config.get('service_contract_v26_enabled'):
        from .service_policy import route_adjust
        candidate=route_adjust(candidate,journal)
    return candidate
