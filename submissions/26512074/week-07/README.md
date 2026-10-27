# Week 07 — totato2203의 가구 시장 에이전트

6주차 A2A 에이전트에 정수 가격의 Ed25519 영수증, 후기·거래 기억을 쓰는
구매자, 실제 시장 성과를 읽는 호가 조정을 추가했습니다.

## 실행

Python 3.12와 로그인된 Codex CLI를 사용합니다. 모델은 `gpt-5.6-terra`,
추론 수준은 `low`입니다. 저장소 루트의 PowerShell에서:

```powershell
python -m venv submissions/26512074/week-07/.venv
submissions/26512074/week-07/.venv/Scripts/python.exe -m pip install -r submissions/26512074/week-07/requirements.txt
$env:PATH = (Resolve-Path submissions/26512074/week-07/.venv/Scripts).Path + ';' + $env:PATH
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
python scripts/market_test_week07.py submissions/26512074/week-07
python scripts/check_week07.py submissions/26512074/week-07 --github totato2203
```

공식 테스트는 키를 생성하며 `*.pem`은 Git에서 제외합니다. 키를 삭제하거나
새로 만들면 시장에 등록된 공개키와 맞지 않으므로 기존 키를 유지해야 합니다.

## 현재 PC 배포

현재 PC에서 `run_market.py`가 공식 `scripts/market_kit.py`를 호출합니다.
수정한 공용 파일은 없습니다. `start-market.ps1`은 숨김 창으로 실행합니다.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File submissions/26512074/week-07/start-market.ps1
```

처음 설정할 때는 공식 cloudflared 실행 파일을 `tools/cloudflared.exe`에
배치하고, 시장의 `/join`에서 받은 claim code를 `.market/owner-code.txt`에
보관합니다. 둘 다 커밋하지 않습니다. 이미 실행 중이면 중복 실행을 거부합니다.
운영 PID는 `.market/operator.pid`, 실제 공개 주소는 `logs/market-kit.log`의
`[shop]` 줄에서 확인합니다. 공개 주소는 재시작할 때 달라질 수 있습니다.

프로세스 실행 중 자동 절전을 방지합니다. **마감까지 PC의 전원과 네트워크,
로그인 세션을 유지해야 합니다.** 재부팅하거나 로그아웃한 경우 위 명령으로
다시 실행합니다. 종료하려면 저장된 PID의 운영 프로세스 트리만 종료합니다.
현재 PC 배포이며 PC가 꺼져도 유지되는 외부 VPS 배포는 아닙니다.

운영자는 주문 요청 간격을 기본 45초로 제한합니다. 필요하면 실행 전에
`MARKET_ORDER_INTERVAL` 환경변수를 설정할 수 있습니다. 모델 호출은 실제
Codex 로그인과 사용 가능 용량에 의존합니다.

## 기능과 증거

- `signing.py`: 네 필드만 정규화해 정수 가격으로 서명하고, 수신한 실수 가격도
  같은 정수로 되돌려 검증합니다.
- `market_buyer.py`: 최근 후기와 이름별 거래 기억을 모델에 보여 줍니다.
  한 번에 최대 12개 상점, 각 6개 후기·4개 과거 거래로 문맥을 제한합니다.
  부정한 영수증이 확인된 상대는 URL이 바뀌어도 이름으로 제외합니다.
- `run_market.py`: 공식 키트의 클라이언트로 받은 공개키를 구매자에게 전달합니다.
  가게와 구매자 자체는 시장 API를 호출하지 않습니다. 운영자는 자기 상점의
  실제 성과·후기를 읽고, 실제 인정 거래 이후 모델에 호가 가이드 변경을 맡깁니다.
- `logs/challenge-1/`: 변경 전후 점유율·평균 가격과 모델의 호가 변경 이유입니다.
- `logs/challenge-2/`: 실제 시장 상점의 부풀린 영수증과 이후 회피 증거입니다.
  별도 검증용 거래는 시장 주문 실적으로 제출하지 않았습니다.
- `logs/market-kit.log`: 실제 가입·주문·양쪽 서명·Round 2 인정 거래 원본 로그입니다.

`trade-memory.json`, `pricing-feedback.json`, 개인키와 claim code는 로컬 운영
상태이며 커밋에서 제외합니다. 평가용 증거는 별도의 로그에 보존했습니다.
실제 실패·라운드별 관찰·배포 방식과 한계는 REPORT.md를 참고하세요.
