# Week 07: Agent Market

학번 `26520024`, GitHub `LeeUichann`, 시장 이름 `leeuichann`.
실제 운영 분석과 한계는 [REPORT.md](REPORT.md)를 참고한다.
기존 배포 코드를 그대로 제출하며 `week-07-2`의 미배포 변경은 포함하지 않는다.

## 환경

- Python 3.10.22, 기존 `eraseanything` 기반 과제 전용 `.venv`.
- 의존성은 `requirements.lock`. conda 환경과 패키지는 이번 작업에서 변경하지 않았다.
- 인증된 Codex CLI `gpt-6-astra`, reasoning `low`. 필요하면 `CODEX_BIN`을 지정한다.
- 새 환경에서만 Python 3.10 이상으로 `.venv`를 만들고 `requirements.lock`을 설치한다.

## 검증

저장소 루트에서 이전 검사 로그를 보존하고 실제 시장 기억과 분리하여 검사한다.

```bash
submissions/26520024/week-07/.venv/bin/python \
  submissions/26520024/week-07/run_lab1_checks.py --phase lab2
submissions/26520024/week-07/.venv/bin/python \
  scripts/check_week07.py submissions/26520024/week-07 --github LeeUichann
```

첫 명령은 오프라인 65개와 공식 시장 검사를 실행한다. 공식 검사는 합성 가게와 실제
모델을 사용하며 공개 시장 참가자에게 거래를 보내지 않는다. 기록은 `logs/lab2-<UTC>/`,
최신 공식 결과는 `test-report.json`에 있다.

## 운영

이미 실행 중이면 중복 실행하지 않는다. tmux에서 기존 로그인과 `MARKET_OWNER`
환경변수를 준비한 뒤 seller와 buyer를 함께 실행한다.

```bash
submissions/26520024/week-07/.venv/bin/python scripts/market_kit.py join \
  --dir submissions/26520024/week-07 \
  --market https://market.ourolabs.ai/ --name leeuichann --tunnel
```

같은 이름과 키를 유지해야 기존 거래·평점이 이어진다. 키, claim code, `.market/` 기억은
커밋하지 않는다. 제출물로 기존 가게를 이어 실행할 때는 개인키와 환경을 별도로 복원해야
하며 임의로 새 키를 만들면 안 된다.

## 구성과 증거

- `seller.py`: 상품 검색, 모델 협상, 가격 제약, Ed25519 영수증.
- `market_buyer.py`, `buyer.py`: 후기 기반 선택, 예산 보호, A2A 협상과 후기.
- `trade_memory.py`: 이름별 SQLite 거래 기억.
- `rps_promotion.py`: 구매자 승리 시 최저가, 그 외 현재 가격을 유지하는 선택형 할인.
- `logs/challenge-1/`, `logs/challenge-2/`: 기존 운영의 필수 도전 증거.
- `logs/historical-verified/`: 원문 위치·해시, 라운드별 스냅샷과 검증된 분석.
- `logs/submission-20261027T054948Z/`: 전체 gzip 스냅샷, 재시작 구간, 온라인 상태.

운영 원본은 계속 쌓이므로 제출 전용 작업 트리에서만 `logs/market-kit.log`를 첫 연속
구간으로 제출했다. 전체 gzip을 풀면 당시 스냅샷이 복구되며 범위와 SHA-256은 REPORT에
명시했다. 원본을 편집하거나 새 모델 실행으로 위장하지 않았다.

[LAB1.md](LAB1.md), [LAB2.md](LAB2.md)는 당시 구현 기록이다. 그 문서의 재시작 전 상태와
6주차 보고서에 관한 언급은 당시 상황이며 최종 상태는 REPORT가 기준이다.
