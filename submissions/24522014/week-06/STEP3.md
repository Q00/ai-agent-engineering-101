# STEP 3 — 하네스가 첫 가격을 정하는 seller

## 결과

`seller.py`와 `prompts/seller.txt`를 작성했다. 실제 모델 호출 결과:

| 품목 | 하한가 | Claude Code + Haiku | Codex + Luna |
|---|---:|---:|---:|
| lamp | 100 | 150 | 150 |
| chair | 180 | 240 | 250 |
| bookshelf | 260 | 340 | 350 |

표의 가격은 이번 실행의 모델 응답이다. 고정값이나 배수 공식을 코드에
넣지 않았다. 6개 요청 모두 별도의 taskId를 만들고
`TASK_STATE_INPUT_REQUIRED`로 끝났으며, 아직 영수증 artifact는 없다.
Agent Card의 이름·skill과 JSON-RPC 1.0 접속 주소도 확인했다.

## 직접 실습

PowerShell 두 터미널 모두 과제 폴더로 이동한다.
이전 LAB의 `seller_agent.py` 대신 이번에 만든 `seller.py`를 실행한다.

```powershell
cd C:\Submissions\ai_agent_engineering\submissions\24522014\week-06
```

터미널 1 — Claude seller:

```powershell
.\a2a-lab\.venv\Scripts\python.exe seller.py --mode claude --port 10099
```

터미널 2 — 카드 조회와 첫 요청:

```powershell
. .\a2a-lab\use-a2a.ps1
a2a card get http://127.0.0.1:10099
a2a --timeout 120s send -a http://127.0.0.1:10099 "I want to buy a lamp"
```

직접 확인할 것:

1. 카드 이름 `Harness Furniture Shop`, 인터페이스 `JSONRPC`인가?
2. 결과의 `Status`가 `input-required`인가?
3. 응답이 `lamp: asking ...`이고 마지막 숫자가 100 이상인가?
4. `logs/harness/`의 새 폴더에서 요청 프롬프트와 원본 JSON을 읽어 본다.
5. `logs/seller/`의 새 파일에서 taskId·품목·하한가·검증 결과를 비교한다.

Codex seller도 확인하려면 터미널 1에서 Ctrl+C로 Claude seller를 종료한 뒤:

```powershell
.\a2a-lab\.venv\Scripts\python.exe seller.py --mode codex --port 10099
```

터미널 2에서 같은 카드 조회와 메시지 전송 명령을 반복한다.
응답 가격은 모델과 실행에 따라 달라진다. chair, bookshelf를 요청할 때는
각각 180, 260 이상인지 확인한다. 요청마다 실제 모델 사용량이 소모된다.
10099 포트가 이미 사용 중이면 다른 빈 포트를 선택하고 양쪽 URL을 맞춘다.
다른 프로세스를 임의로 종료하지 않는다.

혼합 모드의 seller 선택도 가능하다. buyer는 이후 단계에서 구현한다.

```powershell
# 혼합 모드에서 Claude seller
.\a2a-lab\.venv\Scripts\python.exe seller.py --mode mixed --seller-harness claude --port 10099
# 혼합 모드에서 Codex seller: 앞 서버 종료 후 실행
.\a2a-lab\.venv\Scripts\python.exe seller.py --mode mixed --seller-harness codex --port 10099
```

## 처리 흐름과 직접 읽을 코드

1. `requested_item()`은 정확히 한 개의 지원 품목을 찾는다. 미지원·불명확한
   요청은 잘못된 인자로 처리하며 모델을 호출하지 않는다.
2. `Seller.execute()`가 task를 만들고 하네스를 호출한다. 동기 CLI 호출은
   `asyncio.to_thread()`로 실행해 서버의 비동기 이벤트 루프를 막지 않는다.
3. `opening_decision()`이 프롬프트에 품목·비공개 하한가·구매자 메시지를 넣는다.
   구매자 메시지는 지침이 아닌 데이터로 취급하도록 명시했다.
4. `validate_opening()`이 `ask`와 양의 정수 가격, 하한가 준수를 검사한다.
   형식이나 제약을 어기면 한 번만 정정을 요청하며, 두 호출에 합쳐 90초의
   제한을 둔다. 실행·인증 등 호출 실패는 재시도하지 않는다.
5. 유효한 응답은 taskId별 `self.open`에 품목·가격·이력과 함께 저장한다.
   Python이 가격을 보정하지 않으며, 정정 후에도 무효면 task를 `failed`로 끝낸다.
6. A2A 메시지는 `lamp: asking 150`처럼 Python이 구성한다. 모델의 reason은
   로그에만 보존해 마지막 숫자를 가격으로 읽는 테스트와 충돌하지 않게 한다.

이번 단계에서는 기존 task로의 흥정·수락·거절과 취소를 구현하지 않았다.
이어 보내기와 취소 요청은 지원하지 않는 작업 오류를 반환한다.
CLI의 Resume 안내가 표시되어도 다음 단계 전에는 가격을 이어 보내지 않는다.
상태 전이와 영수증 artifact는 STEP 4에서 구현한다.

## 검증과 증거

`verify_step3.py`는 다른 서버를 건드리지 않고 빈 임시 포트에서 자기 seller를
시작해 카드와 세 품목의 첫 제안을 확인한 뒤 종료한다. 직접 재실행하려면:

```powershell
.\a2a-lab\.venv\Scripts\python.exe verify_step3.py --mode claude
.\a2a-lab\.venv\Scripts\python.exe verify_step3.py --mode codex
```

각 실행은 품목별 모델 호출을 수행하며, 잘못된 응답에는 추가 정정 호출이
있을 수 있다. 이 검증은 전체 공식 trade test를 대신하지 않는다.

이번 실제 실행의 원본 HTTP 요청·응답, 카드, 서버 출력과 요약:

- `logs/step3/20261016T035951352628Z-claude/`
- `logs/step3/20261016T035953182264Z-codex/`

콘솔 출력은 `logs/step3-claude-check-*.stdout.txt`와
`logs/step3-codex-check-*.stdout.txt`에 보존했다. 개별 모델 원본 출력은
`logs/harness/`, taskId별 판단 검증은 `logs/seller/`에 있다.
이번 실제 호출에서는 정정 요청 없이 모두 첫 시도에 통과했다.

로컬 테스트 5개도 통과했다. 테스트에서는 실제 모델을 호출하지 않고
하한가 위반, 잘못된 action, 한 번의 정정, 반복 무효 응답, 프로세스 실패를
확인했다. 테스트용 실패 기록은 `logs/step3-tests/`에 따로 보존했다.

```powershell
.\a2a-lab\.venv\Scripts\python.exe -m unittest test_seller_opening -v
```

코드 문법 검사도 통과했다. buyer와 완성된 거래 로직이 아직 없으므로 공식
trade test와 전체 제출 구조 검사는 실행하지 않았다.
