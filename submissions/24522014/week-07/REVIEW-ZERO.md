# 받은 후기 0건의 원인

2026-10-20, Round 1, 가게 kcwarni.

**원장에 기록된 판매 5건 모두 상대 buyer가 별점과 후기 본문을 제출하지 않았다.** 따라서 받은 후기 0건의 직접 원인은 후기 미제출이다. 내 buyer가 상대 가게에 후기를 쓰는 기능과는 별개다.

| 원장 seq | 상대 buyer | 품목 | 가격 | stars | review |
|---:|---|---|---:|---|---|
| 4541 | ggari0703 | chair | 222 | null | 빈 문자열 |
| 4994 | helenjeon101 | chair | 205 | null | 빈 문자열 |
| 5111 | BelieveMe | bookshelf | 340 | null | 빈 문자열 |
| 5299 | BelieveMe | bookshelf | 290 | null | 빈 문자열 |
| 5469 | ddolcom | chair | 250 | null | 빈 문자열 |

조사 시 `/api/board?round=1`은 deals 5, reviews 0, stars null이었다. 공개 원장 seq 1~6415를 빠짐없이 조회하고, 현재 로컬 shop 키에서 파생한 공개키가 등록된 가게 URL 3개를 추적해 같은 가게의 결과를 모았다. Round 1의 전체 결과 제출 9건도 별점·후기 본문이 모두 비어 있었다. 공개 원장의 실제 제출값까지 확인했으므로 단순 UI 표시 문제라고 볼 근거는 없다.

seller의 서명이 없어 5건 모두 sig_seller_ok와 sig_buyer_ok가 false인 점도 확인했다. seq 4541에는 one_result_per_order 실패도 있다. 이들은 별도의 검증 문제이며, 이미 제출된 후기의 표시가 차단되었다는 증거는 아니다. 해당 5건에는 애초에 후기 텍스트가 없었다. 앞서 서명 누락을 후기 0건의 원인일 수 있다고 설명했으나 실제 원장 조사 결과와 구분해 정정한다.

앞으로 내 가게가 후기를 받으려면 상대 buyer가 별점·후기 본문을 제출해야 한다. 우리 쪽 LAB 2의 seller 서명 구현은 검증 가능한 영수증과 Round 2 참여를 위해 추가로 필요하다. 서명을 추가한다고 후기 작성이 보장되거나 과거 빈 후기가 자동으로 채워지지는 않는다.

증거는 `logs/review-investigation/board-initial.json`, `shop-sale-ledger-evidence.json`, `round1-review-summary.json`, `ledger-analysis.log`에 보존했다. 조사 스냅샷 이후 새 판매·후기는 계속 추가될 수 있다.
