"""Evidence-first copy: no unimplemented warranty or invented customer endorsement."""
import math
import time

SHOP_DISPLAY_NAME = 'Don Quixote'
SHOP_DESCRIPTION = (
    'Demand-adaptive verified stock, including food when shelf-life evidence supports it. '
    'honest A/B/C grades; market certificate (cert=true); evidence-scaled warranty; signed receipts. '
    'OFFERS for stock. Ask by SKU, then send a numeric offer on the same task. PROGRAM for benefits.'
)


def price_line(unit, quote, now=None):
    now=time.time() if now is None else now
    price=quote['price'];listed=unit['list_price']
    discount=math.floor((listed-price)*100/listed)
    saving=f' · 정가 대비 {discount}%↓' if discount>0 else ''
    expiry=unit.get('expires_at')
    condition=f' · 남은 기한 {max(0,math.floor((expiry-now)/3600))}h' if expiry else f" · 보증 {quote.get('warranty_hours',0)}h"
    return f"{unit['name'][:28]} [{unit['sku']}] | {unit['grade']} | {price:,}원{saving}{condition}"


def stock_ad(name, offers, now=None):
    lines=[f'[같은 운영자 가게 {name} · 현재 재고]']
    lines.extend(price_line(u,q,now) for u,q in offers[:2])
    lines.append('OFFERS → SKU 견적 → 숫자로 구매. 구매 후 공개 추천 코드 제공. 친구 최대 3% 할인·직접 추천인 최대 2% 크레딧(각 1,000원 한도·마진 제한). 정산·6h 확인 후 지급. 가격·재고 재확인.')
    return '\n'.join(lines)


def paid_post(name, sku, price, grade, listed, honest, ledger, warranty=None):
    discount=math.floor((listed-price)*100/listed) if listed else 0
    saving=f' · 정가 대비 {discount}%↓' if discount>0 else ''
    quality={True:'등급 주장 일치',False:'등급 주장 불일치'}.get(honest,'등급 주장 확인 불가')
    return (f'[실제 구매 기록] {name[:40]} [{sku}]\n'
            f'{price:,}원{saving} · 결제 당시 {grade}등급 · {quality}\n'
            f'정산 #{ledger}. '+(f'결제 보증 {warranty}h. ' if warranty is not None else '')+'이후 고장 여부는 별도 확인 필요.')
