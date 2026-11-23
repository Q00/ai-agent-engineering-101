# STEP 2 — Python에서 하네스를 호출하고 JSON 응답 읽기

## 결과

`harness.py`를 만들었다. Python 표준 라이브러리만 사용하며 A2A 서버는
실행하지 않는다. 현재 계정에서 다음 두 호출의 성공을 실제로 확인했다.

| 실행 | 모델 | 결과 |
|---|---|---|
| `--mode claude --role seller` | Haiku | `ask`, 110, `transport smoke test` |
| `--mode codex --role buyer` | gpt-6-luna | `offer`, 110, `transport smoke test` |

110은 요청한 객체가 그대로 돌아오는지 확인하기 위한 고정 값이다.
이번 단계는 가격 전략, 하한가·예산 준수, A2A 거래를 검증하지 않는다.

## 직접 실행

서버를 켤 필요가 없으며 `a2a`나 `sh`도 사용하지 않는다.
PowerShell에서 과제 폴더로 이동한다. 이전 실습의 가상환경 Python을 직접
지정하므로 별도로 활성화할 필요가 없다.

```powershell
cd C:\Submissions\ai_agent_engineering\submissions\24522014\week-06
.\a2a-lab\.venv\Scripts\python.exe harness.py --mode claude --role seller
.\a2a-lab\.venv\Scripts\python.exe harness.py --mode codex --role buyer
```

직접 확인할 것:

1. 첫 명령은 `harness: claude`, 두 번째는 `harness: codex`인가?
2. 각각 `model: haiku`, `model: gpt-6-luna`로 표시되는가?
3. `response`가 action, price, reason을 가진 JSON인가?
4. 출력된 로그 폴더에서 request, stdout, stderr, result, metadata를 열어 본다.

Claude와 Codex에 로그인된 상태가 필요하고 호출마다 사용량이 소모된다.
현재 검증에서는 추가 로그인이 필요하지 않았다. 개인 환경에서 인증 오류가
나오면 해당 CLI에서 직접 로그인하고 재실행한다. 인증 파일이나 키는 제출하지 않는다.

## 세 가지 모드 선택

`claude` 모드는 role을 바꿔도 Claude, `codex` 모드는 role을 바꿔도 Codex다.
`mixed`는 각각 선택한다. 아래 예시는 아직 거래가 아니라 각 역할의 호출 확인이다.

```powershell
# Claude seller / Codex buyer
.\a2a-lab\.venv\Scripts\python.exe harness.py --mode mixed --role seller
.\a2a-lab\.venv\Scripts\python.exe harness.py --mode mixed --role buyer

# Codex seller / Claude buyer
.\a2a-lab\.venv\Scripts\python.exe harness.py --mode mixed --role seller --seller-harness codex --buyer-harness claude
.\a2a-lab\.venv\Scripts\python.exe harness.py --mode mixed --role buyer --seller-harness codex --buyer-harness claude
```

## 코드 읽기

- `select_harness()`: 모드와 역할로 실행할 하네스를 선택한다.
- `build_command()`: 셸 명령 문자열 대신 인자 목록을 만든다. Claude의 빈
  `--tools` 인자도 보존한다. Windows Codex는 설치된 native 실행 파일을 찾는다.
- `run_harness()`: 빈 작업 폴더에서 실행하고 UTF-8 표준 입력으로 요청한다.
  표준 출력과 표준 오류를 각각 파일에 바로 기록한다. 기본 제한은 90초이며,
  시간 초과 시 시작한 프로세스와 하위 프로세스를 종료하고 오류를 남긴다.
- `parse_response()`: JSON 객체, 필수 필드, action과 양의 정수/null 가격을
  검사한다. 가격으로 boolean, 실수, 문자열을 받아들이지 않는다.
  역할별 행동과 현재 제안·하한가·예산 검사는 이후 구현 단계에서 추가한다.

현재 `run_harness(provider, prompt, timeout)`는 한 번 호출한다. 실패를 다른
하네스로 대체하지 않는다. 실제 거래 프롬프트와 제약 검사, 검증 오류에 대한
최대 1회 재요청은 이후 판단 로직을 연결할 때 구현한다.

## 증거와 검증

실제 모델 호출의 원본 파일:

- `logs/harness/20261016T025711024501Z-339c9d9c-claude/`
- `logs/harness/20261016T025712420192Z-f77f4cd5-codex/`

각 폴더에는 `request.txt`, `stdout.txt`, `stderr.txt`, `result.json`,
`metadata.json`이 있다. 원본 출력은 편집하지 않았다. Codex의 stderr에는
`rmcp`의 `error decoding response body` 진단이 있었지만 종료 코드는 0이었고
최종 JSON은 정상 반환됐다. 이 외부 MCP 진단의 원인은 조사하지 않았다.
호출 성공은 JSON 반환에 대한 것이며 MCP 기능의 정상 작동을 뜻하지 않는다.

문법 검사와 로컬 테스트 5개가 통과했다. 네 가지 역할 조합 선택,
유효·무효 JSON, 비정상 종료 시 오류 보존, 시간 초과 시 부분 출력 보존을
확인했다. 오류·시간 초과 테스트는 모델 대신 로컬 Python 프로세스를 사용했다.
이 테스트 자료는 `logs/step2-tests/` 아래에 보존했다.

```powershell
.\a2a-lab\.venv\Scripts\python.exe -m unittest test_harness -v
```

테스트 콘솔 원본은 `logs/step2-harness-checks-20261016-115936.stderr.txt`다.
`agent.json`과 seller/buyer는 아직 없으므로 공식 trade test 및 제출 구조
검사는 실행하지 않았다. 다음은 STEP 3의 seller 첫 제안 구현이다.

Codex 실행 근거: [공식 비대화형 실행 안내](https://learn.chatgpt.com/docs/non-interactive-mode),
[프로젝트 지침 로딩 설정](https://learn.chatgpt.com/docs/config-file/config-reference).
