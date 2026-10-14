# 단계 2 — 인증과 협상 상태

## 구현한 것

- `market_state.py`: 협상 상태, 읽기용 스냅샷, 협상에 묶인 buyer/seller 토큰, 행동 전 차례 검사.
- `market_server.py`: Streamable HTTP `/mcp`, 관리자용 `/admin/negotiations`, 공개 상태 확인 `/health`, MCP 도구 `get_negotiation`.
- `tests/test_stage2.py`: 실제 loopback HTTP 서버를 띄워 인증과 협상 접근을 검사한다. 모델/외부 모델 API 요청은 없다.

## 원리

negotiation_id는 서버가 만든 UUID이며 레코드를 찾는 식별자다. ID만 알아서는 조회 권한이 생기지 않는다. 별도로 secrets.token_urlsafe(32)로 만든 무작위 bearer token을 서버 내부의 PartyGrant에 연결한다. 이 연결에는 negotiation_id와 역할이 있고 server 계열 조건에서만 자신의 가격 한도도 있다.

SDK가 HTTP 요청의 토큰을 검증한 뒤 도구에 인증 문맥을 전달한다. get_negotiation은 이 문맥의 토큰으로 서버 내부 grant를 찾는다. 역할을 입력하는 도구 인자는 없으며, 다른 협상 ID를 지정하면 HTTP 200의 MCP 결과 안에서 isError=true로 거부한다. 토큰이 없거나 등록되지 않았다면 도구 실행 전 HTTP 401과 WWW-Authenticate 헤더로 거부한다. 토큰에는 이 `/mcp`를 가리키는 resource와 negotiate scope도 지정해 SDK에서 검사한다.

관리자 토큰은 당사자 토큰과 별개다. SDK의 custom_route는 기본적으로 공개 경로이므로 관리자 경로에서 직접 bearer 인증을 검사한다. 관리자 토큰으로 MCP를 호출하거나 당사자 토큰으로 협상을 생성하면 401이다. 협상 생성 응답은 runner만 받으며, 토큰 값이 있으므로 로그에 출력하지 않고 Cache-Control: no-store로 반환한다.

상태 조회에는 negotiation_id, item, role, turn, status, moves만 포함한다. reserve, budget, condition, 토큰과 grant는 포함하지 않는다. 처음에는 buyer 차례, open 상태, 빈 moves다. 반환된 moves를 외부에서 수정해도 서버 기록은 변경되지 않도록 깊은 복사본을 반환한다.

## 실행

제출 디렉터리에서 실행한다. 아래 관리자 토큰 생성 명령은 출력값을 환경변수에만 대입한다. 비밀을 파일에 저장하거나 콘솔에 다시 출력하지 않는다.

```powershell
$env:MARKET_ADMIN_TOKEN = & ./.venv/Scripts/python.exe -c "import secrets; print(secrets.token_urlsafe(32))"
& ./.venv/Scripts/python.exe market_server.py --port 8001
```

관리 요청의 형식은 POST /admin/negotiations, Authorization: Bearer 관리자 토큰, JSON 본문 `{ "scenario": { "id": "example", "item": "item", "reserve": 60, "budget": 55 }, "condition": "server_inject" }`다. 응답은 negotiation_id와 tokens.buyer / tokens.seller를 반환한다. 관리자 값은 환경변수로 전달할 예정이며 runner 연결은 단계 4에서 구현한다.

자동 검증은 저장소 루트에서 실행한다. 토큰을 테스트 자체에서 생성하고 메모리에만 보관한다.

```powershell
& ./submissions/26510121/week-05/.venv/Scripts/python.exe -X utf8 -m unittest discover -s submissions/26510121/week-05/tests -p test_stage2.py -v
```

## 검증과 남은 경계

14개 검사 통과: 관리자 인증, 정수/필수 입력 검사, 토큰 없는 요청의 401과 challenge, 미등록 토큰, 관리자/당사자 권한 분리, 역할 및 비공개 정보 제외, 도구 schema, 다른 협상 및 가짜 ID 거부, buyer 시작 차례와 seller 차례 거부, 스냅샷 보호, 조건별 토큰 한도, 재시작 시 토큰 무효화, MCP 헤더와 본문 불일치 거부.

첫 HTTP 테스트는 헤더 이름을 일반 dict로 변환해 Cache-Control / WWW-Authenticate의 대소문자를 잘못 처리했다. 실패 출력은 checks/stage2-20260929-203648-3772967.txt, 수정 후 성공 출력은 checks/stage2-20260929-203710-0196067.txt에서 확인한다. 원본 출력은 편집하지 않는다.

require_turn은 행동 전 호출할 공통 검사 함수다. 단계 3의 행동 도구는 같은 잠금 안에서 검사와 상태 변경을 수행해야 한다. 이번 테스트는 이 검사 함수가 seller 차례를 거부하고 상태를 보존하는 것까지 확인했다. 실제 행동의 tool error 및 원자적인 상태 변경은 단계 3에서 검증한다.

협상 상태는 메모리에만 있고 서버 재시작 시 사라진다. runner는 서버 재시작 후 새 협상/토큰을 생성해야 한다. 가격 한도는 현재 grant에 담기만 하며 행동에 대한 강제 검사는 단계 3이다. 주입은 단계 5이고, auth_checks.txt의 최종 네 검사는 행동 도구 구현 후 실제 실행 결과로 작성한다. 본 단계의 테스트 결과를 에이전트 실험 결과로 세지 않는다.
