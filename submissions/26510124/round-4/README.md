# Round 4 분리 작업 공간

기존 `../week-08/`의 Round 3 코드와 별도로 준비한 v5 키트 작업 공간이다.
2026-11-21 현재 **개장 전 코드 준비**를 마쳤다. persona·goal, 기존 Claude CLI 연결 어댑터,
구매 조건 도구를 제공하는 미니앱, 시장 미접속 미리보기와 로컬 테스트가 있다.
Claude 로그인 갱신 후 실제 Opus 응답과 앱 페이지 도구 왕복까지 확인했다.
시장 등록·실제 도구 생성·거래·정산은 11월 24일 19:00 KST 개장 후 확인한다.

## Round 3 역할·정책 이식

`run_agent.py run`은 이제 단일 Opus 대신 다음 역할을 실행한다. 공개 Agent Card와 앱은 하나다.

| 기존 역할 | Round 4 실행 |
| --- | --- |
| 상점 거래 담당 | Claude CLI Haiku: 고객 응대·판매·상점 매입, `shop_*` 도구 |
| 운영 전략 담당 | Claude CLI Opus 5.5 low: 2시간 간격, 하루 최대 16회 호출, 제안만 기록 |
| 재고 분석 담당 | Python 계산: 재고·확정 판매·입고 시각으로 보충량과 현금 유보액 산출 |
| 별도 구매 담당 | Claude CLI Opus 5.5 low: 주문 탐색·협상·구매, `buyer_*` 도구 |

따라서 상점 쪽은 **세 역할, 두 LLM**이며 구매 LLM이 별도다. 모델의 판단 루프는 분리되어 있고,
공유 재고·지갑을 다루는 도구 실행과 생성은 순서대로 처리한다. 운영 담당 오류는 상점 루프를 종료하지 않는다.
운영 담당은 도구 생성·실행·지갑·앱 기능을 사용할 수 없으며, 메모에 `PROPOSAL ONLY`를 붙인다.
자동 정책 변경 기능은 없다. 일별 호출량과 분석 주기는 `.local/operations-*.json`에 남아 재시작해도 유지된다.

기본 앱 대화와 공개 A2A 메시지는 상점으로 전달한다. 앱에서 `/buyer 질문`, `/ops 질문`, `/shop 질문`으로
역할을 지정할 수 있다. 공개 메시지의 `role` 또는 `/ops` 문자열로 운영 담당을 호출할 수는 없다.
페이지 제공 도구는 상점만 사용한다. `preview_app.py --model`은 여전히 단일 Opus의 UI 연결 검사이며,
실제 역할 구성 검사 명령은 `.venv/bin/python probe_roles.py`다. 이 검사에서는 모든 시장 도구를 차단한다.

`strategy/SOURCE.json`은 이식 당시 커밋과 원본 해시를 기록한다. 가격 하한·협상 단계·구매 조건 함수의
본문은 `strategy/round3_math.py`에 그대로 보존했고 회귀 검사로 비교한다. 정책 원본 스냅샷과 활성
`strategy/policy.json`은 분리했다. 기존 등급별 마진, 현금 40% 유보, 회당 매입 8개, 총 재고 60개,
구매 예산 90%·정가 85% 상한 등의 기준을 이어 쓴다. Round 4 수수료를 확인한 뒤 판매 순수익 하한과
구매 총비용 상한을 계산하며, 도구 호출 뒤 다음 모델 판단 때 최신 관측으로 다시 계산한다.

Round 3 실행 코드를 통째로 복제한 것은 아니다. 기존 `restock.py`의 전체 플래너·실험 승인 명령·
서버 배포·옛 API 연결은 실행하지 않는다. 이식된 매입 정책은 생성 도구의 기준으로 제공하고,
재고 분석은 새 관측 계약에 맞춘 예측 계산이다. 고정 SKU·시작 가격과 옛 종료일은 비활성화했다.
Book·별점·Saved 목표는 제거했다. 라이브 시장과 연결할 주문·재고·결제 도구는 공식 과제 방식대로
실행 중 에이전트가 forge해야 한다. 이번 변경은 그 도구를 미리 작성하거나 실거래 성공을 보장하지 않는다.

생성된 조회 도구는 실제 응답을 `state/shop/evidence.json` 또는 `state/buyer/evidence.json`의 내부 계약으로
정규화한다. 필드 계약은 `role_runtime.py`의 `EVIDENCE_CONTRACT`에 있다. 이는 시장 API 스키마가 아니다.
Round 4·출처 URL·관측 시각을 확인하며 15분 지난 관측은 무효다. 비용·등급·수수료·주문 자료가 없으면
계산 불가로 표시하고 추측하지 않는다. 운영 지갑이나 과거 재고·매출 데이터는 가져오지 않았다.

## 앱 열기

저장소 루트에서 아래 명령을 실행한 뒤 `http://127.0.0.1:8814`를 연다.

```bash
cd submissions/26510124/round-4
.venv/bin/python preview_app.py
```

기본 미리보기는 고정 응답으로 페이지 도구의 전달 경로를 보여 준다. 화면에도 테스트 모드라고 표시한다.
예산·관심 상품·선택 기준을 저장하고 대화를 보내면 `app_shopping_brief`가 저장한 값을 돌려준다.
입력은 브라우저 탭의 sessionStorage에 보존되며, 초기화할 수 있다. 구매 조건 저장은 결제 승인이 아니다.
채팅·생성된 도구와 최근 행동 표시·외부 앱 허용/거절 UI를 포함하며 모바일 크기에 맞춰 배치가 바뀐다.
종료는 Ctrl-C다. 미리보기는 localhost에만 열리고, 시장·지갑·터널·forge 실행에는 연결하지 않는다.

## 기존 Claude CLI / Opus 연결

사용자가 선택한 기존 로그인 방식을 유지하기 위해 `claude_model.py`에서 공식 Model.complete 인터페이스를
구현했다. 역할에 따라 `haiku` 또는 `claude-opus-5-5`, effort low를 쓰며 CLI 자체의 도구·MCP·hooks·세션 보존은 끈다.
Haiku 별칭은 CLI가 반환한 실제 Haiku 모델 ID를 확인하며 Opus 등 다른 계열로의 대체를 거절한다.
모델은 JSON 행동을 반환하고 실제 실행은 키트가 맡는다. API 과금으로 바뀌지 않도록 API 관련 환경 변수는
CLI 자식 프로세스에서 제거한다. 기존 Round 3의 파일이나 모델 설정은 import·수정하지 않는다.

2026-11-21 첫 응답 검사에서 `OAuth session expired and could not be refreshed`가 반환됐다.
실패 원본은 `logs/model-probe-20261121T130224Z-33387df7.log`와
`logs/model/claude-20261121T130224Z-437185db.log`에 보존했다. 키를 파일에 넣거나 공유할 필요는 없다.
이후 사용자가 이 컴퓨터에서 로그인을 갱신했고, 같은 날 재검사가 통과했다.
`logs/model-probe-20261121T132324Z-453cc495.log`에 성공 결과가 있다.
다시 연결을 확인하려면 아래 명령을 사용한다. 로그인은 만료됐을 때만 갱신하면 된다.

```bash
claude auth login  # 로그인이 만료됐을 때만
.venv/bin/python run_agent.py probe
```

probe는 모델 응답 한 번만 요청하고 시장·지갑·도구는 실행하지 않는다.
통과 후에는 실제 Claude로 시장 없이 앱 대화를 확인할 수 있다.

```bash
.venv/bin/python preview_app.py --model
```

이 모드도 `reply`와 페이지의 `app_shopping_brief`만 허용한다. 운영 에이전트처럼 시장 도구를 생성하지 않는다.
미리보기 활동은 `.local/preview-*/logs/`에 분리되며 실제 운영 증거로 제출하지 않는다.

**이 구성은 `run_agent.py`로 시작해야 한다.** 공식 `agent_kit.py`를 직접 실행하면 CLI 어댑터가 적용되지 않는다.

## 지금 실행하기

저장소 루트에서:

```bash
cd submissions/26510124/round-4
.venv/bin/python run_local.py check
.venv/bin/python run_checks.py
```

전체 검사는 원본 해시와 의존성 확인 → Python 검사 → JavaScript 검사 → ReAct 데모 순서로 실행한다.
JavaScript 검사는 Node.js 18 이상이 필요하다. 이 컴퓨터에는 Node.js가 설치되어 있다.
콘솔 출력은 매번 새로운 `logs/checks-*.log`에 저장된다. 데모만 보려면:

```bash
.venv/bin/python run_local.py demo
```

새 컴퓨터에서는 Python 3.12 환경을 준비한 뒤 이 폴더 안에서 설치한다.
현재 잠금 파일은 macOS / Python 3.12.6에서 검증했다. 다른 플랫폼은 별도 검증이 필요하다.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-lock.txt
.venv/bin/python run_checks.py
```

## 구성과 출처

| 위치 | 역할 |
| --- | --- |
| `vendor/` | Q00/agent-market의 공식 v5 키트 원본과 MIT 라이선스 |
| `vendor/SOURCE.json` | 원본 URL과 파일별 Git blob SHA-1; 읽을 때 해시 검증 |
| `agent.json` | 덕용상점 persona, 크레딧 중심 goal, 역할별 Haiku/Opus 모델 설정 |
| `role_runtime.py` | 상점·구매·운영 루프, 권한·분석 주기·모델 호출량 관리 |
| `strategy/` | 기존 정책·함수 출처, 활성 Round 4 정책과 관측 계산 |
| `claude_model.py`, `run_agent.py` | 독립 CLI 어댑터, 모델 응답 검사와 개장 후 실행 진입점 |
| `app/` | 채팅·구매 조건·활동·승인 UI와 `miniapp.offer('shopping_brief', ...)` |
| `preview_app.py` | 공식 미니앱 SDK와 서버를 쓰는 시장 미접속 미리보기 |
| `meshkit.example.json` | 공식 placeholder 예시; 실제 MeshKit 연결은 아직 설정하지 않음 |
| `tools/` | 이후 실제 에이전트가 forge한 도구가 들어갈 위치; 현재 비어 있음 |
| `run_local.py` | 외부 접속 없는 `check`, `demo` 명령 |
| `offline_demo.py` | 고정된 모의 모델로 공식 Mind/Toolbox 연결 검증 |
| `tests/` | 권한·서명·CLI 응답·개장 제한·페이지 도구·미니앱 계약 검증 |
| `.local/` | Git에 넣지 않는 임시 테스트 작업 공간 |
| `logs/` | 수정하지 않는 콘솔 출력과 완료된 모의 실행 스냅샷 |

공식 키트 원본은 수정하지 않았다. `market_kit.py`는 v5가 지갑·가입·터널 기능을
가져오기 위해 필요한 의존성으로 포함했다. 기존 `week-08/vendor/market_kit_v4.py`를
import하거나 덮어쓰지 않는다. 미리보기와 운영 앱은 공식 `miniapp.py`를 사용한다.
기존 `agent.json`이 있어 starter를 덮어쓰지 않고, 공식 페이지의 필수 메타데이터와 SDK 계약에 맞춰
`app/`을 작성했다. `starter --app`을 같은 폴더에서 다시 실행할 필요가 없다.

출처:

- [Round 4 규칙과 v5 설명](https://github.com/Q00/agent-market/blob/main/market/ROUND4.md)
- [공식 참가자 키트](https://github.com/Q00/agent-market/tree/main/publish/scripts)
- [10주차 실습](https://wpti.dev/ai-agent-engineering-101/week-10.html)

## 기존 작업과의 분리

운영·미리보기 명령은 `week-08` 파일, 지갑, `.market`, 게시 대기열을 읽거나 쓰지 않는다.
출처 회귀 검사만 기존 Python 원본을 읽어 함수와 해시를 비교하며 수정하지 않는다.
SSH·systemd·기존 배포 스크립트를 호출하지 않는다. `run_local.py`는 포트를 열지 않고,
`preview_app.py`는 지정한 localhost 포트만 사용한다. 둘 다 시장에 가입하지 않는다.
기존 서버의 `week07-market.service`는 이번 작업에서 조작하지 않았다. 서버의 실시간 상태는 조회하지 않았다.

공식 키트의 `run`은 별도 동작이다. 실행하면 지갑을 만들고 시장에 가입하므로 로컬 검사에 포함하지 않았다.
공식 `tools` 명령도 지갑 파일과 signer 서버를 생성하므로 검증용 단축 명령으로 쓰지 않는다.
폴더만 나눠도 같은 시장 계정을 동시에 실행하면 등록·주문·잔액에 영향이 생길 수 있다.

키트의 Python 권한 검사는 일반적인 파일·네트워크·프로세스 접근을 제한한다.
문서에서도 악의적인 탈출 코드를 막는 보안 경계는 아니라고 명시한다.
실제 모델이 생성한 코드를 실행할 때는 기존 서버·자격 증명에 접근할 수 없는 별도 OS 계정이나
컨테이너 등으로 실행 환경도 분리해야 한다. 이번 데모는 내용을 확인한 고정 fixture만 실행한다.

## 검증한 것과 검증하지 않은 것

2026-11-21 역할 이식 후 Python 검사 32개, JavaScript 검사 4개, JS 구문 검사와 ReAct 데모가 통과했다.
최종 로컬 검사 원본은 `logs/checks-20261121T140651Z-0e882bd6.log`다.
역할 라우팅, 권한 분리, 실제 공식 forge 경로, 운영 호출량·주기, 매 판단 정책 갱신,
기존 함수 본문 비교, 수수료 경계와 누락·오래된 관측 거절도 포함한다.

세 역할의 실제 CLI 응답·답신 전달도 `probe_roles.py`로 확인했다.
원본은 `logs/role-model-probe-20261121T140758Z-6d6099ca/`에 있다. Haiku는
`claude-haiku-4-5-20251001`(표준 ID `claude-haiku-4-5`), 두 Opus 역할은
`claude-opus-5-5`로 확인됐다. 시장·도구 실행·지갑은 차단한 검사다.
초기 샌드박스 로그인 실패, Haiku 날짜 ID 검증 실패, 발신자 이름으로 잘못 답신한 시도도
각 `role-model-probe-*` 기록에 보존했다. 마지막 검사에서도 상점 답신 후 Haiku가 두 행동을
한 응답에 낸 2건은 검증기가 거절했다. 세 역할의 답신 성공은 확인했지만 모든 모델 결정의
정확성이나 실제 거래 성공을 뜻하지 않는다.

- 첫 모의 도구의 테스트 실패를 기록하고, 수정한 도구를 등록·호출했다.
- 새 버전 등록 후 이전 버전을 보존하고, 새 Toolbox/Mind에서 다시 불러와 재사용했다.
- 실패한 교체는 작동 중인 버전을 유지하며, 해시가 달라진 도구는 새 로드에서 제외됐다.
- 선언하지 않은 네트워크·파일 쓰기·프로세스 실행을 거절했다.
- 메모리에만 만든 테스트용 지갑으로 EIP-191 서명과 변조 검출을 확인했다.
- 송금 상한·음수·다른 역할·만료된 권한을 거절하고 테스트 모드가 RPC를 호출하지 않음을 확인했다.
- 모의 RPC가 돌려준 거래 해시를 회수할 수 있는지, 문서의 수수료 계산이 맞는지 확인했다.
- 공식 미니앱 서버에서 페이지 도구 등록 → 호출 대기 → 페이지 응답 → 채팅 결과의 왕복을 확인했다.
- 페이지 토큰 없는 도구 등록·응답을 거절하고 빈 채팅·만료된 호출을 처리했다.
- CLI 응답 검증은 다른 모델로의 대체·도구 사용·잘못된 행동 JSON을 거절했다.
- 구매 조건의 잘못된 값과 변조된 저장 값을 거절하고, 조건을 결제 승인으로 복원하지 않았다.
- 로그인 갱신 후 실제 `claude-opus-5-5` 응답을 확인했다. 공식 미니앱 HTTP 경로에서 실제 모델이
  `app_shopping_brief`를 요청하고, 전달받은 예산 32,000크레딧·관심 상품 우유를 한국어 답변에 반영했다.
  페이지 응답은 테스트 클라이언트가 제공했으므로 실제 브라우저 조작 검증은 아니다.
  원본은 `logs/real-model-app-check-20261121T132351Z-7d50abc5/`와 `logs/model/`에 있다.

연결 가능한 브라우저가 없어 실제 화면 조작·스크린샷·휴대폰 설치는 검증하지 못했다.
HTTP 라우트·페이지 메타데이터·JavaScript 입력 계약 검증이 시각적 QA를 대신하는 것은 아니다.

`logs/scripted-demo-*/RUN.json`에 명시한 대로 **데모는 고정 응답을 재생한 테스트**다.
리스트 길이를 세는 도구만 만들며 시장 기능·전략은 없다. 로그의 실패는 의도한 테스트 사례다.
이 로그를 실제 LLM이 시행착오를 거쳐 시장 도구를 만든 증거나 실제 매출 개선으로 제출하면 안 된다.
테스트 로그의 `0x111...` 거래 해시 역시 명시적인 모의 값이며 실제 송금이 아니다.

첫 로그인 만료 실패를 보존했고, 갱신 후 모델 응답·로컬 앱 대화 검증은 통과했다.
잔액·체인 송금·checkout·시장 `/api/settle`·A2A 실거래·효과 지표는 아직 검증하지 않았다.
현재 저장소에는 Round 4 전용 과제 검사 스크립트가 없다. 이 검사는 공식 채점 통과를 뜻하지 않는다.

## 개장 후 진행할 단계

기존 사용자 전략을 이식했으며, 시장 도구는 공식 과제 요구대로 에이전트가 생성한다.
공식 키트의 기본 동작은 `forge_tool`, `read_tool`, `reply`, `write_note`, `sleep`이다.

1. Claude 로그인 갱신과 `run_agent.py probe`는 통과했다. 운영할 컴퓨터가 바뀌거나 로그인이 만료되면 다시 확인한다.
2. 개장 후 분리된 실행 환경에서 시장 Agent Card를 읽고, 에이전트가 조회 도구 하나를 forge하게 한다.
   생성·검증·실행·재사용 기록을 실제 `logs/forge.jsonl`, `logs/react.jsonl`로 남긴다.
3. 주문·재고·협상·checkout·정산 도구로 단계적으로 확장한다.
   실제 네트워크 계약은 해당 시점의 entity card로 확인하고, 서명·송금은 `kit` 인터페이스로 한다.
4. Round 4 운영 신원과 기존 Round 3 종료/전환 방식을 정한 뒤 새 지갑·자금·체인 연결을 검증한다.
   두 송금의 성공과 checkout 연결을 확인하고, 정산 재시도에 기존 거래 해시를 재사용한다.
5. 주문 없이 먼저 실행되는 행동 하나를 선택한다. 상대 목표·추정 확률·실패 비용을 적고,
   기준 지표를 기록한 뒤 실제 운영 전후를 비교한다.

개장 후 실행 명령은 아래와 같다. claim code는 `MARKET_OWNER` 환경 변수로만 제공한다.
지금 실행하면 개장 시각 검사에서 중단된다. 실제 운영 등록·신원 전환과 배포는 이번 작업에서 하지 않았다.

```bash
.venv/bin/python run_agent.py run --name dyishappy --app-tunnel
```

시장 도구의 동작을 사람이 미리 채워 넣지 않는다. 에이전트가 생성한 `tools/`와 원본 실행 로그가
기말 증거가 된다. `meshkit.example.json`은 연결 예시이고, 실제 `meshkit.json`과 키는 Git에서 제외한다.

`requirements-lock.txt`는 검증한 의존성 버전을 보존한다. 원본 실행 로그의 공백과 실패 출력은
저장소 지침에 따라 정리하지 않는다. 현재 변경은 로컬 커밋이며 push·PR·배포는 하지 않았다.
