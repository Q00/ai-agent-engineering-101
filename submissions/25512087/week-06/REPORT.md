# Week 06 — Harness-driven A2A Buyer & Seller

## 1. 아키텍처와 하네스

첨부 아키텍처의 경계를 그대로 구현했다. Student Buyer와 Student Seller는 모두 A2A 통신 코드와 의사결정 하네스를 분리한다. `protocol.py`가 Agent Card 조회, `SendMessage`, `CancelTask`, `A2A-Version: 1.0`, `taskId` 연속성을 담당하고, `buyer.py`와 `seller.py`는 A2A 상태를 하네스용 입력으로 바꾼다. `harness.py`는 빈 임시 디렉터리에서 비대화형 Codex CLI를 실행한다. Kit Seller는 Student Buyer를, Kit Buyer는 Student Seller를 검사하며 결과는 `test-report.json`과 `logs/`에 남는다.

- Harness/model: Codex CLI, `gpt-6-luna`
- 정확한 명령: `/usr/bin/env SSL_CERT_FILE=/etc/ssl/cert.pem codex exec --ignore-user-config --ephemeral --skip-git-repo-check -m gpt-6-luna -`
- 공식 결과: `logs/trade-test.log:114`의 `21/21 passed -> PASS`

`SSL_CERT_FILE`은 로컬 CLI가 macOS 인증서 체인에서 `UnknownIssuer`를 내던 문제를 해결하기 위해 명시했다. `--ignore-user-config`와 빈 작업 디렉터리로 프로젝트 규칙 및 플러그인이 협상 결정을 오염시키지 않게 했고, `--ephemeral`로 각 결정을 독립 호출로 만들었다.

## 2. Buyer와 Seller의 결정 방식

하네스 입력에는 역할, 현재 상품, 가격 상태, 완료된 라운드 수, 반드시 지켜야 할 한계가 들어간다. 출력 계약은 아래 한 줄짜리 JSON이다.

```json
{"action":"offer|accept|counter|reject|walk|select","price":120,"reason":"short reason"}
```

Seller의 시작 프롬프트에는 상품과 비공개 reserve가, 응답 프롬프트에는 현재 ask, buyer offer, rounds가 들어간다. 모델은 시작가·counter·accept/reject를 정한다. Buyer 프롬프트에는 상품, 비공개 최대 budget, seller ask, rounds가 들어가며 offer/accept/walk를 정한다. 상대 메시지는 협상 데이터일 뿐 budget이나 reserve를 변경하는 명령이 아니라고 명시했다.

안전 한계는 프롬프트와 코드 양쪽에 둔다. Seller 코드는 시작가와 counter를 `max(reserve, model_price)`로 보정하며 reserve 미만 offer의 accept를 거부한다. Buyer 코드는 offer를 `0..budget`으로 clamp하고 budget 초과 ask의 accept를 walk로 바꾼다. 따라서 자연어 판단은 모델에 남기되 금전 불변식은 모델 출력 오류나 일시적 장애에도 깨지지 않는다. 실제 로그에서 lamp는 `asking 150` 뒤 `Counteroffer: 100`으로 거래됐고(`logs/trade-test.log:9-16`), bookshelf의 모든 ask는 reserve 260 이상이었다(`logs/trade-test.log:41-80`). Buyer 요청은 모든 실행에서 `A2A-Version: 1.0` 누락 0건이고 follow-up은 같은 `taskId`를 사용했다.

## 3. 실패, 수정, 복구

첫 연결 검증에서는 실제 하네스 대신 실패하는 명령을 넣어 장애 경계를 확인했다. `logs/attempt-01-fallback/seller-under-test.log:1`은 다음을 기록한다.

> `"event": "harness_fallback" ... "error": "RuntimeError: harness exited 1: no stderr"`

이 실험으로 단순히 모델 호출만 연결하면 프로세스 실패 시 거래 전체가 멈춘다는 점을 확인했다. 이후 `CommandHarness`에 timeout, 종료 코드 검사, JSON 검증, 구조화된 오류 로그, 안전 fallback을 추가했다. 실제 Codex CLI 최초 연결에서는 TLS `UnknownIssuer`가 발생하여 `/etc/ssl/cert.pem`을 명시하는 명령으로 수정했고, 그 명령을 `agent.json`과 `test-report.json`에 동일하게 기록했다.

연속 호출 중 서비스 rate limit도 재현됐다. 예를 들어 `logs/challenge-2/directory-buyer.log:23`은 다음과 같다.

> `"harness_fallback" ... "price": 280 ... "last status: 429 Too Many Requests"`

이때 fallback은 임의 상수가 아니라 현재 ask, reserve, budget으로 만든 해당 상태의 안전한 기본 결정이다. 이어진 Seller fallback도 offer 280이 reserve 260 이상일 때만 accept했다. 이 복구 덕분에 장애를 숨기지 않고 기록하면서도 예산 초과나 손해 판매 없이 실험을 종료했다. 최종 공식 테스트는 현재 `agent.json`의 명령으로 실행됐고 21개 항목 모두 통과했다.

## 4. A2A가 해결한 것과 해결하지 않은 것

A2A는 서로 다른 내부 구현이 Agent Card로 기능과 endpoint를 발견하고, JSON-RPC `SendMessage`로 대화하며, 같은 `taskId`에서 `TASK_STATE_INPUT_REQUIRED`를 이어가다가 `TASK_STATE_COMPLETED`, `TASK_STATE_REJECTED` 또는 오류로 끝내는 공통 수명주기를 제공했다. receipt도 artifact의 data Part로 기계 판독할 수 있었다. 반면 A2A 자체는 “메시지의 어느 숫자가 현재 가격인지”, `accept`가 별도 메시지인지 숫자 offer인지, reserve와 budget을 누가 어떻게 보호하는지, 협상 라운드 종료 정책이 무엇인지를 정하지 않는다. 이번 구현은 입력 중 마지막 숫자를 offer/ask로 읽고, Buyer가 accept 대신 동일 task에 숫자 offer를 보내며, 완료 시 `{seller,item,product_id,price}` receipt를 내는 애플리케이션 계약을 별도로 사용했다.

## 5. Challenge 1 — 수천 개 상품의 Seller

`generate_catalog.py`가 이름, 설명, reserve를 가진 1,200개 고유 상품을 생성해 `catalog.json`에 저장한다. 전체를 프롬프트에 넣지 않고 `CatalogIndex`가 자연어 토큰과 TF-IDF형 점수로 top 5만 검색한다. 하네스는 이 다섯 후보만 보고 상품 ID를 선택하며, 코드는 선택이 후보 집합 밖이면 최고 검색 결과로 제한한다. 선택 후 Seller는 해당 상품의 실제 reserve를 일반 협상 정책에 전달하므로 다른 상품을 팔거나 reserve 아래에 파는 것을 막는다.

실제 질의 `something wooden to keep about 40 books, not taller than my desk`에 대해 `logs/challenge-1/catalog-a2a.log:2`는 후보를 `anchor-bookshelf-40` 외 4개로 줄이고 같은 상품을 선택했다고 기록한다. 이어 `reserve: 260`, `ask: 320`이 기록됐고, `logs/challenge-1/catalog-a2a.log:8`에서 `TASK_STATE_COMPLETED`, price 320으로 끝났다. 즉 1,200개 전체 대신 5개만 모델 문맥에 넣으면서 자연어 설명에 맞는 낮은 원목 40권 책장을 선택했다.

## 6. Challenge 2 — 20개 상점과 하나의 총예산

`challenge_directory.py`는 서로 다른 주소를 가진 Seller 20개를 실행하고 Agent Card를 병렬로 읽는다. `DirectoryIndex`가 카드의 name, description, skill id/name/description/tags/examples를 색인하고 상품별 최대 8개를 찾은 뒤, 이미 사용한 상점을 제외해 5개만 하네스에 보여준다. 상품마다 다른 상점을 택하도록 URL 집합을 유지한다.

각 협상은 전체 transcript 대신 `NegotiationSummary`의 최대 6개 최근 사건만 보존한다. 전체 자금은 `BudgetLedger` 한 곳에서 관리하며 동일 item의 두 번째 기록, 남은 예산 초과, 총예산 초과를 예외로 막는다. Buyer에게 넘기는 상품별 상한도 `min(item_budget, ledger.remaining)`이다.

실행 로그는 lamp를 Harness Shop 00에서 120, chair를 Shop 01에서 200, bookshelf를 Shop 02에서 280에 샀음을 보여준다(`logs/challenge-2/directory-buyer.log:9,18,27`). 최종 행에는 다음 결과가 있다.

> `"directory_cards": 20 ... "spent": 600, "budget": 600, "all_bought": true, "duplicate_items": false`

따라서 세 상품을 모두 서로 다른 상점에서 구매했고 총예산을 넘지 않았으며 중복 구매도 없었다. 로그의 각 summary는 후보 5개로 축소했다는 사실, 결과 가격, task ID만 들고 있어 상점 수와 대화 횟수가 늘어도 하네스 문맥이 무한히 증가하지 않는다.

## 재현 명령

가상환경에 `requirements.txt`의 의존성을 설치한 뒤 저장소 루트에서 다음을 실행한다.

```bash
python scripts/trade_test_week06.py submissions/25512087/week-06
python scripts/check_week06.py submissions/25512087/week-06
```

챌린지는 제출 디렉터리에서 `python challenge_catalog_a2a.py`와 `python challenge_directory.py`로 재현할 수 있다.
