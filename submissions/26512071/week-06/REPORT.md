# Week 06 REPORT: 하니스가 사고파는 A2A 판매자와 구매자

## 1. 하니스, 모델, 명령어

- 하니스: Claude Code (`claude` CLI), 모델: **sonnet**
- 판매자와 구매자 모두 같은 명령을 씁니다. 프롬프트는 표준입력으로 넘기고, 프로젝트 지침을 읽지 않도록 빈 임시 폴더(`tempfile.mkdtemp`)에서 실행합니다.

```bash
claude -p --model sonnet --tools "" --setting-sources "" --no-session-persistence
```

- `agent.json`
  - 판매자: `python seller.py --port {port}`
  - 구매자: `python buyer.py --url {url} --item {item} --budget {budget}`
- 거래 테스트는 a2a-sdk가 설치된 venv를 켜고 돌렸습니다. 아래 1차·2차는 1.2.2, 챌린지 작업 중 venv를 다시 만든 뒤의 실행(현재 `test-report.json`)은 1.2.3입니다.

| 실행 | 결과 | 시간 | 하니스 호출 |
|---|---|---|---|
| 1차 (커밋 `4296748`) | 21/21 PASS | 5분 45초 | 57회 |
| 2차 (현재 `test-report.json`) | 21/21 PASS | 4분 15초 | 58회 |

## 2. 각 에이전트가 결정하는 방식

**판매자 (`seller.py`)**: 강의 `seller_agent.py`에서 시작했습니다. A2A 쪽(Agent Card, JSON-RPC 경로, task 상태 전이)은 그대로 두고, 가격 규칙(`reserve * 1.3`, 반값 역제안, 3회 후 거절)만 하니스 호출로 바꿨습니다.

- 프롬프트: 물건, reserve, 지금까지의 흥정 기록(`Buyer offers 80.` / `You ask 125.`)을 넣습니다. "reserve 아래로 부르거나 수락하지 말 것, 높을수록 좋지만 거래가 없는 것보다는 거래가 낫다"고 적었습니다.
- 답 형식: JSON 하나. `{"action": "ask" | "accept" | "reject", "price": <정수>, "message": "<한 문장>"}`. 파싱에 실패하면 한 번 다시 묻고, 두 번 다 실패하면 직전 호가를 유지합니다.
- 호가는 메시지 맨 끝에 `Asking price: N`으로 붙입니다. 테스트가 마지막 숫자를 호가로 읽기 때문입니다.
- **reserve는 프롬프트와 코드 양쪽에서 지킵니다.** reserve보다 낮은 `ask`는 reserve로 올리고, reserve보다 낮은 제안에 대한 `accept`는 호가로 바꿉니다. 영수증 가격은 모델이 쓴 숫자가 아니라 구매자의 제안 숫자입니다. 10라운드가 넘으면 코드가 거절합니다.

**구매자 (`buyer.py`)**: A2A 쪽은 코드가 맡습니다. `{url}/.well-known/agent-card.json`에서 카드를 읽고, `supportedInterfaces`에서 JSONRPC 주소를 찾습니다. 모든 요청에 `A2A-Version: 1.0`을 붙이고, 첫 응답의 `taskId`로 협상을 이어 갑니다.

- 프롬프트: 물건, 예산, 흥정 기록을 넣고 "예산을 넘겨 제안하지 말 것, 가게가 예산까지 내려오지 않으면 떠날 것, 가게가 수락하면 그 가격에 거래가 닫히니 낼 생각이 있는 값만 부를 것"이라고 적었습니다.
- 답 형식: JSON 하나. `{"action": "offer" | "walk", "price": <정수>, "reason": "<한 문장>"}`. `walk`면 `CancelTask`를 보내고 끝냅니다. 두 번 다 파싱에 실패하면 떠납니다. 떠나는 쪽은 예산을 깰 일이 없기 때문입니다.
- **예산도 프롬프트와 코드 양쪽에서 지킵니다.** 예산을 넘는 제안은 예산으로 낮춥니다. 제안은 최대 8번이고, 넘으면 떠납니다(300초 제한 대비).

## 3. 통과하기까지 실패한 것과 바꾼 것

거래 테스트 자체는 두 번 모두 처음부터 21/21로 통과했습니다. 다만 로그를 보면, **통과가 프롬프트만으로 보장된 것은 아니었습니다.** 두 번 모두 sonnet이 bookshelf에서 reserve(260)보다 낮은 255를 부르려 했습니다.

1차 실행 (`seller_holds_reserve #3`, `logs/seller-under-test.log`, 커밋 `4296748`):

```
[harness] {"action": "ask", "price": 255, "message": "I can't go lower than 262."}
[guard] ask 255 below reserve 260 -> 260
```

메시지로는 262 아래로 못 내린다고 하면서 `price`에는 255를 넣었습니다. 코드 쪽 장치가 없었다면 테스트 구매자가 255를 호가로 읽어 `seller_holds_reserve`가 실패했을 것입니다. 장치가 260으로 올렸고, 메시지 끝 숫자도 260이라 통과했습니다. reserve를 프롬프트로만 지키는 대신 코드로도 막기로 한 결정이 실제로 필요했다는 근거입니다.

2차 실행 (`seller_holds_reserve #1`, 현재 `logs/seller-under-test.log`):

```
[harness] {"action": "ask", "price": 255, "message": "I can come down to 255"}

Wait, that's below my reserve of 260, so I must correct it:

{"action": "ask", "price": 262, "message": "I can meet you close to the middle at 262 and we have a deal."}
[seller] unparsable answer, attempt 1
```

이번에는 모델이 한 답 안에서 스스로 고쳤습니다. 그런데 JSON이 두 개 들어가는 바람에 `re.search(r"\{.*\}", raw, re.S)`가 첫 `{`부터 마지막 `}`까지를 한 덩어리로 잡아 파싱에 실패했습니다. 재시도에서 262를 받아 넘어갔습니다. 재시도를 넣어 두지 않았다면 직전 호가를 유지하는 쪽으로 빠졌을 것입니다. 이 정규식은 아직 고치지 않았습니다. 고친다면 답에서 마지막 JSON 객체를 고르는 쪽이 맞다고 봅니다. 모델이 정정한 쪽이 뒤에 오기 때문입니다.

거래 테스트 전, 실습 단계에서 막힌 것도 적어 둡니다.

- a2a CLI에서 `--task-id <task id>`를 그대로 입력했습니다. zsh가 `<`를 리다이렉션으로 읽어서 실행이 안 됐습니다.
- `-a` 없이 이어 보내기를 했더니 `Error: either '--agent-card <ref>' or '--endpoint <url> --transport <t>' must be provided`가 나왔습니다. CLI 출력의 `Resume:` 줄에는 `-a`가 빠져 있어서, 그대로 복사하면 안 됩니다.
- 낮은 값을 세 번 보낸 task에 95를 보냈더니 `Task ... is in terminal state: TASK_STATE_REJECTED`가 나왔습니다. 끝난 task는 되살릴 수 없고 새 task를 열어야 한다는 걸 여기서 알았습니다. 과제의 `seller_terminal` 테스트가 요구하는 동작이기도 합니다.
- 구매자 프롬프트의 첫 초안에는 "가게의 최저가를 넘는 제안은 그 값에 거래가 닫힌다"는 문장이 있었습니다. 가게 규칙을 미리 알려 주는 셈이라, 실행 전에 "가게가 수락하면 그 가격에 닫히니 낼 값만 부를 것"으로 바꿨습니다.

## 4. A2A가 해결해 준 것, 바깥에서 합의해야 했던 것

A2A 덕분에 판매자와 구매자는 상대 안에서 무엇이 돌아가는지 몰라도 됐습니다. 교수님 kit 판매자는 규칙 기반이고 제 구매자는 sonnet인데, 카드 주소, `SendMessage`, `taskId`로 이어 가기, `INPUT_REQUIRED`/`COMPLETED`/`REJECTED` 상태, 끝난 task에 대한 오류, `CancelTask`, 영수증 artifact의 data Part까지 같은 방식으로 주고받았습니다. 대화가 어디까지 왔고 끝났는지를 프로토콜이 정해 주니, 코드에서 따로 정할 필요가 없었습니다. 하지만 정작 흥정의 내용은 A2A 바깥의 약속에 기대고 있었습니다. 가격은 텍스트 Part 안의 숫자일 뿐이고, 어느 숫자가 호가인지는 "메시지의 마지막 숫자"라는 약속으로 정했습니다. 그래서 판매자는 모델 문장에 숫자가 섞여도 호가를 맨 끝에 다시 붙여야 했습니다. 구매자의 제안도 "메시지의 첫 숫자"로 읽히고, 수락은 구매자가 호가와 같은 숫자를 다시 보내는 것으로 표현됩니다. 처음 메시지에서 물건 이름을 찾는 방식, 통화와 단위, 영수증의 `price`가 숫자 필드라는 점도 모두 양쪽이 미리 알고 있어야 했습니다. A2A는 대화의 형식을 맞춰 주지만, 흥정이라는 대화의 문법은 여전히 사람이 정하고 프롬프트와 정규식으로 맞춰야 했습니다.

## 5. 챌린지 1, 2: 카탈로그를 하니스에 보여 주는 방법, 협상 상태를 들고 다니는 방법

### 챌린지 1: 판매자와 3000개짜리 카탈로그

카탈로그는 `gen_catalog.py`로 만들었습니다(시드 고정, 3000개, 커밋 `2eb0595`). 가구 18종에 재질, 스타일, 색, 크기를 조합하고, 설명에 높이·폭(cm), 들어가는 책 권수, 앉는 인원 같은 숫자를 넣었습니다. 거래 테스트의 `lamp`/`chair`/`bookshelf`는 reserve 100/180/260으로 맨 앞에 고정했습니다.

**카탈로그 전체를 프롬프트에 넣지 않습니다.** 하니스는 첫 메시지에서 두 번 불립니다.

1. 요청을 검색 조건(spec)으로 바꿉니다: `{"kinds": [...], "wooden", "max_height_cm", "min_height_cm", "max_width_cm", "books", "seats"}`. 가게가 파는 종류 목록만 프롬프트에 들어갑니다.
2. 코드(`search.py`)가 설명에서 정규식으로 읽어 둔 숫자로 spec에 맞는 상품만 남기고, BM25로 순위를 매겨 상위 20개를 하니스에 보여 줍니다. 하니스는 그중 하나의 id를 고릅니다. 코드는 그 id가 20개 후보 안에 있는지 확인하고, reserve는 하니스가 아니라 카탈로그에서 가져옵니다.

요청이 `I want to buy a lamp`처럼 "상품 이름을 사겠다"는 문장 하나뿐이면 검색 없이 바로 그 상품으로 갑니다. 이 구조는 처음부터 이랬던 것이 아니라 두 번 실패하고 나서 정해졌습니다.

- **BM25만 썼을 때:** README 예시 `something wooden to keep about 40 books, not taller than my desk`의 상위 20개가 전부 탁상 램프였습니다(커밋 `d177d10`). 방 크기를 설명하려고 쓴 "desk"가 "books"보다 무거웠습니다. 종류마다 후보를 5개까지만 받게 하자 책장과 수납장이 후보에 들어왔습니다(`aaee23d`).
- **숫자를 하니스에만 맡겼을 때:** 첫 `challenge1.py` 실행은 4/6이었습니다(`logs/challenge-1/run-1020-1142.txt`). 책 요청에서 sonnet의 판단은 맞았습니다.

  ```
  [harness] {"id": null, "why": "No bookcase holds about 40 books and stays under desk height (about 72-76 cm): the 45-book one is 88 cm tall and the 60 cm one holds only 26."}
  ```

  그런데 카탈로그에는 조건에 맞는 나무 수납가구가 58개 있었고, 그중 하나도 후보 20개에 들지 못했습니다. BM25는 숫자를 읽지 못하기 때문에, 숫자 비교를 하니스에 맡겨도 정답이 애초에 후보에 없었습니다. 이래서 1단계(spec 추출)를 넣었습니다(`9da78a7`).
- **이름 직행이 너무 넓었을 때:** `a chair I can sit in all day while coding`에 "chair"가 들어 있다는 이유만으로 식탁 의자 `chair`가 팔렸습니다. 요청 전체가 "buy a <이름>"일 때만 직행하도록 좁혔습니다(`0493b51`).

고친 뒤 실행은 6/6이었습니다(`logs/challenge-1/run-1020-1149.txt`). "책상보다 낮게"를 하니스가 숫자로 바꾸고(`logs/challenge-1/seller.log`), 코드가 그 숫자로 거른 결과가 팔린 상품입니다(run 로그).

```
[spec] {"kinds": ["bookcase", "bookshelf", "cube organizer"], "wooden": true, "max_height_cm": 75, ..., "books": 40, ...}
  sold item-1297 (cube organizer, reserve 97) at 97.0; asks [139, 124]
  item: A modern cube organizer in white solid wood, ... 50 cm tall, 100 cm wide. Holds about 40 books.
```

정리하면, 하니스는 **말을 숫자로 바꾸는 일**과 **좁혀진 후보 중 하나를 고르는 일**을 하고, 코드는 **숫자 비교와 검색, 그리고 하니스가 고른 것이 정말 후보 안에 있는지 확인하는 일**을 합니다. 거래 테스트는 이 변경 뒤에도 21/21이었습니다(`6f01369`).

### 챌린지 2: 상점 20곳, 쇼핑 목록, 총예산 하나

`seller.py --shop N`으로 상점 20곳을 띄웁니다(`shops.py`). 상점마다 4종류를 팔고 reserve에 0.9~1.4배를 곱합니다. 파는 종류는 Agent Card 스킬의 `tags`에 들어갑니다. 구매자는 `shopper.py`입니다.

**계획.** 하니스를 한 번 불러, 카드 20장을 `S3: Sonnet Shop 3 | floor lamp, office chair, shoe cabinet, side table`처럼 한 줄씩 줄인 표를 보여 주고, 물건마다 후보 상점을 최대 3곳과 보낼 요청 문장을 고르게 합니다. 카드 원문 JSON은 넣지 않습니다.

**상태는 코드의 장부에 있습니다.** 물건마다 한 행(상점, 마지막 호가, 내 제안, 상태, 이미 시도한 상점)을 둡니다. 협상의 한 수를 정할 때 하니스가 보는 것은 다음 세 가지뿐입니다.

- 장부 전체를 한 줄씩 쓴 표
- 지금 정하는 협상의 최근 메시지 4개
- 이번 제안의 상한

다른 상점과의 대화 원문은 보지 않습니다. 그래서 프롬프트는 대화가 길어져도 커지지 않고, 물건 수에 비례해서만 커집니다. 장부의 한 행은 이런 모양입니다.

```
2. an office chair to work at a desk all day | BOUGHT at 356.0 from S15; tried S3 rejected, S9 rejected
```

**중복 구매와 예산은 코드가 막습니다.** 모든 물건을 동시에 협상하지만, 한 물건은 한 번에 한 상점과만 협상하고 사면 바로 멈춥니다. 상점은 내가 걸어 둔 제안을 언제든 받을 수 있으므로, 제안 상한은 `총예산 - 산 금액 - 다른 진행 중 협상에 걸어 둔 내 제안`입니다. 하니스가 더 높게 부르면 코드가 상한으로 깎습니다. 상한은 하니스가 생각하는 동안 다른 협상이 움직였을 수 있으므로 답을 받은 뒤 다시 계산합니다.

실행 결과(`logs/challenge-2/run-1020-1159.txt`, 하니스 호출 15회):

```
1. a wooden bookcase for about 40 books | BOUGHT at 318.0 from S6
2. an office chair to work at a desk all day | BOUGHT at 356.0 from S15; tried S3 rejected, S9 rejected
3. a floor lamp to read by | BOUGHT at 137.0 from S3
4. a cabinet for shoes by the front door | BOUGHT at 155.0 from S3
PASS  every item bought
PASS  total 966.0 within budget 1100
PASS  no item bought twice
```

**여기서 보인 문제.** 의자는 S3, S9에서 거절당했습니다. 두 상점 모두 사무용 의자를 파는데도 그랬습니다. 계획 단계의 하니스가 요청을 `with good lumbar support, adjustable height and armrests, and a breathable or well-padded seat`처럼 부풀려 썼고, 판매자의 하니스는 카탈로그 설명에 그런 말이 없다는 이유로 거절했습니다(커밋 `68b0118`의 `logs/challenge-2/shop-9.log`, 상점 로그는 실행마다 덮어씁니다).

```
[harness] {"id": null, "why": "None of the listings mention lumbar support, adjustable armrests, height adjustment, or a padded/breathable seat, ..."}
```

양쪽 모두 같은 sonnet인데, 한쪽은 친절하게 부풀리고 한쪽은 엄격하게 읽었습니다. 이 실행은 세 번째 상점 S15가 받아 줘서 통과했습니다. 그런데 예산을 880으로 조여 다시 돌리자 S15까지 세 곳 모두 거절해 **실패**했습니다(`logs/challenge-2/run-1020-1202-budget880.txt`, 커밋 `28b653e`).

```
2. an office chair to work at a desk all day | failed; tried S3 rejected, S9 rejected, S15 rejected
FAIL  every item bought
```

양쪽 프롬프트를 모두 고쳤습니다.

- 구매자 계획 프롬프트(`7170899`): 요청에는 쇼핑 목록에 적힌 종류와 크기, 수량, 재질만 쓰고 기능을 덧붙이지 말 것.
- 판매자 고르기 프롬프트(`8d92277`): 설명은 짧아서 기능 대부분이 빠져 있으니, 언급되지 않은 기능은 거절할 이유가 아니다. 종류가 맞으면 가장 가까운 것을 고르고, 종류가 아예 다를 때만 none.

한쪽만 고치면 다른 구매자의 부풀린 요청이나 내 구매자의 부풀린 요청 중 하나가 남기 때문에 둘 다 고쳤습니다. 고친 뒤 요청은 `office chair for working at a desk all day`처럼 짧아졌고, 예산 880에서 통과했습니다(`logs/challenge-2/run-1020-1205-budget880.txt`). 고친 판매자로 챌린지 1은 6/6, 거래 테스트는 21/21이었습니다.

```
1. a wooden bookcase for about 40 books | BOUGHT at 356.0 from S6
2. an office chair to work at a desk all day | BOUGHT at 230.0 from S3
3. a floor lamp to read by | BOUGHT at 173.0 from S2
4. a cabinet for shoes by the front door | BOUGHT at 98.0 from S3
PASS  total 857.0 within budget 880
```

이 문제는 4절의 이야기와 이어집니다. A2A는 요청을 텍스트로 실어 나를 뿐이라, "요청에 무엇을 적고 그것을 얼마나 엄격하게 읽을지"는 양쪽 프롬프트가 따로 정하고 있었습니다.

**예산 예약은 프롬프트 쪽에서만 작동을 확인했습니다.** 예산 880은 산 물건들의 reserve 합(829)보다 51 많을 뿐이라, 하니스가 상한을 보고 판단한 흔적이 남았습니다.

```
[item 4] harness: {"action": "offer", "price": 97, "reason": "... which stays within the 144 cap."}
[item 2] harness: {"action": "offer", "price": 230, "reason": "... well within my 274 cap."}
```

하지만 하니스가 매번 상한 안에서 불러서, 코드가 상한을 넘는 제안을 깎은 일은 세 번의 실행에서 한 번도 없었습니다(`[guard]` 0회). 코드 쪽 방어선이 실제로 동작하는지는 아직 보지 못했습니다.
