# Week 06 보고서

## 1. 하네스, 모델 및 실행 명령

- 하네스: Codex CLI 0.163.0
- 모델: `gpt-6.1-sol`
- 추론 강도: low
- Seller 명령: `sh run_agent.sh seller.py --port {port}`
- Buyer 명령: `sh run_agent.sh buyer.py --url {url} --item {item} --budget {budget}`

`run_agent.sh`는 로컬 과제 가상환경이 있으면 해당 Python을 사용하고,
깨끗한 checkout에서는 과제 의존성이 설치된 `python3`를 사용한다. 따라서
Git에서 제외된 가상환경 경로만을 필수 조건으로 두지 않는다.

공식 Trade Test는 약 5분 52초가 걸렸으며 현재 `test-report.json`에는
`passed=true`가 기록되어 있다. `logs/trade-test.log`의 마지막 결과는
`21/21 passed -> PASS`이다.

## 2. Seller와 Buyer의 판단 방식

두 에이전트는 판단마다 Codex를 비대화형으로 한 번 호출한다. Codex의
응답은 각각 `schemas/seller_decision.json` 또는
`schemas/buyer_decision.json`을 만족하는 JSON이어야 한다. 자동 재시도와
fallback은 없다. 프롬프트에는 상품, 현재 가격, 압축한 협상 기록 및 해당
하한가 또는 예산을 제공한다.

Seller는 ask, counter, accept, reject 중 하나를 선택한다. Python 상태 계층은
하한가보다 낮은 최초 가격과 역제안을 거부하며, accept 가격이 최신 Buyer
제안과 같고 하한가 이상일 때만 체결을 허용한다. Buyer는 offer, accept,
walk_away 중 하나를 선택한다. Python 계층은 예산을 초과한 판단과 완료
receipt를 거부한다. 따라서 하한가와 예산 제한은 프롬프트와 코드 양쪽에서
검증한다.

## 3. 실패와 수정 과정

첫 공식 실행에서는 환경에 따라 달라지는 `python` 명령을 사용했다. 보존한
실패 로그에는 다음 내용이 있다.

> `FAIL  seller_card #1: no Agent Card at /.well-known/agent-card.json within 60 s`

규칙 기반 Seller도 Buyer 요청을 전혀 받지 못했다.

> `FAIL  buyer_protocol_deal #1: posts 0, without A2A-Version 1.0 0, follow-ups with taskId 0`

이 실행은 `logs/attempts/official-01-environment-failure/`에 그대로 보존했다.
이후 `agent.json`의 명령을 과제 가상환경 인터프리터의 명시적 경로로
변경했다. 다음 공식 실행에서는 에이전트가 정상 시작되었고 최종적으로
`21/21 passed -> PASS`가 기록되었다.

lamp 진단의 첫 실제 협상은 Seller 150, Buyer 90, Seller 130, Buyer 110,
Seller 120까지 진행된 뒤 다섯 번의 호출 제한으로 `round_limit`이 되었다.
마지막 Buyer 판단 후 이미 반환된 Seller A2A 응답을 한 번 더 검사하도록
수정하고, 진단 전용 프롬프트에 남은 Buyer 판단 횟수를 정확히 제공했다.
다음 다섯 번의 호출에서는 120에 거래가 완료되었다. 이 라운드 안내는 일반
제출용 프롬프트와 분리되어 있다.

## 4. A2A가 정한 것과 별도 합의가 필요했던 것

A2A는 Agent Card 탐색, JSON-RPC 전송, task ID, task 상태, 후속 메시지의
task 연결, 취소 및 artifact 형식을 제공했다. 하지만 이 과제의 협상 언어는
정하지 않는다. 따라서 양측은 텍스트 응답의 마지막 숫자를 현재 가격으로
해석하는 규칙, `accept`의 의미, 완료 receipt의 data part에 정수 `price`를
넣는 규칙을 별도로 공유해야 했다. 자유 형식 모델 응답만 신뢰하지 않고
Python 계층에서 이 규칙들을 다시 검증했다.

## 5. 필수 Challenge

### Challenge 1: 대규모 Seller 카탈로그

`catalog/challenge1_catalog.jsonl`에는 직접 생성한 상품 3,001개가 있으며 각
상품은 ID, 이름, 설명과 하한가를 가진다. 로컬 토큰 검색으로 전체 카탈로그를
최대 8개의 후보로 줄인 뒤 그 후보만 Codex에 전달한다. Codex가 고른 ID가
검색 후보에 포함되어 있는지 코드로 확인하고, 선택한 상품과 하한가를 해당
A2A task가 끝날 때까지 고정한다.

자연어 요청 `something wooden to keep about 40 books, not taller than my desk`에
대해 `logs/challenge-1/server-audit-02.log`에는 다음 실제 선택 기록이 있다.

> `{"candidate_count": 8, "event": "catalog_selection", "reserve": 260, "selected_id": "bookshelf-low-oak-40", "task_id": "1190d520-6d4d-4029-8210-d3a032a137cb"}`

같은 로그의 완료 이벤트는 동일 task ID, 상품 ID
`bookshelf-low-oak-40`, 가격 320 및 `TASK_STATE_COMPLETED`를 기록한다.
따라서 선택 상품, 하한가 보호, receipt의 상품 ID 및 최종 상태를 prompt나
모델의 상세 추론을 저장하지 않고도 확인할 수 있다.

### Challenge 2: 여러 판매점과 쇼핑 목록

`challenge2_shops.json`에는 서로 다른 이름, 상품 구성과 하한가를 가진
판매점 20개가 있다. Buyer는 20개 Agent Card의 skill을 읽고 상품별로
관련도를 계산한 뒤 최대 6개 카드 요약만 Codex에 제공하여 최종 판매점을
고른다. 후보에 없는 URL을 선택하면 코드가 거부한다.

전체 예산은 협상 전에 상품별 고정 할당액으로 나눈다. 각 상품은 독립적인
`BuyerClient`, task ID 및 압축 협상 기록을 사용하며 최대 8개의 협상을
동시에 수행할 수 있다. 상위 조정 계층에는 상품, 판매점 URL, 할당 예산,
결과, task ID와 receipt 가격만 유지한다. 성공하려면 중복 없는 쇼핑 목록의
모든 상품이 각각 한 번 체결되고, 각 가격이 할당 예산 이내이며, task ID가
서로 다르고, 총지출이 단일 전체 예산 이하여야 한다.

실제 실행에서는 `desk lamp`, `office chair`, `bookshelf`와 총예산 900을
사용했으며 상품별 할당액은 300이었다.
`logs/challenge-2/actual-01/buyer.log`에는 `"success": true`와
`"total_spent": 605`가 기록되어 있다. 세 상품은 각각 120, 210, 275에
구매되었고 task ID 세 개는 모두 달랐다. Seller 19001, 19002, 19003의
로그에는 Buyer 결과와 일치하는 `trade_completed` 이벤트와
`"a2a_state": "TASK_STATE_COMPLETED"`가 있다. 같은 실행 디렉터리에 Seller
20개의 원본 출력과 Buyer 원본 출력을 모두 보존했다. 따라서 쇼핑 목록의
모든 상품을 정확히 한 번 구매했고 총지출은 예산 900 이내였으며 task도
재사용되지 않았다.

Buyer 원본 로그의 결과는 다음과 같다.

> `{"purchases": [{"budget": 300, "item": "desk lamp", "outcome": "deal", "price": 120, "shop_url": "http://127.0.0.1:19001", "task_id": "73894021-71af-4f60-9f0d-4e0224228b5d"}, {"budget": 300, "item": "office chair", "outcome": "deal", "price": 210, "shop_url": "http://127.0.0.1:19002", "task_id": "c092f497-3b64-43bc-94b9-eaa5135b65f5"}, {"budget": 300, "item": "bookshelf", "outcome": "deal", "price": 275, "shop_url": "http://127.0.0.1:19003", "task_id": "5a1a86d7-de83-4970-8be2-64b6f22b80c7"}], "success": true, "total_budget": 900, "total_spent": 605}`

North Lamp의 Seller 원본 로그에도 같은 task와 가격이 기록되어 있다.

> `{"a2a_state": "TASK_STATE_COMPLETED", "event": "trade_completed", "item": "desk lamp", "price": 120, "shop": "North Lamp", "task_id": "73894021-71af-4f60-9f0d-4e0224228b5d"}`
