# C 우선만 / A 무보증 제거

- 출발 HEAD 01f4be7; 이전 구현 0a67422 및 보류 d16988c/01f4be7 보고서 확인.
- 기존 dirty market-test.log/test-report.json 및 untracked 원본은 보존/미스테이징.
- week-08 README/checker 없음. 운영 state/.market/키/env/pem 접근 금지.
- 이번 범위는 로컬 수정과 원격 격리 후보 검증뿐. 운영 교체/재시작/push 없음.
- 이전 격리 harness를 새 고유 history에서 재사용하고 baseline부터 실행한다.
- baseline 집중 107/전체 534(2 skip) 통과. 새 계약 red 집중 108 중 실패 27.
- A0 helper/override/세션 옵션 제거 및 고정 A72 복원 후 집중 108 중 실패 1:
  동작이 아닌 기존 보증 안내 문자열 fixture 기대값 차이(실제 Safe-Care 72h).
  원시 green-focused.log는 실패 그대로 보존한다.
- fixture 보증 문자열 교정 + C0 협상 영수증/B72 거절/후속 0보증 A72 유지 회귀 추가.
  final-focused 111 통과; final-all 538 발견/536 실행 통과/2 기존 launcher skip.
- verify_local.py: 7개 py_compile(격리 cfile), 기존 가격 함수 AST 완전 동일,
  소스/정책/runner 한정 diff --check exit 0. 주차 checker 없음, 실거래 미실행.
- 새 read-only VM inspect: HEAD 4a5deb685368343abfd1310c572288a924351b01,
  tracked dirty 없음, 기존 allowlist 56개 전부 이전 보류 기준선 해시와 동일.
  seller/kit/tunnel PID/starttime도 기존 보류 기록과 동일. 키/state 접근 없음.
- 첫 VM 격리 후보: baseline 440(2 skip), red 집중 111/실패 50,
  후보 집중 111 통과, 전체 479/477 실행 통과/2 skip. 운영 변경/시그널 0.
- 추가 self-review에서 A0 당시 일반화했던 무보증 설명 템플릿도 원래 C 전용 문구로
  되돌린다. 동작상 C0 문구는 같지만 불필요한 shared 설명 delta까지 없애기 위함.
  이미 생성한 후보/로그는 보존하고 revision02 별도 후보로 재검증한다.
