# 과제 1단계: 협상 서버

`market_server.py`는 LAB의 `tools_server.py`와 별개인 협상 서버다.
현재 환경은 Python 3.14, mcp 2.2.0, uvicorn 0.54.0이다.

## 동작

- Streamable HTTP: `http://127.0.0.1:8001/mcp`.
  HTTP/1.1의 MCP 응답은 Uvicorn이 정상 청크 형식으로 전송한다.
  로컬 전송 중 Content-Length 헤더가 바뀌는 문제와 해결 기록은 [RUNNER.md](RUNNER.md)에 있다.
- 관리자 전용 `POST /admin/negotiations`가 UUID 협상 ID와 구매자/판매자 토큰을 발급한다.
  JSON 입력은 `item`, 선택적으로 `first_role` (`buyer` 기본값 또는 `seller`)이다.
  응답은 `negotiation_id`, `tokens.buyer`, `tokens.seller`다.
- 토큰은 난수 문자열이다. 서버 메모리의 매핑에 역할과 협상 ID가 저장된다.
  도구 인자로 역할을 받지 않는다. 관리자 토큰은 MCP에 사용할 수 없고,
  구매자/판매자 토큰은 관리자 경로에 사용할 수 없다.
- MCP 토큰 누락/무효: HTTP 401 + `WWW-Authenticate`.
- 다른 협상 ID, 차례 위반, 종료 후 행동, 제안 없이 수락/거절:
  HTTP 200의 MCP 도구 결과에서 `isError: true`와 구체적 사유를 반환한다.
  실패한 행동은 차례와 기록을 바꾸지 않는다.
- `get_negotiation`: 양쪽 모두 언제든 조회 가능. 자기 역할, 물건, 현재 차례,
  상태, 현재 제안, 거래 가격, 행동 기록을 반환한다.
- `propose`: 정수 가격만 허용하며 상대에게 차례를 넘긴다. 역제안은 기존 제안을 대체한다.
- `accept_proposal`: 상대의 현재 제안 가격으로 `deal` 종료.
- `reject_proposal`: 현재 제안을 폐기하고 상대에게 차례를 넘긴다.
- `refuse`: 자기 차례에 `no_deal` 종료. 종료 후 응답의 `turn`은 null이다.
- 상태 검사와 갱신은 같은 잠금 안에서 처리해 동시 요청에 의한 중복 행동을 막는다.

가격 한도와 주입 문장은 후속 단계에서 추가한다.
3단계의 host/runner, 8회 행동 제한과 실험 지표는 [RUNNER.md](RUNNER.md)에 설명한다.
현재는 단일 프로세스의 메모리 상태이므로 서버 재시작 시 협상과 토큰이 사라진다.

## PowerShell 실행

```powershell
Set-Location C:\Submissions\ai_agent_engineering\submissions\24522014\week-05
$env:MARKET_ADMIN_TOKEN = & C:\venvs\submissions\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(32))"
& C:\venvs\submissions\Scripts\python.exe -u .\market_server.py
```

관리자 토큰은 OpenRouter 키와 별개다. 서버 시작에 사용한 값을 runner에도 전달해야 한다.
토큰을 출력하거나 로그에 저장하지 않는다. 실행 예시는 현재 터미널의 환경 변수만 설정한다.
LAB HTTP 서버는 8000, 협상 서버는 8001을 사용한다.

서버를 띄우기 전 같은 PowerShell에서 협상 생성/조회 요청을 미리 준비하거나,
동일한 관리자 토큰을 가진 별도 runner에서 다음과 같이 생성한다:

```powershell
$episode = Invoke-RestMethod -Method Post `
  -Uri 'http://127.0.0.1:8001/admin/negotiations' `
  -Headers @{ Authorization = "Bearer $env:MARKET_ADMIN_TOKEN" } `
  -ContentType 'application/json' -Body '{"item":"used bicycle"}'

$headers = @{
  Authorization = "Bearer $($episode.tokens.buyer)"
  Accept = 'application/json, text/event-stream'
  'Mcp-Protocol-Version' = '2026-07-28'
  'Mcp-Method' = 'tools/call'
  'Mcp-Name' = 'get_negotiation'
}
$body = @{
  jsonrpc = '2.0'; id = 1; method = 'tools/call'
  params = @{
    name = 'get_negotiation'
    arguments = @{ negotiation_id = $episode.negotiation_id }
    _meta = @{
      'io.modelcontextprotocol/protocolVersion' = '2026-07-28'
      'io.modelcontextprotocol/clientCapabilities' = @{}
    }
  }
} | ConvertTo-Json -Depth 10
Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8001/mcp' `
  -Headers $headers -ContentType 'application/json' -Body $body
```

## 검증

```powershell
& C:\venvs\submissions\Scripts\python.exe -B .\test_market_server.py
```

테스트는 ASGI 전송으로 실제 HTTP 라우팅, 인증 미들웨어, MCP 도구 호출까지 실행한다.
포트에 접속하는 테스트나 모델 실행은 아니다. API 키가 필요 없다.
인증, 관리자 경계, 협상 격리, 차례 검사, 제안/역제안/수락/거절/거래 중단,
정수 검사, 동시 요청을 확인한다.

첫 시도 `logs/server-step1-tests-01.txt`에는 테스트 lifespan 관리 및 응답 파싱 실패와
예상 오류를 `ValueError`로 처리해 SDK가 상세 사유를 숨긴 문제가 남아 있다.
두 번째 시도는 7개 테스트가 통과했으나 PowerShell 5가 Python stderr 출력을
`NativeCommandError`로 표시했다. 최종 시도는 추가 동시 요청/격리 검사를 포함하고,
자식 프로세스의 stderr를 stdout으로 합쳐 실제 종료 코드를 보존한다.
이 기록은 과제의 24개 모델 실험 로그나 최종 `auth_checks.txt`를 대체하지 않는다.
