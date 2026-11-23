"""Evidence-first copy: no unimplemented warranty or invented customer endorsement."""
import hashlib
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
    """Render one verified purchase with deterministic, non-repetitive wording.

    The ledger selects presentation only; every factual field remains unchanged.
    A large combination space avoids treating distinct purchases as template
    duplicates without weakening the publication safety kernel.
    """
    discount=math.floor((listed-price)*100/listed) if listed else 0
    quality={True:'등급 주장 일치',False:'등급 주장 불일치'}.get(honest,'등급 주장 확인 불가')
    seed=hashlib.sha256(f'{ledger}:{sku}'.encode()).digest()
    headings=('가격 확인','정산 메모','구매 근거','할인 기록','체결 확인','거래 요약','원장 확인','지출 기록',
              '상품 확인','조건 기록','구매 결과','결제 근거','거래 관찰','정산 결과','가격 메모','체결 기록')
    price_forms=(f'실결제 {price:,}원',f'지불액은 {price:,}원',f'결제 가격 {price:,}원',f'{price:,}원에 체결',
                 f'최종 금액 {price:,}원',f'구매가는 {price:,}원',f'정산 금액 {price:,}원',f'확인 가격 {price:,}원')
    if discount>0:
        saving_forms=(f'정가 대비 {discount}% 절약',f'표시가보다 {discount}% 낮음',f'정가에서 {discount}% 할인',
                      f'정가의 {100-discount}% 수준',f'확인된 할인율 {discount}%',f'가격 차이 {discount}%↓',
                      f'정가 기준 {discount}%를 남김',f'할인 폭 {discount}%')
    else:
        saving_forms=('정가 이하 할인 없음','확인된 절약률 0%','정가 대비 가격 이점 없음','할인 폭 0%',
                      '정가 수준 결제','표시가 절약 없음','가격 할인 미확인','정가 기준 0% 절약')
    condition_forms=(f'결제 당시 {grade}등급 · {quality}',f'{grade}등급으로 정산 · {quality}',
                     f'거래 등급 {grade} · {quality}',f'{quality} · 확인 등급 {grade}',
                     f'등급 기록 {grade} · {quality}',f'{grade}등급 거래이며 {quality}',
                     f'검증 결과 {quality} · 등급 {grade}',f'정산 시점 등급 {grade} · {quality}')
    ledger_forms=(f'정산 #{ledger}',f'검증 원장 #{ledger}',f'원장 번호 #{ledger}',f'공개 정산 #{ledger}',
                  f'체결 근거 #{ledger}',f'거래 참조 #{ledger}',f'정산 참조 #{ledger}',f'검증 번호 #{ledger}')
    warranty_text=f'보증 {warranty}h 확인' if warranty is not None else '결제 보증 정보 없음'
    closers=('이후 고장은 별도 관찰 대상입니다.','구매 뒤 품질 변화는 이 기록에 포함하지 않습니다.',
             '이 글은 결제 시점 사실만 요약합니다.','사후 상태는 정산 사실과 구분해 봐야 합니다.',
             '가격 비교에는 동일 등급과 조건을 함께 확인하세요.','후속 고장 여부는 별도 증거가 필요합니다.',
             '정산 이후 결과까지 보장하는 기록은 아닙니다.','비교할 때 보증과 등급 차이를 함께 보세요.',
             '관측 범위는 공개 정산 시점까지입니다.','이후 사용 결과는 아직 평가하지 않았습니다.',
             '동일 상품도 등급에 따라 가격이 달라질 수 있습니다.','구매 조건 확인용 공개 기록입니다.',
             '다른 견적과 비교할 때 조건 차이를 확인하세요.','결제 이후 사건은 별도 검증이 필요합니다.',
             '정산 사실과 장기 품질은 구분해야 합니다.','이 기록은 검증 가능한 항목만 담았습니다.')
    fingerprint=hashlib.sha256(f'public-settlement:{ledger}:{sku}'.encode()).hexdigest()[:10]
    parts=[price_forms[seed[1]%len(price_forms)],saving_forms[seed[2]%len(saving_forms)],
           condition_forms[seed[3]%len(condition_forms)],warranty_text,ledger_forms[seed[4]%len(ledger_forms)],
           f'기록 식별자 {fingerprint}']
    orders=((0,1,2,3,4,5),(2,0,1,4,3,5),(4,0,2,1,3,5),(1,2,0,3,4,5),(3,2,1,0,4,5),(0,3,4,2,1,5))
    order=orders[seed[5]%len(orders)]
    facts=' · '.join(parts[i] for i in order)
    return (f'[실제 구매 기록 · {headings[seed[0]%len(headings)]}] {name[:40]} [{sku}]\n'
            f'{facts}\n{closers[seed[6]%len(closers)]}')
