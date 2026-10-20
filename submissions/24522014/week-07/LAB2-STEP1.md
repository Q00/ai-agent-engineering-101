# LAB 2 Step 1: 영수증 서명 함수

2026-10-20, 학번 24522014 (kcwarni).

앞서 사용자가 요청한 가게 개선 작업에서 Step 1의 코드를 이미 구현했고, 이번 요청에서는 해당 구현과 실제 운영 적용을 확인했다.

- `seller.py`의 `canon(data)`: 키 정렬, 공백 없는 구분자, ensure_ascii=False, UTF-8로 서명할 JSON 바이트를 만든다.
- `sign_receipt(receipt, key_path=None)`: `shop`, `item`, `price`, `task` 네 필드만 서명한다. price는 양의 정수이며 bool·float를 허용하지 않는다.
- 기존 week-07 디렉터리의 `shop_key.pem`을 읽어 Ed25519 서명을 만들고 base64 문자열 `sig_seller`를 추가해 반환한다. 원본 입력 사전은 수정하지 않는다.
- 키는 새로 생성하거나 교체하지 않았다. 비밀키는 Git에 포함하지 않는다.

검증 결과:

1. 정규 UTF-8 JSON에 대한 서명 검증 성공. 가격·품목·task·가게명 변조 시 검증 실패 확인.
2. seller 검사 6개 통과. 키 사용 실패 시 unsigned 영수증이나 판매 완료를 내보내지 않는 것도 확인.
3. 전체 공식 market test 25/25 통과. seller_signed_receipt 3회 모두 통과.
4. 운영 키트를 재시작한 뒤 공개 가게에서 발급한 lamp 120 영수증을 시장에 등록된 공개키로 검증해 성공.

현재 운영 주소는 https://savings-papua-interim-summit.trycloudflare.com 이다. 공개 가게에 대한 직접 협상은 배포 확인을 위한 진단이며 시장 주문·거래 실적·후기로 제출하지 않았다. 증거는 `logs/shop-improvement/live-verification.json`에 있다.

앞선 개선 요청 범위에서 Step 2의 영수증 발급 연결과 테스트·키트 재시작까지 함께 진행했다. 함수 두 개가 존재하기만 하는 상태와 실제 서버에 적용된 상태를 구분해 확인했다. 이후 단계에서 같은 코드를 중복으로 붙여 넣을 필요는 없다.
