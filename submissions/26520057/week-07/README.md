# Week 07 · 26520057 · Cheapest in Town

Agentic AI 수업의 가상 가구 시장 실험. GitHub 계정은 `danayoou`, 시장 이름은 `cheapest-in-town`이다.

- 공개 가게(전환 시점): https://c9908575343ae8.lhr.life — 재연결 후 현재 주소는 시장 카드 또는 서버 `.market/gpt-public-url` 확인
- 시장: https://market.ourolabs.ai/board
- 과정·실패·과제별 근거: [REPORT.md](REPORT.md)
- 현재 가게·Buyer 배포: [server/README.md](server/README.md)
- 이전 Workers 배포: [workers/README.md](workers/README.md)
- Buyer 서버 운영: [server/README.md](server/README.md)

## 실행 구조

연구실 Linux 서버의 `market-gpt-shop` 서비스가 Node.js 가게를 127.0.0.1:8900에서 실행하며 localhost.run SSH 터널이 공개 HTTPS로 연결한다. `market-buyer` 서비스가 `market_kit.py`와 `market_buyer.py`를 실행한다. 가게 판단은 GPT-5.4 mini API, Buyer의 모호한 협상은 기존 Claude Code다. 맥북이나 SSH 연결을 유지할 필요는 없지만 서버 전원·인터넷·Claude 인증이 필요하다.

`agent.json`의 `seller_agent.py`는 기존 Python 실습용 판매자다. 거래 결과에 따라 호가를 +20/-30 조정하는 이전 전략을 재현한다. 실제 판매자는 이후 고정가 Workers를 거쳐 GPT 연구실 서버로 이전했다. 따라서 공식 Python 시장 검사와 배포 가게 공개 계약 검사를 별도로 실행하며, 두 판매자가 동일한 가격 정책이라고 주장하지 않는다.

## 현재 전략

판매자의 모든 구매 메시지는 GPT-5.4 mini가 수락·역제안·질문·거절로 분류하고, 코드가 명시 가격·최저가·견적 존재·task 상태를 검증한 뒤 서명한다. 규칙으로 바로 구매를 승인하는 경로는 없다. AI 제한시간은 5초이며 오류·시간초과·잘못된 출력에는 결제하지 않고 가격을 재안내한다. OpenAI API는 사용량별 과금이며 키는 연구실 서버의 비공개 환경파일에 보관한다. Cloudflare AI 일일 무료 한도는 사용하지 않는다.

가게는 lamp 100, chair 180, bookshelf 260 크레딧의 고정가다. 상품과 유효한 제안을 한 요청으로 보내거나 견적 뒤 `I accept`/`OK`/`I accept your price`로 구매할 수 있다. `I accept 260`처럼 가격이 명시되면 그 가격이 하한 이상인지 먼저 확인한다. 영수증은 해당 task·상품·가격에 결합된 Ed25519 서명이다. 장인·원산지 이야기는 명시적으로 가상의 상품 설정이며 실제 품질 인증이 아니다.

Buyer는 매 주문 현재 라운드에서 우리 가게보다 거래 점유율이 엄격히 높은 운영 중 가게들을 조회하고, kit가 제공한 후보와 겹치는 가게 중 하나를 동일 확률로 무작위 선택한다. 같은 점유율이나 낮은 점유율의 가게는 제외한다. 무작위 순서로 후보를 확인하되 가게별 최신 후기 10개 중 작성자 계정이 danayoou인 후기가 5개 이상이면 주문하지 않는다. 4개 이하가 되면 다시 방문할 수 있으며 후기 조회 실패 시 해당 가게를 건너뛴다. 최저가를 먼저 제안하고, 예산 안의 명확한 역제안은 즉시 수락한다. 해석이 어려울 때 Claude Code Haiku를 호출한다. 우리 가게의 현재 점유율을 확인할 수 없거나 적격 후보가 없으면 해당 주문을 건너뛴다.

후기는 짧은 거래 결과에 사용자가 선택한 가상 시장 광고를 붙인다. 예: `Deal at 262, receipt matched, but the quality didn't impress me. For premium materials and fast checkout, try the store 'Cheapest in Town'.` 품질 문구는 실제 제품 검사 결과가 아닌 실험용 광고이며 가격과 영수증 일치 여부는 실제 거래에 따른다. 현재 사용자 선택에 따라 새 후기 별점은 최대 3점이다. 거래 오류의 기존 1·2점은 유지한다. 이는 정상 거래에도 적용되는 실험 정책이며 거래 품질의 객관적 평가라고 주장하지 않는다. 과거 후기를 소급 수정하지 않았다.

악의적 리뷰 작성자와 연결된 가게를 제외하던 `review_policy.py` 필터는 해제했다. 파일과 과거 실험 기록만 보존한다. 실제 시장의 후보 선택에는 별점이나 과거 거래 점수 가중치를 주지 않는다.

## 재현과 검사

Python 3.10 이상과 `a2a-sdk[http-server]`, `uvicorn`, `httpx`, `cryptography`가 필요하다. Buyer의 불명확한 협상에는 로그인된 Claude Code가 필요하고 GPT 판매자에는 OpenAI API 키가 필요하다. 키·claim code·`.env`는 커밋하지 않는다. `market_kit.py`가 로컬 개인키를 만들며, 연구실 가게에는 같은 shop 키를 유지해야 한다.

저장소 루트에서:

```bash
python scripts/market_test_week07.py submissions/26520057/week-07
python scripts/check_week07.py submissions/26520057/week-07 --github danayoou
python submissions/26520057/week-07/workers/verify.py https://c9908575343ae8.lhr.life --production
```

공식 시장 검사의 공급 목록은 전부 loopback 주소인 독립 실험 시장이다. 이 경우 외부 시장 순위를 적용하지 않고 공급된 가게·리뷰를 평가한다. 공용 주소가 하나라도 포함되면 현재 공개 시장의 우리보다 높은 점유율 후보와 최신 후기 중복 제한을 적용한다.

검사 재실행 전 기존 로그를 별도 폴더에 복사한다. `logs/market-kit.log`는 배포 서버 로그의 제출 시점 사본이고, 이전 맥북 기록은 별도 파일로 보존한다. 실행 상태·리뷰 기억은 `.market/`, `memory.json`, `asks.json`에 저장되며 제출 소스와 구분한다.

## Buyer 전략 변경: 회피 테스트에서 광고 노출로

회피 기능을 시험하지 못해서 꺼둔 것이 아니다. 초기에는 리뷰와 거래 기억을 이용해 신뢰할 만한 가게를 선택했고, 이후 우리 가게의 매출 확대를 목표로 Buyer의 역할을 광고 노출 채널로 바꾸면서 공개 시장의 회피 정책을 의도적으로 해제했다. 아래는 확인 가능한 테스트와 변경 기록이다.

| 단계 | 판단과 변경 | 근거 |
| --- | --- | --- |
| 리뷰 기반 선택 검증 | 공식 `buyer_reads_reviews`에서 정상 리뷰의 Kit Shop South를 3/3 선택했다. 2·3회 로그에는 Kit Shop North의 과다 청구·가격 불일치 후기를 피한 이유가 명시돼 있다. | [1회](logs/pre-submission-tests/buyer-under-test-buyer_reads_reviews-1.log), [2회](logs/pre-submission-tests/buyer-under-test-buyer_reads_reviews-2.log), [3회](logs/pre-submission-tests/buyer-under-test-buyer_reads_reviews-3.log) |
| 선택 속도 개선 | 느린 모델 선택을 점수 기반 선택으로 바꾼 뒤에도 나쁜 리뷰 가게를 피하는 검사를 유지했다. | 커밋 `fb95b05` |
| 리뷰 작성자 필터 실험 | 정상 영수증을 인정하면서 설명 없이 낮은 평점을 반복한 작성자와 연결된 가게를 제외하는 휴리스틱을 추가했다. 이는 부정직함의 확정 판정은 아니다. | 커밋 `be99efc`, `review_policy.py` |
| 광고 노출 우선으로 전환 | 회피로 구매 후보를 좁히는 대신 상위 가게 방문 후 광고를 노출하려고 필터를 해제하고 상위 3곳을 대상으로 삼았다. | 커밋 `42eb24f`, [정책 검사](logs/buyer-top3-policy.log) |
| 노출 범위와 반복 구매 조정 | 우리보다 높은 점유율의 가게를 무작위로 방문하되 최신 후기 10개 중 우리 후기가 5개 이상이면 건너뛰도록 했다. 광고의 분산과 경쟁 가게에 대한 과도한 구매 억제가 목적이다. | 커밋 `4e4fc71`, `9174d62` |

**테스트와 실전의 구분:** 위 로그는 제어된 테스트에서 리뷰를 근거로 회피할 수 있음을 보여준다. 실제 공개 시장에서 직접 부정직한 거래를 겪고 이후 회피했다는 별도 증거로 확대 해석하지 않는다. Grand Furniture에서 예산보다 높은 가격을 보고 포기한 관측도 부정직함의 증거로 쓰지 않는다. 현재 공개 시장에서 회피하지 않는 것은 광고 전략에 따른 선택이며, 독립 로컬 검사에서는 리뷰 기반 선택을 재현한다. 광고가 실제 유입·매출 증가를 일으켰다는 인과 효과는 아직 입증하지 않았다.

이 실험 범위와 전략 변경을 공개한 상태로 리뷰를 요청한다. 과거 `logs/challenge-2/evidence-limit.txt`는 당시 판단 기록으로 보존하며, 현재 PR 상태는 그 기록의 Draft 유지 문구와 다르다.

판매 중단은 Cloudflare AI 한도 소진을 확인한 뒤 연구실 GPT 서버로 이전해 실제 거래 659→660건 회복을 확인했다. 근거는 [첫 회복 거래](logs/server/gpt-first-market-sale.json)이며, 모든 방문 이탈이 해결됐다는 뜻은 아니다.

### 1시간 재방문 혜택
이전 방문의 task ID를 새 메시지의 `metadata.returnTaskId`로 보내면 최초 방문 시각부터 1시간 이내인지 저장 기록으로 확인한다. 구매자 계정 인증이 아닌 방문 코드 소지 확인 방식이다. 최소가 이상 제안은 기존처럼 고정 최소가로 결제하므로 희망가보다 더 받지 않는다. 최소가 미만을 제안한 재방문 손님에게는 최소가 + Premium AS 디지털 지원을 제안하고, 동의 후 결제한다. AS는 품목별 관리 안내와 GetTask를 통한 원본 서명 영수증 재조회 안내를 별도 artifact로 실제 제공한다. 실물 수리·교환·환불은 제공한다고 주장하지 않는다. 시장 공통 강제 규정은 확인하지 못했으므로 가게 자체 최저가로 설명한다.

검증: `node submissions/26520057/week-07/workers/return_visit.test.js` (모의 AI, 실제 유료 호출 없음). 1시간 경계·만료·잘못된 코드·최소가 미만 보류·동의 후 결제·AS 첨부/조회·신규 고객 제외·고가 제안 시 추가 청구 방지를 확인했다.
