# Week 07 — 에이전트 시장: 명품 가게와 기억하는 buyer

starter(claude -p haiku)에서 출발해 영수증 서명과 `market_buyer`를 붙이고, 가게와 buyer를 연구실 서버에서 `bjeon01`(GitHub `@BJEon01`)로 시장에 계속 올려 두었다. 가게는 Round 1 도중 데이터 기반 가격, 이어서 명품(boutique) 전략으로 바꿨다.

## 요약

| 항목 | 결과 |
|---|---|
| market test (`scripts/market_test_week07.py`) | **25/25 PASS** |
| 시장 (`check_week07.py`, 10-26) | 가게 `bjeon01` 실행 중, Round 2 인정 거래 346건. buyer `bjeon01` 실행 중, Round 2 인정 거래 2,436건 |
| 가게 R2 | 별점 **4.77** (후기 346개), 평균 판매가가 시장 평균보다 품목마다 약 11% 높음, 점유율 0.1% |
| 도전 1 | 시작가를 내 거래·후기에서 정하도록 바꾼 뒤 명품 전략으로. 명품가 거래는 시장 평균보다 약 25% 비싸게 팔림 |
| 도전 2 | 강사 가게 3곳(maple-home, elm-street, willow-goods)의 부풀린 영수증과 서명 없는 영수증을 잡았고, 그 뒤로 한 번도 다시 거래하지 않음 |

---

## 1. buyer가 가게를 고르는 방법과 기억의 형식

| 단계 | 하는 일 (`market_buyer.py`) |
|---|---|
| 1. 서명 안 하는 가게 빼기 (코드) | 기억에서 `signed: false`였던 가게와, Round 0 키트 로그(`[sign] receipt has no sig_seller`)에 나온 가게를 후보에서 뺀다. 빼면 `[avoid]` 줄을 남긴다 |
| 2. 고르기 (harness) | 가게마다 한 줄 요약을 보여 주고 `{"shop", "reason"}`을 받는다. 이유는 `[choose]` 줄로 키트 로그에 남는다. 목록에 없는 이름이면 평균 별점이 가장 높은 가게(후기 없으면 3점으로 침) |
| 3. 흥정 (harness) | week-06과 같다. 예산을 넘는 제안은 코드가 예산으로 자른다 |
| 4. 영수증 검사 (코드) | 영수증 가격 = 내가 마지막에 낸 금액(합의가)인지, 품목, task id, 예산 이내인지 확인 |
| 5. 후기 (harness + 코드) | harness가 별점과 한 줄 후기를 쓴다. 영수증이 합의와 다르면 코드가 별점 1과 사실 문장으로 고정 |
| 6. 기억 (코드) | 거래마다 한 줄을 `buyer_memory.jsonl`에 추가 |
| 불가능한 주문 (코드) | 예산이 시장 하한가(lamp 100, chair 180, bookshelf 260)보다 낮으면 첫 메시지만 보내고 CancelTask |

**기억 형식** (`buyer_memory.jsonl`, 한 줄에 거래 하나. 가게는 이름으로 기억한다. URL은 재시작마다 바뀌기 때문이다)

```json
{"time": "2026-10-22 04:23:20", "shop": "elm-street", "item": "bookshelf", "budget": 360, "outcome": "deal",
 "agreed": 280, "receipt_price": 320.0, "receipt_ok": false, "signed": true, "stars": 1}
```

10-26 기준 3,674건이 쌓였다.

## 2. 후기를 harness에 보여 주는 방법

`shops.json`에는 가게마다 후기가 최대 10개씩 있고, 가게는 30~250곳이다. 전부 넣지 않고 **가게당 한 줄**로 줄인다(`shop_line()`). week-06 도전 2에서 협상마다 한 줄 요약을 쓴 방법과 같다.

```
<이름> | <후기 수> reviews, avg stars <평균> | <최신 후기 3개, 각 90자> | <내 기억: 거래 수, 성사 수, 최근 가격 3개, 영수증 불일치 수>
```

프롬프트는 "일부 가게는 합의 뒤 더 비싼 영수증을 낸다", "서명 없는 영수증은 이 시장에서 인정되지 않는다", "후기 없는 가게가 나쁜 후기 가게보다 낫다"를 알려 주고, 가게 하나를 고르게 한다. market test의 `buyer_reads_reviews`는 3번 모두 좋은 후기 가게를 골랐다.

## 3. Round 0, 1, 2에서 일어난 일

| 라운드 | 내 가게 (dashboard `/api/stats`) | 내 buyer | 시장 평균가 lamp / chair / bookshelf |
|---|---|---|---|
| R0 | 13건, 점유율 1.0%, 후기 없음 | week-06 buyer가 무작위 가게와 거래. 서명 없는 영수증을 받은 가게 5곳이 기록됨 | 113.7 / 205.3 / 297.6 |
| R1 | 21건, 점유율 1.2%, 후기 29개, **별점 3.66** | `market_buyer`가 후기와 기억으로 선택. maple-home의 부풀린 영수증을 잡음 | 108.1 / 194.7 / 282.1 |
| R2 | 346건, 점유율 0.1%, 후기 346개, **별점 4.77**, 매출 66,350 | 인정 거래 2,436건. elm-street의 부풀린 영수증, willow-goods의 서명 없는 영수증을 잡음 | 102.1 / 182.8 / 265.1 |

- **R1 별점 3.66의 원인은 가격이 아니었다.** 전략을 바꾸느라 키트를 여러 번 재시작했고, 그 사이 손님이 받은 오류가 그대로 후기가 됐다: `Shop did not answer: error: Expecting value: line 1 column 1 (char 0).`, `Server error '502 Bad Gateway'`. 같은 시기의 정상 거래 후기는 4~5점이었다(`I paid 104 for the lamp after its ask went from 126 to 112 to 106. The receipt shows exactly the agreed price and is signed by the shop.`). R2는 서명된 거래의 후기만 보여서, 이런 오류 후기가 빠지고 4.77이 됐다.
- **R2 점유율 0.1%:** R2 시장은 거래 26만 건으로 커졌는데, 내 가게는 한 턴마다 `claude -p`를 한 번 부르는 속도에 묶여 있었다. 방문 924회 중 346회가 성사됐다.
- **R2 첫 주문:** `12:31:12 [buyer] [choose] corner-store: It has 10 reviews averaging 4.6 stars, all reporting receipts that matched the deal ...` → `12:31:26 [market] ledger #9825: bookshelf at 300, stars 4, verified True, review accepted`.

## 4. 서명을 붙이며 실패한 것과 고친 것

| # | 실패 | 로그 / 근거 | 고친 것 |
|---|---|---|---|
| 1 | 서명 자체는 첫 market test에서 3번 모두 통과했다 (`PASS seller_signed_receipt #1: sig_seller verifies with shop_key.pem`). price는 처음부터 `int(price)`로 서명했다 | 커밋 `b0b320e` | – |
| 2 | 같은 실행에서 buyer 쪽 6개가 `no JSON result line at the end of the output`로 실패했다(19/25). 원인은 서명이 아니었다. 테스트용 기억 경로를 상대경로로 줬고, buyer가 결과 줄을 내기 전에 `FileNotFoundError: ... .market/test-memory.jsonl`로 죽었다 | 커밋 `b0b320e` → `2300bf6` | 절대경로 사용, 기억 폴더를 코드가 만들게 함 → 25/25 |
| 3 | 서명은 맞아도 시장이 인정하지 않는 영수증이 있었다. maple-home은 합의 100에 영수증 115를 냈다(예산 105). 키트가 `sig_buyer`를 붙이지 않았고 시장은 `verified False (failed: price_within_budget, sig_buyer_ok)`로 기록했다 | `12:06:43 [market] ledger #7321` | buyer는 이 가게를 기억에 `receipt_ok: false`로 남김 (도전 2) |
| 4 | 반대로 elm-street는 합의 280에 영수증 320을 냈는데, 예산 360 안이고 서명도 맞아서 시장은 `verified True`로 받았다. 시장이 확인하는 것은 서명, 주문, 예산이지 합의가가 아니다. 합의가는 buyer만 안다 | `04:23:21 [market] ledger #220860: bookshelf at 320, stars 1, verified True` | buyer의 영수증 검사가 별점 1과 사실 후기를 남기는 것이 유일한 방어였다 |
| 5 | 서명 외 실패: 명품 전략의 "시간당 5개" 재고가 명품 이전 판매까지 세서, 12시대에 모든 품목이 품절로 나왔다. 손님을 로그도 없이 돌려보냈다 | 커밋 `2a64833` | 명품 이후 거래만 세고 품절을 로그에 남김. 이후 실제 수량은 무제한으로 바꿈 (`c94fb46`) |

## 5. 도전 1과 2

### 5.1 도전 1: 내 거래와 후기로 가격 바꾸기 (`logs/challenge-1/summary.txt`)

| 단계 | 언제 (10-20) | 정책 |
|---|---|---|
| P0 | ~11:55 | starter: harness가 자유롭게 부름 |
| P1 | 11:56~12:16 | 시작가 = 하한가 × (1 + 최근 성사가의 평균 마크업 + 0.10). 성사율 50% 미만이거나 후기에 가격 불만이 2개 이상이면 낮춤. 3% 차이면 수락 |
| P2 | 12:16~ | 명품: 시작가 +40~55%(최근 성사율이 높을수록 높게), 자체 최저가 115 / 210 / 300, 눈에 보이게 양보. 12:43부터 품목별 매시간 선착순 3명 런칭가(최저가 = 공식 하한가), 12:50부터 하루 10장 번호·서명된 인증서 |

| 단계 | 협상 | 성사 | 성사율 | 평균가 lamp / chair / bookshelf |
|---|---|---|---|---|
| P0 | – | 15 | – | 119.8 / 205.2 / 269.5 |
| P1 | 27 | 19 | 70% | 104.8 / 187.6 / 280.6 |
| P2 전체 | 677 | 346 | 51% | 113.6 / 202.8 / 293.1 |
| P2 런칭가 | | 254 | | 107.0 / 195.9 / 281.7 |
| P2 명품가 | | 92 | | **127.7 / 226.3 / 330.0** |

| 비교 (R2) | lamp | chair | bookshelf |
|---|---|---|---|
| 시장 평균가 | 102.1 | 182.8 | 265.1 |
| 내 가게 평균가 | 113.6 (+11%) | 202.8 (+11%) | 293.1 (+11%) |
| 내 가게 명품가 거래 | 127.7 (+25%) | 226.3 (+24%) | 330.0 (+24%) |

명품 전략으로 같은 물건을 시장보다 비싸게 팔면서 별점은 3.66(R1)에서 4.77(R2)로 올랐다. 대가는 성사율(70% → 51%)과 점유율이었다. 혜택이 결과를 좌우하기도 했다. 손님이 많아지자 매시간 런칭가 3자리가 금방 차서 성사 346건 중 254건이 런칭가였다. 손님을 끄는 미끼가 판매의 주력이 된 셈이다. 희소성 문구는 모두 실제로 지키는 규칙(런칭가 자리 수, 인증서 번호, 제안 6번 한도)만 썼다.

### 5.2 도전 2: 정직하지 않은 참가자 (`logs/challenge-2/summary.txt`)

| 가게 | 누구 | 한 일 | 걸린 때 | 그 뒤 거래 |
|---|---|---|---|---|
| maple-home | 강사(house) | 합의 100, 영수증 115 (lamp) | 10-20 12:06 | 0 |
| elm-street | 강사(house) | 합의 280, 영수증 320 (bookshelf) | 10-22 04:23 | 0 |
| willow-goods | 강사(house) | 서명 없는 영수증 | 10-21 14:27 | 0 |
| ggari-shop | 학생 | 합의 110, 영수증 103 | 10-20 12:23 | 0 |
| moss-and-oak | 학생 | 합의 280, 영수증 272 | 10-22 05:10 | 0 |

- **피하게 된 경로:** 영수증 검사 → 기억에 `receipt_ok: false` → 다음 선택 때 그 가게 줄에 `1 receipts differed from the agreed price`가 붙음 → harness가 고르지 않음. 서명 없는 가게는 코드가 후보에서 아예 뺀다.
- **maple-home 장면** (`logs/market-kit.log`):
  ```
  12:06:37 [buyer] -> '100'  <- TASK_STATE_COMPLETED | Deal: lamp at 100.
  12:06:37 [buyer] [receipt] agreed 100, receipt says 115.0; receipt price 115.0 is above the budget 105
  12:06:42 [buyer] [harness] {"stars": 1, "review": "I agreed to buy a lamp for 100 after negotiating down from the asking price of 130, but the receipt charged 115, which is above my 105 budget."}
  ```
- **한계 1. 싸게 나온 영수증까지 벌줬다.** ggari-shop과 moss-and-oak은 합의보다 낮은 영수증을 냈다. 내 검사는 "다르면 불일치"라서 별점 1을 줬다. buyer에게 손해가 아니므로 과한 판정이다.
- **한계 2. 기억이 낡는다.** Round 0에 서명하지 않았던 학생 가게(thisjonghoon, kcwarni, psyoung82, mjforge)는 나중에 서명을 붙여 R2에서 수천 건씩 인정받았다. 내 buyer는 끝까지 이들을 피했다(`[avoid]` 4,199줄). 정직하지 않은 상대를 피하는 장치가 고친 상대를 다시 믿는 경로는 갖고 있지 않았다.

## 6. 배포: 어디서, 어떻게

| 항목 | 내용 |
|---|---|
| 장소 | 연구실 Linux 서버(daintlabK). 상시 켜져 있음 |
| 실행 | tmux 세션 `market`에서 `market_kit.py join --dir submissions/26512072/week-07 --market https://market.ourolabs.ai --name bjeon01 --tunnel`. claim code는 환경변수 `MARKET_OWNER`로만 넘김 |
| 가게 공개 주소 | 키트가 여는 cloudflared quick tunnel (가게와 buyer가 같은 서버라 `--deployed` 대신 `--tunnel`) |
| harness | `claude -p --model haiku`. 서버에는 CLI가 따로 없어 VS Code 확장에 들어 있는 Claude Code 바이너리를 `~/.local/bin/claude` 래퍼로 실행 |
| Python | python 3.12 venv (`a2a-sdk` 1.2.3, `cryptography`, `httpx`) |
| 키 | `shop_key.pem`, `buyer_key.pem`은 서버에만 있고 `.gitignore`로 커밋 제외 |
| 장애 | 10-22 07:58경 키트가 주문 요청에서 `httpx.ReadTimeout`으로 죽었고, 10-26 02:05에 발견해 다시 접속했다. 약 4일 동안 시장에서 빠져 있었다 |
| 재발 방지 | 키트를 `while true; do ... join ...; sleep 10; done`으로 감싸 죽으면 10초 뒤 같은 이름과 키로 다시 들어가게 했다. 재접속은 `logs/kit-restarts.log`에 남는다 |

재현:

```bash
pip install "a2a-sdk[http-server]" uvicorn httpx cryptography
python scripts/market_test_week07.py submissions/26512072/week-07
MARKET_OWNER=<claim code> python scripts/market_kit.py join --dir submissions/26512072/week-07 \
    --market https://market.ourolabs.ai --name bjeon01 --tunnel
python submissions/26512072/week-07/challenges.py      # logs/challenge-1/, logs/challenge-2/
```
