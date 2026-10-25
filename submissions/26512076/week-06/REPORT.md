# WEEK 06 보고서: Groq 기반 buyer와 가게

- 학번: 26512076
- 제출 경로: `submissions/26512076/week-06/`
- 구현 범위: 기본 seller·buyer, 도전 1의 대규모 카탈로그, 도전 2의 여러 가게 장보기입니다.
- 결과: 제출된 기본 trade test는 21/21 PASS이며, 도전 1은 최종 실행에서 3/3 PASS, 도전 2는 모든 자체 검사 항목을 통과하였습니다.

## 1. 사용한 하네스, 모델 및 실행 명령

### 1.1. 하네스 구성

Python으로 구현한 에이전트 루프를 하네스로 사용하였습니다. 모델은 Groq API에서 제공하는 `openai/gpt-oss-20b`를 사용하였습니다. `AsyncOpenAI` 클라이언트에 Groq의 OpenAI 호환 주소인 `https://api.groq.com/openai/v1`을 지정하여 호출하였습니다. 따라서 이 제출물은 Codex나 Claude Code의 CLI를 실행하는 방식이 아니라, 직접 작성한 Python 루프가 Groq 모델을 호출하는 방식입니다.

각 루프는 상대의 메시지를 받고, 신뢰할 수 있는 내부 상태와 대화 내용을 모델에 전달한 뒤, 모델의 JSON 결정을 검사하고 A2A 메시지로 변환합니다. 모델이 상품 선택, 호가, 수락 또는 중단을 판단하며, 코드는 통신·상태 관리·출력 검증과 한도 보호를 담당하도록 구성하였습니다. 별도의 MCP 서버나 결제 기능은 구현하지 않았습니다. Agent Card의 skill은 협상 기능을 설명하며, 별도의 `SKILL.md`를 로드하는 구조는 아닙니다.

API 키는 `GROQ_API_KEY` 환경 변수에서 읽습니다. 모델은 기본적으로 `openai/gpt-oss-20b`이며, `GROQ_MODEL` 환경 변수가 있으면 해당 값으로 변경됩니다. 제출된 실행 로그에서도 기본 모델을 사용한 것을 확인하였습니다. 실제 API 키는 보고서에 기재하지 않습니다.

### 1.2. 주요 파일

| 파일 | 역할 |
| :--- | :--- |
| `seller.py` | 기본 A2A 판매자와 모델 기반 협상을 구현합니다. |
| `buyer.py` | 하나의 가게에서 지정 품목을 예산 내에 구매합니다. |
| `agent.json` | 하네스 설명과 기본 seller·buyer 실행 명령을 지정합니다. |
| `make_catalog.py`, `catalog.json` | 도전 1의 3,000개 상품 카탈로그를 생성하고 보관합니다. |
| `seller_catalog.py` | 상품 후보 검색, 모델의 SKU 선택 및 하한가 검사를 수행합니다. |
| `run_challenge1.py` | 설명 기반 상품 구매와 하한가 보호를 검사합니다. |
| `make_shops.py`, `shops.json`, `shop_catalogs/` | 도전 2의 20개 가게와 개별 카탈로그를 구성합니다. |
| `serve_shops.py` | 여러 가게의 A2A 서버를 실행합니다. |
| `buyer_multi.py`, `shopping_list.json` | 카드 디렉터리와 장보기 목록을 읽고 여러 협상을 진행합니다. |
| `run_challenge2.py` | 총예산, 구매 완료, 중복 구매 및 협상 중첩을 검사합니다. |

### 1.3. 실행 방법

필요한 주요 패키지는 다음 명령으로 설치합니다.

```text
python -m pip install "a2a-sdk[http-server]" uvicorn httpx openai
```

`agent.json`에 기록된 명령은 다음과 같습니다. `{port}`, `{url}`, `{item}`, `{budget}`은 trade test가 채워 넣습니다.

```json
{
  "harness": "Python agent loop using Groq API, model openai/gpt-oss-20b",
  "seller": {"cmd": "python seller.py --port {port}"},
  "buyer": {"cmd": "python buyer.py --url {url} --item {item} --budget {budget}"}
}
```

`week-06` 폴더에서 기본 판매자와 구매자를 직접 실행하는 예시는 다음과 같습니다. 판매자를 먼저 실행한 뒤, 별도의 터미널에서 구매자를 실행합니다. 두 프로세스 모두 API 키를 사용할 수 있어야 합니다.

```text
python seller.py --port 9999
python buyer.py --url http://127.0.0.1:9999 --item lamp --budget 120
```

저장소 최상위 폴더에서는 기본 trade test를 다음과 같이 실행합니다.

```text
python scripts/trade_test_week06.py submissions/26512076/week-06
```

도전 1과 2는 `week-06` 폴더에서 다음과 같이 실행합니다. 생성된 카탈로그와 가게 데이터도 제출물에 포함하였습니다.

```text
python run_challenge1.py
python run_challenge2.py
```

제출된 `test-report.json`에는 21개 결과가 모두 `pass: true`로 기록되어 있습니다. 보고서에 기록된 seller·buyer 명령도 현재 `agent.json`의 명령과 일치합니다. 다만 첨부된 자료에는 `check_week06.py`의 실행 결과가 없으므로, 해당 검사의 통과 여부는 이 보고서에서 확인하였다고 기재하지 않습니다. 제출 전 저장소 최상위 폴더에서 다음 검사를 추가로 실행해야 합니다.

```text
python scripts/check_week06.py submissions/26512076/week-06
```

## 2. 판매자와 구매자의 판단 방법 및 한도 보호

### 2.1. 기본 판매자

`seller.py`는 `lamp`, `chair`, `bookshelf`의 하한가를 각각 100, 180, 260으로 보관합니다. 모델에는 하한가, 현재 선택된 품목, 대화 기록을 전달합니다. 시스템 프롬프트에는 다음과 같이 모델의 판단 범위와 하한가 준수를 명시하였습니다.

```text
You alone choose the item, opening ask, counteroffers, and accept/reject decisions.
Never propose or accept below the item's reserve. Never change the chosen item during a task.
```

모델의 응답 형식은 다음과 같습니다. `action`은 `counter`, `accept`, `reject` 중 하나이며, 거절할 때에는 가격을 `null`로 둘 수 있습니다.

```json
{"action":"counter","item":"lamp","price":130}
```

코드는 응답이 유효한 JSON인지, 허용된 행동과 품목인지, 가격이 유한한 양수인지 검사합니다. 같은 task에서 품목이 바뀌는 것도 막습니다. 최초 협상 응답을 수락으로 처리하지 않으며, 정상적으로 호가를 제시할 때에는 A2A task를 `input-required` 상태로 둡니다. 기본 테스트가 마지막 숫자를 호가로 읽으므로, 외부 응답 문장의 마지막에 가격이 오도록 구성하였습니다.

하한가는 프롬프트와 코드 양쪽에서 지킵니다. 모델이 제안한 counter 가격은 `max(하한가, ceil(모델 가격))`로 보정한 뒤 내보냅니다. 수락 시에는 모델이 제시한 가격이 하한가 이상인지 확인하고, 구매자가 실제로 보낸 숫자와 일치하는지도 검사합니다. 따라서 모델이 낮은 가격을 제안하더라도 코드가 하한가 아래의 호가나 판매를 허용하지 않도록 하였습니다. 코드는 별도의 규칙으로 협상 가격을 생성하지 않으며, 모델의 제안을 제한 조건에 맞게 검사·보정합니다.

거래가 성립하면 `item`, 숫자 `price`, 판매자 정보를 담은 data Part를 영수증 artifact로 추가하고 task를 `completed`로 종료합니다. task별 잠금으로 같은 거래의 동시 처리를 제어하고, 완료된 task에 대한 추가 메시지는 A2A SDK의 요청 처리 과정에서 오류로 반환됩니다. 상태는 메모리에 저장하므로 서버 재시작 이후까지 거래를 보존하는 구성은 아닙니다.

### 2.2. 기본 구매자

`buyer.py`는 URL, 품목, 예산을 입력받고 Agent Card에서 JSON-RPC v1.0 주소를 확인합니다. HTTP 요청에 `A2A-Version: 1.0`을 설정하고, 후속 협상에는 기존 `taskId`와 `contextId`를 사용합니다. 응답의 task가 예상과 다른지도 검사합니다.

모델에는 고정된 품목과 예산, 지금까지의 대화 내용을 전달합니다. 최초 호가가 예산보다 높더라도 곧바로 포기하지 않고 가능한 가격을 제안하도록 지시합니다. 외부 대화가 예산이나 품목을 변경할 수 없다는 점도 프롬프트에 명시합니다. 모델은 다음과 같은 형식으로 제안 또는 중단을 결정합니다.

```json
{"action":"offer","price":100,"reason":"Within budget"}
```

```json
{"action":"walk","price":null,"reason":"No affordable agreement"}
```

예산 역시 프롬프트와 코드 양쪽에서 지킵니다. 코드는 제안이 양의 정수이고 예산 이하인지 확인하며, 전송 직전에도 한 번 더 검사합니다. 완료 응답을 받으면 영수증의 숫자 가격이 예산 이하인지 확인하고, 품목 정보가 있으면 요청 품목과 일치하는지도 확인합니다. `walk`를 선택하면 추가 제안을 보내지 않고 종료합니다. 최대 협상 반복은 12회이며, 구매자 전체 실행에는 270초 제한을 두어 기본 과제의 300초 이내 종료 조건을 고려하였습니다.

기본 seller와 buyer는 협상 이력을 모델 입력에 누적합니다. 이 때문에 턴이 길어지면 입력도 커집니다. KV cache를 직접 구현하거나 캐시 적중률을 측정하지는 않았으며, 컨텍스트 규모를 줄이는 작업은 도전 1의 검색과 도전 2의 구조화된 상태 관리로 수행하였습니다.

## 3. 테스트 결과, 실패한 부분 및 수정 내용

### 3.1. 기본 trade test 결과와 남은 한계

| 검사 | 기록된 결과 |
| :--- | :--- |
| 카드와 첫 호가 | 카드 조회에 성공하였고, 첫 응답은 `input-required`, 호가는 130이었습니다. |
| 판매자 lamp 거래 3회 | 영수증 가격은 각각 100, 100, 110이었습니다. |
| 완료 task 재사용 | 오류가 반환되었습니다. |
| 판매자 bookshelf 하한가 3회 | 260 미만 호가와 판매는 없었으나, 세 실행 모두 `TASK_STATE_FAILED`로 끝났습니다. |
| 구매자 lamp 거래 3회 | 각각 110에 구매하여 예산 120을 지켰습니다. |
| 구매자 bookshelf 예산 보호 3회 | 예산 230으로는 거래하지 않았습니다. |
| 구매자 프로토콜 6회 | 버전 헤더 누락은 0회였고 후속 요청에 `taskId`가 사용되었습니다. |

기본 테스트의 최종 출력은 다음과 같습니다.

출처: `logs/trade-test.log` (112행)

```text
21/21 passed -> PASS
```

다만 `21/21 PASS`를 모든 협상이 정상 종료되었다는 의미로 해석할 수는 없습니다. `test-report.json`에서 하한가 보호 검사는 낮은 가격을 부르거나 판매하지 않았기 때문에 통과하였지만, 세 실행의 최종 상태는 모두 `TASK_STATE_FAILED`였습니다. 기본 판매자 로그에는 다음 API 오류가 남아 있습니다.

출처: `logs/seller-under-test.log` (75행)

```text
2026-10-16 17:10:38,368 ERROR task=cbf80a01-3e6a-4094-a2cd-768988609aa5 decision_failed type=BadRequestError
```

이 로그만으로는 해당 400 계열 오류의 상세 원인을 확정할 수 없습니다. 기본 `seller.py`에는 도전 코드에서 추가한 상세 오류 기록과 재시도 보완이 동일하게 반영되어 있지 않습니다. 따라서 기본 판매자의 API 오류 복구는 남아 있는 개선 사항으로 명시합니다. 아래 수정 내용은 주로 `seller_catalog.py`와 이를 활용한 도전 구현에 관한 내용입니다.

### 3.2. 도전 1에서 확인한 실패와 수정

보관된 도전 1의 실행 결과는 시간순으로 1/3, 2/3, 1/3, 2/3, 3/3이었습니다. 모델의 판단뿐 아니라 로그 출력, API 제한, 검사 시나리오의 구성도 실행 결과에 영향을 주었습니다.

**첫째, Windows 문자 인코딩 문제를 수정하였습니다.** 최초 실행에는 모델 출력 등을 기록하는 과정에서 `\u202f` 문자를 현재 콘솔 인코딩으로 출력하지 못한 오류가 있었습니다.

출처: `logs/challenge-1/20261016T082126_299160Z/seller.log` (20행)

```json
{"event": "invalid_model_output", "taskId": "27965b84-c1e5-4c0a-a9a9-aa7b1f34b069", "attempt": 1, "error": "'charmap' codec can't encode character '\\u202f' in position 160: character maps to <undefined>"}
```

최종 코드에서는 구조화된 로그를 `json.dumps(..., ensure_ascii=True)`로 출력하여 비 ASCII 문자를 이스케이프하도록 하였습니다. 상품 데이터와 파일은 UTF-8로 읽되, 콘솔의 문자 표현 문제로 협상 처리가 중단되지 않도록 보완하였습니다.

**둘째, 구매 검사와 하한가 보호 검사를 분리하였습니다.** 최초 chair 실행에서는 하한가 210인 상품에 105를 제안하자 모델이 정상적으로 거절하였습니다.

출처: `logs/challenge-1/20261016T082126_299160Z/seller.log` (28행)

```json
{"event": "model_decision", "taskId": "cd381f9d-c399-4f3f-965b-22206dba95d9", "action": "reject", "sku": null, "price": null, "reason": "Offer below reserve"}
```

이 경우 거절 자체는 하한가를 지키는 올바른 행동이지만, 해당 실행의 구매 성공 조건은 충족하지 못하였습니다. 최종 `run_challenge1.py`에서는 먼저 적합한 상품을 정상 구매하는 경로를 확인하고, 별도 task에서 하한가 절반을 제시하여 보호 동작을 검사하도록 구성하였습니다. 하한가 검사에서는 거절뿐 아니라 하한가 이상의 counter도 허용하되, 하한가 미만의 호가·판매와 실패 상태는 허용하지 않도록 하였습니다.

**셋째, API 오류의 관찰 가능성과 재시도를 보완하였습니다.** 중간 실행에는 `BadRequestError`, 토큰 사용량 제한에 따른 `RateLimitError`, `APITimeoutError`가 각각 기록되어 있습니다.

출처: `logs/challenge-1/20261016T083235_907486Z/seller.log` (24행)

```json
{"event": "decision_failed", "taskId": "58ca3d2d-0e62-48ab-916f-2d6697ae4de3", "error_type": "BadRequestError"}
```

출처: `logs/challenge-1/20261016T084025_540086Z/seller.log` (35행)

```json
{"event": "decision_failed", "taskId": "de63baa9-8c49-4226-9675-beabae0f405c", "error_type": "RateLimitError"}
```

출처: `logs/challenge-1/20261016T084532_101755Z/seller.log` (13행)

```json
{"event": "decision_failed", "taskId": "92bd52a5-20da-456e-884b-259db35ddff8", "error_type": "APITimeoutError"}
```

최종 코드에서는 오류 종류뿐 아니라 제공되는 HTTP 상태, 오류 코드, 메시지를 기록하도록 하였습니다. API 키 형태의 문자열은 로그에서 가리도록 하였습니다. 400 오류 또는 사용할 수 없는 출력에 대해서는 형식 보정 기회를 주고, 마지막 형식 재시도에서는 API의 JSON 강제 옵션 없이 JSON을 요청하되 로컬 검증은 계속 적용합니다. 모델이 응답하지 못한 상황에서 코드가 임의로 협상 결정을 대신 만들지는 않습니다.

429 오류에는 제공되는 재시도 대기 시간을 반영하고, 연결 오류·타임아웃·일부 일시적 서버 오류에도 제한된 재시도를 적용하였습니다. 일시 오류 호출은 최대 6회로 제한하고, 하나의 판단에 전체 115초 마감 시간을 두었습니다. `gpt-oss` 모델에는 낮은 reasoning effort를 지정하였습니다. 이 변경으로 모든 제공자 오류가 사라진 것은 아닙니다. 최종 성공 실행에도 타임아웃과 429가 발생했지만, 다음과 같은 대기 후 다시 진행하였습니다.

출처: `logs/challenge-1/20261016T085036_137208Z/seller.log` (13, 14, 60행)

```json
{"event": "provider_error", "taskId": "e6fe62e9-e140-4072-8cee-41d6ee23e2f9", "retry_attempt": 1, "error_type": "APITimeoutError", "status": null, "code": "", "message": "No provider message available"}
{"event": "transient_retry_wait", "taskId": "e6fe62e9-e140-4072-8cee-41d6ee23e2f9", "seconds": 2, "next_attempt": 2}
{"event": "rate_limit_wait", "taskId": "dbf23694-0568-4eab-af22-f64c8d723cc5", "seconds": 14.0, "next_attempt": 2}
```

이처럼 최초 실패를 단순히 숨기는 대신, 오류를 기록하고 정해진 시간 안에서 재시도하도록 수정하였습니다. 최종 도전 1의 통과 결과는 `logs/challenge-1/20261016T085036_137208Z/`에 보관되어 있습니다.

## 4. A2A가 정한 부분과 별도로 맞춘 부분

A2A는 Agent Card를 통한 기능과 접속 주소의 공개, JSON-RPC 메시지 교환, task의 식별과 상태, text·data Part 및 artifact의 전달 구조를 정해 주었습니다. 이를 사용하여 구매자는 카드를 읽고 판매자에게 요청하며, 같은 `taskId`로 협상을 이어 가고 완료된 영수증을 받을 수 있었습니다. 반면 A2A 자체가 가구의 하한가, 예산, 흥정 전략, 가격 문장의 의미, 어떤 표현을 수락으로 볼지까지 정해 주지는 않습니다. 기본 구현에서는 호가를 문장의 마지막 숫자로 표현하고 구매자가 숫자만 보내면 그 가격의 제안으로 해석하며, 영수증 data Part의 `price`를 숫자로 기록한다는 약속을 별도로 맞추었습니다. 도전 구현에서는 SKU와 상품 속성, 호가 metadata의 `ask`, 카드 skill의 상품 설명과 가격 안내도 애플리케이션 수준에서 정하였습니다. 모델과 코드 사이의 `action`, `price`, `sku` JSON은 내부 결정 형식으로, 에이전트 사이의 A2A 형식과는 별개입니다. 따라서 A2A 호환만으로 모든 가게와 구매자의 거래 의미가 자동으로 일치하는 것은 아니며, 거래 데이터와 수락 규칙에 대한 추가 합의가 필요하다는 점을 확인하였습니다.

## 5. 도전 1과 2의 큰 컨텍스트 처리

### 5.1. 도전 1: 3,000개 상품 중 후보만 전달하기

직접 생성한 카탈로그에는 bookshelf, lamp, chair가 각각 1,000개씩, 총 3,000개 들어 있습니다. 각 상품에는 고유 SKU, 이름, 설명, 하한가와 재질·높이·용량·기능 등의 속성을 저장하였습니다. 구매자는 SKU 대신 자연어 설명으로 요구 사항을 전달합니다.

카탈로그 전체를 매번 프롬프트에 넣는 대신 `Catalog.search()`가 상품 이름과 설명을 검색하여 최대 16개 후보를 선택합니다. 검색에서는 소문자 토큰화, `wooden → wood`와 같은 동의어 정규화, 문서 빈도를 반영한 가중치와 토큰 겹침 점수를 사용합니다. 임베딩 검색을 사용한 것은 아닙니다. 코드는 후보를 줄이고, 모델이 후보의 속성과 구매자의 조건을 비교하여 최종 SKU와 가격을 선택하도록 하였습니다. 적합한 후보가 없으면 거절하도록 프롬프트에 지시하였습니다.

SKU가 선택된 다음에는 해당 상품 1개만 모델에 전달합니다. 선택한 SKU를 중간에 바꾸거나 후보에 없는 SKU를 내보내는 것은 코드에서 막습니다. 하한가도 해당 SKU의 카탈로그 값을 기준으로 검사합니다. 영수증에는 SKU와 상품 속성을 함께 기록하여 테스트가 실제로 적합한 상품을 샀는지 확인하도록 하였습니다.

최종 실행에서 모델 입력 로그로 확인한 후보 수와 입력 payload의 문자 수는 다음과 같습니다. `prompt_chars`는 JSON payload의 문자 수이며, 시스템 프롬프트를 포함한 전체 토큰 수나 실제 비용 측정치는 아닙니다.

| 구매 요구 유형 | 첫 후보 수 / payload 문자 수 | 선택 후 후보 수 / payload 문자 수 | 구매 SKU | 하한가 / 구매가 |
| :--- | :--- | :--- | :--- | :--- |
| 약 40권을 보관할 낮은 목재 책장 | 16 / 3,796 | 1 / 511 | B0035 | 128 / 128 |
| 밝기 조절이 가능한 금속 책상 램프 | 16 / 3,873 | 1 / 514 | L0406 | 50 / 50 |
| 높이 조절이 가능한 인체공학적 의자 | 16 / 4,510 | 1 / 577 | C0020 | 210 / 210 |

이 수치는 카탈로그 3,000개를 모두 보내지 않고 후보를 줄였음을 보여 줍니다. 다만 도전 1의 선택된 task에서는 대화 이력 자체는 계속 누적됩니다. 장기 대화 전체를 요약한 것은 아닙니다. 또한 자연어 조건의 해석은 모델에 의존하며, 모든 가능한 요구 문장을 코드가 검증하는 것은 아닙니다. 최종 테스트는 세 가지 요구 조건과 별도의 낮은 가격 제안에서 적합한 구매와 하한가 보호를 확인한 범위입니다.

최종 실행의 후보 검색과 상품 선택·협상 결정은 다음 로그에서 확인할 수 있습니다.

출처: `logs/challenge-1/20261016T085036_137208Z/seller.log` (2행)

```json
{"event": "retrieval", "taskId": "0e4e85c2-49dc-4e0b-a87f-f44bcf16f110", "query": "I need something wooden to keep about 40 books, no taller than 75 cm, to fit beside my desk.", "catalog_count": 3000, "candidate_count": 16, "candidate_skus": ["B0030", "B0035", "B0040", "B0045", "B0050", "B0055", "B0150", "B0155", "B0160", "B0165", "B0170", "B0175", "B0270", "B0275", "B0280", "B0285"]}
```

출처: `logs/challenge-1/20261016T085036_137208Z/seller.log` (5행)

```json
{"event": "model_decision", "taskId": "0e4e85c2-49dc-4e0b-a87f-f44bcf16f110", "action": "counter", "sku": "B0035", "price": 128, "reason": "Wood bookshelf 70cm meets height and capacity constraints"}
```

하한가 보호 검사에서는 상대의 낮은 제안을 받은 뒤에도 안전한 호가를 유지하였으며, 최종 결과는 3/3 통과였습니다. 키워드가 겹치지 않는 표현에서는 적합한 상품이 후보에 포함되지 않을 수 있으므로, 향후에는 검색 성능과 후보 누락에 대한 추가 평가가 필요합니다.

### 5.2. 도전 2: 카드 디렉터리, 예산 분배 및 협상별 상태

서로 다른 URL을 가진 자체 가게 20개를 구성하였습니다. 이 가게들은 같은 하네스 구현을 사용하지만, 각각 별도의 카드와 상품 카탈로그를 갖습니다. 디렉터리에는 가게 주소를 넣고, 구매자가 모든 카드를 읽어 skill의 상품 설명을 확인하도록 하였습니다. 전체 예산은 800이며, 장보기 목록은 lamp, chair, bookshelf 각 1개입니다.

**카드 선택 단계에서는 배치를 사용하였습니다.** 원본 카드는 로그로 보관하고, 모델에는 가게 이름과 skill 등 필요한 내용을 추려 5개씩 네 번 전달하였습니다. 모델은 각 배치에서 품목별 후보 가게를 추천합니다. 이후 후보 가게만 모아 최종 구매 가게와 품목별 예산 상한을 결정하도록 하였습니다. 카드 skill의 공개 가격 안내를 참고하였으며, 판매자의 비공개 하한가를 구매자에게 직접 전달하는 방식은 사용하지 않았습니다.

최종 예산 배분은 다음과 같았습니다.

출처: `logs/challenge-2/20261016T090015_037317Z/buyer.log` (31행)

```json
{"event": "budget_reserved", "allocations": [{"item_id": "desk_light", "shop_id": "shop_00", "cap": 130}, {"item_id": "office_seat", "shop_id": "shop_01", "cap": 234}, {"item_id": "book_storage", "shop_id": "shop_02", "cap": 338}], "total_caps": 702, "total_budget": 800}
```

품목별 상한 합계는 702로 총예산 800 이하였습니다. 코드는 모든 품목이 계획에 포함되었는지, 가게가 유효한지, 상한의 합계가 총예산을 넘지 않는지 검사합니다. 협상마다 해당 상한 이하의 가격만 전송하도록 하여, 여러 거래가 겹쳐 진행되어도 전체 예산을 침범하지 않도록 하였습니다.

**협상 단계에서는 전체 대화 대신 구조화된 최신 상태를 전달하였습니다.** `item`, `shop_id`, `cap`, `task_id`, `status`, `last_seller_text`, `last_quote`, `last_offer`, `turn`, `selected_sku`를 보관하여 다음 모델 판단에 사용하였습니다. 이전 메시지 전체를 구매자 모델 입력에 다시 붙이지는 않습니다. 원본 A2A 메시지는 로그로 남깁니다. 실제 상태의 예시는 다음과 같습니다.

출처: `logs/challenge-2/20261016T090015_037317Z/buyer.log` (39행)

```json
{"event": "negotiation_summary", "item_id": "book_storage", "summary": {"item": {"item_id": "book_storage", "item": "bookshelf", "description": "One bookshelf for my books"}, "shop_id": "shop_02", "cap": 338, "task_id": "c13124ed-c41e-4c95-9a70-1fe5c1b17aee", "status": "TASK_STATE_INPUT_REQUIRED", "last_seller_text": "bookshelf from Shop 02. Asking 260", "last_quote": 260.0, "last_offer": null, "turn": 1, "selected_sku": "S02-bookshelf"}}
```

이 방식은 대화 턴마다 전체 기록이 증가하는 문제를 줄입니다. 다만 `last_seller_text`의 길이를 고정한 것은 아니므로 모델 입력 크기의 절대 상한을 보장한다고 주장하지는 않습니다. 최종 실행에서 개별 협상 판단에 전달한 payload는 bookshelf 346자, chair 335자, lamp 323자로 기록되었습니다. 이 수치 역시 전체 토큰 수가 아닌 payload 문자 수입니다.

**중복 구매와 총액은 코드에서 보호하였습니다.** `asyncio.gather`로 선택된 세 가게와의 협상을 겹쳐 진행하고, 품목별 잠금과 공유 구매 장부의 잠금을 사용하였습니다. 각 품목에 하나의 구매 경로와 예산 상한을 배정하고, 영수증을 검증한 뒤 구매 완료로 기록합니다. 테스트에서는 구매자 장부뿐 아니라 판매자의 실제 판매 이벤트 수도 확인하여, 중복 거래가 단순히 장부에서 덮어써지는 경우를 놓치지 않도록 하였습니다.

구매 완료 로그는 다음과 같습니다.

출처: `logs/challenge-2/20261016T090015_037317Z/buyer.log` (54, 56, 58행)

```json
{"event": "purchased", "item_id": "book_storage", "price": 260.0, "total_spent": 260.0}
{"event": "purchased", "item_id": "office_seat", "price": 180.0, "total_spent": 440.0}
{"event": "purchased", "item_id": "desk_light", "price": 100.0, "total_spent": 540.0}
```

최종 결과는 다음과 같습니다.

| 항목 | 결과 |
| :--- | :--- |
| 읽은 카드 | 20개입니다. |
| 선택한 가게 | lamp는 shop_00, chair는 shop_01, bookshelf는 shop_02입니다. |
| 품목별 실제 구매가 | 각각 100, 180, 260입니다. |
| 총구매액 / 총예산 | 540 / 800입니다. |
| 구매자 구매 건수 / 판매자 판매 건수 | 3 / 3이며 중복 구매는 없었습니다. |
| 최대 열린 협상 수 | 3개입니다. |
| 자체 검사 | 전 품목 구매, 총예산 준수, 중복 없음, 판매자 하한가 준수, 20개 카드 조회, 협상 중첩 검사가 모두 통과하였습니다. |

근거는 `logs/challenge-2/20261016T090015_037317Z/report.json`, `buyer-report.json`, `buyer.log`, 판매자 로그입니다. 여러 HTTP 협상은 겹쳐 진행되지만, 모델 호출에는 세마포어를 두어 구매자 측과 판매자 측에서 각각 호출량을 제어합니다. 따라서 20개 가게와 동시에 모델 호출을 수행한 결과로 해석하지 않습니다.

이 실행에서는 첫 호가가 각 품목의 상한 안에 들어와 바로 구매하였습니다. 따라서 협상별 상태 전달과 동시 구매 관리는 확인하였지만, 장시간의 여러 턴 협상이나 모델 컨텍스트 창 초과 상황까지 실험한 것은 아닙니다. 선택한 가게에서 구매에 실패했을 때 다른 가게로 재탐색하거나 남은 예산을 재분배하는 기능도 추가 개선 사항입니다. 도전 3의 긴 메시지·프롬프트 주입 대응 평가, 공개 URL 배포 및 다른 하네스와의 교차 거래는 이번 제출 결과에 포함하지 않았습니다.
