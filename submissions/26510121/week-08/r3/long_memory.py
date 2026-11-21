"""Versioned facts, immutable experience summaries and expiring procedures.

Raw financial events are preserved. This is a derived, exact-key retrieval layer;
anonymous visitors are never turned into guessed personal identities.
"""
from collections import defaultdict
import json,re,statistics,time
from .state import identity

SCHEMA='market-memory-v2'
_FACT_CACHE={}


def schema(c):
    c.execute('CREATE TABLE IF NOT EXISTS memory_episodes(id TEXT PRIMARY KEY,kind TEXT,at REAL,subject TEXT,body TEXT)')
    c.execute('CREATE INDEX IF NOT EXISTS memory_episode_kind ON memory_episodes(kind,at)')


def refresh(journal,now=None):
    now=time.time() if now is None else now
    if 0<=now-journal.get('memory-refreshed-at',0)<120:return
    with journal.connect() as c:
        schema(c)
        since=max(now-172800,journal.get('memory-sale-watermark',now-172800)-1)
        rows=c.execute("SELECT id,ts,data FROM events WHERE kind='shop_settled' AND ts>=? AND ts<=? ORDER BY ts",(since,now)).fetchall()
        for eid,at,body in rows:
            sale=json.loads(body)
            selected={k:sale.get(k) for k in ('task','unit','sku','category','grade','warranty_hours','price','cost','paid_at','strategy_version')}
            c.execute('INSERT OR IGNORE INTO memory_episodes VALUES(?,?,?,?,?)',
                ('sale:'+eid,'shop_sale',at,None,json.dumps(selected)))
        saved=c.execute("SELECT body,at FROM memory_episodes WHERE kind='shop_sale' AND at>=?",(now-172800,)).fetchall()
    if rows:journal.put('memory-sale-watermark',rows[-1][1])
    catalog=journal.snapshot('catalog.json',{});groups=defaultdict(list)
    score_epoch=journal.get('market_rules',{}).get('buyer_score_epoch',0)
    for body,episode_at in saved:
        sale=json.loads(body);product=catalog.get(sale.get('sku'),{})
        if (sale.get('paid_at') if type(sale.get('paid_at')) in (int,float) else episode_at)<score_epoch:continue
        value=product.get('list_price');price=sale.get('price')
        if type(value) not in (int,float) or value<=0 or type(price) not in (int,float):continue
        key=identity([sale.get('category'),sale.get('grade'),sale.get('warranty_hours') or 0])
        groups[key].append(price/value)
    journal.put('memory-contract-facts:v2',{'at':now,'score_epoch':score_epoch,'groups':{k:{'samples':len(v),
        'median_paid_to_list_ratio':statistics.median(v)} for k,v in groups.items()}})
    # Reviews are linked only through explicit market ledger references. Missing
    # authors are retained as observations, never guessed account means.
    sales={eid.partition('settlement:')[2]:json.loads(body) for eid,at,body in rows if eid.startswith('settlement:')}
    for row in journal.events('shop_settled',10000):
        seq=row.get('_event_id','').partition('settlement:')[2]
        if seq:sales.setdefault(seq,row)
    review_groups=defaultdict(list);known_accounts=set();matched=0
    deduplicated={}
    for review in journal.events('shop_review_observed',1000):
        key=identity([review.get('ledger'),review.get('stars'),review.get('review')])
        old=deduplicated.get(key)
        if old is None or review.get('identity_known') and not old.get('identity_known'):
            deduplicated[key]=review
    reviews=list(deduplicated.values())
    for review in reviews:
        sale=sales.get(str(review.get('ledger')))
        if not sale:continue
        matched+=1
        if review.get('identity_known'):known_accounts.add(review['author'])
        key=identity([sale.get('grade'),sale.get('warranty_hours') or 0])
        review_groups[key].append(review)
    ratings={}
    for key,rs in review_groups.items():
        accounts=defaultdict(list)
        for row in rs:
            if row.get('identity_known'):accounts[row['author']].append(row['stars'])
        ready=len(accounts)>=5 and sum(map(len,accounts.values()))>=20
        ratings[key]={'reviews':len(rs),'verified_accounts':len(accounts),'mode':'shadow',
            'account_balanced_stars':statistics.mean(statistics.mean(v) for v in accounts.values()) if ready else None,
            'ready_for_holdout_validation':ready,'official_all_delta_available':False}
    journal.put('shop-rating-shadow',{'at':now,'reviews':len(reviews),'matched_trades':matched,
        'verified_accounts':len(known_accounts),'groups':ratings,'mode':'shadow'})
    journal.put('memory-refreshed-at',now)
    journal.put('memory-schema',SCHEMA)


def archive_customer(c,task,data):
    schema(c)
    # A small complete episode survives FIFO eviction of the working visit.
    rows=c.execute("SELECT data FROM events WHERE kind IN ('customer_paid','customer_identity') AND json_extract(data,'$.task')=?",(task,)).fetchall()
    paid=None;payer=data.get('signer')
    for (body,) in rows:
        row=json.loads(body)
        if 'price' in row:paid=row
        if row.get('payer'):payer=row['payer']
    episode={'task':task,'category':data.get('category'),'sku':data.get('sku'),'kind':data.get('kind'),
             'paid':paid,'identity_scope':'verified' if payer else 'anonymous'}
    c.execute('INSERT OR IGNORE INTO memory_episodes VALUES(?,?,?,?,?)',
              ('visit:'+task,'customer_visit',time.time(),payer,json.dumps(episode)))


def contract_summary(journal,unit,warranty=None,now=None):
    now=time.time() if now is None else now
    category=unit.get('category');grade=unit.get('grade')
    epoch=journal.get('market_rules',{}).get('buyer_score_epoch',0)
    version=journal.get('memory-refreshed-at',0);cache_key=(str(getattr(journal,'md',id(journal))),version,epoch)
    facts=_FACT_CACHE.get(cache_key)
    if facts is None:
        facts=journal.get('memory-contract-facts:v2',{})
        if len(_FACT_CACHE)>8:_FACT_CACHE.clear()
        _FACT_CACHE[cache_key]=facts
    row=facts.get('groups',{}).get(identity([category,grade,warranty or 0]),{}) if 0<=now-facts.get('at',0)<=600 and facts.get('score_epoch',0)==epoch else {}
    return {'schema':SCHEMA,'samples':row.get('samples',0),'median_paid_to_list_ratio':row.get('median_paid_to_list_ratio'),
            'grade':grade,'warranty_hours':warranty,'source':'own settled contracts; 48h; exact category/grade/warranty',
            'not_a_conversion_probability':True}


def observe_reviews(journal,shops,now=None):
    now=time.time() if now is None else now
    shop=next((r for r in shops if r.get('name')=='seochanit'),{})
    for review in shop.get('reviews',[])[:20]:
        stars=review.get('stars')
        if type(stars) not in (int,float) or not 1<=stars<=5:continue
        text=str(review.get('review',''))[:1500]
        refs=re.findall(r'(?:ledger\s*#|Deal\s*#)\s*(\d+)',text,re.I)
        author=review.get('buyer') or review.get('author')
        if not isinstance(author,str):author=None
        row={'stars':stars,'review':text,'ledger':int(refs[0]) if len(set(refs))==1 else None,
             'author':author,'identity_known':bool(author),'source':'market_api_shop_reviews','round':review.get('round')}
        journal.add('shop_review_observed',row,eid='shop-review:'+identity(row),role='shop',ts=now)


def observe_market_result(journal,event):
    """Use a verified result only when its task is one of our accepted sales."""
    data=event.get('data',{});receipt=data.get('receipt') or {};checks=data.get('checks') or {}
    if data.get('verified') is not True or checks.get('settled') is not True or receipt.get('shop')!='Seochanit Shop':return False
    task=receipt.get('task');stars=data.get('stars')
    if not task or type(stars) not in (int,float) or not 1<=stars<=5:return False
    with journal.connect() as c:
        sale=c.execute("SELECT id FROM events WHERE kind='shop_settled' AND json_extract(data,'$.task')=? LIMIT 1",(task,)).fetchone()
    if not sale:return False
    seq=sale[0].partition('settlement:')[2]
    if not seq.isdigit():return False
    buyers=journal.snapshot('board.json',{}).get('buyers',[])
    buyer=next((r for r in buyers if r.get('name')==data.get('buyer')),{})
    owner=buyer.get('owner');owner=owner.get('login') if isinstance(owner,dict) else owner
    author='owner:'+owner.casefold() if isinstance(owner,str) and owner else None
    row={'stars':stars,'review':str(data.get('review',''))[:1500],'ledger':int(seq),
        'author':author,'reviewer_label':data.get('buyer'),'identity_known':bool(author),
        'source':'verified_market_result_and_accepted_own_task','round':event.get('round')}
    return journal.add('shop_review_observed',row,eid='shop-review-ledger:'+str(event['seq']),role='shop',ts=event.get('ts'))


def manifest(journal):
    rules=journal.get('market_rules',{})
    return {'system_revision':'system-v13-contract-frontier','memory_schema':SCHEMA,
            'rules_sha256':rules.get('sha256'),'facts_not_instructions':True,
            'board_checked_at':journal.get('board_checked_at'),'ledger_caught_up_at':journal.get('public_feed_caught_up_at'),
            'execution':'bounded numeric contracts','review_forecast':'shadow_until_account_linked_data'}


def rating_summary(journal,unit,warranty):
    data=journal.get('shop-rating-shadow',{})
    return data.get('groups',{}).get(identity([unit.get('grade'),warranty or 0]),
        {'mode':'shadow','reviews':0,'verified_accounts':0,'account_balanced_stars':None,
         'ready_for_holdout_validation':False,'official_all_delta_available':False})
