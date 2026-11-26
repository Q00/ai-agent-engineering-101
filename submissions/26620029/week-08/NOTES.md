# week-08 작업 메모

## 현재 구성 (2026-10-27)
- 가게: 지난주 Workers 샵 그대로 (https://ddolcom-shop.ddolcom.workers.dev), 시장에 이미 등록됨 (공개키 일치 확인)
- buyer: 키트 v3, `--buyer-only` (--dir submissions/26620029/week-07)
- 가게 join을 다시 하면 409 (이미 이 card URL이 등록됨) -> 가게 join은 건너뜀
- Workers 샵의 /.well-known/acp.json 은 404 (키트는 --deployed 가게에 ACP를 열어 주지 않음, week-08.html STEP 3 표와 일치)

## 키트 AcpShop 읽은 결과 (scripts/market_kit.py, class AcpShop)
Worker에 직접 만들어야 하는 것:
1. GET  /.well-known/acp.json  -> {protocol, version 2026-04-17, endpoint, capabilities, tools}
   buyer는 endpoint의 hostname이 가게 card URL의 hostname과 같아야 결제한다 (acp_pay)
2. POST {endpoint}/checkout_sessions  body {line_items:[{id,quantity}], receipt}
   - receipt의 sig_seller를 가게 공개키로 검증, line_items가 receipt와 일치, price는 int
   - 같은 task로 세션 중복 금지 (409)
   - checkout_jwt = EdDSA JWT(payload: shop, shop_url, item, price, task, currency credits, iat), 가게 키로 서명
3. GET  {endpoint}/checkout_sessions/{id}
4. POST {endpoint}/checkout_sessions/{id}/complete  body {payment_data:{order, checkout_mandate, payment_mandate}}
   -> 시장 POST /api/settle 로 전달 (order, checkout_jwt, 두 mandate), 200이면 completed
5. POST {endpoint}/checkout_sessions/{id}/cancel

## 열린 질문
- /api/settle 은 "shop" 역할 Bearer 토큰이 필요하다. 키트는 join 때 받은 토큰을 쓴다.
  Worker는 이 토큰이 없다. Worker가 SHOP_KEY_PEM 으로 서명한 join 요청으로 직접 토큰을 받을 수 있는지 확인 필요.
- Worker의 세션 저장소: 기존 Durable Object(TASKS)를 재사용할지.
