# Week 07 · 26520057 · Cheapest in Town

Agentic AI 수업의 가상 가구 시장 실험. GitHub 계정은 `danayoou`, 시장 이름은 `cheapest-in-town`이다.

- 공개 가게: https://cheapest-in-town-26520057.cheapest-in-town-worker.workers.dev
- 시장: https://market.ourolabs.ai/board
- 과정·실패·과제별 근거: [REPORT.md](REPORT.md)
- 가게 배포: [workers/README.md](workers/README.md)
- Buyer 서버 운영: [server/README.md](server/README.md)

## 실행 구조

Cloudflare Workers의 `workers/src.js`가 실제 시장 가게다. 연구실 Linux 서버의 systemd 서비스가 `market_kit.py`와 `market_buyer.py`를 실행한다. 맥북이나 SSH 연결을 유지할 필요는 없지만 서버 전원·인터넷·Claude 인증이 필요하다.

`agent.json`의 `seller_agent.py`는 기존 Python 실습용 판매자다. 거래 결과에 따라 호가를 +20/-30 조정하는 이전 전략을 재현한다. 실제 Workers 판매자는 이후 실험에서 고정가로 바뀌었다. 따라서 공식 Python 시장 검사와 Workers 공개 계약 검사를 별도로 실행하며, 두 판매자가 동일한 가격 정책이라고 주장하지 않는다.

## 현재 전략

가게는 lamp 100, chair 180, bookshelf 260 크레딧의 고정가다. 상품과 유효한 제안을 한 요청으로 보내거나 견적 뒤 `I accept`/`OK`/`I accept your price`로 구매할 수 있다. `I accept 260`처럼 가격이 명시되면 그 가격이 하한 이상인지 먼저 확인한다. 영수증은 해당 task·상품·가격에 결합된 Ed25519 서명이다. 장인·원산지 이야기는 명시적으로 가상의 상품 설정이며 실제 품질 인증이 아니다.

Buyer는 매 주문 현재 라운드 거래 점유율 상위 5개를 조회하고, 그 안에서 공개 별점과 자신의 과거 거래 별점의 평균으로 선택한다. 최저가를 먼저 제안하고, 예산 안의 명확한 역제안은 즉시 수락한다. 해석이 어려울 때 Claude Code Haiku를 호출한다. 공개 순위를 확인할 수 없으면 6위 이하로 우회하지 않고 해당 주문의 구매를 포기한다.

후기에는 거래 사실과 별도의 품질 취향·소재 콘셉트·빠른 결제 광고가 붙는다. 광고 문구는 상품을 실제 검사한 결과가 아니다. 별점 4점 상한은 철회했으며 정상 거래에는 다시 5점이 가능하다. 과거 후기를 소급 수정하지 않았다.

`review_policy.py`는 공개 피드의 최근 최대 1,000개 리뷰를 중복 제거해 기억한다. 정상 영수증을 인정하면서 문제 설명 없이 1~2점을 준 사례가 동일 계정에서 3개 이상, 관측 표본의 75% 이상이면 그 계정과 연결된 가게를 구매 후보에서 제외한다. 이는 불완전한 표본과 영문 표현에 의존하는 보수적 휴리스틱이며 악의 확정 판정이 아니다. 낮은 별점 하나·4점·원인 설명이 있는 불만은 제외 근거로 삼지 않는다.

## 재현과 검사

Python 3.10 이상과 `a2a-sdk[http-server]`, `uvicorn`, `httpx`, `cryptography`가 필요하다. 불명확한 협상에는 로그인된 Claude Code가 필요하다. 키·claim code·`.env`는 커밋하지 않는다. `market_kit.py`가 로컬 개인키를 만들며, 배포 가게에는 같은 shop 키를 Workers secret으로 설치해야 한다.

저장소 루트에서:

```bash
python scripts/market_test_week07.py submissions/26520057/week-07
python scripts/check_week07.py submissions/26520057/week-07 --github danayoou
python submissions/26520057/week-07/workers/verify.py https://cheapest-in-town-26520057.cheapest-in-town-worker.workers.dev --production
```

공식 시장 검사의 공급 목록은 전부 loopback 주소인 독립 실험 시장이다. 이 경우 외부 시장 순위를 적용하지 않고 공급된 가게·리뷰를 평가한다. 공용 주소가 하나라도 포함되면 공개 시장 상위 5개 제한을 적용한다.

검사 재실행 전 기존 로그를 별도 폴더에 복사한다. `logs/market-kit.log`는 배포 서버 로그의 제출 시점 사본이고, 이전 맥북 기록은 별도 파일로 보존한다. 실행 상태·리뷰 기억은 `.market/`, `memory.json`, `asks.json`에 저장되며 제출 소스와 구분한다.

## 남은 과제 근거

Challenge 2의 실제 부정직한 참여자 조우와 이후 회피를 입증하는 실전 로그는 아직 부족하다. 로컬 리뷰 선택 검사 통과나 가격이 비싼 가게를 포기한 일을 그 증거로 대체하지 않는다. 자세한 한계는 REPORT에 기록한다.

실전 판매 전환 문제도 조사 중이다. 공개 계약 검사는 통과하지만, 충분한 예산의 Buyer가 거래에 실패한 공개 기록이 계속 있어 해결로 표시하지 않았다.
