"""Read-only v20 operating-window economics and shared API cost reconciliation."""
from collections import Counter,defaultdict
from datetime import datetime,timezone,timedelta
import json
from pathlib import Path
import sqlite3
from r3.state import ROOT,read_json,policy

KST=timezone(timedelta(hours=9))


def main():
    start_path=ROOT/'logs/autonomous-deploy-before-20261112T053423471758Z.json'
    end_path=ROOT/'logs/autonomous-deploy-before-20261112T082837273128Z.json'
    before,after=read_json(start_path),read_json(end_path);lo,hi=before['at'],after['at'];hours=(hi-lo)/3600
    own=lambda board:next(r for r in board['shops'] if r['name']=='seochanit')
    b,a=own(before['board']),own(after['board']);inv0,inv1=before['inventory'],after['inventory']
    stock0=sum(u['cost'] for u in inv0['units']);stock1=sum(u['cost'] for u in inv1['units'])
    with sqlite3.connect((ROOT/'.market/r3.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
        db.execute('BEGIN')
        def events(kind,start=lo,end=hi):
            return [dict(json.loads(raw),event_at=ts) for ts,raw in db.execute(
                'SELECT ts,data FROM events WHERE kind=? AND ts>=? AND ts<=? ORDER BY ts,rowid',(kind,start,end))]
        all_sales=events('shop_settled',0,hi);units={r['unit']:r for r in all_sales if r.get('unit')}
        sales={r['unit']:r for r in all_sales if lo<=r.get('paid_at',r['event_at'])<=hi}
        refunds={}
        for row in events('public_broken')+events('public_refund'):
            amount=row.get('refunded',row.get('amount'));unit=row.get('unit')
            if unit in units and type(amount) in (int,float) and amount>=0:refunds[unit]=max(refunds.get(unit,0),amount)
        supply=events('supply_execution_status');usage=events('model_usage');research=events('market_research_published')
        day_start=datetime.fromtimestamp(hi,KST).replace(hour=0,minute=0,second=0,microsecond=0).timestamp()
        day_usage=events('model_usage',day_start,hi)
    cfg=policy()
    def costs(rows):
        buckets=defaultdict(lambda:{'calls':0,'priced_estimate_usd':0.,'conservative_recorded_usd':0.,'output_tokens':0})
        for row in rows:
            model=row.get('model','unknown');u=row.get('usage') or {};rates=cfg.get('prices_usd_per_million',{}).get(model)
            cache=cfg.get('cache_prices_usd_per_million',{}).get(model)
            details=u.get('input_tokens_details') or {};cached=details.get('cached_tokens',0);written=details.get('cache_write_tokens',0)
            price=row.get('estimated_usd',0)
            if rates and cache:
                price=(max(0,u.get('input_tokens',0)-cached-written)*rates[0]+cached*cache[0]+written*cache[1]+u.get('output_tokens',0)*rates[1])/1e6
            key=row.get('role','unknown')+' / '+model;entry=buckets[key]
            entry['calls']+=1;entry['priced_estimate_usd']+=price;entry['conservative_recorded_usd']+=row.get('estimated_usd',0)
            entry['output_tokens']+=u.get('output_tokens',0)
        return {'calls':sum(b['calls'] for b in buckets.values()),'priced_estimate_usd':sum(b['priced_estimate_usd'] for b in buckets.values()),
                'conservative_recorded_usd':sum(b['conservative_recorded_usd'] for b in buckets.values()),'by_role_and_model':dict(buckets)}
    known=[r for r in sales.values() if type(r.get('cost')) in (int,float)]
    gross=sum(r['price']-r['cost'] for r in known)
    refund_details=[{'unit':unit,'sku':units[unit].get('sku'),'amount':amount,
        'sale_price':units[unit].get('price'),'sale_cost':units[unit].get('cost'),
        'sale_paid_at':units[unit].get('paid_at'),'sale_in_review_window':lo<=units[unit].get('paid_at',0)<=hi,
        'grade':units[unit].get('grade'),'warranty_hours':units[unit].get('warranty_hours')}
        for unit,amount in refunds.items() if amount>0]
    asset_delta=inv1['balance']+stock1-inv0['balance']-stock0
    sold=set(sales);remaining={u['unit'] for u in inv1['units']}
    expired_initial=[u for u in inv0['units'] if u.get('expires_at') and lo<=u['expires_at']<=hi and u['unit'] not in sold|remaining]
    acquired=sum(r.get('qty',0) for r in supply if r.get('status')=='complete')
    catalog=read_json(ROOT/'.market/catalog.json',{});mix={}
    for kind in ('food','goods'):
        rows=[r for r in known if catalog.get(r['sku'],{}).get('kind')==kind]
        mix[kind]={'sales':len(rows),'revenue':sum(r['price'] for r in rows),'gross_margin':sum(r['price']-r['cost'] for r in rows)}
    def component(board,metric):
        me=own(board);eligible=[r for r in board['shops'] if type(r.get('all')) in (int,float) and type(r.get(metric)) in (int,float)]
        return {'value':me.get(metric),'rank_by_published_value':1+sum(r[metric]>me[metric] for r in eligible),'population':len(eligible)}
    peers=[];old={r['name']:r for r in before['board']['shops']}
    top={r['name'] for r in sorted((r for r in after['board']['shops'] if r.get('all') is not None),key=lambda r:r['all'],reverse=True)[:7]}|{'seochanit'}
    for row in after['board']['shops']:
        if row['name'] not in top or row['name'] not in old:continue
        prior=old[row['name']]
        peers.append({'name':row['name'],'all':row.get('all'),'book_delta':row['book']-prior['book'],
            'book_per_hour':(row['book']-prior['book'])/hours,'revenue_delta':row['revenue']-prior['revenue'],
            'deals_delta':row['deals']-prior['deals']})
    result={'window_kst':[datetime.fromtimestamp(t,KST).isoformat() for t in (lo,hi)],'hours':hours,
        'version_scope':'v20 operating interval, before v21 deployment; includes boundary snapshot/poll timing',
        'sources':[str(p.relative_to(ROOT)) for p in (start_path,end_path)],
        'official':{'all_before':b['all'],'all_after':a['all'],'book_delta':a['book']-b['book'],
            'revenue_delta':a['revenue']-b['revenue'],'deals_delta':a['deals']-b['deals'],
            'components':{m:{'before':component(before['board'],m),'after':component(after['board'],m)} for m in ('all','book','revenue','stars_rank')}},
        'assets':{'cash_delta':inv1['balance']-inv0['balance'],'stock_cost_delta':stock1-stock0,
            'cash_plus_stock_cost_delta':asset_delta,'start_units':len(inv0['units']),'end_units':len(inv1['units']),
            'confirmed_acquired_units':acquired,'local_distinct_sold_units':len(sales),
            'unit_reconciliation_unclassified_removed':len(inv0['units'])+acquired-len(sales)-len(inv1['units']),
            'known_expired_initial_units':len(expired_initial),'known_expired_initial_cost':sum(u['cost'] for u in expired_initial)},
        'local_economics':{'gross_margin':gross,'missing_sale_costs':len(sales)-len(known),
            'below_cost_sales':sum(r['price']<r['cost'] for r in known),'unit_matched_confirmed_refunds':sum(refunds.values()),
            'refund_details':refund_details,
            'unclassified_consumption_or_timing_residual':gross-sum(refunds.values())-asset_delta,
            'goods_holding_cost_per_day_at_end_stock':sum(u['cost'] for u in inv1['units'] if u.get('kind')=='goods')*.01,
            'sales_mix':mix},'matched_peer_changes':peers,
        'research_repetition':{'publications':len(research),
            'cover_cycles_histogram':dict(Counter(str(r.get('operating_policy',{}).get('cover_cycles')) for r in research)),
            'distinct_operating_policies':len({json.dumps(r.get('operating_policy'),sort_keys=True) for r in research})},
        'shared_api_cost_in_window':costs(usage),'daily_shared_api_cost':costs(day_usage),
        'explicitly_marked_validation_cost_in_day':costs([r for r in day_usage if r.get('validation_only')]),
        'unresolved_costs_not_invoice':True,'model_calls_by_this_script':0,'market_transactions':0,
        'limits':['Virtual market asset gains cannot be divided by real USD API spend as monetary ROI.',
                  'Residual is not assigned entirely to storage, expiry or fees without a full cash ledger.',
                  'Public component rankings are based on rounded published values, not the unpublished tie rule.',
                  'Research API cost serves Shop and Buyer jointly; this does not attribute all of it to Shop.']}
    path=ROOT/'logs'/('v21-shop-cost-review-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json')
    path.open('x',encoding='utf-8').write(json.dumps(result,ensure_ascii=False,indent=2));print('SOURCE',path)
    print(json.dumps(result,ensure_ascii=True))


if __name__=='__main__':main()
