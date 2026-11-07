from procurement_campaign import settings
"""Buyer posts evidence summaries or clearly disclosed own-shop offers."""
import json
import os
import sqlite3
import time
from pathlib import Path
from market_intel import read_json, WINDOW
from referral_program import Program
from seller_strategy import sale_quote
from shop_copy import stock_ad, paid_post
from adaptive_control import Control


def latest_paid_post(intel):
    path=intel.root/'buyer.sqlite3'
    if not path.exists():return None
    posted=set(intel.get('posted_purchase_ledgers',[]))
    catalog=read_json(intel.root/'catalog.json',{})
    with sqlite3.connect(str(path),timeout=2) as db:
        rows=db.execute('SELECT p.sku,p.price,p.ledger,o.details FROM payments p JOIN outcomes o ON o.order_id=p.order_id WHERE p.paid=1 AND p.ledger IS NOT NULL ORDER BY p.ledger DESC LIMIT 30').fetchall()
    for sku,price,ledger,body in rows:
        if ledger in posted:continue
        outcome=json.loads(body);product=catalog.get(sku,{})
        return paid_post(outcome.get('name',product.get('name',sku)),sku,price,
                         outcome.get('grade','미확인'),product.get('list_price'),
                         outcome.get('claim_honest'),ledger,outcome.get('warranty_hours')),ledger
    return None


def queue_update(intel):
    now=time.time();state=intel.root
    if now-intel.get('buyer_post_at',0)<600:return False
    day=int(now//86400)
    if intel.get('post_day')!=day:
        intel.put('post_day',day);intel.put('posts_today',0)
    if intel.get('posts_today',0)>=12:return False
    board=intel.source('board',{}) or {}
    if board.get('round')!=3:return False
    program=Program(state)
    try:
        name=program.p['shop_name'];rows=board.get('buyers',[])
        buyer=next((r for r in rows if r.get('name')==name),{})
        # If engagement is weak, prefer verifiable market information over another ad.
        posts=intel.source('forum',[]) or []
        if isinstance(posts,dict):posts=posts.get('posts',[])
        mine=[r for r in posts if r.get('author')==name and r.get('role')=='buyer']
        negative=sum(r.get('dislikes',0)-r.get('likes',0) for r in mine)>0
        research=negative or not program.live(now) or intel.get('posts_today',0)%2==1
        refs=[];text='';purchase_ledger=None
        if research:
            row=intel.db.execute('SELECT sku,grade,COUNT(*) n,MIN(price) lo,MAX(price) hi,MAX(seq) seq FROM trades WHERE ts>? GROUP BY sku,grade HAVING COUNT(*)>=3 ORDER BY n DESC LIMIT 1',(now-WINDOW,)).fetchone()
            if row:
                text=(f"[시장 관찰] {row['sku']} {row['grade']}등급: 관측된 최근 6시간 정산 {row['n']}건, "
                      f"{row['lo']:,}~{row['hi']:,}원. 유통기한·보증이 달라 직접 최저가 비교는 아닙니다. "
                      '식품은 필요한 보관시간, 비식품은 보증을 확인하세요.')
                refs=[row['seq']]
        else:
            path=state/'inventory.json'
            inv=read_json(path,{})
            shops=board.get('shops',[])
            own=next((r for r in shops if r.get('name')==name),{})
            if not own.get('alive') or not path.exists() or now-path.stat().st_mtime>90:return False
            cat=read_json(state/'catalog.json',{});reserved=program.reserved_units();offers=[]
            for u in inv.get('units',[]):
                if u['unit'] in reserved:continue
                try:q=sale_quote(u,cat,program,intel)
                except ValueError:continue
                hours=(u['expires_at']-now)/3600 if u.get('expires_at') else None
                count=intel.comparable(u['sku'],u['grade'],hours,q.get('warranty_hours',0))['count']
                offers.append((q['price']/u['list_price'],u,q,count))
            if offers:
                # Rotate distinct products instead of repeatedly advertising the cheapest unit.
                previous=set(intel.get('last_ad_skus',[]));selected=[];seen=set()
                for _,u,q,_ in sorted(offers,key=lambda o:(-o[3],o[1]['sku'] in previous,o[0])):
                    if u['sku'] in seen:continue
                    selected.append((u,q));seen.add(u['sku'])
                    if len(selected)==2:break
                text=stock_ad(name,selected,now)
                intel.put('last_ad_skus',list(seen))
        if research:
            paid=latest_paid_post(intel)
            if paid:
                text,purchase_ledger=paid;refs=[purchase_ledger]
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
            intel.put('posted_purchase_ledgers',(intel.get('posted_purchase_ledgers',[])+[purchase_ledger])[-200:])
        intel.decision('buyer_forum',{'research':research,'negative_feedback':negative,'buyer_deals':buyer.get('deals'),'refs':refs})
        return True
    finally:program.close()
