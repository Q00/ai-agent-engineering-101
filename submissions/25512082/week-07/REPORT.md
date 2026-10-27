# Week 07 실험 보고서

## 1. 개요 및 실행 환경

이번 실험에서는 `Atlas Shop` Seller와 `sohyunkim11` Buyer를 실제 Agent Market Round 2에 등록했습니다. 실행 환경은 Ubuntu 24.04 WSL이며, Seller의 외부 공개에는 무료 Cloudflare Quick Tunnel을 사용했습니다. 공식 `scripts/market_kit.py`가 Seller 실행, 시장 등록, Buyer 주문 수신, Seller 서명 검증, Buyer 서명 추가와 결과 제출을 담당했습니다.

실제 운영은 2026-10-27 14:46:02 KST에 시작했고 17:40:18 KST에 사용자의 종료 요청에 따라 정상 종료했습니다. 약 2시간 54분 동안의 단기 운영이며, 원래 기대할 수 있는 일주일 단위의 지속 운영은 충족하지 못했습니다. 운영 콘솔은 `logs/market-kit.log`, 시각별 대시보드 집계는 `logs/operations/leaderboard.jsonl`에 보존했습니다.

## 2. Buyer 판매자 선택 알고리즘

`market_buyer.py`는 먼저 공식 kit가 제공한 가게 목록을 읽고 각 가게의 리뷰와 이전 거래 기억을 점수화합니다. 기본 점수는 평균 별점의 10배입니다. 성공 경험은 최대 5건까지 건당 2점을 더하고, 무거래 경험은 최대 5건까지 건당 1점을 뺍니다. 낮은 별점 리뷰에서 과금·영수증 불일치를 암시하는 표현이 발견되면 건당 12점, 과거 영수증 불일치가 있으면 건당 25점을 감점합니다. 가장 높은 점수의 가게 하나를 선택하며, 선택 이유와 세부 점수를 로그에 남깁니다.

실제 운영에서는 `thisjonghoon`을 234회, `moss-and-oak`을 2회 선택했습니다. 이것은 허위 거래를 만들기 위한 고정 선택이 아니라, 당시 공개 리뷰와 누적된 정상 거래 경험으로 계산된 결과입니다.

## 3. Buyer 거래 기억의 구조와 관리 방식

기억은 `.market/runtime-state/buyer-memory.json`에 저장합니다. 최상위 `shops` 객체 아래에 가게 이름을 키로 두고 `deals`, `no_deals`, `receipt_mismatches`, `last_outcome`, `last_price`를 기록합니다. 가게 URL은 Quick Tunnel 재시작 때 달라질 수 있어 키로 쓰지 않습니다. 테스트 상태는 `.market/test-state/`에 따로 두어 실제 운영 기억을 오염시키지 않습니다. `.market/`과 `*.pem`은 `.gitignore`로 제외했습니다.

## 4. 후기 정보를 하네스에 제공한 방법

공개 리뷰 원문 전체를 Codex 하네스에 전달하지는 않았습니다. `market_buyer.py`가 리뷰의 유효 별점, 평균, 무결성 경고 수를 로컬에서 계산하고 그 결과로 가게를 먼저 선택합니다. 선택한 뒤의 가격 협상만 `buyer.py`와 `harness.py`가 담당합니다. 이 방식은 불필요한 외부 전달을 줄이면서도 리뷰가 실제 선택에 반영되게 합니다. 선택 이유는 `[choose]` 로그로 남겨 재현할 수 있습니다.

## 5. Round 0·1·2 운영 결과

### Round 0

Round 0 실제 시장 참여를 입증하는 별도 로그나 대시보드 수치를 확보하지 못했습니다. Week 06 기반 Seller·Buyer 코드와 로컬 테스트 기록만 있으므로, Round 2 수치를 Round 0 실적으로 소급하지 않습니다.

### Round 1

Round 1의 실제 등록·거래 로그도 확보하지 못했습니다. 따라서 해당 Round의 Seller·Buyer 실적은 확인 불가로 기록합니다.

### Round 2

14:46:02 KST에 Seller와 Buyer가 모두 `sohyunkim11` 이름으로 등록됐습니다. 종료 후 17:41:54 KST에 조회한 시장 집계는 Seller 101건, Buyer 194건을 기록했습니다. `logs/market-kit.log`에도 `verified True`와 `review accepted`가 함께 있는 Buyer 거래가 194건 있어 대시보드의 Buyer 거래 수와 일치합니다. `verified False` 37건은 주로 상품 reserve보다 예산이 낮아 성사될 수 없었던 주문이며 성공 거래에 포함하지 않았습니다. 네 차례의 `result refused: 502`도 시장이 인정한 거래로 세지 않았습니다.

## 6. Ed25519 서명 적용 과정과 실패·해결 사례

초기 실패는 `logs/attempts/unsigned-receipt/market-test.log`에 보존했습니다. 당시 receipt가 `{seller, item, price}` 형식이어서 `shop`, `task`, `sig_seller`가 없었고 `seller_signed_receipt`가 실패했습니다. 이후 `seller.py`에 정렬된 키, 공백 없는 UTF-8 JSON을 만드는 `canon`과 Ed25519 서명 함수 `sign_receipt`를 추가했습니다. 최종 receipt는 `{shop, item, price, task, sig_seller}` 형식입니다.

공식 kit는 등록된 Seller 공개키로 `sig_seller`를 검증하고, 주문의 item·budget·task가 맞을 때 `sig_buyer`를 추가해 시장에 제출합니다. 변조된 가격의 서명이 거부되는지는 `test_seller_signing.py`에서 별도로 확인했습니다. 최종 `test-report.json`은 Seller 서명 검증 3회를 포함한 25/25 통과를 기록합니다. 운영용 `shop_key.pem`과 `buyer_key.pem`은 재생성하지 않았고 Git에서 제외했습니다.

## 7. Challenge 1 실험 및 결과

시점별 실적과 평판 분석은 `logs/challenge-1/README.md`에 정리했습니다. 14:47:11 KST의 Seller는 거래 1건, 이익 35, Profit 47위였고, 17:41:54 KST에는 거래 101건, 이익 2,712, Profit 39위였습니다. 최종 판매 전환율은 56.74%, 평균 별점은 4.43이었습니다.

다만 운영 중 가격 결정 코드를 변경하거나 변경된 전략을 재배포하지 않았습니다. 따라서 관측된 개선을 새로운 가격 전략의 효과라고 주장하지 않으며, Challenge 1의 실시장 전후 비교는 미완료입니다. 안정적으로 거래 중인 Tunnel과 Seller를 재시작하지 않기로 한 운영 판단, 그리고 최종 종료 요청을 따른 결과입니다.

## 8. Challenge 2 실험 및 결과

구현과 증거는 `logs/challenge-2/README.md`에 정리했습니다. 낮은 별점과 과금·영수증 관련 경고 문구, 무거래 경험, 영수증 불일치를 감점하고 정상 거래 경험을 가점합니다. 단위 테스트는 나쁜 리뷰의 가게를 피했고, 공식 `buyer_reads_reviews`도 좋은 리뷰의 가게를 3/3 선택해 통과했습니다.

실제 Round 2에서는 무결성 경고나 영수증 불일치가 누적된 판매자를 만나지 못했습니다. 따라서 실제 참여자를 부정직하다고 지목하지 않으며, 실제 회피 사례가 있었다고 과장하지 않습니다. Challenge 2의 판단·회피 로직은 격리 테스트로 검증했고, 실제 운영에서는 서명 불일치 없이 194건이 검증됐습니다.

## 9. 실제 시장 배포 방법

Seller와 Buyer는 같은 WSL 환경에서 공식 `market_kit.py join`으로 실행했습니다. Seller는 로컬 포트에서 A2A v1.0 JSON-RPC를 제공하고 Cloudflare Quick Tunnel의 임시 HTTPS 주소를 통해 공개했습니다. Buyer는 kit가 발급받은 실제 주문마다 `market_buyer.py`를 실행했습니다. Claim Code는 `/tmp`의 권한 0600 파일을 통해 환경 변수로만 전달했고 로그와 Git에는 기록하지 않았습니다. Ed25519 개인키 두 개도 권한 0600으로 유지하고 제출 대상에서 제외했습니다.

## 10. Seller·Buyer 최종 리더보드 성적

최종 측정 시각은 2026-10-27 17:41:54 KST입니다.

| 구분 | 최종 결과 |
|---|---|
| Seller Profit | 39위, 이익 2,712 |
| Seller Deals | 45위, 판매 101건 |
| Seller Revenue | 46위, 매출 22,492 |
| Seller Stars | 33위, 평균 4.43, 리뷰 101개 |
| Seller 전환율 | 방문 178회 중 101건, 56.74% |
| Buyer Saved | 41위, 총 절감액 6,080 |
| Buyer Deals | 42위, 주문 231건 중 검증 거래 194건 |
| Buyer Best price | 30위, paid 0.873 |
| Buyer 성공률 | 83.98% |

이 스냅샷은 프로세스 종료 약 1분 뒤 조회되어 대시보드의 `alive`가 아직 true로 남아 있었습니다. 로컬 프로세스는 17:40:18 KST에 정상 종료됐으므로, 이 표시는 생존 확인 주기의 지연이며 제출 시점의 실제 온라인 상태는 오프라인입니다. Seller 판매 101건과 Buyer 구매 194건은 서로 다른 역할의 수치이므로 합산하지 않았습니다.

## 11. 한계 및 후속 개선 방향

가장 큰 한계는 하루에 약 2시간 54분만 운영해 일주일 규모의 안정성을 보여 주지 못한 점입니다. Round 0·1의 실제 자료가 없고, Challenge 1의 새 가격 전략도 시장에 배포하지 못했습니다. 또한 실제 시장에서는 명확한 부정직 판매자를 만나지 않아 Challenge 2의 회피 효과를 실거래로 비교하지 못했습니다.

후속 실험에서는 Seller가 자신의 완료 거래와 공개 리뷰를 구조화해 읽고, reserve를 지키면서도 할인 폭과 협상 횟수를 조절하는 두 전략을 같은 시간 동안 비교할 계획입니다. Buyer 기억에는 상품별 가격과 예산 대비 지불 비율을 더해, 신뢰도뿐 아니라 가격 효율도 장기적으로 학습하게 만들 수 있습니다. 장기 배포가 필요하다면 절전과 네트워크 단절의 영향을 받는 WSL 대신 지속 실행 환경을 준비하는 편이 안전합니다.
