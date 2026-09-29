# Week 05 — 협상장 MCP 서버

상태: 단계 3까지 서버 인증·상태 조회·행동 도구 4개·토큰 가격 한도 검사 구현, 32개 검사 통과. host·runner는 미구현이고 주입·모델 실행·실험은 아직 수행하지 않았다. Codex CLI 로그인은 미완료다.

과제 기준: `weeks/week-05/README.md`. 진행 순서와 완료 기준은 `TASKS.md`, 구성과 데이터 경계는 `ARCHITECTURE.md`에 기록한다.

## 구성

```text
week-05/
├── README.md            현재 상태와 실행 안내
├── TASKS.md             단계별 체크리스트와 완료 기준
├── ARCHITECTURE.md      책임 분리와 실험 설계 초안
├── MEASUREMENT.md       확정한 집계·종료 기준
├── ENVIRONMENT.md       실제 환경과 설치 과정
├── experiment.json      Codex host와 소형 모델 실험 설정
├── prompts.json         조건 간 공유하는 역할별 프롬프트
├── verify_design.py     단계 1의 입력·구문·SDK import 검사
├── requirements.txt     MCP SDK 버전
├── requirements-lock.txt 실제 설치한 Windows 의존성
├── market_server.py     인증·관리 HTTP 경로 및 MCP 도구 5개
├── market_state.py      서버 상태·토큰 grant·차례 검사
├── STAGE2.md            인증 구조와 서버 실행 안내
├── STAGE3.md            행동 상태 전이·가격 한도·검증 설명
├── host.py              MCP host 구현 위치 (현재 미구현)
├── runner.py            실험 runner 구현 위치 (현재 미구현)
├── results.csv          정확한 제출 헤더만 작성; 실행 결과 없음
├── REPORT.md            보고서 틀; 실제 결과로 채울 예정
├── scenarios.json       시나리오 4개; 본 실행 전에 커밋 완료
├── SCENARIOS.md         시나리오별 목적과 가격 구간
├── auth_checks.txt      실제 서버 검사 후 네 줄 작성
├── logs/                실제 run의 원본 콘솔 로그
└── tests/               stage2 인증 14개 + stage3 행동 18개 검사
```

`auth_checks.txt`와 실제 실험 로그는 아직 작성하지 않았다. 현재 제출물은 최종 CI 통과 상태가 아니다. `checks/`의 설계·서버 검사 출력은 에이전트 실험 로그로 세지 않는다. 서버 실행은 `STAGE2.md`, 전체 검증 방법은 `STAGE3.md`에 있다.

## 환경과 실행

- Python은 Anaconda 3.12.4를 발견해 제출 디렉터리에 전용 `.venv`를 생성했다. PATH의 `python`과 py launcher 미등록은 Python 설치 부재를 뜻하지 않았다.
- SDK는 mcp 2.2.0, host는 Codex CLI 0.156.1의 `codex exec`, 모델은 gpt-6-luna, reasoning effort는 low다. temperature는 CLI 옵션으로 노출되지 않아 미지정이다.
- `codex login status`는 `Not logged in`을 반환했다. 모델 실행 전에 ChatGPT 로그인과 선택 모델 접근 확인이 필요하다. 전역 모델 설정은 바꾸지 않았다.
- API 키와 토큰은 커밋하지 않는다. `.env` 파일도 제출하지 않는다.
- 작업 단위별로 커밋하고, 실패한 시도와 원본 실행 로그를 보존한다.

설계 검사 (저장소 루트에서, 모델 호출 없음):

```powershell
& ./submissions/26510121/week-05/.venv/Scripts/python.exe -X utf8 submissions/26510121/week-05/verify_design.py
```

환경 재현은 `ENVIRONMENT.md`를 참고한다. 최종 구조 검사는 `.venv`가 없는 깨끗한 checkout/export에서 실행한다. 과제 검사기는 `.venv` 내부의 외부 패키지 파일까지 재귀적으로 읽기 때문이다:

```powershell
python scripts/check_week05.py submissions/26510121/week-05
```

CI는 구조 검사다. 실제 인증, 차례, 한도 검사는 별도로 실행해야 한다.
