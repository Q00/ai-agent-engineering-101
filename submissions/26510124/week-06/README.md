# Week 06 — 하네스로 판단하는 판매자와 구매자

판매자는 `seller_agent.py`, 구매자는 `buyer_agent.py`다. 둘 다 Claude Code의
`haiku`를 호출한다. 실제 가격과 수락·거절·포기는 모델이 정하고, Python은
A2A 메시지와 task 상태, 응답 형식 및 가격 제한을 관리한다.

## 환경 준비

이 디렉터리(`submissions/26510124/week-06`)에서 실행한다.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-lock.txt
claude --version
claude auth status
```

Claude Code가 설치되어 있고 로그인된 상태여야 한다. 로그인하지 않았다면
`claude`를 직접 실행해 계정 로그인을 완료한다. 코드에 키를 넣지 않는다.
검증에 사용한 Python은 3.12, Claude Code는 2.1.287이다.
`agent.json`의 명령은 이 폴더에서 `.venv/bin/python`을 사용하므로 가상환경을
활성화하지 않아도 되지만, 위 준비 단계로 `.venv`를 만들어야 한다.

## 직접 거래해 보기

터미널 1:

```bash
.venv/bin/python seller_agent.py --port 9999
```

서버는 경고 이상의 로그만 표시하므로 시작 문구 없이 대기할 수 있다.
터미널 2도 이 디렉터리에서 실행한다.

```bash
.venv/bin/python buyer_agent.py --url http://127.0.0.1:9999 --item lamp --budget 120
```

구매자의 요청·응답·모델 결정이 터미널 2에 표시된다. 모델의 원본 입력과 출력은
매 호출마다 다른 이름으로 `logs/`에 저장된다. 두 프로그램 모두 실제 Claude
호출을 하므로 로그인 계정의 사용량을 소비한다. 거래 결과와 가격은 실행마다
달라질 수 있다. `Ctrl+C`로 판매자 서버를 종료한다.

## 검증

이 디렉터리에서 오프라인 검사를 실행한다.

```bash
.venv/bin/python -m unittest discover -s . -p 'test_*.py' -v
```

공식 거래 검사는 다음 보조 명령으로 실행할 수 있다. 보조 명령은 기존 공식
결과와 고정 이름의 로그를 `logs/previous-runs/`에 그대로 복사한 다음,
수정하지 않은 `scripts/trade_test_week06.py`를 호출한다. 공식 스크립트가 직접
`test-report.json`과 표준 로그를 생성한다.

```bash
.venv/bin/python run_trade_check.py
```

레포 최상위에서 공식 스크립트를 직접 실행하는 명령은 다음과 같다.
직접 재실행할 때도 이전 결과를 먼저 커밋하고 보존한다.

```bash
submissions/26510124/week-06/.venv/bin/python scripts/trade_test_week06.py submissions/26510124/week-06
submissions/26510124/week-06/.venv/bin/python scripts/check_week06.py submissions/26510124/week-06
```

## 코드를 읽는 순서

1. `prompts/seller_opening.txt`: 높은 가격에서 시작해 흥정 여유를 둔다는 사용자 선택.
2. `prompts/seller.txt`, `prompts/buyer.txt`: 각 역할의 목표와 JSON 응답 약속.
3. `harness.py`: 빈 디렉터리에서 하네스를 호출하고 원본 로그·응답 검증을 처리.
4. `seller_agent.py`: 판단을 input-required, completed, rejected 상태로 변환.
5. `buyer_agent.py`: 카드 확인, 같은 task에서 협상, 영수증·예산 확인.
6. `REPORT.md`: 실제 실패, 수정, 검증 결과와 이 구현의 한계.
