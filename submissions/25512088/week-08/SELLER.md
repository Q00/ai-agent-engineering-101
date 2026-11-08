# Seller 구조

실행 진입점은 두 개뿐이다.

- `seller.py`: 재고 선택, 견적, 협상, 서명 영수증 발행
- `seller_procurement.py`: 시장 관찰 결과로 발주하고 재고 자본을 관리

보조 모듈은 직접 실행하지 않는다.

- `seller_strategy.py`: 등급·유통기한·보증·경쟁 체결가를 반영한 가격
- `seller_market.py`: 48시간 buyer 선호, 현재 활동, 정산, 경쟁 재고·리뷰 집계
- `seller_ledger.py`: 판매·보증·기존 회원 약속의 멱등 정산
- `seller_forum.py`: 실제 재고 광고와 ledger 동기화
- `seller_offers.py`: 현재 판매 가능한 SKU 목록

점수 목표는 Shop ALL의 Book, Revenue, Stars다. 발주는 익명 견적 반복을 실제 buyer로
간주하지 않으며, 서로 다른 정산 buyer와 경쟁 리뷰를 우선한다. 한 SKU 기본 노출은
전체 20%로 제한하고 서로 다른 buyer 수가 충분할 때만 최대 35%까지 확대한다.

검증:

```sh
python -m unittest -v test_referral_seller.py test_procurement.py \
  test_strategic_market.py test_stock_pricing.py test_rollout.py
```
