"""Evidence-first buyer forum publisher with score-aware pacing."""
import json
import os
import sqlite3
import time
from pathlib import Path
from statistics import median
from market_intel import read_json, WINDOW
from referral_program import Program
from shop_copy import paid_post
from adaptive_control import Control


AGGRESSIVE_INTERVAL = 310       # official limit is one buyer post per 300 seconds
AGGRESSIVE_DAILY_LIMIT = 270    # below the theoretical 288/day maximum
CAUTIOUS_INTERVAL = 900
CAUTIOUS_DAILY_LIMIT = 96
MAINTENANCE_INTERVAL = 1800
MAINTENANCE_DAILY_LIMIT = 48
TARGET_POST_PERCENTILE = .90


def publication_policy(board, name, negative_feedback=False, now=None, intel=None):
    """Return interval/rolling-limit from official score evidence, never forum text."""
    now=time.time() if now is None else now
    eligible=[r for r in board.get('buyers',[]) if (r.get('deals') or 0)>0]
    buyer=next((r for r in eligible if r.get('name')==name),{})
    if not buyer:return CAUTIOUS_INTERVAL,CAUTIOUS_DAILY_LIMIT,0.0
    posts=buyer.get('posts') or 0
    percentile=sum((r.get('posts') or 0)<=posts for r in eligible)/len(eligible) if eligible else 0.0
    forum=buyer.get('forum')
    forum=float(forum) if isinstance(forum,(int,float)) else 0.0
    cooldown=bool(intel and intel.get('buyer_forum_cooldown_until',0)>now)
    if intel:
        baseline=intel.get('buyer_forum_rating_baseline')
        if not baseline:
            intel.put('buyer_forum_rating_baseline',{'at':now,'score':forum})
        elif now-baseline.get('at',0)>=21600:
            if forum<baseline.get('score',forum)-.01:
                intel.put('buyer_forum_cooldown_until',now+21600);cooldown=True
            intel.put('buyer_forum_rating_baseline',{'at':now,'score':forum})
    if percentile>=TARGET_POST_PERCENTILE:
        return MAINTENANCE_INTERVAL,MAINTENANCE_DAILY_LIMIT,percentile
    if negative_feedback or cooldown or forum<.84:
        return CAUTIOUS_INTERVAL,CAUTIOUS_DAILY_LIMIT,percentile
    return AGGRESSIVE_INTERVAL,AGGRESSIVE_DAILY_LIMIT,percentile


def latest_paid_post(intel):
    path=intel.root/'buyer.sqlite3'
    if not path.exists():return None
    posted=set(intel.get('posted_purchase_ledgers',[]))
    catalog=read_json(intel.root/'catalog.json',{})
    with sqlite3.connect(str(path),timeout=2) as db:
        rows=db.execute('SELECT p.sku,p.price,p.ledger,o.details FROM payments p JOIN outcomes o ON o.order_id=p.order_id WHERE p.paid=1 AND p.ledger IS NOT NULL ORDER BY p.ledger DESC LIMIT 6000').fetchall()
    for sku,price,ledger,body in rows:
        if ledger in posted:continue
        outcome=json.loads(body);product=catalog.get(sku,{})
        return paid_post(outcome.get('name',product.get('name',sku)),sku,price,
                         outcome.get('grade','미확인'),product.get('list_price'),
                         outcome.get('claim_honest'),ledger,outcome.get('warranty_hours')),ledger
    return None


def _name(catalog,sku):
    return catalog.get(sku,{}).get('name',sku)[:42]


def warranty_premium_post(intel,catalog,now):
    rows=intel.db.execute('''SELECT seq,sku,grade,warranty,price FROM trades
        WHERE ts>? ORDER BY ts DESC''',(now-WINDOW,)).fetchall();groups={}
    for row in rows:
        if catalog.get(row['sku'],{}).get('kind')!='goods':continue
        groups.setdefault((row['sku'],row['grade']),{0:[],1:[]})[int(row['warranty']>0)].append(row)
    choices=[(len(v[0])+len(v[1]),k,v) for k,v in groups.items() if len(v[0])>=3 and len(v[1])>=3]
    if not choices:return None
    _,(sku,grade),parts=max(choices);plain=int(median([r['price'] for r in parts[0]]));covered=int(median([r['price'] for r in parts[1]]))
    premium=round((covered/plain-1)*100) if plain else 0;refs=[r['seq'] for r in parts[0][:2]+parts[1][:2]]
    text=(f"[시장 인사이트 · 보증 가격] 신기한 점입니다. {_name(catalog,sku)} [{sku}] {grade}등급의 최근 6시간 정산에서\n"
          f"무보증 {len(parts[0])}건 중앙 {plain:,}원 vs 보증 포함 {len(parts[1])}건 중앙 {covered:,}원: 관측 프리미엄 {premium:+d}%.\n"
          "보증시간이 섞인 관찰이라 인과관계는 아닙니다. 견적에서 보증값이 이 차이보다 합리적인지 비교하는 데 도움이 되었으면 합니다.")
    return text,refs


def shelf_life_post(intel,catalog,now):
    rows=intel.db.execute('''SELECT seq,sku,grade,hours,price FROM trades
        WHERE ts>? AND hours IS NOT NULL ORDER BY ts DESC''',(now-WINDOW,)).fetchall();groups={}
    for row in rows:
        if catalog.get(row['sku'],{}).get('kind')=='food':groups.setdefault((row['sku'],row['grade']),[]).append(row)
    choices=[(len(v),k,v) for k,v in groups.items() if len(v)>=8]
    if not choices:return None
    _,(sku,grade),sample=max(choices);cut=median([r['hours'] for r in sample])
    short=[r for r in sample if r['hours']<=cut];long=[r for r in sample if r['hours']>cut]
    if len(short)<3 or len(long)<3:return None
    short_price=int(median([r['price'] for r in short]));long_price=int(median([r['price'] for r in long]));premium=round((long_price/short_price-1)*100) if short_price else 0
    refs=[r['seq'] for r in short[:2]+long[:2]]
    text=(f"[시장 인사이트 · 유통기한 값] 신기한 점입니다. {_name(catalog,sku)} [{sku}] {grade}등급 {len(sample)}건을 남은 기한 {cut:.0f}h로 나누면\n"
          f"짧은 쪽 중앙 {short_price:,}원 vs 긴 쪽 중앙 {long_price:,}원: 관측 프리미엄 {premium:+d}%.\n"
          "필요 사용시간을 넘기는 범위에서만 비교한 관찰입니다. 같은 등급에서도 남은 기한의 값을 따져보는 데 도움이 되었으면 합니다.")
    return text,refs


def concentration_post(intel,catalog,now):
    rows=intel.db.execute('''SELECT seq,sku,shop,price FROM trades
        WHERE ts>? ORDER BY ts DESC''',(now-WINDOW,)).fetchall();groups={}
    for row in rows:groups.setdefault(row['sku'],[]).append(row)
    choices=[]
    for sku,sample in groups.items():
        shops={r['shop'] for r in sample if r['shop']}
        if len(sample)>=9 and len(shops)>=3:
            largest=max(sum(r['shop']==shop for r in sample) for shop in shops)
            choices.append((largest/len(sample),len(sample),sku,sample,len(shops)))
    if not choices:return None
    share,n,sku,sample,shop_count=max(choices);prices=sorted(r['price'] for r in sample);refs=[r['seq'] for r in sample[:3]]
    text=(f"[시장 구조 · 판매처 집중도] 신기한 점입니다. {_name(catalog,sku)} [{sku}]는 최근 6시간 {n}건이 {shop_count}개 판매처에서 정산됐지만, "
          f"가장 큰 한 곳의 비중이 {share*100:.0f}%였습니다. 가격 범위 {prices[0]:,}~{prices[-1]:,}원, 중앙 {int(median(prices)):,}원.\n"
          "거래가 한곳에 몰린 상품은 대표 시세가 특정 판매처 정책에 좌우될 수 있습니다. 여러 견적을 비교하는 데 도움이 되었으면 합니다.")
    return text,refs


def forum_gap_post(intel):
    """Publish useful statistics absent from recent high-volume forum themes."""
    now=time.time();catalog=read_json(intel.root/'catalog.json',{});forum=intel.source('forum',[]) or []
    if isinstance(forum,dict):forum=forum.get('posts',[])
    text=' '.join(str(p.get('text','')) for p in forum if p.get('role')=='buyer')
    candidates=[(text.count('보증 가격'),warranty_premium_post),
                (text.count('유통기한 값'),shelf_life_post),
                (text.count('판매처 집중도'),concentration_post)]
    for _,builder in sorted(candidates,key=lambda item:item[0]):
        result=builder(intel,catalog,now)
        if result:return result
    return None


def queue_update(intel):
    now=time.time();state=intel.root
    day=int(now//86400)
    if intel.get('post_day')!=day:
        intel.put('post_day',day);intel.put('posts_today',0)
    board=intel.source('board',{}) or {}
    if board.get('round')!=3:return False
    program=Program(state)
    try:
        name=program.p['shop_name'];rows=board.get('buyers',[])
        buyer=next((r for r in rows if r.get('name')==name),{})
        posts=intel.source('forum',[]) or []
        if isinstance(posts,dict):posts=posts.get('posts',[])
        mine=[r for r in posts if r.get('author')==name and r.get('role')=='buyer']
        negative=sum(r.get('dislikes',0)-r.get('likes',0) for r in mine)>0
        interval,daily_limit,post_percentile=publication_policy(board,name,negative,now,intel)
        if now-intel.get('buyer_post_at',0)<interval:return False
        if intel.get('posts_today',0)>=daily_limit:return False
        # Buyer posts are independent purchase evidence, never promotion for our shop.
        research=True
        refs=[];text='';purchase_ledger=None
        post_kind='purchase'
        digest=forum_gap_post(intel) if intel.get('posts_today',0)%3==1 else None
        if digest:
            text,refs=digest;post_kind='forum_gap_analysis'
        paid=None if text else latest_paid_post(intel)
        if paid:
            text,purchase_ledger=paid;refs=[purchase_ledger]
        elif not text:
            row=intel.db.execute('SELECT sku,grade,COUNT(*) n,MIN(price) lo,MAX(price) hi,MAX(seq) seq FROM trades WHERE ts>? GROUP BY sku,grade HAVING COUNT(*)>=3 ORDER BY n DESC LIMIT 1',(now-WINDOW,)).fetchone()
            if row:
                text=(f"[시장 관찰] {row['sku']} {row['grade']}등급: 관측된 최근 6시간 정산 {row['n']}건, "
                      f"{row['lo']:,}~{row['hi']:,}원. 유통기한·보증이 달라 직접 최저가 비교는 아닙니다. "
                      '식품은 필요한 보관시간, 비식품은 보증을 확인하세요.')
                refs=[row['seq']]
        if not text:return False
        guard=Control(state)
        try:allowed,_,_=guard.allow_publication('buyer',text,refs,now)
        finally:guard.close()
        if not allowed:return False
        folder=state/'buyer-forum';folder.mkdir(exist_ok=True)
        # Timestamp before append favors losing one post over duplicate flooding.
        intel.put('buyer_post_at',now);intel.put('posts_today',intel.get('posts_today',0)+1)
        fd=os.open(folder/'outbox.jsonl',os.O_CREAT|os.O_APPEND|os.O_WRONLY,0o600)
        try:os.write(fd,(json.dumps({'as':'buyer','post':text,'refs':refs},ensure_ascii=False)+'\n').encode())
        finally:os.close(fd)
        if purchase_ledger:
            intel.put('posted_purchase_ledgers',(intel.get('posted_purchase_ledgers',[])+[purchase_ledger])[-6000:])
        intel.decision('buyer_forum',{'research':research,'negative_feedback':negative,
            'buyer_deals':buyer.get('deals'),'refs':refs,'interval':interval,
            'daily_limit':daily_limit,'post_percentile':round(post_percentile,4),
            'post_kind':post_kind})
        return True
    finally:program.close()
