# Round 3 운영 판단 분석 (2026-11-10 21:50–22:10 KST)

이 폴더는 2026-11-10 저녁에 공개 API(`/api/board`, `/api/ledger`, `/api/supply-prices`)를 읽어 만든 분석이다.
주문·구매·호가 변경은 하지 않았다. 시장 쓰기 호출은 없다.

## 재현

```
python fetch_ledger.py      # ledger_dump.json (41 MB, 커밋하지 않음): 최근 1시간 전부 + 24시간 15분 간격 표본 96개
python ana.py               # settles.json (1h 전수, 24h 표본과 배율)
python sku.py               # sku_rows.json: SKU별 정산 수, 구매자 수, 등급별 체결가/정가
python cand.py              # cands.json: 공급 후보 (예상/최악 원가, 기대 이익)
python rank.py              # board ALL 공식 재현 검증
python scen.py              # 학생 Players 백분위 시나리오 (scen-output.txt)
```

`board-20261110T2155.json`은 분석에 쓴 board 스냅샷이다.

## 검증한 사실

- board ALL 공식은 agent.md의 가중치로 재현된다. 가게 ALL은 `stars`가 아니라 `stars_rank`를 쓸 때 평균 오차 0.0002,
  buyer ALL은 0.0003, 학생 전용 백분위(`student_shop`)는 최대 오차 0.0004 (rank-output.txt).
- 24시간 추정은 표본 배율 8.5를 쓴다(표본 96개, 약 9,765초). 1시간 값은 전수다.
- 등급별 체결가/정가 중앙값(1h+24h 표본): 비식품 A 0.705, B 0.626, C 0.549 / 식품 A 0.712, B 0.604, C 0.540.
- 비식품 고장: 같은 구간 고장 328건 / 비식품 정산 4,128건 (약 7.9%). 등급이 확인된 39건 기준 상대 비율로 추정하면 A 약 2%, B 약 12%, C 약 36%
  (표본이 작아 불확실). 비식품 정산의 80%에 보증이 붙어 있다.
- 자기 Seller 거절 사유(.market/seller-ledger.json 81줄): `no_verified_inventory` 72, `no_price_agreement` 1, 합의 8.

## 한계

- 다른 참가자가 계속 거래하므로 시나리오의 백분위는 다른 사람이 지금 상태로 멈춘다고 가정한 낙관값이다.
- `stars_rank` 계산식은 공개되지 않았다. 시나리오의 값(20건 4.15, 30건 4.2, 50건 4.28)은 비슷한 리뷰 수를 가진 가게들에서 읽은 값이다.
- 호가에 따른 전환율은 관측하지 않았다. "시장 정산 중 내 호가 이상 비율"은 경쟁력을 보여 주는 대리 지표일 뿐이다.
