# Week 06 REPORT: 하네스로 만든 가게와 buyer (25512092 추현빈)

## 1. 하네스, 모델, 실행 명령

- 하네스: Claude Code 2.1.304 (Windows 네이티브 설치), Claude Max 구독
- 모델: haiku
- 하네스 호출 명령 (프롬프트는 stdin으로 전달, 매번 빈 임시 폴더를 작업 폴더로 사용)

```
claude -p --model haiku --tools "" --setting-sources "" --no-session-persistence
```

- trade test 명령 (agent.json)

```
seller: python seller.py --port {port}
buyer:  python buyer.py --url {url} --item {item} --budget {budget}
```

- 도전 1: `python make_catalog.py` → `python catalog_seller.py` + `python challenge1_run.py`
- 도전 2: `python shop_cluster.py` + `python shop_buyer.py`

코드는 A2A 통신(카드, JSON-RPC, `A2A-Version: 1.0`, taskId 유지, task 상태 전환, 영수증 artifact)과 상태 보관만 맡고, 호가·제안·수락·거절·포기는 매 턴 하네스가 정한다.

## 2. 가게와 buyer가 판단하는 방법

### 가게 (seller.py)

- 첫 메시지: 코드가 품목(lamp, chair, bookshelf)을 찾고, 하네스가 첫 호가를 정한다.
- 이후 매 턴: 하네스에 품목, 하한가, 지금까지의 대화, buyer의 최신 제안, 현재 호가를 주고 JSON 하나로 답하게 한다.

```
{"action": "counter" | "accept" | "reject", "price": <new ask>, "say": "<one short sentence>"}
```

- 코드가 하네스의 문장 뒤에 `asking N.` 또는 `Counteroffer: N.`를 붙여서, 메시지의 마지막 숫자가 항상 호가가 되게 한다.
- 하한가는 프롬프트와 코드 양쪽에서 지킨다. 하네스가 하한가 아래로 부르거나 하한가 아래 제안을 수락하면, 코드가 이유를 붙여 한 번 더 묻는다(`Your previous answer was not allowed: asking 245 is below your reserve 260.`). 두 번째 답도 어기면 코드가 하한가로 역제안한다.

### buyer (buyer.py)

- 카드를 읽어 JSONRPC 주소를 고르고, 모든 요청에 `A2A-Version: 1.0`을 붙이고, 첫 응답의 task id로 흥정을 이어 간다.
- 하네스는 예산, 대화, 현재 호가, 내 마지막 제안을 보고 JSON으로 답한다.

```
{"action": "offer" | "accept" | "walk", "price": <offer>}
```

- accept는 가게의 현재 호가 숫자를 그대로 보내는 것으로 표현한다.
- 예산도 프롬프트와 코드 양쪽에서 지킨다. 제안이나 수락할 호가가 예산을 넘으면 한 번 되묻고, 그래도 넘으면 코드가 예산 금액으로 제안한다.
- 270초 또는 10턴이 지나면 CancelTask를 보내고 끝낸다(300초 제한 대비).

## 3. 실패한 것과 고친 것

### 3.1 v1: 하한가를 프롬프트에만 둔 가게 (19/21)

처음에는 하한가와 예산을 프롬프트에만 두고 trade test를 돌렸다. buyer 테스트는 모두 통과했지만 가게는 bookshelf에서 하한가 260을 두 번 뚫었다.

```
<- TASK_STATE_INPUT_REQUIRED | We're getting close, but this solid build is worth a little more, so I can come down only slightly to this price. Counteroffer: 245.
FAIL  seller_holds_reserve #1: TASK_STATE_INPUT_REQUIRED, asks [340, 320, 295, 275, 260, 245, 260, 260, 260]
FAIL  seller_holds_reserve #2: TASK_STATE_INPUT_REQUIRED, asks [340, 330, 300, 285, 270, 250, 250, 260, 260]
19/21 passed -> FAIL
```

두 실패 모두 kit buyer가 230을 반복해서 보내는 구간에서 나왔다. 하네스는 프롬프트에 "Never ask below reserve"가 있는데도, 상대가 버티면 한 번 더 양보하는 쪽으로 답했다. 같은 조건에서 buyer 쪽 하네스는 세 번 모두 예산 230을 넘지 않았다. 이 비대칭 때문에 한도는 프롬프트만으로는 보장되지 않는다고 판단했고, 2절의 코드 guard(되묻기 1회 후 고정)를 양쪽에 넣었다.

### 3.2 v2 첫 실행: buyer가 램프를 두 번 삼 (18/21)

guard를 넣은 첫 실행에서는 가게 테스트가 모두 통과했지만 buyer_deal이 세 번 다 깨졌다.

```
kit seller saw: offers [90, 110, 90, 110], deals [110, 110], canceled 0, continued 4, posts without A2A-Version 1.0: 0
FAIL  buyer_deal #1: deals [110, 110] (budget 120)
```

원인은 하네스가 아니라 파일 교체 실수였다. 새 buyer.py를 기존 파일의 커서 위치(`if __name__ == "__main__":` 바로 뒤)에 붙여넣어서, 파일 끝에 옛 파일의 `main()` 줄이 남았고 흥정 전체가 두 번 실행됐다.

```
buyer.py:202:if __name__ == "__main__":"""Week 06 buyer v1 — ...
buyer.py:430:if __name__ == "__main__":
```

가게 입장에서는 서로 다른 task 두 개가 정상적으로 들어온 것이라 A2A 안에서는 이중 구매를 막을 방법이 없었다. 파일을 통째로 교체하고 `__main__`이 하나인지 확인한 뒤 다시 돌려 21/21로 통과했다. 최종 실행에서 bookshelf 호가는 `[340, 320, 300, 280, 270, 265, 262, 261, 260]`처럼 하네스가 260에 붙어서 멈췄고, 코드가 하한가로 고정할 때 쓰는 고정 문장("This is the lowest I can go.")은 응답에 나오지 않았다.

### 3.3 Windows 환경

- `winget install a2aproject.a2acli`가 `0x8a15000f : 원본에 필요한 데이터가 없습니다`로 실패해서, GitHub Releases의 `a2a_0.3.0_windows_amd64.zip`을 받아 썼다.
- Claude Code를 설치한 직후 Python에서 하네스를 부르면 `FileNotFoundError: [WinError 2] 지정된 파일을 찾을 수 없습니다`가 났다. 설치 폴더 `C:\Users\LG\.local\bin`이 PATH에 없었기 때문이고, 사용자 PATH에 추가해서 해결했다.

### 3.4 도전 1: 키워드 검색만으로는 후보가 틀렸다

처음에는 buyer의 문장을 그대로 BM25에 넣어 후보 12개를 뽑았다. 하네스를 붙이기 전 오프라인 점검에서, 예시 요청 "something wooden to keep about 40 books, not taller than my desk"의 후보 12개가 모두 desk lamp였고 조건에 맞는 품목은 0개였다. 요청에 있는 "desk"가 "desk lamp"와 강하게 겹쳤기 때문이다. 높이와 책 수 같은 숫자 조건도 키워드로는 걸러지지 않았다. 그래서 하네스가 먼저 요청을 조건(카테고리, 재질, 색, 높이, 책 수)으로 바꾸고 코드가 그 조건으로 거른 뒤 BM25로 순위를 매기게 바꿨다(5절).

## 4. A2A가 정해 준 것과 따로 맞춰야 했던 것

A2A는 상대를 찾는 방법(`/.well-known/agent-card.json`), 요청 형식(JSON-RPC `SendMessage`, `A2A-Version` 헤더), 한 거래를 하나의 task로 묶는 방법(taskId), 거래가 어디까지 왔는지(`INPUT_REQUIRED`, `COMPLETED`, `REJECTED`, `CANCELED`), 결과물을 담는 곳(artifact의 data Part)을 정해 주었다. 덕분에 끝난 task에 다시 보내면 오류가 나는 것, 흥정 중인지 끝났는지는 코드가 따로 약속하지 않아도 양쪽이 같은 뜻으로 읽었다. 반면 메시지 안의 내용은 A2A가 정하지 않아서 가게와 buyer가 밖에서 맞춰야 했다. 가격은 "input-required 메시지의 마지막 숫자가 호가"라는 trade test의 약속에 기대야 했고, 그래서 가게 코드는 하네스의 문장 뒤에 호가를 항상 마지막에 붙였다. 수락에는 FIPA의 accept-proposal 같은 행위 이름이 없어서, buyer는 가게의 호가 숫자를 그대로 보내는 것으로 수락을 표현했고, 그것을 수락으로 읽을지는 가게가 정했다. 실제로 도전 1에서 buyer가 호가 255보다 높은 270을 보냈을 때 가게 하네스는 수락하지 않고 262로 역제안했다. 영수증의 `price` 필드 이름, 품목 이름(lamp, chair, bookshelf), 하한가와 예산 같은 위임 조건도 A2A 밖의 약속이었다.

## 5. 도전 1과 2

### 도전 1: 큰 카탈로그 가게 (catalog_seller.py)

카탈로그는 `make_catalog.py`(시드 6)로 만든 3,000개 품목이다. 품목마다 이름, 카테고리 15종, 재질 10종, 색, 높이·폭, 책장류는 책 수, 설명, 하한가가 있다. 하네스는 카탈로그 전체를 보지 않는다.

1. 하네스가 buyer의 문장을 조건으로 바꾼다. "not taller than my desk" 같은 표현을 cm로 바꾸는 것도 하네스가 한다.
2. 코드가 조건으로 3,000개를 거르고 BM25로 순위를 매겨 12개만 남긴다. 걸러진 결과가 없으면 색 → 재질 → 크기 순으로 조건을 풀고 그 사실을 로그에 남긴다.
3. 하네스는 후보 12개(이름, 설명, 하한가)만 보고 품목 하나와 첫 호가를 고른다. 이후 흥정과 하한가 guard는 seller.py와 같다.

설명형 요청 6개로 확인한 결과다(`logs/challenge-1/`).

```
picked F00120 Nordic Birk Air Bookshelf | Nordic bookshelf in solid oak, wooden, light brown finish. 63 cm tall, 144 cm wide. 1 shelf, holds about 57 books. | reserve 245
RESULT: fits=True reserve_ok=True asks=[300, 285, 265, 255, 262] deal=262.0
6/6 requests: right item and reserve held
```

### 도전 2: 여러 가게와 장보기 목록 (shop_buyer.py)

`shop_cluster.py`가 가게 20곳을 띄운다. 조명·의자·수납·테이블 전문점이 각 3곳(가격 배율 0.95, 1.1, 1.3), 일반점 4곳, 소규모 전문점 4곳이다. 일반점 중 Bargain King은 카드에 "Lowest prices in town!"이라고 쓰지만 배율은 1.4로 가장 높다. 카드의 문장은 가게가 직접 쓴 주장이라 buyer는 카드만 믿지 않고 견적으로 실제 호가를 받아 비교한다.

buyer는 장보기 목록 5개와 총예산 1000을 받아 이렇게 움직인다.

1. 카드 20개를 읽고, 하네스가 품목마다 후보 가게 두 곳을 고른다.
2. 후보 가게들에 견적을 요청한다(A2A task 10개, 동시).
3. 하네스가 품목마다 가게 하나와 상한(cap)을 정한다. cap의 합이 예산을 넘으면 코드가 비율로 줄인다.
4. 다섯 품목을 동시에 흥정한다. 매 턴 하네스는 다른 가게와의 대화 전체 대신, 품목당 한 줄짜리 상태판(상태, 가게, 호가, 내 마지막 제안, cap)과 지금 가게의 마지막 메시지만 받는다. 실패하면 예비 견적으로 넘어가고, 성공하면 나머지 견적 task는 CancelTask로 닫는다.
5. 예산 guard: 제안은 `예산 − 이미 쓴 돈 − 다른 진행 중 품목의 cap`을 넘을 수 없다. 먼저 싸게 끝난 품목의 남은 돈은 자동으로 다른 품목의 여유가 된다.

상태판 덕분에 가게와 대화가 늘어나도 한 턴의 프롬프트 크기가 거의 일정했다. 남은 품목의 한도가 앞선 거래 뒤에 늘어나는 것도 로그에 보인다.

```
harness(turn 3, prompt 1368 chars): '{"action": "offer", "price": 210}'
[3] -> Central Tables: offer 210 (ask 250, limit 482)
[3] -> Central Tables: accept 236 (ask 236, limit 489.0)
harness(turn 0, prompt 1230 chars): '{"action": "accept"}'
[0] -> Book Nook: accept 265 (ask 265, limit 492.0)
```

결과는 다섯 품목 모두 구매, 총 773으로 예산 안, 품목당 한 번씩이었다(131초, `logs/challenge-2/`).

```
total 773.0 of 1000
PASS  every item bought
PASS  total within budget
PASS  no item twice
```
