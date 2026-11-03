# Week 08: Round 3 lifecycle actors

2026-11-03 21:06 KST에 kit v4로 Buyer·Shop을 모두 `seochanit`으로 **Round 3 등록**했다. 이후 실제 양쪽 정산, 재고 매입, Forum 발송을 확인했다. [등록 감사 기록](logs/live-registration-20261103T120813305637Z.json)과 [실행 로그](logs/strategy-join-20261103T120630782000Z.txt)를 참고한다. 당시 board는 양쪽 정산 4건으로, 각 20건의 과제 기본 조건은 아직 충족하지 않았다. 아래의 미등록·Round 2 표기는 이전 준비 단계의 기록이다.

최신 [시장 전략 교정](MARKET_STRATEGY_UPDATE.md)은 공급 창, 현금 하한·보증·보관비 준비금, Forum 수요 분석·유효 기간·역할별 발송, 변경된 Buyer 점수를 설명한다. [고객 행동 메모리](CUSTOMER_MEMORY.md)는 방문·취향 관측과 FIFO 한도를 설명한다. 실행은 백그라운드로 유지하며 PC와 네트워크가 켜져 있어야 한다.

## 제한된 전략 자동 조정

`r3/adaptive.py`는 기존 Actor·Critic 위에 추가한 수치 기반 전략 제어기다. 모델 학습이나 추가 LLM 호출 없이 다음 세 변수만 변경한다.

- Buyer 할인 역제안 비율: 기본 0.92, 범위 0.88–0.98, 한 번에 0.02 변경. 값이 높으면 할인 요구가 약해진다.
- Shop 목표 재고: 기본 20개, 범위 10–30개, 한 번에 5개 변경.
- Shop 카테고리 가중치: 기본 1, 범위 0.75–1.5. 현재 제안기는 내 판매와 공개 거래가 함께 뒷받침하는 카테고리를 0.1씩 높인다. 여러 카테고리 중 매입 후보 선택에 반영되며 SKU별 수량·현금 제약은 유지한다.

신선한 Round 3 board가 있을 때 시간당 한 번 평가한다. 처음에는 기존 전략을 유지하고 6시간 이상 지난 해당 버전의 관측 20건 이상과 이전 board 이력이 있어야 시험을 시작한다. Buyer는 정산 결과가 확인된 실패 또는 품질 결과가 성숙한 구매만 센다. 예상 보증 환불과 구매 귀속이 불명확한 고장 결과는 확정 성과로 쓰지 않는다. Shop은 충분한 시간이 지난 방문·정산 기록을 사용하되 미정산을 가격 거절이라고 단정하지 않는다.

가게·구매자의 공식 profit/surplus 시간당 변화와 동일 참여자의 공개 지표 변화를 비교하고, 내 거래 성공률·품질도 확인한다. 최소 5건의 성숙한 성공 거래를 포함해 근거가 개선된 시험을 채택한다. 악화·큰 현금 손실·48시간 동안 개선 근거 부족 시 이전 설정으로 복구하고 24시간 쉬었다가 재평가한다. 이는 관측 기반 운영 시험이며 상품·트래픽 변화가 섞이므로 인과적인 성능 향상 증명은 아니다.

기본 `policy.json`, 모델·API 비용 상한, 현금 보유 비율, SKU·서명·결제·인증 검사는 자동 변경하지 않는다. 진행 중 Buyer 주문과 Shop 협상은 시작 버전을 고정하고 새 결정부터 최신 버전을 읽는다. 버전·근거·변경 이력은 SQLite에 보존하며 다음 명령으로 `logs/`에 제출 가능한 스냅샷을 저장한다.

```powershell
./submissions/26510121/week-08/.venv/Scripts/python.exe submissions/26510121/week-08/strategy_status.py --save
```

기존 10분 Round 3 감시기는 유지한다. 다음 시장 실행 시 전략 제어기를 포함한 코드를 읽는다. 실제 시장에서 새 전략이 개선됐다는 결과는 아직 없다.

Round 3 자동 대기·참가(이번 프로세스에만 실행 정책 예외 적용; 전역 설정 변경 없음):

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File ./submissions/26510121/week-08/watch_round3.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File ./submissions/26510121/week-08/watch_round3.ps1 -Status
# 감시와 이 감시기가 실행한 시장 프로세스를 정상 종료:
powershell -NoProfile -ExecutionPolicy Bypass -File ./submissions/26510121/week-08/watch_round3.ps1 -Stop
```

감시기는 즉시 한 번 확인한 뒤 공개 `/api/board`의 `current`를 600초마다 확인한다. 정확히 3일 때 Pinggy 경로로 Shop과 Buyer를 함께 시작한다. 공개 확인 중에는 모델 API를 호출하지 않는다. 감지 지연은 최대 약 10분이며 접속 준비 시간이 추가된다. 실행 실패는 다음 10분 확인 시 재시도하고, 양쪽 등록 로그를 확인한 뒤에만 참가 완료로 표시한다. OS 파일 잠금으로 week-08 감시기와 시장 실행기의 중복 실행을 막는다. 기존 week-07 실행기가 있다면 먼저 종료해야 한다.

이 기능은 이 PC의 숨겨진 백그라운드 프로세스다. PC가 켜져 있고 인터넷이 연결되어 있어야 한다. 실행 중 자동 유휴 절전을 억제하지만 수동 절전·전원 종료·재부팅을 막거나 재부팅 후 자동 복구하지는 않는다. 정책의 종료 시각인 2026-11-17 23:59 KST에 종료를 요청한다. 상태는 Git-ignored `.market/round3-watch.json`, 확인 이력은 `logs/round3-watch-*.jsonl`, 실제 실행 출력은 `logs/auto-join-*.txt`에 남는다. 자동 참가 이후에는 기존 모델 예산과 실제 매입·판매 정책이 그대로 적용된다.

> 2026-11-03 공개 연결 점검: week-07에서 Shop 등록에 성공했던 Pinggy SDK 0.3.1 경로를 복구해 기본값으로 설정했다. 실제 seller → 키트 Proxy → 임시 Pinggy 터널을 통한 공개 A2A·ACP discovery가 모두 HTTP 200으로 검증됐다. 근거: `logs/shop-transport-20261103T082440055660Z.json`. 이 점검은 시장 등록·거래를 하지 않았으며 임시 터널은 종료됐다. 앞선 Cloudflare 45초 점검 실패 기록도 보존했다. 실제 양쪽 참가 완료 여부는 별도로 확인해야 한다.

시장에 등록하거나 거래하지 않고 가게 접속만 점검하려면 다음을 실행한다. 임시 터널은 점검 후 종료되며 거래 요청은 차단된다.

```powershell
./submissions/26510121/week-08/.venv/Scripts/python.exe submissions/26510121/week-08/check_shop_transport.py
# Cloudflare HTTP/2 경로를 별도로 진단할 때:
./submissions/26510121/week-08/.venv/Scripts/python.exe submissions/26510121/week-08/check_shop_transport.py --provider cloudflare --protocol http2
```

이 명령은 진단용이다. 실제 참가 명령은 아래 `join.ps1`이며, `shop registered`와 `buyer registered` 두 줄을 모두 확인해야 한다.

`join.ps1`의 터널 기본값은 `pinggy`다. `-TunnelProvider cloudflare`로 대체 경로를 선택할 수 있으며 Cloudflare는 기존 week-07과 같은 HTTP/2·IPv4 설정을 사용한다. 터널 프로세스에는 API 키와 MARKET_OWNER를 넘기지 않는다. 키트의 기존 watchdog이 운영 중 가게 서버·터널을 감시하고 죽은 터널의 URL을 교체해 재등록한다. 아직 성공하지 않은 최초 공개 연결은 참가 완료로 표시하지 않는다.

이 폴더는 키트 v4의 SKU·재고 계약을 사용하는 새로운 구현이다. 기존 week-07 구현과 week-08의 이전 전략·로그는 보존했다. 과거 `readiness.py`, `STRATEGY.md`, `RANKING_STRATEGY.md`의 구형 계약 판단 대신 이 문서와 `runtime_readiness.py`를 사용한다.

## 현재 확인한 범위

- 로컬 계약·A2A·암호서명·합성 정산·터널·자동 감시 및 전략 시험 테스트 44개 통과.
- Luna/Terra의 소량 구조화 판단 사례 6개 통과.
- 실제 Luna가 shop·buyer 결정을 내리는 로컬 합성 정산 통과. 결제는 한 번만 적용되고 재사용은 거부됨.
- 기존 가게·buyer 키 및 owner 설정을 Git-ignored 경로로 복사했고 API 키는 기존 week-06 파일에서 메모리로 읽는다.
- cloudflared 2026.10.7을 공식 SHA-256으로 확인해 `.market/bin/`에 설치했다.
- 공개 시장 등록·실제 Round 3 정산·양쪽 20건 조건·최종 순위는 아직 검증하지 않았다. 마지막 공개 조회는 Round 2였다.

## 재현

저장소 루트의 PowerShell에서:

```powershell
python -m venv submissions/26510121/week-08/.venv
uv pip install --python submissions/26510121/week-08/.venv/Scripts/python.exe -r submissions/26510121/week-08/requirements-lock.txt
./submissions/26510121/week-08/.venv/Scripts/python.exe submissions/26510121/week-08/verify.py
```

유료 API 검증은 명시적으로 선택한다. 기존 키 재사용은 사용자에게 승인받았다.

```powershell
./submissions/26510121/week-08/.venv/Scripts/python.exe submissions/26510121/week-08/model_probe.py
./submissions/26510121/week-08/.venv/Scripts/python.exe submissions/26510121/week-08/verify.py --live-integration
```

신원·터널 준비와 현재 상태:

```powershell
./submissions/26510121/week-08/.venv/Scripts/python.exe submissions/26510121/week-08/prepare_runtime.py
./submissions/26510121/week-08/.venv/Scripts/python.exe submissions/26510121/week-08/prepare_tunnel.py
./submissions/26510121/week-08/.venv/Scripts/python.exe submissions/26510121/week-08/runtime_readiness.py
```

## 시장 실행

기존 week-07 프로세스가 같은 신원으로 운영 중이면 먼저 그 프로세스를 정상 종료한다. 두 역할을 함께 실행하는 명령:

```powershell
& ./submissions/26510121/week-08/join.ps1
```

터미널을 유지하며 Ctrl-C로 종료한다. 연결 성공은 `logs/market-kit.log`의 버전 4 및 shop/buyer 등록 줄로 확인한다. 시장이 아직 Round 2이면 새 buyer는 구형 상품 거래를 실행하지 않고 대기한다. 터널이 열리지 않으면 `-BuyerOnly`로 buyer만 시험할 수 있지만 그것은 양쪽 참여가 아니다.

공유 `scripts/market_kit.py`를 직접 실행하면 exact-SKU 어댑터가 빠지므로 `join.ps1` 또는 `run_market.py`를 사용한다. 키·`.env`·`.market/`은 커밋하지 않는다.

## 모델·비용

`policy.json` 기본은 actor `gpt-6-luna`, 선택적 의미 critic `gpt-6.1-terra`다. 수치 critic은 코드로 계산한다. 일일 모델 비용 상한은 두 역할·훅이 공유하는 UTC 하루 $1이며 설정 가능하다. 오류·청구 불확실성은 예약 비용을 보수적으로 유지한다. 예산 소진·API 오류는 기록된 수치 기반 대체 경로를 사용한다.

가격은 2026-11-02 확인한 [Luna 공식 문서](https://developers.openai.com/api/docs/models/gpt-6-luna)와 [Terra 공식 문서](https://developers.openai.com/api/docs/models/gpt-6.1-terra)의 표준 짧은 문맥 기준이다. 캐시·지역·서비스 계층의 할증·할인은 이 로컬 비용 추정에 완전히 반영하지 않는다. API 청구서와 동일하다고 주장하지 않는다.

## 설계와 제한

- `r3/state.py`: 사건 ID, 영구 기억, 단위 예약, 공유 비용 상한, 지연 결과 수정.
- `r3/economy.py`, `r3/meta.py`: 원가·회수·유효 잉여·성사율·고장 근거·순위 기회 근사.
- `r3/model.py`: 구조화 후보 선택과 선택적 critic, 시간·비용 제한.
- `seller_r3.py`, `market_buyer.py`, `restock.py`, `review.py`: 실제 v4 훅과 A2A 서비스.
- `r3/adapter.py`: 정확한 checkout SKU, 주문 요구보다 약하지 않은 accept, 직렬 checkout 예약, 미완료 세션 만료.

매입 수량·가격 배율·불명확한 고장 위험의 스트레스 값은 실험할 초기 정책이다. 실제 수요나 최적 정책으로 확정한 값이 아니다. 운영 결과에 따라 검토해야 한다. 받은 게시판 점수나 Players 순위의 개선도 아직 실증하지 않았다.

전체 문제 정의는 `ROUND3_FORMULATION.md`, 전략 후보와 실패 조건은 `ROUND3_STRATEGY_REVIEW.md`, 구현 결과는 `REPORT.md`, 실제 모듈 구조는 `architecture.tex`와 `architecture.pdf`를 참고한다.
