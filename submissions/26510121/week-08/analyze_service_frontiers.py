"""Mechanism checks on the fixed six-hour service audit; no live policy changes."""
from collections import Counter,defaultdict
from datetime import datetime,timezone
import json
import math
import re
import sqlite3
import statistics
from r3.state import ROOT,read_json
from r3.buyer_scoring import contribution,score_inputs
from r3.protocol import quote_terms,task_text

AUDIT='logs/v26-service-strategy-audit-20261113T033621239495Z.json'
PEERS='logs/peer-market-source-20261113T034324668548Z.json'


def main():
    audit=read_json(ROOT/AUDIT);hi=datetime.fromisoformat(audit['at']).timestamp();lo=datetime.fromisoformat(audit['since']).timestamp()
    catalog=read_json(ROOT/'.market/catalog.json');source=read_json(ROOT/PEERS)
    shops=source['responses']['/api/shops']['data']['shops'];board=source['responses']['/api/board']['data']
    accounts={r['name']:(r.get('owner') or {}).get('login') for r in board['buyers'] if isinstance(r.get('owner'),dict)}
    reviews=[]
    for s in shops:
        for r in s.get('reviews',[]):
            author=r.get('buyer') or r.get('author')
            reviews.append({'shop':s['name'],'buyer':author,'stars':r.get('stars'),'review':r.get('review'),
                'owner':accounts.get(author)})
    cross=[]
    for author in ('sergiobuilds','dy-76','dyishappy','11im'):
        rs=[r for r in reviews if r.get('owner')==author or r.get('buyer')==author]
        cross.append({'author':author,'sample':len(rs),'shops':len({r['shop'] for r in rs}),
            'mean':statistics.mean(r['stars'] for r in rs) if rs else None,'examples':rs[:6]})
    with sqlite3.connect((ROOT/'.market/r3.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
        db.execute('BEGIN')
        def events(k):
            return [dict(json.loads(raw),_ts=ts) for ts,raw in db.execute(
                'SELECT ts,data FROM events WHERE kind=? AND ts>=? AND ts<? ORDER BY ts,rowid',(k,lo,hi))]
        orders={r['order']:r for r in events('buyer_order')};paid={r['order']:r for r in events('purchase')};closed={r['order'] for r in events('market_result')}
        qs=events('quote_comparison');wire=events('buyer_quote_wire');decisions={r.get('decision'):r.get('chosen') for r in events('decision')}
        groups=defaultdict(list)
        for r in wire:groups[(r.get('order'),r.get('shop'))].append(r)
        unknown=Counter();parser_cases=[];recoverable=[];first_counter={};positive_skip=0;accept_counter_lost=[]
        incomplete_orders=set();incomplete_unpaid=set();zero_warranty_opportunities=[]
        for q in qs:
            if orders.get(q['order'],{}).get('kind')!='goods':continue
            a=q['assessment'];conditional=a.get('conditional_terms') or [];unknown['total_goods_quotes']+=1
            if conditional:
                unknown['incomplete']+=1;unknown['+'.join(sorted(conditional))]+=1
                incomplete_orders.add(q['order'])
                if q['order'] in closed and q['order'] not in paid:incomplete_unpaid.add(q['order'])
            if conditional==['warranty_hours']:
                # Optional *explicitly uninsured* contract scenario; never assume
                # an unstated warranty exists or pay without a verified grade.
                f=a['failure'];L=catalog[q['sku']]['list_price'];order=orders[q['order']]
                safe=min(order['budget'],L*(1-f))
                v=contribution(q['ask'],L,f,0,**score_inputs(a['saved_state']))
                if q['ask']<=safe and v>=a['saved_state']['saved']:
                    zero_warranty_opportunities.append({'order':q['order'],'shop':q['shop'],'sku':q['sku'],
                        'ask':q['ask'],'failure':f,'saved_if_explicitly_uninsured_scenario':v,
                        'currently_unpaid':q['order'] in closed and q['order'] not in paid})
            complete=not conditional and q['ask']<=a['cap']
            expected=contribution(q['ask'],catalog[q['sku']]['list_price'],a['failure'],a['covered_failure'],**score_inputs(a['saved_state']))
            if q['order'] in closed and q['order'] not in paid and complete and expected>=a['saved_state']['saved']:
                recoverable.append({'order':q['order'],'shop':q['shop'],'sku':q['sku'],'ask':q['ask'],
                    'expected_contribution_scenario':expected,'current_saved':a['saved_state']['saved']})
            if 'warranty_hours' not in conditional:continue
            before=[r for r in groups[(q['order'],q['shop'])] if 0<=q['_ts']-r['_ts']<=15]
            if not before:continue
            r=before[-1];task={'metadata':r.get('metadata',{}),'status':{'state':r.get('state'),'message':r.get('message',{})}}
            text=task_text(task)
            hits=re.findall(r'\bwarranty\s*[:=]\s*(\d+(?:\.\d+)?)\s*(?:h|hours?)\b',text,re.I)
            if hits and len(set(hits))==1:
                parser_cases.append({'order':q['order'],'shop':q['shop'],'text':text[:1000],'additional_warranty_pattern':float(hits[0])})
        for ts,raw in db.execute("SELECT ts,data FROM events WHERE kind='prediction' AND ts>=? AND ts<? AND json_extract(data,'$.role')='buyer' ORDER BY ts,rowid",(lo,hi)):
            p=json.loads(raw);ctx=p.get('context',{});oid=(ctx.get('order') or {}).get('order');kind=orders.get(oid,{}).get('kind')
            if ctx.get('operation') not in ('price','final_purchase') or kind!='goods':continue
            chosen=decisions.get(p.get('decision'));cs=p.get('candidates',[]);selected=next((c for c in cs if c.get('id')==chosen),{})
            accept=next((c for c in cs if c.get('id')=='accept'),None)
            if chosen=='continue_search' and accept and (accept.get('objective_utility') or 0)>0:positive_skip+=1
            if selected.get('action',selected.get('id','').partition(':')[0])=='counter':
                key=(oid,ctx.get('shop'))
                first_counter.setdefault(key,{'order':oid,'shop':key[1],'price':selected['price'],
                    'sku':ctx.get('sku'),'ask':ctx.get('ask'),'predicted':selected.get('completion_probability'),'at':ts})
                if accept and oid in closed and oid not in paid and (accept.get('objective_utility') or 0)>0:
                    accept_counter_lost.append({'order':oid,'shop':key[1],'ask':ctx.get('ask'),
                        'accept_utility':accept['objective_utility'],'counter_utility':selected.get('objective_utility'),
                        'counter_probability':selected.get('completion_probability')})
        calibration=[]
        for r in first_counter.values():
            if r['order'] not in closed or r['predicted'] is None:continue
            purchase=paid.get(r['order'],{});same=purchase.get('shop')==r['shop'] and purchase.get('sku')==r['sku']
            calibration.append(dict(r,paid_eventually=same,paid_at_first_bid_or_less=same and purchase.get('price',math.inf)<=r['price'],
                different_sku_false_positive=purchase.get('shop')==r['shop'] and purchase.get('sku')!=r['sku']
                    and purchase.get('price',math.inf)<=r['price']))
        stock=events('stockout');shelf=[r for r in stock if r.get('reason')=='shelf_life']
        impossible=[r for r in shelf if r.get('required_use_hours',0)>catalog.get(r.get('sku'),{}).get('shelf_hours',math.inf)]
    count=len(calibration)
    output={'audit':AUDIT,'peer_source':PEERS,'incomplete_terms':dict(unknown),
        'goods_orders_with_incomplete_quotes':len(incomplete_orders),
        'unpaid_goods_orders_with_incomplete_quotes':len(incomplete_unpaid),
        'explicit_zero_warranty_positive_scenarios':{
            'quote_count':len(zero_warranty_opportunities),
            'unpaid_orders':len({r['order'] for r in zero_warranty_opportunities if r['currently_unpaid']}),
            'examples':zero_warranty_opportunities[:8]},
        'additional_warranty_parser_matches':len(parser_cases),'warranty_parser_examples':parser_cases[:10],
        'closed_unpaid_orders_with_full_terms_quote_at_or_above_saved_reference':len({r['order'] for r in recoverable}),
        'recoverable_quote_scenarios':recoverable[:12],
        'positive_accept_incorrectly_skipped_by_main':positive_skip,
        'closed_unpaid_orders_after_counter_over_positive_accept':len({r['order'] for r in accept_counter_lost}),
        'accept_counter_examples':accept_counter_lost[:10],
        'first_goods_counter_calibration':{'n':count,'mean_predicted':statistics.mean(r['predicted'] for r in calibration) if count else None,
            'paid_at_first_bid_or_less':sum(r['paid_at_first_bid_or_less'] for r in calibration),
            'paid_eventually_same_seller_same_sku':sum(r['paid_eventually'] for r in calibration),
            'different_sku_false_positive_under_old_join':sum(r['different_sku_false_positive'] for r in calibration)},
        'public_recent_review_comparisons':cross,'review_schema':list((shops[0].get('reviews') or [{}])[0]),
        'shelf_rejections':len(shelf),'requests_beyond_catalog_shelf_hours':len(impossible),
        'numeric_quote_score_formula_mismatch':{'current_legacy_example':[.4,.6],
            'margin_share_example':[1.,.8],'example':'L=100; A cost=70 price=60 versus C cost=25 price=40; healthy scenario'},
        'market_transactions':0,'model_calls':0,'policy_writes':0,
        'limitations':['Current peer review samples are each shops newest limited records, not unbiased reviewer cohorts.',
            'A lower paid-at-first-bid rate can include subsequent concessions or self-cancellation; it does not identify pure seller refusal.',
            'A quote available earlier is not guaranteed reservable at checkout; recoverable counts are opportunities, not guaranteed conversions.',
            'Numeric score inconsistency is in the RFQ score/ordering; Main already uses whole-objective deltas.']}
    path=ROOT/'logs'/('v26-service-frontier-analysis-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json')
    path.open('x',encoding='utf-8').write(json.dumps(output,ensure_ascii=False,indent=2));print('SOURCE',path)
    print(json.dumps({k:v for k,v in output.items() if k not in ('public_recent_review_comparisons','warranty_parser_examples','recoverable_quote_scenarios','accept_counter_examples','limitations')},ensure_ascii=True))


if __name__=='__main__':main()
