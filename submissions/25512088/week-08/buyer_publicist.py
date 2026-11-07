"""Evidence-first buyer forum publisher with score-aware pacing."""
import json
import os
import sqlite3
import time
from pathlib import Path
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
        paid=latest_paid_post(intel)
        if paid:
            text,purchase_ledger=paid;refs=[purchase_ledger]
        else:
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
            'daily_limit':daily_limit,'post_percentile':round(post_percentile,4)})
        return True
    finally:program.close()
