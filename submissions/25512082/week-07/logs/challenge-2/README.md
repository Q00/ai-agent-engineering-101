# Challenge 2 검증 기록

## 구현 내용

`market_buyer.py`의 `choose_shop`은 공개 리뷰와 로컬 거래 기억을 함께 점수화합니다. 별점 평균은 긍정 신호로 사용하고, 낮은 별점의 리뷰에 `overcharg`, `receipt`, `did not match`, `higher than`, `avoid` 같은 표현이 있으면 무결성 경고로 계산하여 건당 12점을 감점합니다. 과거 영수증 불일치는 건당 25점, 무거래 경험은 건당 1점을 감점하며, 성공 거래는 최대 5건까지 건당 2점을 더합니다.

기억은 `.market/runtime-state/buyer-memory.json`에 가게 이름을 키로 저장합니다. 각 가게에는 `deals`, `no_deals`, `receipt_mismatches`, `last_outcome`, `last_price`가 기록됩니다. URL은 Quick Tunnel 재시작 때 달라질 수 있으므로 기억의 키로 사용하지 않습니다. `.market/`은 Git에서 제외되어 운영 상태나 개인 정보가 제출물에 섞이지 않습니다.

## 격리 검증 결과

- `test_market_buyer.py::test_bad_reviews_are_avoided`는 과금과 영수증 불일치를 지적한 1점 리뷰의 `Bad` 가게 대신 5점 리뷰의 `Good` 가게를 고르는지 확인하며 통과했습니다.
- 공식 `logs/market-test.log`의 `buyer_reads_reviews`에서는 나쁜 리뷰를 가진 `Kit Shop North`에 세 번 모두 요청을 보내지 않았고, 좋은 리뷰를 가진 `Kit Shop South`에서 세 번 모두 거래했습니다.
- `test-report.json`은 `shop with good reviews in 3 of 3 runs`를 기록하며 전체 25/25 통과 상태입니다.

## 실제 시장에서의 한계

실제 Round 2 로그에서는 무결성 경고나 영수증 불일치가 1건 이상 누적된 판매자를 만나지 못했습니다. 실제 선택은 `thisjonghoon` 234회, `moss-and-oak` 2회였으며, 시장에서 특정 판매자를 부정직하다고 단정할 증거는 확보하지 못했습니다. 따라서 실제 사람을 부정직한 판매자로 지목하지 않으며, Challenge의 회피 동작은 재현 가능한 격리 테스트로만 입증합니다.

Seller 서명의 공개키 검증과 시장 주문 일치 여부는 공식 `market_kit.py`가 확인한 뒤 Buyer 서명을 추가합니다. 실제 운영 로그에는 `sig_seller ok`가 남았고, 시장이 인정한 194건은 모두 `verified True`와 `review accepted`가 함께 기록되었습니다.

