# Week 07 myeonggi

구매자는 예산 안에서 가능한 정상 구매를 성사시키고 판매자는 하한가를 지키면서 가성비와 체결률을 우선함
이름은 Agent Card와 시장 등록 닉네임 모두 `myeonggi` 사용함

저장소 루트에서 실행함

```bash
source .venv/bin/activate
export PATH="$HOME/.local/bin:$PATH"
python -m pip install -r submissions/26510358/week-07/requirements.txt
python scripts/market_test_week07.py submissions/26510358/week-07
python scripts/check_week07.py submissions/26510358/week-07
```

시장 재참가는 `/join`에서 받은 본인 claim code를 `MARKET_OWNER` 환경변수에 설정한 뒤 실행함
이미 실행 중인 키트와 중복 실행하지 않음

```bash
python scripts/market_kit.py join \
  --dir submissions/26510358/week-07 \
  --market https://market.ourolabs.ai \
  --name myeonggi \
  --tunnel
```

실시간 로그 확인

```bash
tail -n 100 -F submissions/26510358/week-07/logs/market-kit.log
```

- `seller.py`는 실제 모델이 가격과 판매 여부를 정하고 코드는 하한가와 정수 가격을 검사함
- `buyer.py`는 실제 모델이 구매를 결정하고 코드는 예산·품목·합의 가격·task ID·중복 구매를 검사함
- `market_buyer.py`는 리뷰와 `.market/buyer-memory.json`을 읽어 상점을 고름
- `receipts.py`는 `{shop,item,price,task}`를 정규화해 Ed25519 서명함
- `inference_pool.py`는 기본 판매자 2개·구매자 1개 추론 슬롯을 사용해 판매 요청 폭주가 구매자를 막지 않게 함
- 명확한 과제 프로토콜 가격 문장은 추가 추론 없이 읽고 모호한 문장은 실제 관찰 하네스로 확인함
- 리뷰 조작 지시와 모순된 영수증은 기억하고 회피함
- 판매자 서명의 등록 키 검증과 구매자 서명 추가는 공식 시장 키트가 담당함
- 정책은 현재 체결 우선 고정 기준임 이전 합성 bandit 정책은 `policies/week06-synthetic-reference.json`에 보존했으며 실거래로 학습한 RL이라고 주장하지 않음

시장 라운드는 운영자가 정함 Round 1에는 리뷰·기억을 사용하며 Round 2에는 같은 코드에 영수증 서명 검증이 적용됨
개인키 `*.pem`, 실행 상태 `.market/`, 대량의 호출별 상세 로그 `logs/runtime/`는 Git에서 제외함
공식 검증 로그와 과제 증거는 `logs/`에 보존함

판매자·공통 코드·`agent.json`을 바꾸면 키트를 재시작함
같은 닉네임과 키를 사용해야 이후 거래가 같은 이력에 쌓임
`SUNGMYEONGGI`, `gacha-shop`의 이전 거래는 기존 이름에 남고 `myeonggi` 이력은 새로 집계됨
현재 임시 공개 주소와 로컬 PID는 `.market/session.json`에서 확인함

## 프롬프트 수정과 등록 이름 유지

`harness.py`의 여러 줄 프롬프트는 `textwrap.dedent`로 소스 들여쓰기를 제거함
역할별 프롬프트 문구는 유지하며 현재 추론 강도는 `medium`임
`agent.json`의 설명도 같은 설정으로 맞춤

시장 등록 이름은 shop과 buyer 모두 `myeonggi`로 유지함
기존 키트를 종료한 뒤 저장소 루트에서 다음 명령으로 다시 참가함

```bash
bash submissions/26510358/week-07/join_market.sh
```

이 스크립트는 등록 이름을 고정하고 기존 `shop_key.pem`과 `buyer_key.pem`이 없으면 실행을 중단함
상점의 표시 이름은 `seller.py` 설정의 `name`이며 등록 닉네임과 별개임
표시 이름을 바꿀 때도 참가 명령의 `--name`과 두 개인키는 유지함
buyer 이름은 키트의 `--name`을 사용하므로 현재 키트에는 별도 표시 이름 설정이 없음
이전 닉네임의 프로필은 마켓에 오프라인 기록으로 남으며 현재 키트에는 삭제·병합 기능이 없음

현재 실행 방식은 Cloudflare Quick Tunnel로 로컬 Python 서버를 공개하는 방식임
Cloudflare Workers에 코드를 업로드한 상태가 아니며 로컬 키트와 터널이 실행 중이어야 함
재시작하면 공개 터널 주소가 바뀌지만 같은 이름과 키로 기존 등록을 갱신함

`system_prompt`의 실행 중인 역할은 `seller`, `buyer`, `shop_selector`, `budget_allocator`, `quote_observer`임
새로 작성한 `review`, `memory`/`memory_reputation`, `shop_profile` 프롬프트는 아직 호출 경로와 출력 스키마에 연결되지 않음
현재 리뷰와 기억은 `market_buyer.py`와 `market_memory.py`의 코드가 처리하고 공개 소개는 `seller.py`의 Agent Card 설정으로 생성함
쿠폰 발급·사용, 추천인 보상, 적립 수량, 다품목 주문도 프롬프트 문구만으로 실행 기능이 추가되지는 않음
판매자 프롬프트에 언급한 공개 `message` 필드는 현재 판매자 출력 스키마에 없으며 공개 응답은 기존 판매자 코드가 생성함
따라서 프롬프트 반영 확인과 위 기능의 구현 완료 여부는 별도로 확인해야 함

board는 공개 Agent Card와 거래 집계를 표시하며 비공개 시스템 프롬프트를 표시하지 않음
새 프롬프트는 재시작한 판매자와 이후 실행되는 구매자의 판단부터 적용됨

2026-10-20 22:34 KST에 동일 이름·키로 재등록한 뒤 공개 Agent Card의 HTTP 200과 shop·buyer 온라인 상태를 확인함
기존 세 등록 이름의 집합은 유지됐고 새 프로필이 추가되지 않았으며 board의 갱신 주기는 4초임
22:39 KST에 판매자·구매자·상점 선택·견적 해석의 실제 입력이 수정된 프롬프트와 일치하고 `medium`을 사용하는 것을 확인함
시장 역할 검사 14개와 견적 정규화 검사 24개가 통과했으며 확인 결과는 `logs/prompt-update-verification.json`에 보존함

## Ssadassa Shop 표시 이름

상점의 기본 Agent Card 이름은 `seller.py`에서 `Ssadassa Shop`으로 설정함
마켓 등록 ID와 buyer 이름은 계속 `myeonggi`이며 `join_market.sh`의 `--name`과 기존 개인키를 유지함
board는 상점 Agent Card 이름을 표시하므로 등록 프로필을 추가하지 않고 상점 이름만 변경할 수 있음

이전 `SUNGMYEONGGI`, `gacha-shop`의 삭제는 공개 `/join` 화면, 키트와 안내 문서에서 지원 경로를 확인하지 못함
현재 마켓 웹 조회의 `/api/me`는 `login: null`이므로 소유자 웹 로그인도 연결되지 않은 상태임
이전 기록은 삭제하지 않았으며 마켓 운영자가 제공하는 삭제 기능이나 관리 권한을 통한 정리가 필요함

2026-10-20 22:50 KST에 board와 공개 Agent Card 모두 `Ssadassa Shop`을 표시함을 확인함
등록 ID와 buyer 이름은 `myeonggi`, 기존 키와 프로필 수는 그대로이며 shop·buyer 모두 온라인임
로컬 실제 서버 검증과 공개 반영 결과는 `logs/shop-rename-local-check.json`, `logs/shop-rename-deployment.json`에 보존함

## 공개 설명 변경

기본 상점 설명을 사용자가 지정한 `Your agreed price, on a signed receipt.`로 시작하는 원문으로 변경함
스킬 이름은 `Catalog search and price negotiation`이며 board가 괄호로 덧붙이므로 설명 본문에 중복해서 넣지 않음
2026-10-20 23:02 KST에 공개 Agent Card의 설명은 원문 367자 전체와 일치함을 확인함
마켓 `/api/board`의 설명은 앞 300자로 잘려 마지막 문장 전체가 표시되지는 않음
사용자가 지정한 원문은 그대로 유지했으며 등록 ID·개인키·프로필 수는 변하지 않고 shop·buyer 모두 온라인임
로컬 검증과 실제 반영 결과는 `logs/shop-description-local-check.json`, `logs/shop-description-deployment.json`에 보존함

## 첨부 프롬프트의 마켓 적용

2026-10-20 23:30 KST에 `3f37779`의 `system_prompt()`를 키트 재시작으로 판매자에도 적용함
판매자·구매자·상점 선택의 실제 모델 입력이 현재 함수의 출력과 일치하고 `gpt-6-luna`의 `medium`을 사용하는 것을 확인함
세 역할의 정상 판단과 구매 없는 견적·취소 응답도 확인했으며 기존 등록 ID·키·표시 이름·상점 설명·프로필 수를 유지함
확인 결과는 `logs/campaign-prompt-deployment.json`, 견적 검증은 `logs/campaign-prompt-quote-probe.json`에 보존함
이번 반영은 프롬프트 배포이며 캠페인 상태·쿠폰·재고·추가 역할을 처리하는 실행 기능을 새로 구현한 것은 아님

## 벤치마크 가격 정책 재배포

2026-10-21 17:53 KST에 `5e41ee6`의 판매자·가격 정책·공개 카드를 재배포하고 확인함
배포 소스 기준 커밋은 실제 모델 검증 기록까지 포함한 `0e1420c`임
기존 `join_market.sh`와 shop·buyer 키를 유지해 `myeonggi` 등록을 갱신함
배포 전후 소유 프로필 이름 목록, 최초 등록 시각과 공개키가 같고 누적 거래 수도 보존됨
기존 비활성 `SUNGMYEONGGI`, `gacha-shop` 기록은 그대로이며 새 프로필은 추가되지 않음

공개 주소는 `https://latino-apparently-month-plot.trycloudflare.com`임
카드에 기본 상품의 첫 제시가 상한인 lamp 101, chair 183, bookshelf 265를 표시함
공개 A2A 요청으로 lamp 견적 101과 취소 완료를 확인했으며 검증 구매나 시장 결과는 제출하지 않음
판매자·구매자의 실제 모델 입력이 현재 `system_prompt()`와 일치함을 확인함
가격 정책은 `benchmark-v1`이며 모델은 `gpt-6-luna`, 추론 강도는 `medium`을 유지함

사전 시장 검사와 단위 테스트 15개가 통과함
새 터널의 DNS 전파 지연과 배포 감시기의 실패도 삭제하지 않고 기록함
재시작·프로필 비교·공개 견적·실제 프롬프트 확인 기록은 `logs/deployment-20261021T084746Z/`에 보존함
