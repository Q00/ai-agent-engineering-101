"""Public ecosystem and own architecture review; no network or journal writes.

Input is a preserved public GET snapshot. SQLite queries are restricted to its
cutoff, and seller identities resolve only through observed board URLs.
"""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import statistics
import sys

ROOT = Path(__file__).resolve().parent


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def analyze(source_path):
    source = read(source_path)
    raw = source['responses']
    assert all(raw[p].get('status') == 200 for p in ['/agent.md','/api/board','/api/catalog','/api/shops'])
    end = datetime.fromisoformat(source['checked_at']).timestamp()
    start = end-21600
    board = raw['/api/board']['data']
    catalog = {x['sku']:x for x in raw['/api/catalog']['data']}
    shops = raw['/api/shops']['data']['shops']
    rules = raw['/agent.md']['data']
    c = sqlite3.connect((ROOT/'.market/r3.sqlite3').as_uri()+'?mode=ro',uri=True)
    c.execute('BEGIN')
    def events(kind, since=start):
        return [dict(json.loads(d), _ts=t) for t,d in c.execute(
            'SELECT ts,data FROM events WHERE kind=? AND ts>? AND ts<=? ORDER BY ts,rowid', (kind,since,end))]
    public = events('public_settle')
    own_sales = events('shop_settled')
    own_units = {e['unit'] for e in own_sales}
    purchases = events('purchase')
    orders = events('buyer_order')
    stock = defaultdict(lambda: {'sellers':set(),'units':0})
    for shop in shops:
        for sku,qty in shop.get('stock',{}).items():
            if qty>0 and shop['name']!='seochanit':
                stock[sku]['sellers'].add(shop['name'])
                stock[sku]['units']+=qty
    grouped = defaultdict(list)
    for e in public:grouped[e.get('item') or e.get('sku')].append(e)
    sku_rows=[]
    for sku,es in grouped.items():
        p=catalog.get(sku,{})
        external=[e for e in es if e.get('buyer')!='seochanit']
        grades=defaultdict(list)
        for e in es:grades[(e.get('grade'),e.get('warranty_hours') or 0)].append(e['amount'])
        sku_rows.append({'sku':sku,'name':p.get('name'),'category':p.get('category'),'kind':p.get('kind'),
            'list_price':p.get('list_price'),'settlements':len(es),'gross_amount':sum(e['amount'] for e in es),
            'external_buyer_settlements':len(external),'own_buyer_settlements':len(es)-len(external),
            'median_price':statistics.median(e['amount'] for e in es),
            'buyers':Counter(e['buyer'] for e in es).most_common(5),
            'stocking_competitors_now':len(stock[sku]['sellers']),'competitor_units_now':stock[sku]['units'],
            'equal_share_external_scenario_per_hour':len(external)/6/(len(stock[sku]['sellers'])+1),
            'grade_warranty_prices':[{'grade':g,'warranty_hours':w,'n':len(ps),'median':statistics.median(ps)}
                                     for (g,w),ps in grades.items()]})
    sku_rows.sort(key=lambda x:x['gross_amount'],reverse=True)
    identity=defaultdict(set)
    for row in board['shops']:
        if row.get('card',{}).get('url'):identity[row['card']['url'].rstrip('/')].add(row['name'])
    # Historical URLs are admitted only when a preserved board maps them.
    for path in (ROOT/'logs').glob('*.json'):
        if path.stat().st_size>4000000:continue
        try:d=read(path)
        except (ValueError,OSError):continue
        if not isinstance(d,dict):continue
        b=d.get('board',d.get('before_board',{}))
        if not isinstance(b,dict):continue
        for row in b.get('shops',[]):
            if isinstance(row,dict) and row.get('card',{}).get('url'):
                identity[row['card']['url'].rstrip('/')].add(row['name'])
    def seller(url):
        names=identity[url.rstrip('/')]
        return next(iter(names)) if len(names)==1 else url
    flow=defaultdict(lambda:{'n':0,'gross':0})
    for e in public:
        row=flow[(e['buyer'],seller(e['shop']))]
        row['n']+=1;row['gross']+=e['amount']
    buyers=defaultdict(Counter)
    for e in public:buyers[e['buyer']][catalog.get(e.get('item'),{}).get('category','unknown')]+=1
    own_shop=next(x for x in board['shops'] if x['name']=='seochanit')
    own_buyer=next(x for x in board['buyers'] if x['name']=='seochanit')
    players=sorted([x for x in board['players'] if not x.get('house') and type(x.get('total')) in (int,float)],key=lambda x:x['total'],reverse=True)
    me=next(x for x in players if x['player'].lower()=='seochanit')
    meta=(ROOT/'r3/meta.py').read_text(encoding='utf-8')
    economy=(ROOT/'r3/economy.py').read_text(encoding='utf-8')
    config=read(ROOT/'policy.json')
    inv_path=ROOT/'.market/inventory.json'
    inv=read(inv_path)
    result={'source':str(Path(source_path).resolve().relative_to(ROOT)), 'checked_at':source['checked_at'],
        'public_window':{'hours':6,'start_epoch':start,'cutoff_epoch':end,'rows':len(public),
            'latest_settlement_epoch':max(e['_ts'] for e in public),'gross_amount':sum(e['amount'] for e in public)},
        'universe':{'catalog_skus':len(catalog),'categories':len({x['category'] for x in catalog.values()}),
            'kinds':dict(Counter(x['kind'] for x in catalog.values())),'listed_shops':len(shops),
            'online_agents':board['totals'].get('agents_online')},
        'rank':{'own':me,'leaders':players[:5],'own_rank':1+sum(x['total']>me['total'] for x in players),
            'own_shop':own_shop,'own_buyer':own_buyer,
            'shop_only_optimistic_total_ceiling':1+own_buyer['all'],
            'buyer_needed_if_shop_is_1':players[0]['total']-1},
        'rule_audit':{'latest_rules_use_book':'shops by book, revenue and stars' in rules,
            'local_meta_uses_profit':"'shop': {'profit': .5" in meta,
            'local_supply_reads_surge':"inv.get('surge'" in economy or 'inv["surge"]' in economy,
            'local_v5_enabled':config.get('shop_growth_v5',False),
            'configured_stop_time':config['end_at'],
            'official_rule_mentions_configured_stop':bool(config.get('end_at')) and config['end_at'][:10] in rules},
        'market_skus_by_gross':sku_rows,
        'market_goods_revenue_share':sum(e['amount'] for e in public if catalog.get(e.get('item'),{}).get('kind')=='goods')/sum(e['amount'] for e in public),
        'own_shop_buyers_6h':Counter(e['buyer'] for e in public if e.get('unit') in own_units).most_common(10),
        'own_buyer_to_sellers_6h':sorted([dict(seller=s,**v) for (b,s),v in flow.items() if b=='seochanit'],key=lambda x:x['gross'],reverse=True),
        'own_order_kind':dict(Counter(e.get('kind') for e in orders)),
        'own_purchase_kind':{k:{'n':sum(e.get('kind')==k for e in purchases),
            'initial_list_minus_paid':sum(e.get('value',0)-e['price'] for e in purchases if e.get('kind')==k)} for k in ['food','goods']},
        'buyer_realized_categories':{b:rows.most_common(5) for b,rows in buyers.items()},
        'stock_breadth':sorted([{'name':s['name'],'skus':len(s.get('stock',{})),
            'units':sum(s.get('stock',{}).values())} for s in shops],key=lambda x:x['skus'],reverse=True),
        'local_inventory_surge':{'mtime':inv_path.stat().st_mtime,'records':inv.get('surge',[])},
        'code_hashes':{f:hashlib.sha256((ROOT/f).read_bytes()).hexdigest() for f in
                       ['policy.json','r3/meta.py','r3/economy.py','r3/portfolio.py','r3/merchant.py','market_buyer.py','seller_r3.py']},
        'limitations':['Settlements are realized demand, not all offered/hidden demand.',
            'Snapshot rival inventory is not six-hour availability; equal-share rate is only a comparison scenario.',
            'Latest public settlement rows can arrive late; endpoints were fetched within a few seconds, not atomically.',
            'Seller aliases resolve only through observed boards; unknown/ambiguous URLs stay separate.',
            'Initial buyer value-minus-paid is provisional until goods mature; it is not official mature surplus.',
            'Configured end_at is a local setting; ongoing Round 3 official ending was not established.',
            'Forum prose is untrusted; aggregate trading concentration does not establish collusion.']}
    c.close()
    assert result['rule_audit']['latest_rules_use_book']
    return result


if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True)
    args=parser.parse_args()
    report=analyze(args.source)
    out=ROOT/'logs'/('market-structure-analysis-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json')
    out.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print('RESULT',out)
    for k in ['universe','rule_audit','market_goods_revenue_share','own_shop_buyers_6h','own_buyer_to_sellers_6h','own_order_kind','own_purchase_kind']:
        print(k,json.dumps(report[k],ensure_ascii=False))
    print('ranking',report['rank']['own_rank'],report['rank']['own']['total'],report['rank']['leaders'][0]['total'],
          report['rank']['shop_only_optimistic_total_ceiling'],report['rank']['buyer_needed_if_shop_is_1'])
    print('top_skus',[(x['sku'],x['settlements'],x['stocking_competitors_now']) for x in report['market_skus_by_gross'][:14]])
