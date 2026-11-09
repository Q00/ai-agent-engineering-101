"""Model-selected, unit-backed public quote ceilings with durable expiry."""
from contextlib import closing
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3

import seller_quality
import seller_warranty

TTL=1800
HOLD_SECONDS=600
MAX_ACTIVE=3


def floor(unit,now):
    cost=int(unit['cost']);age=max(0.,now-float(unit.get('supplied_at') or now))
    carry=cost*.01*(age+HOLD_SECONDS)/86400
    covered=unit.get('kind')=='goods'
    return max(math.ceil((cost+carry)/(1-seller_warranty.REFUND_SHARE if covered else 1))+1,
               seller_warranty.economics(unit,now=now,warranty_hours=seller_warranty.hours(unit))['price_floor'])


def proposals(units, market_dir, now, *, price_caps=None):
    rows=[]
    for unit in units:
        if unit.get('grade') not in ('A','B','C') or type(unit.get('cost'))is not int or unit['cost']<=0:continue
        reference=unit.get('list_price')
        if type(reference)is not int or reference<=0:continue
        quality=seller_quality.quality(unit,market_dir,now,TTL+HOLD_SECONDS)
        if quality['claim']!=unit['grade']:continue
        if unit.get('kind')=='food' and (unit.get('expires_at') or 0)-now<6*3600:continue
        base=floor(unit,now)
        if price_caps is None:
            prices = [(math.ceil(base * (1 + margin)), margin) for margin in (.10, .20)]
        else:
            cap = min(price_caps.get(unit['unit'], 0), math.floor(reference * .90))
            price = min(math.ceil(base * 1.10), cap)
            prices = [(price, round(price / base - 1, 4))] if price >= math.ceil(base * 1.02) else []
        for price, margin in prices:
            if price>reference*.90:continue
            digest=hashlib.sha256(f"{unit['unit']}:{unit['grade']}:{price}:{int(now)}".encode()).hexdigest()[:10]
            rows.append({'id':'FO-'+digest,'sku':unit['sku'],'unit':unit['unit'],'name':unit.get('name'),
                'grade':unit['grade'],'price':price,'list_price':reference,'price_floor':base,
                'savings_ratio':round(1-price/reference,6),'expires_at':unit.get('expires_at'),
                'warranty_hours':seller_warranty.hours(unit),'created_at':now,'valid_until':now+TTL,
                'margin_scenario':margin,'caution':'Cost/holding and 10% goods refund scenario covered; actual breakage odds are unknown. Not a market-lowest claim.'})
    rows.sort(key=lambda r:(-r['savings_ratio'],r['price'],r['id']))
    # Many interchangeable units must not crowd every other product out of the
    # bounded editor context. Offer both price choices for one real unit per SKU.
    preferred={}
    for row in rows:preferred.setdefault(row['sku'],row['unit'])
    unique={r['id']:r for r in rows if r['unit']==preferred[r['sku']]}
    return list(unique.values())[:24]


def active(path,now):
    path=Path(path)
    if not path.exists():return []
    try:
        with closing(sqlite3.connect(f'file:{path.resolve()}?mode=ro',uri=True,timeout=2)) as db:
            rows=db.execute("SELECT body FROM featured_quotes WHERE phase IN ('queued','published') AND valid_until>?",(now,)).fetchall()
        return [o for r in rows if (o:=json.loads(r[0])).get('created_at',math.inf)<=now]
    except (OSError,sqlite3.Error,ValueError):return []


def validate(selected,choices,units,market_dir,now):
    if not selected or len(selected)>2 or len(set(selected))!=len(selected):raise ValueError('featured offer selection')
    options={r['id']:r for r in choices};available={u['unit']:u for u in units};result=[]
    for key in selected:
        offer=options.get(key)
        if not offer or not offer['created_at']<=now<offer['valid_until']:raise ValueError('featured offer expired or unknown')
        unit=available.get(offer['unit'])
        if (not unit or unit.get('sku')!=offer['sku'] or unit.get('grade')!=offer['grade']
                or unit.get('expires_at')!=offer.get('expires_at') or floor(unit,now)>offer['price']
                or unit.get('list_price')!=offer['list_price']):raise ValueError('featured unit or economics changed')
        quality=seller_quality.quality(unit,market_dir,now,max(0,offer['valid_until']-now)+HOLD_SECONDS)
        if quality['claim']!=offer['grade']:raise ValueError('featured grade will not remain stable')
        result.append(offer)
    if len({r['sku'] for r in result})!=len(result):raise ValueError('one featured offer per SKU per post')
    return result


def activate(path,offers,client_id,now):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with closing(sqlite3.connect(path,timeout=3)) as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('''CREATE TABLE IF NOT EXISTS featured_quotes (
            offer_id TEXT PRIMARY KEY, unit TEXT, valid_until REAL, client_id TEXT,
            phase TEXT, body TEXT NOT NULL)''')
        db.execute('BEGIN IMMEDIATE')
        existing=db.execute("SELECT offer_id,unit FROM featured_quotes WHERE phase IN ('queued','published') AND valid_until>?",(now,)).fetchall()
        ids={r[0] for r in existing};by_unit={r[1]:r[0] for r in existing}
        if len(ids|{o['id'] for o in offers})>MAX_ACTIVE:raise ValueError('featured capacity reached')
        for offer in offers:
            if offer['unit'] in by_unit and by_unit[offer['unit']]!=offer['id']:raise ValueError('active unit already has a public quote')
            db.execute('INSERT OR IGNORE INTO featured_quotes VALUES (?,?,?,?,?,?)',
                (offer['id'],offer['unit'],offer['valid_until'],client_id,'queued',json.dumps(offer,ensure_ascii=False)))
        db.commit()


def acknowledge(path,client_id,status):
    if status not in ('published','failed'):return
    try:
        with closing(sqlite3.connect(path,timeout=2)) as db:
            db.execute('UPDATE featured_quotes SET phase=? WHERE client_id=?',(status,client_id));db.commit()
    except sqlite3.OperationalError:pass  # no offers table in a legacy runtime


def requested(message,offers):
    codes=re.findall(r'\bFO-[a-f0-9]{10}\b',message,re.I)
    if not codes:return None
    matches=[o for o in offers if o['id'].casefold() in {c.casefold() for c in codes}]
    return matches[0] if len(codes)==1 and len(matches)==1 else {'unavailable':True}


def apply(unit,ordinary,offers,now):
    valid=[o for o in offers if o['unit']==unit['unit'] and o['grade']==unit['grade']
           and o.get('expires_at')==unit.get('expires_at') and o['created_at']<=now<o['valid_until']]
    if not valid:return None
    offer=min(valid,key=lambda o:o['price']);minimum=floor(unit,now)
    if minimum>offer['price']:raise ValueError('public quote is no longer viable')
    price=max(minimum,min(ordinary,offer['price']))
    return {'kind':'featured','event_id':offer['id'],'offer_id':offer['id'],'ordinary_quote':ordinary,
        'price':price,'price_floor':minimum,'saving':max(0,ordinary-price),'quoted_at':now,
        'campaign_ends_at':offer['valid_until'],'discount_bps':0,'public_price_ceiling':offer['price']}
