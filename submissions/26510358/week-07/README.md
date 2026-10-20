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
  --name gacha-shop \
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

## Step 4 등록 결과

2026-10-20 19:57 KST에 공식 시장 Round 0에 상점·구매자를 모두 `SUNGMYEONGGI` 이름으로 등록함
시장 `/api/board`에서 두 역할의 `owner.login`이 `SUNGMYEONGGI`이고 `alive`가 true임을 확인함
공개 Agent Card도 HTTP 200으로 응답하고 통신 주소가 터널의 `/a2a/0`을 가리킴을 확인함

등록 당시 공개 주소는 `https://legend-clinics-suffered-nickel.trycloudflare.com`임
`--tunnel` 방식이므로 웹 페이지의 수동 등록 폼을 채울 필요 없이 키트가 공개 주소와 공개키를 함께 등록함
개인키는 같은 파일을 유지하며 재시작 시 임시 주소가 바뀔 수 있음
키트를 실행한 머신을 유지해야 상점·구매자의 시장 참여가 계속됨

최초 시도는 이미지에서 claim code 문자를 잘못 읽어 서버가 등록을 거절했으며 사용자에게 받은 텍스트 코드로 재시도해 성공함
claim code는 소스·실행 명령 인자·공개 로그에 기록하지 않음
현재 키트는 백그라운드에서 주문을 처리하며 `logs/market-kit.log`에 실행 원문을 계속 추가함
로컬 실행 PID와 콘솔·등록 확인 상태는 Git에서 제외한 `.market/`에 기록함

## 거래 로그 확인과 변경 반영

저장소 루트에서 아래 명령으로 주문·협상·영수증·시장 결과를 실시간으로 확인함

```bash
tail -n 100 -F submissions/26510358/week-07/logs/market-kit.log
```

상세한 모델 입력·판단·A2A 요청과 응답·예산 원장은 `logs/runtime/*.jsonl`에 기록함
공개 시장 화면에서는 거래·방문·점유율과 공개 거래 내역을 확인할 수 있음
로그를 분석한 뒤 코드·프롬프트·정책을 수정하고 로컬에서 검증한 다음 키트를 재시작하면 새 거래부터 적용됨
`agent.json`은 키트를 시작할 때 읽으며 판매자 설정과 정책도 프로세스 시작 시 읽음
구매자는 주문마다 새 프로세스로 실행하므로 구매자 소스 변경은 다음 주문부터 읽힐 수 있음
공통 모듈까지 수정한 경우 판매자와 구매자에 함께 반영하려면 키트를 재시작함
현재는 로그를 읽어 자동으로 재학습하거나 실행 중인 코드를 자동으로 갱신하는 기능은 없음

화면의 상점 표시 이름은 `seller.py`의 기본 설정 `name`이며 현재 값은 `Gacha shop`임
키트를 재시작하고 같은 등록 닉네임·개인키로 참가하면 새 Agent Card가 시장에 등록됨
공개 주소는 `.market/session.json`의 `public_base_url`을 기준으로 확인함

2026-10-20 20:09 KST에 공개 Agent Card와 시장 `/api/board` 모두 `Gacha shop`을 표시함을 확인함
기존 등록 닉네임·GitHub 소유자·상점과 구매자의 키·이전 거래 이력을 유지한 채 재등록함

## 점유율 표의 등록 닉네임

상점 표시 이름은 Agent Card의 `Gacha shop`이며 점유율 표는 참가 명령의 `--name`으로 집계함
닉네임은 공백 없는 `gacha-shop`으로 설정하고 GitHub 계정은 `SUNGMYEONGGI`에 연결함
2026-10-20 20:16 KST에 상점·구매자 모두 `gacha-shop` 등록과 온라인 상태를 확인함
이전 `SUNGMYEONGGI`의 거래 이력은 해당 이름으로 남고 이후 체결은 새 닉네임으로 집계함
점유율 표는 체결이 있는 상점만 표시하므로 첫 새 체결 전에는 `gacha-shop` 행이 표시되지 않음
