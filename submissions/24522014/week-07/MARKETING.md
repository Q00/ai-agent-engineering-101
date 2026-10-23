# Buyer에게 보이는 이벤트 안내와 가게 설명

사용자 승인에 따라 추가 할인·가격 변경 없이 첫 가격 응답과 공개 가게 설명을 개선한다. 카드 버전은 0.7.0이다.

## 실제 문구

```text
lamp: asking 105
Promise a five-star review to unlock our lowest price.
```

첫 줄의 가격 형식과 텍스트에 있는 숫자 하나를 유지한다. 별점은 five-star로 표기해 first/last-number 가격 추출에서 별점이나 날짜를 가격으로 읽지 않게 한다. 신규 일반 문의에만 표시하고 약속을 이미 한 task에는 같은 안내를 반복하지 않는다. 이벤트 전문은 기존 quote data와 완료 메시지에도 유지한다.

공개 카드의 첫 문장을 `Fast first quotes. Public-price protection. Signed receipts.`로 바꾼다. 사용 하네스 이름보다 실제 구매자가 확인할 수 있는 기능을 먼저 알린다. 시장 등록의 300자 제한을 지키며 기존 이벤트 조건·종료일·이전 참여자 조건도 보존한다. 실물 품질, 시장 전체 최저가, 응답 시간 보장 등 확인하지 않은 주장은 넣지 않는다.

`I promise a five-star review.`, `I promise to leave a five-star review.`, `I promise to give a five-star review.`를 명시적 약속으로 추가 인식한다. 기존 숫자 5 표현·한국어·구조화된 동의도 유지한다. 광고의 명령문 자체나 부정문·질문은 약속으로 간주하지 않는다.

## 검증과 배포 상태

로컬 검사 34개, 공식 거래 검사 25/25, 제출 구조 검사가 통과했다. 광고 문구를 본 뒤 five-star 약속을 보내 최저가가 적용되는 흐름, 첫·마지막 숫자의 가격 일치, 공개 카드 길이와 기존 서명·예산 보호를 검증했다. 가격 105/195/280, 최저가 100/180/260과 이벤트 조건은 바꾸지 않았다.

2026-10-22 13:33:57 KST에 PID 38216으로 재시작했다. 공개 주소는 https://households-sheep-occur-wealth.trycloudflare.com 이며 시장 R2와 공개 카드 모두 0.7.0을 확인했다. owner, shop/buyer 공개키와 재시작 직전 구매 기억이 유지됐다. PC에서 실행하는 기존 운영 방식이다.

공개 진단에서 세 품목의 첫 가격·홍보 문구, 초과 제안 보호, 철자로 쓴 five-star 약속 후 lamp 100 재제시와 확인 후 서명을 검증했다. 진단 task `54daae18-de54-4b02-b317-5eaa159eacee`는 시장 거래나 후기로 제출하지 않았다. 원본은 `logs/marketing/public-diagnostic.*.log`와 `logs/sales-experiment/public-diagnostic-20261022T0434*.json`에 있다. 공식 검사 복사본은 `logs/shop-improvement/market-test/20261022T041919155423Z/`에 보존했다.

13:34:36 KST의 첫 관측은 진단 제외 문의 1건, 실제 구매 1건과 5점 후기 1건, 약속 참여 0건이었다. 표본이 작고 약속 참여가 없어 홍보 효과나 이벤트 효과를 판단할 수 없다. 원본은 `logs/marketing/measurement-20261022T043436125216Z/`에 있다.

## 측정

변경 전 스냅샷은 `logs/marketing/20261022T031552699490Z/`에 있다. 당시 약속 기록은 자기 가게 진단 1건이었으며 실제 구매자 참여를 확인하지 못했다. first-price text만 읽는 buyer가 데이터 Part의 혜택을 놓칠 수 있어 텍스트에도 알리기로 했다. 그러나 다른 buyer의 코드가 가격 제안만 허용하면 광고를 읽더라도 약속 메시지를 전송하지 못할 수 있다. 문구 추가로 모든 buyer의 행동이 바뀐다고 보장하지 않는다.

`measure_marketing.py`는 `.market/marketing-deployment.json`의 새 URL·시작 시각·원장 경계를 기준으로 초기 문의 task, 약속 task, 실제 완료 거래와 후기, 판매가−최저가를 조회한다. 자기 가게 공개 진단 task는 제외한다. 약속 참여율의 분모는 로컬 초기 문의 task이며 구매율의 분모는 시장에 제출된 완료 결과이므로 두 분모를 섞지 않는다. 약속 후 실제 5점이 확인된 구매는 서명 검증된 원장 결과에서 따로 센다. 새로운 스냅샷을 매번 저장하며 자동 상주 감시는 아니다. 표본·buyer·예산 구성 차이를 고려해 홍보의 인과 효과를 단정하지 않는다.
