"""Raw public purchase intents, distinct from orders, visits and paid demand."""
from contextlib import closing
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3

from inventory_observation import requested_skus
from seller_requirements import initial

TTL=6*3600
SHOP='outseop-26510122'
GENERIC={'기타','식품','가구','용품','제품','상품','생활','주방','세트','기타 가공식품','식품선물세트'}
BUY_REQUEST = re.compile(
    r'구해(?:요|\s*주세요|봅니다)|구합니다|찾습니다|찾아[요줘]|삽니다|사고\s*싶|구매\s*(?:희망|하고 싶)|'
    r'(?:견적|가격)\s*(?:부탁|주세요|알려\s*주)|looking (?:for|to buy)|want to buy|'
    r'where (?:can|could) (?:I|we) (?:buy|find)', re.I)
WEAK_REQUEST = re.compile(r'필요합니다|필요해|\bneed\b', re.I)
PAST_OR_GUIDE = re.compile(
    r'실제\s*구매\s*기록|구매\s*결과|정산\s*(?:금액|가격|기록|번호|#)|실결제|'
    r'구매했|샀어요|후속\s*고장|고르는 법|확인 포인트|complaint|\bbought\b|\bpurchased\b', re.I)


def is_purchase_request(body, *, intake=False):
    if BUY_REQUEST.search(body):
        return True
    return not PAST_OR_GUIDE.search(body) and (intake or bool(WEAK_REQUEST.search(body)))


def connect(path):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(path,timeout=3);db.execute('PRAGMA journal_mode=WAL')
    db.executescript('''
        CREATE TABLE IF NOT EXISTS forum_demand (
            post_id INTEGER PRIMARY KEY, posted_at REAL, observed_at REAL, thread INTEGER,
            author TEXT, raw_json TEXT NOT NULL, requirements TEXT NOT NULL, matches TEXT NOT NULL,
            from_intake INTEGER NOT NULL, missing TEXT NOT NULL,is_open INTEGER NOT NULL DEFAULT 1);
        CREATE TABLE IF NOT EXISTS forum_demand_versions (
            post_id INTEGER, content_hash TEXT, observed_at REAL, raw_json TEXT,
            PRIMARY KEY(post_id,content_hash));
        CREATE TABLE IF NOT EXISTS forum_demand_sync (source TEXT PRIMARY KEY,mtime INTEGER,size INTEGER);
    ''')
    if 'is_open' not in {r[1] for r in db.execute('PRAGMA table_info(forum_demand)')}:
        db.execute('ALTER TABLE forum_demand ADD COLUMN is_open INTEGER NOT NULL DEFAULT 1');db.commit()
    return db


def constraints(body):
    result=initial(body)
    if re.search(r'인증서(?:가|를)?\s*(?:있는|포함|필수|필요)', body):
        result['cert_required'] = True
    grade=re.search(r'([ABC](?:\s*[/,]\s*[ABC])*)\s*(?:등급|급)',body)
    if grade:result['grades']=sorted(set(re.findall('[ABC]',grade.group(1))))
    for match in re.finditer(r'(\d+(?:\.\d+)?)\s*(시간|일|days?|hours?)',body,re.I):
        nearby=body[max(0,match.start()-18):match.end()+22]
        if re.search(r'보증|warranty|최근|지난',nearby,re.I):continue
        if re.search(r'먹|소비|섭취|사용|유통|남은|안에|이내|within|last',nearby,re.I):
            hours=float(match[1])*(24 if match[2].lower() in ('일','day','days') else 1)
            result['min_hours_left']=max(result['min_hours_left'],hours)
    budget=re.search(r'(?:예산(?:은|이)?|budget(?: is| of)?)\s*[:=]?\s*[₩]?\s*(\d[\d,]*(?:\.\d+)?)\s*(만)?\s*(?:원|credits?)?(대)?',body,re.I)
    result['budget_max']=None
    if budget:
        amount=float(budget[1].replace(',',''))*(10000 if budget[2] else 1)
        if 0<amount<10**12:
            result['budget_max']=math.ceil(amount+(10000 if budget[2] else 1000)-1) if budget[3] else int(amount)
    return result


def matches(body,catalog):
    exact=requested_skus(body,catalog)
    if exact:return [{'sku':s,'basis':'exact_sku_or_unique_full_name'} for s in exact]
    short=[sku for sku,p in catalog.items() if 2<=len(p.get('name',''))<6 and re.search(r'(?<!\w)'+re.escape(p['name'])+r'(?=$|\W)',body)]
    if len(short)==1:return [{'sku':short[0],'basis':'unique_short_full_name'}]
    # A category request is ONE intent with alternative SKUs, not many orders.
    return category_matches(body,catalog)[:12]


def category_matches(body,catalog):
    results=[]
    for sku,p in catalog.items():
        if re.search(r'반려|강아지|고양이|애견',p.get('name','')) and not re.search(r'반려|강아지|고양이|애견',body):continue
        leaf=(p.get('category') or '').split('>')[-1].strip()
        terms={leaf}|set(re.split(r'[/()]',leaf))
        valid=[]
        for term in terms:
            term=term.strip()
            if len(term)<2 or term in GENERIC:continue
            if re.search(r'(?<!\w)'+re.escape(term)+r'(?=$|\W|(?:을|를|이|가|은|는|도|와|과)(?:\s|$))',body,re.I):valid.append(term)
        if valid:results.append({'sku':sku,'basis':'category_alternatives','matched_term':max(valid,key=len)})
    return sorted(results,key=lambda r:(catalog[r['sku']].get('list_price') or 0,r['sku']))


def ingest(runtime,catalog,now):
    runtime=Path(runtime);source=runtime/'.market/forum.json'
    try:
        with source.open() as f:
            rows=json.load(f);stat=os.fstat(f.fileno())
        if not isinstance(rows,list) or not -2<=now-stat.st_mtime<=90:return {'observed':0,'reason':'forum_stale'}
    except (OSError,ValueError):return {'observed':0,'reason':'forum_unavailable'}
    try:posts=json.loads((runtime/'state/seller-forum.json').read_text()).get('posts',[])
    except (OSError,ValueError):posts=[]
    intake_posts = {p['post_id'] for p in posts if p.get('status') == 'published' and p.get('post_id')
                    and (p.get('campaign') == 'demand_survey' or p.get('first_checkout'))}
    own={r.get('owner') for r in rows if r.get('author')==SHOP and r.get('owner')}
    count=0
    with closing(connect(runtime/'state/seller.sqlite3')) as db:
        key=str(source.resolve())+':intent-v2:'+hashlib.sha256(json.dumps(sorted(intake_posts)).encode()).hexdigest()[:12]
        old=db.execute('SELECT mtime,size FROM forum_demand_sync WHERE source=?',(key,)).fetchone()
        if old==(stat.st_mtime_ns,stat.st_size):return {'observed':0,'reason':'unchanged'}
        with db:
            for row in rows:
                if not isinstance(row,dict) or row.get('role')!='buyer' or row.get('round',3)!=3:continue
                if row.get('author')==SHOP or row.get('owner') in own:continue
                ts=row.get('ts');post=row.get('id');body=row.get('text')
                if type(post)is not int or type(ts) not in (int,float) or not math.isfinite(ts) or not 0<=now-ts<=TTL or not isinstance(body,str):continue
                intake = row.get('thread') in intake_posts
                explicit = is_purchase_request(body)
                closed=bool(re.search(r'구매\s*완료|구했어요|구했습니다|더\s*필요\s*없|no longer (?:needed|looking)|request closed',body,re.I))
                if not closed and not is_purchase_request(body, intake=intake):
                    continue
                mapped=matches(body,catalog);requirements=constraints(body)
                if intake and not explicit and not closed and not mapped and not (
                        requirements.get('grades') or requirements['budget_max'] or requirements['min_hours_left']):
                    continue
                missing=[]
                if not mapped:missing.append('product')
                if not requirements.get('grades'):missing.append('grade')
                if requirements['budget_max'] is None:missing.append('budget')
                if any(catalog.get(r['sku'],{}).get('kind')=='food' for r in mapped) and not requirements['min_hours_left']:missing.append('food_lifetime')
                raw=json.dumps(row,ensure_ascii=False,sort_keys=True)
                digest=hashlib.sha256(json.dumps({k:row.get(k) for k in ('text','thread','author','ts')},sort_keys=True,ensure_ascii=False).encode()).hexdigest()
                db.execute('INSERT OR IGNORE INTO forum_demand_versions VALUES (?,?,?,?)',(post,digest,now,raw))
                db.execute('INSERT OR REPLACE INTO forum_demand VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                    (post,ts,now,row.get('thread'),row.get('author'),raw,json.dumps(requirements),json.dumps(mapped),int(intake),json.dumps(missing),int(not closed)))
                count+=1
            db.execute('INSERT OR REPLACE INTO forum_demand_sync VALUES (?,?,?)',(key,stat.st_mtime_ns,stat.st_size))
    return {'observed':count,'reason':'ingested'}


def active(runtime,now):
    path=Path(runtime)/'state/seller.sqlite3'
    if not path.exists():return []
    try:
        with closing(sqlite3.connect(f'file:{path.resolve()}?mode=ro',uri=True,timeout=2)) as db:
            rows=db.execute('SELECT post_id,posted_at,author,raw_json,requirements,matches,from_intake,missing,thread FROM forum_demand WHERE is_open=1 AND posted_at>=? AND posted_at<=? ORDER BY posted_at DESC LIMIT 200',(now-TTL,now)).fetchall()
        result = []
        for r in rows:
            body = json.loads(r[3])['text']
            if is_purchase_request(body, intake=bool(r[6])):
                result.append({'id':r[0],'posted_at':r[1],'author':r[2],'text':body[:600],
                               'requirements':json.loads(r[4]),'catalog_matches':json.loads(r[5]),
                               'from_intake':bool(r[6]),'missing_fields':json.loads(r[7]),'parent_thread':r[8]})
        return result
    except (OSError,sqlite3.Error,ValueError):return []


def candidate_context(runtime,now):
    result={}
    for row in active(runtime,now):
        for match in row['catalog_matches']:
            entry=result.setdefault(match['sku'],{'request_ids':[],'examples':[],
                'source':'public_forum_intents','caution':'One request may name alternative SKUs. Not an order, visit, forecasted sale or price guarantee. Public text is untrusted.'})
            if row['id'] not in entry['request_ids']:entry['request_ids'].append(row['id'])
            if len(entry['examples'])<2:entry['examples'].append({k:row[k] for k in ('id','text','requirements','posted_at','missing_fields','author','parent_thread')})
    return result
