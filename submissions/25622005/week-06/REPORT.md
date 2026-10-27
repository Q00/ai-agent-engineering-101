# Week 06 — 내 하네스로 만드는 buyer와 가게

학번 25622005 · Codex CLI를 하네스로 두고, 코드는 A2A만 말하는 가게(seller.py)와 buyer(buyer.py)를 만들었다. trade test 21/21 PASS.

## 1. 하네스, 모델, 명령

- 하네스: Codex CLI 0.162.1 (`brew install --cask codex`), ChatGPT 로그인.
- 모델: `gpt-6.1-sol` (`~/.codex/config.toml`의 기본값. 명령에서 따로 지정하지 않음).
- 호출 명령 (harness.py):

```
codex exec --skip-git-repo-check --ephemeral -s read-only --color never -C <빈 임시 폴더> -o <답 파일> -
```

프롬프트는 stdin으로 넣고, 마지막 답만 `-o` 파일로 받는다. `-C`를 빈 임시 폴더로 주어 저장소의 AGENTS.md 같은 지시 파일을 읽지 않게 했고, `--ephemeral`로 세션을 남기지 않는다. 한 호출은 평균 5.8초, 최대 8.3초였다(trade test 중 39회).

- agent.json: 가게 `python seller.py --port {port}`, buyer `python buyer.py --url {url} --item {item} --budget {budget}`.

## 2. 가게와 buyer가 판단하는 방법

### 프롬프트와 답 형식

프롬프트는 `prompts/seller.md`, `prompts/buyer.md`에 있고 코드는 `[[item]]`, `[[reserve]]`, `[[budget]]`, `[[history]]`, `[[offer]]`/`[[ask]]` 자리만 채운다. 프롬프트에는 역할, 한도(하한가 또는 예산), 지금까지의 대화 전체, 상대의 최근 값, 그리고 답 형식이 들어간다. 답은 JSON 한 줄로 받는다.

- 가게: `{"action": "ask"|"counter"|"accept"|"reject", "price": <int>}`
- buyer: `{"action": "offer"|"accept"|"walk", "price": <int>}`

코드는 `price` 숫자만 가져다 쓰고 상대에게 보낼 문장은 코드가 조립한다. 예를 들어 가게는 항상 `"<offer> is too low. Counteroffer: <price>."`로 답한다. trade test가 호가를 "input-required 메시지의 마지막 숫자"로 읽기 때문에, 하네스가 자유롭게 쓴 문장에 다른 숫자가 끝에 오는 일을 코드가 막은 것이다.

### 하한가와 예산을 지키는 곳: 프롬프트 + 코드

프롬프트에 한도를 적고, 코드에서도 한 번 더 막는다. 판단 테스트가 3회씩 돌고 한 번만 틀려도 전체가 실패하기 때문이다.

- 가게(seller.py `decide`): 하네스가 하한가 아래로 `ask`/`counter`하면 하한가로 올리고, 하한가 미만 제안을 `accept`하면 하한가로 역제안으로 바꾼다. 답을 못 쓰면(JSON 없음, 타임아웃) 하한가의 1.3배로 부른다.
- buyer(buyer.py `decide`): 예산 초과 `offer`는 예산으로 깎고, 호가가 예산보다 높은데 `accept`하면 예산을 제안한다. 답을 못 쓰면 `walk`로 CancelTask를 보낸다.

실제 trade test에서는 가드가 한 번도 발동하지 않았다. 로그에 `guard:` 줄이 없다. Codex가 프롬프트의 한도를 모두 지켰고, 역제안도 하한가 바로 위에서 멈췄다.

```
[seller] harness said action='ask' price=338 (offer None, reserve 260)
[seller] harness said action='counter' price=312 (offer 140, reserve 260)
[seller] harness said action='counter' price=286 (offer 170, reserve 260)
[seller] harness said action='reject' price=0 (offer 200, reserve 260)
```

buyer도 예산 230에서 호가 260이 내려오지 않자 169, 200, 230까지 올린 뒤 스스로 그만뒀다.

```
[buyer] harness said action='offer' price=230 (ask 260, budget 230)
[buyer] <- TASK_STATE_INPUT_REQUIRED | 230 is too low. Counteroffer: 260.
[buyer] harness said action='walk' price=0 (ask 260, budget 230)
[buyer] done: TASK_STATE_CANCELED after 21s
```

## 3. 실패한 것과 고친 것

trade test 자체는 첫 실행에서 21/21 통과했다(logs/trade-test.log). 그 전까지의 실패는 실습과 오프라인 검증에서 나왔다.

**실습 STEP 5: 빈 변수로 보낸 task id.** `T=<task id>`를 꺾쇠까지 그대로 적어 변수가 비었고, `--task-id 80`처럼 숫자가 id 자리로 들어가 메시지가 없어졌다. lab/logs/lab-cli.txt에 그대로 남아 있다.

```
"code": "A2ACLI_ERR_USAGE",
"message": "provide a message as text, or via --text-part, --file-part, --data-part, or --request-payload",
exit code 2
```

노트가 예고한 exit code 1(UNSUPPORTED_OPERATION)이 아니라 2가 나와서 원인을 찾았다. 로그에 남은 실제 id로 변수를 다시 잡고 이어서 보내니 completed와 영수증이 왔다. 이 경험 때문에 buyer.py는 id를 사람이 복사하지 않고 응답의 `task.id`를 그대로 다음 요청의 `taskId`에 넣는다.

**STEP 6: 빈 로그.** `a2a send ... | tee` 로 받은 Claude Shop 로그가 0바이트였다. stderr가 tee에 잡히지 않아서였고, `2>&1`을 붙여 다시 보내 completed와 답 artifact를 받았다. 그래서 buyer.py와 seller.py의 모든 로그는 trade test가 stdout과 stderr를 한 파일로 모으는 것을 전제로 `[buyer]`, `[seller]`, `[harness]` 접두어를 붙여 찍는다.

**과제 2번 확인: 포트 충돌.** 가게를 두 번 띄워 `[Errno 48] address already in use`가 났다. 첫 실행이 다른 창에서 이미 성공해 있었다. `lsof`로 찾아 끄고 다시 띄웠다.

**오프라인 검증에서 잡은 가드 동작.** Codex를 부르기 전에 harness.ask를 일부러 틀린 답을 내는 가짜 함수로 바꿔 가게와 buyer를 HTTP로 붙여 봤다. bookshelf 호가 200, 230 수락, 예산 초과 제안 280을 넣었더니 코드가 각각 260으로 올리고, 260 역제안으로 바꾸고, 230으로 깎았다. CancelTask 뒤 서버의 task가 TASK_STATE_CANCELED가 되는 것도 여기서 확인했다. 실제 trade test에서는 이 경로가 쓰이지 않았지만, 다른 모델이나 다른 프롬프트로 바꿀 때 안전망이 된다.

## 4. A2A가 정해 준 것과 밖에서 맞춘 것

A2A가 정해 준 것은 "어디로 어떻게 보내는가"다. 카드의 위치와 안에 적힌 JSON-RPC 주소, SendMessage와 CancelTask라는 메서드, taskId로 대화를 이어 가는 방식, input-required / completed / rejected / canceled라는 상태, 영수증을 artifact의 data Part로 붙이는 자리, 끝난 task에 다시 보내면 오류가 온다는 규칙까지다. 덕분에 buyer는 Codex 가게든 실습의 규칙 가게든 Kit Shop이든 코드를 바꾸지 않고 거래했다. 반면 "무엇을 말하는가"는 A2A 밖에서 따로 맞춰야 했다. 가격이 메시지의 마지막 숫자라는 약속, 수락이 "호가와 같은 숫자를 보내는 것"이라는 약속, 영수증 data Part에 `price`라는 키가 있다는 약속, 하네스와 코드 사이의 `{"action": ..., "price": ...}` 형식은 전부 A2A 규격에 없고 이번 과제의 trade test와 내 프롬프트가 정한 것이다. 같은 A2A 서버라도 가게가 "Counteroffer: 120 for 2 units" 처럼 답했다면 buyer는 2를 호가로 읽었을 것이다. 프로토콜은 봉투를 정하고, 봉투 안의 말은 양쪽이 따로 합의해야 한다.

## 5. 원격으로 띄운 가게 (평가자 확인용)

trade test와 별개로, 같은 가게를 Docker 컨테이너로 띄우고 Cloudflare Quick Tunnel로 공개했다. 채점 기준은 여전히 agent.json으로 다시 돌리는 trade test이고, 이 절은 설치 없이 가게와 바로 거래해 볼 수 있게 덧붙인 것이다.

- 주소: https://conf-comfortable-prefix-intensity.trycloudflare.com/
- 카드: https://conf-comfortable-prefix-intensity.trycloudflare.com/.well-known/agent-card.json (`supportedInterfaces[0].url`이 위 주소)
- 띄운 방법: Dockerfile, compose.yaml. 컨테이너 안에서도 하네스는 같은 `codex exec`이고, Codex 로그인은 이미지에 넣지 않고 실행할 때 `/codex-home`으로 연결한다. 카드에 공개 주소를 쓰도록 seller.py에 `--public-url`, 컨테이너에서 받도록 `--host`를 더했다(기본값은 trade test 때와 같다).

거래해 보는 방법(둘 중 하나):

```bash
# 이 저장소의 buyer로
python buyer.py --url https://conf-comfortable-prefix-intensity.trycloudflare.com/ --item lamp --budget 120

# a2a CLI로 직접
a2a send -a https://conf-comfortable-prefix-intensity.trycloudflare.com/ "I want to buy a lamp"
a2a send -a https://conf-comfortable-prefix-intensity.trycloudflare.com/ --task-id <위 응답의 task id> "110"
```

공개 직후 내 buyer로 붙여 본 결과다(lamp, 예산 120).

```
[buyer] card: 'Codex Shop' binding=JSONRPC v1.0 url=https://conf-comfortable-prefix-intensity.trycloudflare.com/
[buyer] <- TASK_STATE_INPUT_REQUIRED | lamp: asking 130. Send your offer as a number.
[buyer] <- TASK_STATE_INPUT_REQUIRED | 65 is too low. Counteroffer: 120.
[buyer] <- TASK_STATE_COMPLETED | Deal: lamp at 120.
[buyer]    artifact receipt: [{'item': 'lamp', 'price': 120.0, 'seller': 'Codex Shop'}]
[buyer] done: TASK_STATE_COMPLETED after 26s
```

주의: Quick Tunnel 주소는 터널을 다시 띄우면 바뀌고, 가게는 내 Codex 구독으로 판단하므로 상시 운영이 아니다. 주소가 응답하지 않으면 agent.json의 명령으로 trade test를 돌려 확인하면 된다.
