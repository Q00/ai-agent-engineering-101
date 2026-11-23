# 공개 구현 참고와 적용 기록 — 2026-11-04
코드를 통째로 덮어쓰지 않고 확인한 알고리즘을 기존 키·회원·예산·결제 프로토콜에 맞춰 새로 구현했다. 공개 브랜치와 상대의 실제 배포 버전이 같다는 보장은 없다.
- [inextro/market_buyer.py](https://github.com/inextro/ai-agent-engineering-101/blob/week-08/submissions/25520014/week-08/market_buyer.py)
- [inextro/seller.py](https://github.com/inextro/ai-agent-engineering-101/blob/week-08/submissions/25520014/week-08/seller.py)
- [inextro/restock.py](https://github.com/inextro/ai-agent-engineering-101/blob/week-08/submissions/25520014/week-08/restock.py)
- [Nocha12/profit_price.py](https://github.com/Nocha12/ai-agent-engineering-101/blob/week-08/submissions/26622007/week-08/profit_price.py)

## 적용한 대응

- inextro buyer: 상위 후보 동시 견적, 한도 내 절대 surplus 비교, 실패한 후보 다음 후보 검토. 우리 구현은 최대3개 동시 요청·SQLite 연결 분리·결제 불명 시 중단·한 주문 한 영수증을 유지한다.
- inextro seller: 낮은 제안에 하한까지 양보. 우리 창립단골/추천/크레딧 정책을 재계산하며 하한과 기존 더 낮은 약속을 지킨다.
- Nocha12 profit_price: (원가+유보이익)/(1-고장률) 접근. 15%는 상대 코드의 사전 가정이며 실제 고장률이라고 주장하지 않는다. 우리 할인/추천 최악 비용을 함께 반영하고 6시간 보증 금액 전액을 별도로 유보한다. 실제 환불 손실 가능성은 남는다.
- inextro restock: 보충 주기와 판매속도/환불 노출. 우리 총24개·SKU당최대2개·현금50% 및5% 지출한도는 유지하며 최근1시간 판매와 약20분 보충 가정으로 목표1~2개를 고른다. 상대의 대규모 재고 한도를 복사하지 않았다.
- 거래 결과 모델: 제안까지 진행해 결과가 확정된 거래만 Beta(2,2)로 축소 추정. 인과·보정 성능 검증 전 초기 모형이며 기존 신뢰도와 별도로 로그에 남긴다.

## 카드·포럼 비교에서 반영

확보한9개 카드: inextro, nocha12, mjforge, lee-sungsoo, dragonash, yms979, myeonggi, winun1127, chaewoonee. dae-yangkim 카드는 오프라인으로 미확보.
공통으로 실제 재고·인증·등급·보증·SKU 문의/숫자 구매 절차를 앞세운다. 우리 카드도 동일한 사실 중심 구조로 바꾸고 실제 구현된6시간 보증을 명시했다. 상대의 '100% 정직' 같은 누적 실적 주장은 복사하지 않았다.
포럼 표본에서 dragonash·shaun0927·nocha12는 장부 근거 시세, 25620027은 실제 정산의 등급·보증·가격을 제시했다. 우리 실구매 글에도 결제 보증을 넣고 가게 글에는 실제 구매 가능한 SKU·등급·가격·조건을 먼저 노출한다. totato의 맞좋아요 유도 문구는 도입하지 않았다. 포럼 한도는 그대로 유지하며 추가 수동 발행하지 않는다.

## 아직 해결하지 못한 범위

기존 서명 예약: 옛 영수증의 유효기간/철회 근거가 없고 이미 발급된 checkout JWT는 단순 취소만으로 시장에서 무효화되는지 보장되지 않는다. 새 견적의 임시 예약은 공유하고 취소 시 해제하지만 기존 서명 예약은 삭제하지 않았다. 이 문제의 완전 해결과 추천 실제 전환은 미완료다.
고장률 실측 보정·엄밀한 기대수익 모형 검증·기존 보증 요구 완화도 완료라고 표시하지 않는다.
