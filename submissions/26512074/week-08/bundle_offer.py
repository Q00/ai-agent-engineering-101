"""Progressive, receipt-backed cross-SKU offers; no multi-item checkout claims."""
import re
from collections import defaultdict
from math import ceil
from shop_profit import ownership_tags

PROMO_KO='추가구매 할인: 최근 30분 서로 다른 상품 정산 확인 시 다음 상품 1%·2%·3%·4%·최대 5% 할인 제안. 상품별 할인 가능 여부는 견적에서 확인하세요. 주문별 개별 결제. 이전 거래 task ID를 보내주세요.'
PROMO_EN='Add another distinct item: verified purchases within 30 minutes unlock 1%, 2%, 3%, 4%, up to 5% off the next quote, subject to its minimum price. Each item needs a separate market order. Send prior paid receipt task IDs for a discount quote.'
FIRST_PROMO='게시 후 첫 정산 구매자 혜택: 다음 구매 견적 추가 1% 할인(첫 구매 영수증 task ID 제출, 정산 후 30분 이내, 기존 할인과 중복 적용, 상품별 할인 가능 여부 확인).'
PROMO_KO+=' '+FIRST_PROMO


def first_purchase_orders(ledger,posts,now,tags):
    sales=sorted({e.get('seq',e.get('data',{}).get('order')):e for e in ledger if e.get('kind')=='settle' and e.get('round')==3
                  and e.get('data',{}).get('shop') in tags and 0<=now-e.get('ts',0)<=1800}.values(),
                 key=lambda e:(e['ts'],e.get('seq',0)))
    winners=set()
    for post in posts:
        if post.get('author') not in tags or '게시 후 첫 정산 구매자 혜택:' not in post.get('text','') or not 0<=now-post.get('ts',0)<=1800:continue
        winner=next((e for e in sales if post.get('ts',now+1)<=e['ts']),None)
        if winner:winners.add(winner['data']['order'])
    return winners


def discount(text,sku,ask,floor,ledger,now):
    refs=set(re.findall(r'\b[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\b',text))
    if not refs:return {'price':ceil(ask),'rate':0,'prior_distinct_items':0}
    tags=ownership_tags()
    from pathlib import Path
    from round3 import read
    winners=first_purchase_orders(ledger,read(Path(__file__).resolve().parent/'.market/forum.json',[]),now,tags)
    first=False
    settled={e.get('data',{}).get('order') for e in ledger
             if e.get('kind')=='settle' and e.get('round')==3 and 0<=now-e.get('ts',0)<=1800
             and e.get('data',{}).get('shop') in tags}
    groups=defaultdict(set)
    for e in ledger:
        d=e.get('data',{});r=d.get('receipt') or {}
        if (e.get('kind')=='result' and d.get('verified') is True and r.get('task') in refs
                and d.get('order') in winners):first=True
        if (e.get('kind')=='result' and e.get('round')==3 and 0<=now-e.get('ts',0)<=1800
                and d.get('verified') is True and d.get('order') in settled
                and r.get('task') in refs and r.get('item')!=sku and d.get('buyer')):
            groups[d['buyer']].add(r.get('item'))
    count=max((len(items-{None}) for items in groups.values()),default=0)
    rate=min(5,count)*.01+(.01 if first else 0)
    return {'price':max(ceil(floor),ceil(ask*(1-rate))), 'rate':rate,'prior_distinct_items':count}
