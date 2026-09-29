# Week 05 — 협상장 MCP 서버

상태: 작업 골격 작성 완료. 서버·host·runner 및 실험은 아직 구현/실행하지 않았다.

과제 기준: `weeks/week-05/README.md`. 진행 순서와 완료 기준은 `TASKS.md`, 구성과 데이터 경계는 `ARCHITECTURE.md`에 기록한다.

## 구성

```text
week-05/
├── README.md            현재 상태와 실행 안내
├── TASKS.md             단계별 체크리스트와 완료 기준
├── ARCHITECTURE.md      책임 분리와 실험 설계 초안
├── market_server.py     서버 구현 위치 (현재 미구현)
├── host.py              MCP host 구현 위치 (현재 미구현)
├── runner.py            실험 runner 구현 위치 (현재 미구현)
├── results.csv          정확한 제출 헤더만 작성; 실행 결과 없음
├── REPORT.md            보고서 틀; 실제 결과로 채울 예정
├── scenarios.json       단계 1에서 작성하고 실행 전에 커밋
├── auth_checks.txt      실제 서버 검사 후 네 줄 작성
├── logs/                실제 run의 원본 콘솔 로그
└── tests/               인증·상태·한도 검사 테스트
```

`scenarios.json`, `auth_checks.txt`, 실제 로그와 테스트는 아직 작성하지 않았다. 빈 틀은 실험 증거가 아니며, 현재 제출물은 최종 CI 통과 상태가 아니다.

## 환경과 실행

- 2026-09-29 확인: `python` 명령을 찾지 못했고 `py --list-paths`는 설치된 Python이 없다고 반환했다. Python 환경 확보 후 구문 및 실행 검증을 진행한다.
- MCP SDK, host, 모델, temperature와 의존성 버전은 단계 1에서 결정한다. 설치/실행 명령은 실제 검증 후 이 문서에 기록한다.
- API 키와 토큰은 커밋하지 않는다. `.env` 파일도 제출하지 않는다.
- 작업 단위별로 커밋하고, 실패한 시도와 원본 실행 로그를 보존한다.

최종 구조 검사 (저장소 루트에서):

```powershell
python scripts/check_week05.py submissions/26510121/week-05
```

CI는 구조 검사다. 실제 인증, 차례, 한도 검사는 별도로 실행해야 한다.
