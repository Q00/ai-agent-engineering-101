# SETUP Step 2: week-06 가게와 buyer 가져오기

2026-10-20, 학번 24522014 (kcwarni).

기존 week-06 구현을 사용하는 경로를 선택했다. starter는 생성하지 않았다.

복사한 파일은 `seller.py`, `buyer.py`, `harness.py`, `agent_profiles.py`, `agent.json`, `prompts/`, `profiles/`다. 코드는 수정하지 않고 그대로 복사했다. 기본 프로필은 seller와 buyer 모두 Claude Code + Haiku다.

Step 1에서 만든 `.venv`, `tools`, 의존성 파일과 로그는 유지했다. week-06의 과거 로그·보고서·테스트 결과는 원래 위치에 보존하고 week-07의 실행 증거로 복사하지 않았다. 하네스가 만드는 임시 작업 디렉터리 `.harness-work/`는 기존 week-06 설정처럼 Git에서 제외한다.

복사 로그는 `logs/setup-step2-copy.log`, 검증 결과는 `logs/setup-step2-verification.log`에 있다. 복사한 파일의 SHA-256 일치, Python 문법과 import, 프로필·프롬프트 로딩, seller/buyer 명령의 `--help`를 확인한다.

Step 3의 실제 거래 테스트와 하네스 응답 확인은 아직 수행하지 않았다. 영수증 서명과 market_buyer는 이후 LAB에서 추가한다.
