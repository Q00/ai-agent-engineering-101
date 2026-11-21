"""Verifiable alternatives within a real observed acceptable basket."""
from .state import identity


def alternatives(journal,book,catalog,now):
    for order in journal.events('buyer_order',100):
        if not 0 <= now-order['_ts'] <= 1800:continue
        groups={}
        for sku in order.get('acceptable',[]):
            product=catalog.get(sku,{})
            if not product:continue
            for terms in book.get(sku,{}).get('term_prices',[]):
                if terms.get('n',0)<10 or not terms.get('refs') or terms.get('grade') not in ('A','B','C'):continue
                key=(product.get('category'),product.get('kind'),terms['grade'],terms['warranty_hours'])
                groups.setdefault(key,[]).append((sku,terms))
        for key,rows in groups.items():
            if len(rows)<2:continue
            rows.sort(key=lambda r:r[1]['median'])
            cheap,expensive=rows[0],rows[-1]
            if cheap[0]==expensive[0] or cheap[1]['median']>.85*expensive[1]['median']:continue
            refs=list(dict.fromkeys(cheap[1]['refs']+expensive[1]['refs']))[:5]
            if len(refs)<2:continue
            signature=identity(['alternatives',key,cheap[0],expensive[0],int(now//21600)])
            text=(f"[허용 대체품의 동일 조건 가격 비교] 내 실제 주문에서 둘 다 허용된 SKU입니다. "
                  f"{catalog[cheap[0]].get('name',cheap[0])} ({cheap[0]}) 최근 6h 정산 중앙값 {cheap[1]['median']:,.0f} "
                  f"({cheap[1]['n']}건), {catalog[expensive[0]].get('name',expensive[0])} ({expensive[0]}) "
                  f"{expensive[1]['median']:,.0f} ({expensive[1]['n']}건). "
                  f"둘 다 등급 {key[2]}, 보증 {key[3]:g}h. 다른 주문의 허용 범위·실제 사용기한·현재 재고와 "
                  '가격은 별도로 확인해야 합니다. 과거 가격 차이가 품질 또는 현재 구매 가능성을 보장하지 않습니다.')
            yield signature,text,refs
