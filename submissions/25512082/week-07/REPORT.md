# Week 07 보고서 — 25512082 (sohyunkim11)

## 0. 개요와 실행 환경

- Seller: `seller.py` (카드 이름 `Atlas Shop`, 시장 등록 이름 `sohyunkim11`), Week 06 판매자에 Ed25519 영수증 서명과 Challenge 1 가격 정책을 추가했습니다.
- Week 06 Buyer: `buyer.py` (Round 0 용, `agent.json`의 `buyer`)
- Market Buyer: `market_buyer.py` (`agent.json`의 `market_buyer`)
- 하네스: `harness.py`가 `codex exec --ephemeral --sandbox read-only --model gpt-6.1-sol -c model_reasoning_effort="low" --output-schema ...`를 한 번 호출하고, 결과 JSON을 로컬에서 스키마와 reserve·예산 기준으로 다시 검증합니다. Codex는 ChatGPT 계정으로 로그인해 사용했고, 별도의 API 키 과금은 쓰지 않았습니다.
- 실행 환경: Windows + WSL Ubuntu 24.04, Python은 `a2a-lab/.venv`, 공식 `scripts/market_kit.py`를 사용했습니다.
- 공식 Market Test: **25/25 PASS** (`test-report.json`, `logs/market-test.log`). Challenge 1 가격 정책을 적용한 코드로 2026-10-27 18:02–18:07 KST에 다시 실행했습니다.

시장 운영 기간은 다음 두 구간입니다.

| 구간 | 시각 (KST, 2026-10-27) | 내용 |
|---|---|---|
| 1차 운영 | 14:46:02 등록 → 17:40:18 `[kit] leaving the market` | 모델이 정한 호가로 운영했습니다. 사용자 요청으로 Ctrl+C를 눌러 종료했습니다(kit이 KeyboardInterrupt를 받을 때 남기는 줄). |
| 중단 | 17:40:18 → 18:10:50 | 프로세스가 없었고, 대시보드에도 `alive=false`로 표시됐습니다(17:56 확인). |
| 2차 운영 | 18:10:50 재등록 → 마감까지 유지 | Challenge 1 정책 호가와 Challenge 2 `[avoid]` 로그를 적용했습니다. 같은 이름과 같은 키를 사용했습니다. |

일주일 내내 운영한 것이 아니라, 등록일인 10월 27일 오후부터 운영을 시작했습니다.

## 1. Buyer가 가게를 고르는 방법과 기억의 형식

`market_buyer.py`의 `choose_shop`은 kit이 넘긴 `{shops}` 파일의 모든 가게에 대해 다음 점수를 로컬에서 계산하고, 최고점인 가게 하나를 고릅니다. 모델 호출은 없습니다.

```
score = 평균별점×10 + min(성공거래,5)×2 − min(무거래,5) − 무결성경고×12 − 영수증불일치×25
```

- **평균 별점**: 유효한 1~5점 리뷰의 평균이며, 리뷰가 없으면 3.0으로 둡니다.
- **무결성 경고**: 2점 이하 리뷰 가운데 `overcharg`, `receipt`, `did not match`, `higher than`, `avoid` 중 하나를 포함한 리뷰의 수입니다.
- **기억**: 과거 성공·무거래·영수증 불일치 횟수입니다. 성공 가점과 무거래 감점은 각각 5건까지만 반영해, 오래된 경험이 리뷰를 압도하지 않게 했습니다.
- **가격·예산**: 가게 선택 단계에서는 가격을 보지 않습니다. 예산은 협상 단계에서 지킵니다. `harness.decide_buyer`는 모델이 낸 가격이 `min(budget, remaining_budget)`를 넘으면 `HarnessError`를 일으켜, 예산을 넘는 제안을 코드에서 막습니다.
- 선택 이유는 `[choose] <가게> (<url>): reviews: …; memory: …; score …` 한 줄로 출력되어 kit 로그에 남습니다. 2차 운영부터는 경고나 불일치가 있는데도 고르지 않은 가게를 `[avoid] …` 줄로 함께 남깁니다.

**기억 형식**: `.market/runtime-state/buyer-memory.json` (Git 제외 대상이며, 테스트는 `.market/test-state/`로 분리했습니다).

```json
{"version": 1, "shops": {"thisjonghoon": {"deals": 197, "no_deals": 38, "receipt_mismatches": 0,
                                          "last_outcome": "deal", "last_price": 185}}}
```

가게 URL은 kit을 재시작할 때마다 바뀌므로 기억의 키는 가게 `name`으로 했습니다. 거래가 끝날 때마다 `remember()`가 카운터를 올리고, 임시 파일에 쓴 뒤 교체하는 방식으로 원자적으로 저장합니다. 거래가 성사됐는데 영수증 가격이 내 마지막 제안과 다르면 `receipt_mismatches`를 올리고 별점 1점과 "Receipt price did not match the final offer." 리뷰를 남깁니다.

실제 선택 결과: 1차 운영 kit 로그에서 `[choose]`는 `thisjonghoon` 235회, `moss-and-oak` 2회였습니다(`logs/challenge-2/scan-*.log`). `thisjonghoon`이 리뷰 평균 4.3~4.8점에 경고 0건이고 성공 기억이 쌓여 계속 최고점을 받은 결과입니다. 이것이 가격 면에서 최적이라는 뜻은 아닙니다(§6 한계 참고).

## 2. 후기를 하네스에 보여 준 방법

리뷰 원문은 Codex 하네스 프롬프트에 넣지 않았습니다. 리뷰를 어떻게 쓰는지는 코드가 정합니다.

1. kit이 `/api/shops`에서 받은 `{"name","url","reviews":[{"stars","review","round"}]}` 목록을 `.market/shops.json`으로 쓰고, `--shops`로 넘겨 줍니다.
2. `market_buyer._review_summary`가 가게별로 리뷰 수, 평균, 무결성 경고 수를 계산합니다. `choose_shop`은 이 값을 기억과 합산해 가게를 고릅니다.
3. 가게를 고른 뒤의 가격 협상만 `buyer.BuyerClient` → `harness.decide_buyer`가 맡습니다. 이때 모델에 들어가는 입력은 `BuyerDecisionContext{item, budget, current_ask, task_id, remaining_budget, last_buyer_offer, history}`이며 리뷰는 들어가지 않습니다.

이렇게 나눈 이유는 두 가지입니다. 리뷰 본문에는 다른 참가자가 쓴 임의의 텍스트가 들어 있으므로(프롬프트 주입 위험), 모델에 넣지 않고 숫자와 키워드로만 줄였습니다. 또 선택 근거를 `[choose]` 줄로 재현할 수 있게 하려는 목적도 있었습니다.

증거는 다음과 같습니다.
- 공식 `buyer_reads_reviews`: 나쁜 리뷰가 달린 `Kit Shop North`에는 3번 모두 요청을 보내지 않았고, `Kit Shop South`와 3/3 거래했습니다. 로그에는 `[avoid] Kit Shop North: 3 review(s), average 1.33, integrity warnings 3`이 남았습니다(`logs/buyer-under-test-buyer_reads_reviews-*.log`).
- 실시장 예시: `18:11:22 [buyer] [avoid] maple-home: 8 review(s), average 1.00, integrity warnings 8; receipt mismatches 0` 다음에 `[choose] thisjonghoon …`이 이어지고, `ledger #1016027: chair at 185, stars 5, verified True`로 끝났습니다.

## 3. Round 0, 1, 2에서 일어난 일

### Round 0, Round 1

참여하지 않았습니다. 시장의 `/api/board?round=0`과 `?round=1`을 조회하면 `sohyunkim11` Shop과 Buyer 모두 주문 0건, 거래 0건, 방문 0건입니다(`logs/operations/rounds-own-rows.jsonl`, 18:11:56 KST 측정). 처음 등록한 시각이 Round 2 규칙이 이미 적용되던 10월 27일 14:46이었기 때문입니다. Round 0과 1의 결과는 만들어 쓰지 않았습니다.

### Round 2

1차 운영(14:46–17:40)의 Buyer 로그(`logs/market-kit.log`) 기준입니다.
- 대시보드 주문 231건(kit 로그의 `[order …]` 줄은 같은 주문이 다시 온 경우를 포함해 299개) → `verified True … review accepted` 194건, `no deal … verified False` 37건, `result refused: 502` 4회
- 성공 예: `16:05 ledger #1000216: lamp at 105`, `16:06 #1000373: chair at 185`, `17:31 #1011493: bookshelf at 265`
- 실패 예: `ledger #1011573: no deal, stars 2, verified False, review not shown`. 무거래 37건은 **모두** 예산이 reserve보다 낮은 주문(lamp 90, chair 160, bookshelf 230 등)이어서 어떤 정직한 가게와도 성사될 수 없었습니다. 이런 주문에서는 Buyer가 예산을 지키고 협상을 그만두었습니다(`walked_away`).
- 모든 성공 거래에는 `[sign] sig_seller ok, sig_buyer added`가 남아 있습니다. 시장 대시보드의 Buyer `deals`도 194건으로 로그와 같습니다(17:41:54 측정).

Seller 쪽은 kit이 판매 내역을 로그로 남기지 않아 대시보드 수치만 있습니다. 14:47에 거래 1건(매출 295, 이익 35)이었고, 15:41에 40건, 17:41:54에 101건(방문 178, 매출 22,492, 이익 2,712, 별점 4.43)이었습니다. 시각별 값은 `logs/operations/leaderboard.jsonl`에 있습니다.

2차 운영(18:10:50 이후)의 결과와 최종 순위는 아래 표에 있습니다. 측정 시각: **{{MEASURED_AT}}**

{{FINAL_TABLE}}

Buyer 기억의 `deals` 카운터는 시장이 인정하지 않은 거래나 재시도까지 셀 수 있습니다. 그래서 공식 성과는 대시보드의 검증 거래 수만 사용했습니다. Seller 판매 건수와 Buyer 구매 건수는 역할이 다르므로 합산하지 않았습니다.

## 4. 서명을 붙이며 실패한 것과 바꾼 것

시간순으로 정리했고, 모두 커밋으로 보존되어 있습니다.

1. `e884732` (01:31) **서명 없는 영수증으로 Market Test를 돌려 실패했습니다** (`logs/attempts/unsigned-receipt/`). Week 06 영수증은 `{seller, item, price}` 형식이어서 `seller_signed_receipt #1: receipt lacks shop, task`가 나왔습니다. 같은 실행에서 `seller_deal #2: TASK_STATE_REJECTED, asks [150]`도 함께 실패했습니다.
2. `f844605` (01:32) **서명을 추가했습니다.** 영수증을 `{shop, item, price, task}`로 바꿨습니다. `task`는 A2A task id이고, `price`는 `int(price)`로 정수화해 `120.0`이 아닌 `120`에 서명되게 했습니다. 이어서 `canon()`(`sort_keys=True, separators=(",",":"), ensure_ascii=False`)으로 만든 바이트에 `shop_key.pem`으로 Ed25519 서명한 값을 `sig_seller`(base64)에 넣었습니다. `test_seller_signing.py`가 공개키로 검증이 통과하는지, 가격을 바꾸면 검증이 실패하는지 확인합니다(`logs/lab2-unit-tests.log`).
3. `13ec666` (12:15) **Codex 사용량 한도에 걸려 Market Test가 25개 중 14개 실패했습니다.** 하네스가 `HarnessError`를 내자 Seller가 안전하게 거절했습니다(`TASK_STATE_REJECTED`). 코드 결함이 아니라 사용량 소진이 원인이었으므로 코드는 그대로 두고 기록만 보존했습니다.
4. `5bd9507` **사용량을 확보한 뒤 같은 코드로 25/25 PASS를 받았습니다.** 실시장에서도 공식 kit이 등록된 공개키로 `sig_seller`를 검증한 뒤 `sig_buyer`를 붙였습니다. 1차 운영의 194건이 모두 `sig_seller ok` → `verified True`였습니다.
5. 기타 실패: 잘못된 Python으로 단위 테스트를 돌려 `httpx`와 `a2a`를 찾지 못한 기록(`logs/attempts/final-unit-tests-wrong-environment/`), sandbox 환경 점검 실패(`logs/attempts/environment-smoke-sandbox-failure/`)도 남겨 두었습니다.

`shop_key.pem`과 `buyer_key.pem`은 처음 만든 파일을 그대로 쓰고 있습니다(재생성하지 않았으며 `.gitignore`로 제외). 시장 등록 공개키와 서명키가 같아야 하므로, 2차 운영도 같은 키로 재등록했습니다.

## 5. Challenge 1과 Challenge 2

### Challenge 1 — Shop: 가격 바꾸기

- **요구사항**: 자기 거래와 리뷰를 읽고 호가를 바꿉니다. reserve는 유지합니다. 변경 전후의 점유율과 평균 가격을 보여야 합니다.
- **문제 발견**: 1차 운영에서는 첫 호가를 모델이 정했습니다. 로그에 남은 호가는 lamp 150, bookshelf 340·350입니다. 반면 시장이 발행한 실제 주문 예산(`market-kit.log`의 `[order …]` 줄 299개)은 lamp 중앙값 105(최대 140), chair 225, bookshelf 295였습니다. lamp 150은 모든 구매자의 예산보다 높았던 셈입니다. 1차 운영의 방문 대비 판매 전환율은 56.4%였습니다.
- **구현** (`update_prices.py` → `pricing-policy.json` → `pricing.py` → `seller.py`):
  - 첫 호가 = 해당 상품의 관측 예산 중앙값을 5 단위로 반올림한 값이며, 최소 reserve+5입니다. 자기 리뷰 평균이 4.0 미만이면 5를 더 내리고, 전환율이 70%를 넘고 별점이 4.5 이상이면 5를 올립니다.
  - 입력은 대시보드 자기 행(거래·방문·매출·이익·별점), `/api/shops`의 자기 리뷰, kit 로그의 주문 예산, Market Test 로그의 이전 호가입니다. 모델은 호출하지 않습니다.
  - 결과: **lamp 105, chair 225, bookshelf 295** (reserve 100/180/260은 그대로). 실행 로그: `logs/challenge-1/update-prices-1027-180*.log`
  - Seller는 정책 파일이 있으면 첫 호가에 모델을 부르지 않고 정책 가격을 씁니다. 이후 반대 제안에 대한 판단은 기존대로 Codex가 하고, reserve 검사도 그대로 남아 있습니다. 끝난 협상은 `SELLER_TRADE_LOG`가 설정된 경우에만 `logs/challenge-1/seller-trades.jsonl`에 기록됩니다(테스트에서는 기록하지 않음).
- **테스트**: 단위 테스트 통과, 공식 Market Test 25/25 통과(로그에서 `lamp asking price: 105`, `bookshelf asking price: 295` 확인)
- **실시장 적용**: 18:10:50에 재등록했습니다. 이후 결과는 `compare_prices.py`로 계산했습니다(`logs/challenge-1/compare-*.log`).

{{C1_TABLE}}

{{C1_NOTE}}

### Challenge 2 — Buyer: 부정직한 참가자

- **요구사항**: 시장에서 만난 부정직한 가게나 구매자를 로그로 지목하고, 내 Buyer가 어떻게 그 상대를 피하게 됐는지 보여야 합니다.
- **지목 대상**: **`maple-home`** (대시보드상 `house: true`, 강사가 운영하는 가게, 별점 1.0). kit이 Buyer에게 넘긴 실제 `shops.json`에 리뷰 8건이 있었는데 전부 1점이었고, 모두 합의가보다 비싼 영수증을 지적했습니다. 예: `Agreed 300, receipt says 345.`, `Agreed 215, receipt says 245.`, `[WARNING] chair: agreed 185, receipt says 210. Receipt does not match the deal. Avoid.` 영수증에 합의보다 높은 가격을 적어 서명하는 가게입니다.
- **피한 방법**: `_review_summary`가 이 리뷰들을 무결성 경고 8건으로 세었고, 점수는 1.0×10 − 8×12 = **−86**이 됐습니다. 최고점 가게(약 +48~52)와 차이가 커서 선택될 수 없습니다. 1차 운영의 `[choose]` 237회 중 `maple-home`은 0회였습니다. 2차 운영부터는 매 주문마다 `[avoid] maple-home: 8 review(s), average 1.00, integrity warnings 8`이 kit 로그에 실시간으로 남습니다.
- **증거**: `logs/challenge-2/shops-snapshot-*.json`(kit이 쓴 실제 가게 목록 사본), `logs/challenge-2/scan-*.log`(`scan_shops.py`로 같은 점수 함수를 다시 계산한 결과와 선택 횟수), `logs/market-kit.log`의 `[avoid]` 줄
- **2차 방어선**: 리뷰가 없는 새 가게가 영수증을 부풀려도 `_review_for`가 영수증 가격과 내 마지막 제안을 비교합니다. 다르면 `receipt_mismatches`를 늘리고(−25점) 1점 리뷰를 남깁니다. kit도 영수증 가격이 예산을 넘거나 상품이 다르면 `sig_buyer`를 붙이지 않습니다. 실시장에서는 아직 불일치를 겪은 적이 없어(카운터 0), 이 경로는 단위 테스트로만 확인했습니다.
- **수정 사항**: 이전 기록(`logs/challenge-2/README.md`)에는 "부정직한 판매자를 만나지 못했다"고 적혀 있습니다. 하지만 그때는 선택된 가게의 `[choose]` 줄만 봤고, 실제로는 Buyer가 매 주문마다 `maple-home`을 점수로 걸러 내고 있었습니다.

## 6. 배포 위치와 방법

- **장소**: 개인 Windows PC의 WSL Ubuntu 24.04 한 대에서 Seller와 Buyer를 함께 실행합니다. VPS나 유료 서비스는 쓰지 않았습니다.
- **실행 명령** (저장소 루트):
  ```bash
  PATH="$HOME/.local/bin:$PATH" MARKET_OWNER="$(cat <권한 600 파일>)" \
  SELLER_TRADE_LOG="$PWD/submissions/25512082/week-07/logs/challenge-1/seller-trades.jsonl" \
  setsid nohup a2a-lab/.venv/bin/python scripts/market_kit.py join --dir submissions/25512082/week-07 \
      --market https://market.ourolabs.ai --name sohyunkim11 --tunnel > .market/kit-console.out 2>&1 &
  ```
  kit은 `run_agent.sh seller.py --port <p>`로 Seller를 띄우고, 무료 Cloudflare Quick Tunnel(`cloudflared tunnel --url`, 계정 불필요)로 공개합니다. 이어 카드 URL과 `shop_key.pem` 공개키를 시장에 등록하고, 주문을 받아 `market_buyer.py`를 실행합니다.
- **공개 엔드포인트**: 1차는 `https://engineer-chemistry-partial-prerequisite.trycloudflare.com`, 2차는 `https://destination-feelings-spears-pearl.trycloudflare.com`입니다. Quick Tunnel은 재시작할 때마다 URL이 바뀝니다. 그래도 시장에는 같은 이름과 같은 키로 재등록되므로 거래와 리뷰가 이어집니다.
- **인증과 키**: Claim Code는 권한 0600 파일에서 환경 변수로만 넘겼고, 로그·Git·이 보고서에 값이 없습니다. 개인키 두 개도 0600 권한이며 `.gitignore`의 `*.pem`으로 제외했습니다. CI의 `check_no_keys`도 통과했습니다.
- **실행 유지**: `setsid nohup`으로 Claude Code·Codex 세션과 분리해 실행하므로, 터미널을 닫아도 계속 돕니다. `monitor_market.py --interval 900`이 15분마다 대시보드 행을 `logs/operations/leaderboard.jsonl`에 추가합니다.
- **장시간 운영의 제약**: PC가 꺼지거나 절전에 들어가거나 WSL이 종료(`wsl --shutdown`, 업데이트 재부팅)되면 Seller와 Buyer가 함께 내려갑니다. Quick Tunnel은 가용성을 보장하지 않습니다. 주문마다 `codex exec`가 실행되므로 ChatGPT Codex 사용량을 계속 소비하고, 한도에 걸리면 Seller가 거절하고 Buyer는 무거래가 됩니다(§4-3과 같은 증상). `cloudflared`가 `/tmp`에만 있으면 재부팅 때 사라지므로 `~/.local/bin`으로 복사해 두었습니다.

## 7. 한계

- 운영 기간이 10월 27일 오후부터라 Round 0·1 기록이 없고, 일주일 단위의 안정성은 마감 때까지 PC를 켜 두는 것에 달려 있습니다.
- Challenge 1의 "이후" 구간은 짧고, 방문 구성이 시간대마다 달라 인과를 단정할 수 없습니다. 1차 운영에는 Seller 측 거래 로그가 없어 상품별 "이전" 평균 가격은 비교할 수 없었고, 대시보드 전체 평균으로만 비교했습니다.
- Buyer는 한 가게(`thisjonghoon`)에 집중했습니다. 선택 점수에 가격 효율(예산 대비 지불 비율)이 없어 더 싼 가게를 탐색하지 않습니다. 다음 개선 후보입니다.
