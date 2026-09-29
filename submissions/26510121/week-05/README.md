# Week 05 — 협상장 MCP 서버

상태: 구현 및 gpt-6-luna 본 실험 24개 완료. 실제 HTTP 검사 38개 통과, 본 실험 도구 호출 310개를 CLI·서버·CSV와 독립 대조했다. 두 조건 모두 correct 10/12, 거래 위반·한도 밖 시도·거부 0건이다. REPORT.md에 전체 결과와 주입 조회 이후의 실제 행동 근거를 기록했다.

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
├── host.py              격리된 codex exec와 환경변수 bearer 인증
├── runner.py            실제 실험·재개·CSV·원본 로그 저장
├── audit.py             서버 MCP 호출과 결과·위반 시도 관측
├── verify_evidence.py   CLI·서버·CSV 독립 대조
├── build_report.py      검증된 실제 결과로 보고서 생성
├── final_check.py       .venv 없는 제출 export에서 공식 검사
├── publish_submission.py 기존 Git 인증으로 push 및 upstream PR
├── results.csv          실제 본 실행 결과
├── REPORT.md            설정·결과·비교·로그 근거 해석
├── scenarios.json       시나리오 4개; 본 실행 전에 커밋 완료
├── SCENARIOS.md         시나리오별 목적과 가격 구간
├── auth_checks.txt      실제 서버 검사 후 네 줄 작성
├── logs/                실제 run의 원본 콘솔 로그
└── tests/               인증·행동·주입·audit 총 38개 HTTP 검사
```

`auth_checks.txt`는 실제 HTTP 서버의 네 검사 결과다. `checks/`의 설계·서버·예비 출력은 본 실험 로그로 세지 않는다. 실패한 예비 설정도 pilot-results.csv와 원본 checks/에 보존했다. 서버는 `STAGE2.md`, 상태 전이는 `STAGE3.md`, host·측정·실패 과정은 `STAGE4-5.md`에 설명한다.

## 환경과 실행

- Python은 Anaconda 3.12.4를 발견해 제출 디렉터리에 전용 `.venv`를 생성했다. PATH의 `python`과 py launcher 미등록은 Python 설치 부재를 뜻하지 않았다.
- SDK는 mcp 2.2.0, host는 Codex CLI 0.156.1의 `codex exec`, 모델은 gpt-6-luna, reasoning effort는 low다. temperature는 CLI 옵션으로 노출되지 않아 미지정이다.
- sandbox의 초기 `Not logged in`은 인증 저장소 접근 제한에 따른 값이었다. 정상 사용자 환경에서 기존 ChatGPT 로그인과 실제 소형 모델 접근을 확인했다. 전역 모델 설정은 바꾸지 않았다.
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

본 실험 및 최종 검증 (현재 컴퓨터, 저장소 루트):

```powershell
& ./submissions/26510121/week-05/.venv/Scripts/python.exe submissions/26510121/week-05/runner.py --mode experiment --tag main
& ./submissions/26510121/week-05/.venv/Scripts/python.exe submissions/26510121/week-05/verify_evidence.py
& ./submissions/26510121/week-05/.venv/Scripts/python.exe submissions/26510121/week-05/build_report.py
& ./submissions/26510121/week-05/.venv/Scripts/python.exe submissions/26510121/week-05/final_check.py
```

같은 tag는 중단 후 재개, 새 tag는 별도 실험이다. 완료된 기존 행과 로그를 지우지 않는다. final_check.py는 최초 증거 파일을 덮어쓰지 않으므로 재검사는 깨끗한 checkout에서 공식 명령을 쓰거나 새 캡처 이름을 선택한다. `.runtime/`과 `.venv/`는 제출 제외다.
