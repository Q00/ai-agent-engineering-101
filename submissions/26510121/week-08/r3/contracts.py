"""A contract frontier: remember failed terms, preserve recovery and price caps."""
import time
from collections import defaultdict
from .state import identity
_IDENTITIES={}
_HISTORY={}


def shop_identity(journal,url):
    key=str(getattr(journal,'md',id(journal)));now=time.time();cached=_IDENTITIES.get(key)
    if cached is None or now-cached[0]>5:
        rows=dict(journal.snapshot('registry.json',{}));rows.update(journal.get('market-shop-identities',{}))
        if len(_IDENTITIES)>16:_IDENTITIES.clear()
        cached=(now,rows);_IDENTITIES[key]=cached
    row=cached[1].get(url,{})
    owner=row.get('owner');owner=owner.get('login') if isinstance(owner,dict) else owner
    if isinstance(owner,str) and owner:return 'owner:'+owner.casefold()
    if row.get('pubkey'):return 'key:'+identity(row['pubkey'])
    return 'url:'+url.rstrip('/')


def history(journal,now=None):
    now=time.time() if now is None else now;cache_key=str(getattr(journal,'md',id(journal)))
    cached=_HISTORY.get(cache_key)
    if cached and 0<=now-cached['now']<3:return cached
    groups=defaultdict(dict)
    for row in journal.events('buyer_contract_result',6000):
        if not 0<=now-row['_ts']<=3600:continue
        key=(row['shop_identity'],row['sku'],row.get('kind'))
        groups[key].setdefault(row['order'],row)
    paid={}
    for p in journal.events('purchase',2000):
        at=p.get('paid_at',p['_ts'])
        if 0<=now-at<=3600:
            key=(shop_identity(journal,p.get('shop','')),p.get('sku'),p.get('kind'))
            paid[key]=max(paid.get(key,0),at)
    category_groups=defaultdict(list)
    for key,values in groups.items():
        for row in values.values():
            if row.get('category'):category_groups[(key[0],row['category'])].append(row)
    result={'groups':{key:list(rows.values()) for key,rows in groups.items()},'paid':paid,'now':now,
            'category_groups':dict(category_groups)}
    if len(_HISTORY)>16:_HISTORY.clear()
    _HISTORY[cache_key]=result
    return result


def prepare(candidates,order,journal,config):
    if not config.get('system_framework_enabled') or order.get('kind')!='goods':return candidates
    context=history(journal);now=context['now'];kept=[];paused=[]
    for candidate in candidates:
        candidate['contract_gap_power']=config.get('buyer_contract_gap_power',2)
        key=(shop_identity(journal,candidate['shop']),candidate['sku'],'goods')
        rows=[r for r in context['groups'].get(key,[]) if r.get('class') in ('price','contract')]
        rows.sort(key=lambda r:r['_ts'],reverse=True)
        if not rows:
            if not category_paused(candidate,order,journal,config,context,now): kept.append(candidate)
            else: paused.append(candidate)
            continue
        recent=[r for r in rows if r.get('signature')==rows[0].get('signature')]
        burst=len(recent)>=3 and recent[0]['_ts']-recent[2]['_ts']<=600
        seconds=min(3600,600*2**min(2,max(0,len(recent)//3-1)))
        blocked=burst and now-recent[0]['_ts']<seconds and context['paid'].get(key,0)<recent[0]['_ts']
        probe=int(identity([order['order'],key])[:8],16)%100<config.get('buyer_contract_probe_percent',8)
        candidate['evidence']['contract_route']={'failed_orders':len(recent),'paused':blocked,'probe':probe,
            'until':recent[0]['_ts']+seconds if blocked else None,'stable_identity':key[0]}
        if blocked and not probe:paused.append(candidate)
        elif category_paused(candidate,order,journal,config,context,now):paused.append(candidate)
        else:kept.append(candidate)
    if not kept and paused:
        # Category-wide rejection cannot justify retrying every sibling SKU on
        # every order. Retain a timed recovery probe, even if all paths paused.
        candidate=paused[0]
        category=candidate.get('evidence',{}).get('category_frontier',{})
        key='frontier-last-probe:'+identity([shop_identity(journal,candidate['shop']),candidate['product'].get('category')])
        if not category.get('paused') or now-journal.get(key,0)>=120:
            kept=[candidate]
            kept[0].setdefault('evidence',{}).setdefault('contract_route',{})['probe']=True
            if category.get('paused'):journal.put(key,now)
    if config.get('buyer_contract_diverse_quotes'):
        unique=[];repeated=[];seen=set()
        for candidate in kept:
            (repeated if candidate['sku'] in seen else unique).append(candidate)
            seen.add(candidate['sku'])
        kept=unique+repeated
    if paused:journal.add('buyer_contract_pause',{'order':order['order'],'paused':len(paused),'kept':len(kept)},role='buyer')
    return kept


def category_paused(candidate,order,journal,config,context,now):
    if not config.get('buyer_frontier_category_enabled'): return False
    owner=shop_identity(journal,candidate['shop']);category=candidate['product'].get('category')
    if not category: return False
    rows=[r for r in context.get('category_groups',{}).get((owner,category),[]) if r.get('kind')=='goods' and r.get('class')=='price'
                    and 0<=now-r['_ts']<1200 and r.get('ask',0)>r.get('cap',float('inf'))
                    and type(r.get('list_price')) in (int,float) and r['list_price']>0]
    orders={r['order'] for r in rows};skus={r['sku'] for r in rows}
    if len(orders)<8 or len(skus)<3: return False
    term=candidate.get('evidence',{}).get('seller_term_prior',{})
    comparable=[r for r in rows if r.get('grade')==term.get('grade') and r.get('warranty_hours')==term.get('warranty_hours')]
    if len({r['order'] for r in comparable})<8: return False
    worst_ratio=min(r['ask']/r['list_price'] for r in comparable)
    improved=candidate['reference_price']/max(1,candidate['product']['list_price'])<worst_ratio*.95
    probe=int(identity([order['order'],owner,category])[:8],16)%100<config.get('buyer_contract_probe_percent',8)
    blocked=not improved and not probe
    # Actual paid recovery, including a sibling SKU, invalidates category-wide
    # negative evidence. Only term-comparable explicit refusals qualify above.
    last=max(r['_ts'] for r in comparable)
    for key,at in context['paid'].items():
        if key[0]==owner and key[2]=='goods' and at>=last:
            blocked=False
    candidate['evidence']['category_frontier']={'paused':blocked,'failed_orders':len(orders),'distinct_skus':len(skus),
        'probe':probe,'public_terms_improved':improved,'stable_identity':owner,
        'source':'same owner/category/grade/warranty; explicit above-cap no-agreement; 20 minutes'}
    return blocked


def observe(journal,order,candidate,terms,assessment,ask):
    contract={'grade':terms.get('grade'),'warranty_hours':terms.get('warranty_hours'),
              'ask':ask,'cap':assessment['cap']}
    signature=identity([contract['grade'],contract['warranty_hours'],int(ask/max(1,candidate['product']['list_price'])*50)])
    candidate['last_contract']=dict(contract,signature=signature)
    candidate.setdefault('evidence',{})['contract_frontier']={'ask':ask,'safe_cap':assessment['cap'],
        'gap_fraction':max(0,ask-assessment['cap'])/max(1,ask),'conditional':bool(assessment.get('conditional_terms'))}


def probability(journal,order,candidate,assessment,ask,base):
    if order.get('kind')!='goods':return base
    context=history(journal);key=(shop_identity(journal,candidate['shop']),candidate['sku'],'goods')
    signature=candidate.get('last_contract',{}).get('signature')
    rows=[r for r in context['groups'].get(key,[]) if r.get('signature')==signature]
    prior=base*(min(1,assessment['cap']/max(1,ask))**max(1,min(3,candidate.get('contract_gap_power',2))))
    if assessment.get('conditional_terms'):prior=min(prior,.15)
    # Failures refer to the same declared term bucket. A paid order after the
    # failures releases this penalty; term changes are evaluated afresh.
    failures=sum(r.get('class') in ('price','contract') for r in rows)
    if rows and context['paid'].get(key,0)>=max(r['_ts'] for r in rows):failures=0
    value=max(.02,min(base,2*prior/(failures+2)))
    candidate['evidence']['contract_completion']={'scenario':value,'same_term_failed_orders':failures,
        'prior':prior,'not_a_calibrated_causal_probability':True}
    return value


def failed(journal,order,candidate,reason,task=None):
    if order.get('kind')!='goods':return
    contract=candidate.get('last_contract')
    if not contract:return
    if reason=='no_agreement':category='price' if contract['ask']>contract['cap'] else 'negotiation'
    elif reason in ('quote_grade_changed','quote_warranty_changed','quote_sku_mismatch','receipt_integrity_failure','conflicting_offer_terms'):category='contract'
    else:return  # Outages, local deadlines and unknown payments are not price evidence.
    row={'order':order['order'],'kind':'goods','shop':candidate['shop'],
         'shop_identity':shop_identity(journal,candidate['shop']),'sku':candidate['sku'],
         'category':candidate['product'].get('category'),'list_price':candidate['product'].get('list_price'),
         'task':task,'class':category,'reason':reason,**contract}
    journal.add('buyer_contract_result',row,eid='contract-result:'+identity([order['order'],row['shop_identity'],candidate['sku']]),role='buyer')
