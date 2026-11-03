# Week 08: Round 3 lifecycle actors

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

- 로컬 계약·A2A·암호서명·합성 정산 및 터널 경계 테스트 23개 통과.
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
