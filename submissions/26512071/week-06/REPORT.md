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
- 거래 테스트는 a2a-sdk 1.2.2가 설치된 venv를 켜고 돌렸습니다.

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
