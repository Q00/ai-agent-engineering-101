# Week 07 시장 참가 준비

강의 SETUP Step 2에 따라 Week 06의 실행 소스·카탈로그·스키마·고정 정책·회귀 검사를 복사함
원본은 `week-06` 브랜치의 `3004c51166ec4e42102423032355552df917cfdc` 커밋임
이전 주차의 로그·검사 보고서·제출 문서는 이번 주차의 실행 결과로 복사하지 않음

저장소 루트에서 가상환경을 활성화한 뒤 아래 순서로 실행함

```bash
source .venv/bin/activate
python -m pip install -r submissions/26510358/week-07/requirements.txt
python scripts/trade_test_week06.py submissions/26510358/week-07
python scripts/market_kit.py join \
  --dir submissions/26510358/week-07 \
  --market https://market.ourolabs.ai \
  --name SUNGMYEONGGI \
  --tunnel
```

거래 검사는 실제 Codex CLI 로그인을 사용하며 새 결과를 `test-report.json`과 `logs/trade-test.log`에 기록함
시장 참가 시 키트가 판매자와 터널을 실행하고 주문별 구매자를 시작하며 결과를 `logs/market-kit.log`에 남김
생성되는 개인키 `*.pem`과 실행 상태 `.market/`는 커밋 대상에서 제외함

현재 코드는 Step 2의 Week 06 기반 코드이며 서명 영수증과 리뷰·기억을 사용하는 `market_buyer`는 이후 LAB에서 구현할 항목임
GitHub 계정 연결은 시장의 `/join`에서 발급받은 claim code를 `MARKET_OWNER`로 설정한 뒤 참가 명령을 실행함

## Step 3 실행 결과

2026-10-20에 현재 공식 `scripts/trade_test_week06.py`로 새로 실행해 21/21 통과함
`test-report.json`의 키트 SHA가 현재 스크립트와 일치함을 확인함
램프 판매 3회는 각각 120, 구매 3회는 103·102·100에 체결됐으며 구매 예산은 120임
예산 230·최저가 260의 책장은 판매자·구매자 검사 모두 미체결로 종료함
실행 원문은 `logs/trade-test.log`와 `logs/*under-test*.log`에 보존함
