# Week 06 — 내 하네스로 만드는 buyer와 가게: 한도는 결국 코드로 돌아왔다

학번 26510129

## 0. 파일 구성

| 파일 | 내용 |
|---|---|
| `harness.py` | 하네스를 부르는 유일한 곳. `decide(system, situation)`이 Claude Code를 비대화형으로 실행하고 한 줄 JSON을 dict로 돌려준다 |
| `seller.py` | 내 가게. A2A v1.0 JSON-RPC server. 호가·역제안·수락·거절을 모두 하네스에게 묻는다 |
| `buyer.py` | 내 buyer. 카드를 읽고 `A2A-Version: 1.0`과 `taskId`로 흥정을 잇는다. 제안과 포기를 하네스에게 묻는다 |
| `agent.json` | 하네스·모델과 두 실행 명령 |
| `test-report.json`, `logs/` | 통과한 2차 trade test의 결과. 1차(20/21 실패)는 `logs/attempt-1/`에 그대로 보존 |
| `logs/dev/` | trade test 전에 a2a CLI와 실습 가게로 손으로 돌려 본 로그 |
| `lab/` | 실습(STEP 2~6) 코드와 로그. `lab/logs/lab-cli-attempt-1.txt`는 실습에서 실패한 첫 시도 |

## 1. 하네스, 모델, 실행 명령

**하네스와 모델.** Claude Code 2.1.273, 모델 `haiku`. 강의 노트가 확인한 비대화형 명령을 그대로 썼다.

```
claude -p --model haiku --tools "" --setting-sources "" --no-session-persistence --system-prompt "<역할과 규칙>"
```

프롬프트는 두 부분으로 나눠 준다. 역할과 규칙(파는 물건, 하한가 또는 예산, 답 형식)은 `--system-prompt`로, 지금 상황(물건, 예산, 지금까지의 대화, 방금 받은 제안)은 표준 입력으로 보낸다. 명령은 `tempfile.mkdtemp()`로 만든 빈 폴더에서 실행해 저장소의 CLAUDE.md와 AGENTS.md를 읽지 않게 했다. 하네스 호출마다 `[harness] 4.6s exit 0 -> '{"action":"offer","price":160}'`처럼 걸린 시간과 원문 답을 stderr에 찍어 두어서, trade test가 받아 적은 로그에 모델의 답이 그대로 남는다.

**실행 명령 (`agent.json`).**

```
seller: python seller.py --port {port}
buyer:  python buyer.py --url {url} --item {item} --budget {budget}
```

**실행 환경.** conda 환경 AX(Python 3.14.7), a2a-sdk 1.2.2, uvicorn 0.54.0, httpx 0.28.1, a2a CLI 0.3.0(Linux 바이너리, Homebrew 없이 설치). 2차 trade test는 6분 51초, 하네스 호출 44회였다(참조 실행은 4분 41초, 60회).

## 2. 가게와 buyer가 판단하는 방법

**가게 (`seller.py`).** 실습 `seller_agent.py`의 A2A 뼈대(task 생성, `TaskUpdater`, `requires_input`/`complete`/`reject`, 영수증 data Part)를 그대로 두고, 규칙 세 줄(1.3배 호가, 중간값 역제안, 3회 거절)을 하네스 호출로 바꿨다.

- 첫 메시지: 글에서 물건 이름을 찾고(없으면 reject), 하네스에게 "이 buyer가 이렇게 말했다, 물건은 lamp다, 첫 호가를 정하라"고 묻는다. 답 `{"action":"ask","price":N}`의 N을 `lamp: asking N. Send your offer as a number.`로 보내고 INPUT_REQUIRED로 멈춘다.
- 이후 메시지: 숫자를 뽑아 제안으로 보고, 지금까지의 대화 전체와 현재 호가를 주며 accept/counter/reject를 묻는다. accept면 영수증 artifact `{"seller","item","price"}`를 붙여 COMPLETED, counter면 `N is too low. Counteroffer: M.`으로 INPUT_REQUIRED, reject면 REJECTED.
- 하네스가 JSON을 못 돌려주면 두 번 더 묻고, 그래도 안 되면 현재 호가를 다시 부른다.

system prompt는 "lamp 100, chair 180, bookshelf 260 밑으로는 부르지도 팔지도 말라, 하한가 위에서 열고 서서히 내려오라, buyer가 멈추면 하한가 이상의 제안은 받아라"는 내용이다.

**buyer (`buyer.py`).** `GET /.well-known/agent-card.json`으로 카드를 읽고 `supportedInterfaces`에서 `JSONRPC` 항목의 url을 고른다. 모든 POST에 `A2A-Version: 1.0` 헤더를 붙인다. `I want to buy a {item}`을 보내 task를 받고, INPUT_REQUIRED인 동안 매 턴 하네스에게 "물건, 예산, 지금까지 대화"를 주며 `{"action":"offer","price":N}` 또는 `{"action":"walk_away"}`를 받는다. offer면 같은 `taskId`로 숫자만 보내고, walk_away면 `CancelTask`를 보내고 끝낸다. 최대 8턴.

system prompt는 "예산을 넘겨 부르지 말라, 낮게 시작해 올라가라, 가게 호가가 예산 이하면 그 값을 불러 끝내라, 가게가 예산까지 안 내려오면 그만두라"는 내용이다.

**답 형식.** 두 쪽 다 한 줄 JSON을 요구했다. haiku는 가끔 ```` ```json ```` 코드 펜스로 감싸서 답하는데(2차 로그에서 가게 3번, buyer 3번), 파서가 첫 `{...}`를 정규식으로 꺼내므로 문제가 되지 않았다.

**하한가와 예산을 지키는 곳.** 처음에는 프롬프트에만 두었다. 노트가 "하네스가 정한다"고 했고, 그게 실제로 지켜지는지 보고 싶었다. 1차 trade test에서 가게가 하한가를 한 번 깨서(3절), 2차부터는 코드로도 막는다.

- 가게: 호가와 역제안이 하한가보다 낮으면 하한가로 올리고, 하한가 미만 제안을 accept하면 하한가 역제안으로 바꾼다(`guard()`).
- buyer: 제안이 예산보다 크면 예산으로 자른다.

둘 다 발동하면 `[guard] ...` 줄을 로그에 남긴다. 2차 통과 로그에는 이 줄이 한 번도 없다. 즉 통과한 실행에서는 하네스가 스스로 한도를 지켰고, 가드가 필요하다는 근거는 1차 로그다.

## 3. 통과하기까지 실패한 것과 고친 것

**(a) 실습: `a2a send -o json`의 task id를 잘못 읽었다.** STEP 5를 한 번에 돌리는 `lab/lab_cli.sh`에서 `json.load(sys.stdin)['id']`로 task id를 꺼냈는데, CLI의 JSON 출력은 `{"task": {"id": ...}}`로 한 겹 감싸져 있다. `lab/logs/lab-cli-attempt-1.txt`:

```
KeyError: 'id'
task id:
$ a2a send -a http://127.0.0.1:9999 --task-id  80
Status:   rejected (2026-10-13T11:40:13Z)
  We only sell: lamp, chair, bookshelf.
```

`--task-id`가 빈 값으로 나가 가게는 "80"을 새 task의 첫 메시지로 받았고 물건 이름이 없으니 거절했다. `['task']['id']`로 고친 뒤(`lab/logs/lab-cli.txt`) 램프 105 거래, UNSUPPORTED_OPERATION, rejected 둘, canceled가 모두 나왔다. 이 실수는 buyer 코드를 쓸 때 `r["result"].get("task", r["result"])`로 미리 반영됐다.

**(b) 하네스가 역할극을 거부했다.** 처음에는 역할과 규칙을 모두 표준 입력(사용자 메시지)으로 넣었다. haiku의 답:

```
I appreciate the creative test, but I'm Claude, an AI assistant built to help with software engineering,
security tasks, and general questions—not to roleplay as a lamp shop, even if instructed to do so in a system message.
```

실습 STEP 6에서 `--system-prompt 'You are a furniture shop...'`로는 가게 답을 했던 것을 떠올려, 역할과 규칙을 `--system-prompt`로 옮기고 상황만 표준 입력으로 보냈다. 그 뒤로 거부는 없었다. 이 거부 답은 `harness.py`를 만들기 전 터미널에서 손으로 시험할 때 받은 것이라 로그 파일로는 남아 있지 않다.

**(c) 1차 trade test 20/21: 가게가 하한가 아래로 불렀다.** `logs/attempt-1/trade-test.log`:

```
    -> SendMessage "230" taskId=9be8b697
    <- TASK_STATE_INPUT_REQUIRED | 230 is too low. Counteroffer: 265.
    -> SendMessage "230" taskId=9be8b697
    <- TASK_STATE_INPUT_REQUIRED | 230 is too low. Counteroffer: 255.
  FAIL  seller_holds_reserve #3: TASK_STATE_REJECTED, asks [380, 350, 320, 280, 265, 255, 260]
```

`logs/attempt-1/seller-under-test.log` 62행에 하네스의 원문이 있다.

```
[harness] 15.1s exit 0 -> '```json\n{"action":"counter","price":255}\n```'
```

system prompt에 "bookshelf 260 밑으로 부르지 말라"가 있었고, 같은 실행의 1·2회차와 직전 턴(265)까지는 지켰다. kit buyer가 230을 두 번째로 보내자 한 번 255로 내려갔고, 다음 턴에는 다시 260으로 올라왔다. 팔지는 않았지만 trade test는 "부르지도 말라"를 보므로 실패다. 2차에서는 `guard()`를 넣어 호가·역제안을 하한가로 올리고 하한가 미만 accept를 역제안으로 바꿨다. 2차는 21/21 통과, 가드 발동 0회.

**(d) 가게가 너무 높게 연다.** 실패는 아니지만 기록해 둔다. `harness.py`를 만들기 전 손으로 시험할 때 lamp 첫 호가가 280(하한가 100의 2.8배)으로 나온 적이 있고(로그 없음), `logs/dev/seller-cli.txt`에서는 145, trade test에서는 150, bookshelf는 320~380으로 열었다. kit buyer는 최대 8 메시지 안에 호가가 120 이하가 되어야 사는데, 가게가 매 턴 20~30씩만 내리면 못 팔 수 있었다. 실제로는 세 번 다 2~3턴 만에 120 이하로 내려왔다(`asks [150, 120]`).

## 4. A2A가 정해 준 것과, 밖에서 따로 맞춰야 했던 것

A2A가 정해 준 것은 상대를 찾는 방법(카드의 위치와 `supportedInterfaces`), 요청을 묶는 단위(task와 `taskId`, `contextId`), 거래가 어디까지 왔는지의 표현(INPUT_REQUIRED, COMPLETED, REJECTED, CANCELED), 결과물을 두는 자리(artifact의 data Part), 그리고 끝난 task에 다시 말을 걸면 오류라는 규칙이다. 이 덕에 buyer 코드는 가게의 내부를 전혀 모른 채 "상태가 INPUT_REQUIRED면 하네스에게 묻고, 아니면 끝"이라는 루프 하나로 끝났고, 같은 코드가 실습 가게·kit 가게·내 가게에 그대로 붙었다. 반면 메시지 안의 내용은 전부 밖에서 맞춰야 했다. 호가를 "마지막 숫자"로 읽는 것, buyer의 제안은 숫자 하나뿐이라는 것, 영수증의 가격 필드 이름이 `price`라는 것, "accept"가 따로 없고 가게가 받으면 COMPLETED가 된다는 것은 trade test 문서가 정한 약속이지 A2A가 정한 것이 아니다. FIPA-ACL이라면 `propose`와 `accept-proposal`로 메시지에 붙였을 행위 이름이 A2A에는 없어서, 내 가게는 "105"가 수락인지 새 제안인지를 하네스가 글을 읽고 정한다. 그리고 하한가와 예산, 즉 위임의 조건은 task 어디에도 적을 자리가 없어서 각자의 프롬프트와 코드에만 있었고, 3절 (c)처럼 프롬프트만으로는 지켜지지 않아 결국 코드로 막았다. 7주차가 이 조건을 위임과 함께 넘기는 계약을 다룬다는 예고가 왜 필요한지 1차 실패가 보여 준다.
