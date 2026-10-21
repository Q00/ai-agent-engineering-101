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

## 3위 기준 가격 전환

사용자가 정한 규칙은 현재 Round 2 보드의 거래 건수 순위 3위 이내면 회복 모드,
4위 이하로 내려가면 다시 공세 모드로 전환하는 것임
`rank_pricing.py`는 새 견적 요청 시 공식 `/api/board`를 최대 60초에 한 번 조회함
순위는 보드 기본 Deals 정렬과 같은 거래 건수 내림차순·별점 내림차순으로 판단함

- 공세: 기본 lamp 100, chair 180, bookshelf 260을 첫 제시가 상한으로 사용함
- 회복: 기본 상품의 첫 제시가 상한을 이전 benchmark 값인 101, 183, 265로 복원함
  회복 견적보다 낮은 제안에는 현재 견적을 한 번 재안내하며 반복되면 협상을 종료함
- 이미 열린 거래는 최초 가격 단계와 수락 조건을 유지하며, 순위 변동으로 견적을 올리지 않음
- 조회 실패·다른 라운드·본인 등록 누락은 순위 하락으로 해석하지 않고 이전 단계를 유지함

상태는 `.market/rank-pricing.json`에 원자적으로 저장하고 재시작 후에도 복원함
관측 순위와 단계 전환은 판매자 실행 로그의 `pricing_rank_observed`, `pricing_phase_changed`에 남음
기본 3품목을 제외한 카탈로그에는 기존 마진 설정을 유지함
모든 상품의 reserve와 시장 가격 하한은 그대로이며, 이는 원가 자료나 손실 회수 실적을 의미하지 않음
따라서 실제 손실액·회수액이나 향후 가격 인상 성공을 계산·보장하지 않음
공개 카드는 공세 가격과 일반 견적을 구분하며 현재 견적을 요청하도록 안내함

검증 기록은 `logs/rank-cycle-20261021T103703Z/`에 보존함
전체 단위 테스트 178개에는 TEST ONLY 순위 자료로 두 방향 전환과 기존 거래 조건 보존을 검사한 사례가 포함됨
실제 모델로 공세 제시가·회복 제시가·회복 재제안·기존 공세 견적 수락·회복 견적 수락의 5개 판단도 통과함

2026-10-21 19:45 KST에 `e8db863`을 기존 이름과 키로 재배포하고 공개 견적을 확인함
공개 주소는 `https://xml-extension-only-honolulu.trycloudflare.com`임
현재 관측 순위는 10위, 단계는 acquisition이며 기본 3품목의 공개 견적 100·180·260과 취소가 모두 정상임
실제 판매자 입력의 단계·정책 버전·프롬프트가 소스와 일치함을 확인함
프로필 목록·키·최초 등록 시각·누적 거래가 보존됐고 새 프로필은 추가되지 않음
터널 초기 HTTP 530 응답은 재등록 없이 조회를 재시도해 해소했으며 관측 기록도 보존함

## 구매자 접근 제한과 공개 안내

사용자 요청에 따라 `data/buyer-access.json`에 `cheapest-in-town` 구매자를 등록함
`buyer_access.py`는 가격 판단과 분리된 상점 내부 접근 정책이며, 부정행위 판정이나 공개 평판 목록이 아님
확인 자료는 `logs/buyer-access-20261021T105709Z/reviews-before.json`임
해당 시점 최근 리뷰 40개 중 15개가 이 구매자의 리뷰이며 자기 상점 추천 문구를 포함함
상품 품질을 독립적으로 검증한 자료는 없으므로 공개 카드에 ‘블랙컨슈머’라는 주장을 게시하지 않음

인증된 SDK 사용자 이름 또는 요청·메시지 metadata의 `buyerName`, `buyer_name`,
`buyer` 문자열이나 `buyer.name`이 차단 이름과 일치하면 모델 호출·순위 조회·서명 전에 거래를 거절함
자유로운 상품 요청 본문, 리뷰 본문, GitHub owner 이름만으로는 구매자를 추정하지 않음
인증된 사용자가 있으면 인증되지 않은 metadata보다 우선함
자기 신고에 의한 차단은 로그에 `authenticated: false`로 기록함

현재 market kit는 구매자 이름이나 서명된 신원 정보를 판매자에게 전달하지 않음
따라서 신원이 없는 호출은 계속 허용되며, 이 구매자의 익명 요청이나 카드 조회까지 차단했다고 주장할 수 없음
완전한 선택 차단에는 시장 측 구매자 인증 전달 또는 모든 구매자와 호환되는 신원 인증이 필요함
전체 익명 구매자를 막거나 IP·본문의 유사성으로 동일인을 추측하는 방식은 적용하지 않음
목록 수정 후에는 판매자 재시작이 필요함

다른 구매자에게 보이는 공개 Agent Card에는 실제 공세·일반 가격, 견적 요청·수락 절차,
정확한 합의 금액의 Ed25519 영수증, 추가 수수료 없음과 기존 견적 유지 조건을 안내함
별도 메시지 방송이나 타 구매자의 판단·평점 지시는 하지 않음
실제 완료된 거래 리뷰에는 광고임을 밝힌 추천 코드 안내를 덧붙임
초기 테스트 명령의 가상환경 경로 해석 오류와 이후 검증 출력도 같은 로그 디렉터리에 보존함

## 회복 모드 REVIEW10 추천 가격

사용자가 회복 모드의 일반 판매가 조정을 선택하여 정책을 `rank-cycle-referral-v2`로 변경함
3위 이내의 새 기본 상품 견적은 실제 일반가 lamp 120, chair 200, bookshelf 290임
3위 밖의 공세 견적 100·180·260과 상품별 reserve는 그대로 유지함

회복 모드에서 첫 메시지에 `lamp REVIEW10`처럼 상품과 코드를 함께 보내거나
메시지 metadata에 `referralCode: REVIEW10`을 제시하면 일반가에서 정확히 10%를 할인함
할인 견적은 lamp 108, chair 180, bookshelf 261이며 원 단위 반올림이나 하한 미만 할인은 없음
이 일반가는 코드가 없는 실제 새 회복 거래에도 적용하며, 할인 표시만을 위한 가상 비교가가 아님
첫 메시지 이후 코드를 붙여도 기존 견적을 다시 계산하지 않으며 중복 할인도 하지 않음
이미 발급한 공세·회복 견적은 단계와 적용 조건을 그대로 유지함
다른 SKU, 공세 모드, 정수·판매 하한 안에서 온전한 10%를 적용할 수 없는 견적에는 할인을 약속하지 않음

이는 공개적으로 재사용할 수 있는 판촉 코드이며 구매자별 1회성 쿠폰이 아님
코드를 아는 사실만 확인할 수 있고 실제 리뷰 열람이나 신규 방문자 신원을 증명하지 못함
로그의 `review_referral_claimed`, `review_referral_redeemed`는 `read_verified: false`로 남김
영수증의 서명 대상 4개 필드와 최종 가격은 기존 규칙을 유지함
추가 할인 안내 필드는 참고용이며 기존 Ed25519 서명의 독립적인 보호 대상은 아님
할인 견적보다 많은 금액을 제시하면 최종 할인 금액을 다시 제시해 정확한 금액으로 주문하도록 함

구매자 리뷰 별점은 순위와 무관하게 기존 거래·영수증 기준을 유지함
완료된 거래의 실제 후기 뒤에 `Ad (my shop)`이라고 표시한 우리 상점·코드·회복 모드 한정 조건을 추가함
구매 없는 거래나 불일치 영수증에 광고를 덧붙이지 않으며, 타 상점의 품질·서비스를 지어내지 않음
리뷰 안내 설정은 구매 시도 전에 읽어 설정 오류가 이미 받은 영수증을 버리는 원인이 되지 않게 함
이 안내는 리뷰 작성 대가나 긍정 평점 조건이 있는 보상이 아님

검증은 같은 `logs/buyer-access-20261021T105709Z/` 디렉터리에 보존함
189개 단위 테스트에는 정수 할인·하한·두 방향 단계 전환 중 견적 유지·중복 할인 방지·초과 입찰 재안내,
거래 재전송 시 중복 정산 방지와 상대 순위에 독립적인 솔직한 별점을 확인한 사례가 포함됨

2026-10-21 20:11 KST에 `eb41318`의 실제 배포를 확인함
공개 주소는 `https://pending-surround-congratulations-openings.trycloudflare.com`임
기존 `myeonggi` 이름·shop/buyer 키·프로필 이름 목록·최초 등록 시각을 유지했으며 중복 등록 프로필은 없음
실행 중인 kit는 하나이고 shop·buyer가 모두 온라인이며 소스 해시도 배포 기준과 일치함

공개 A2A에서 차단 이름을 자기 신고한 시험 요청은 영수증 없이 거절됨
이는 실제 해당 구매자의 익명 방문을 식별하거나 차단했다는 증거가 아님
일반 lamp 견적과 `REVIEW10`을 붙인 기본 3품목 견적을 확인하고 모두 취소했으며 구매 결과를 제출하지 않음
실제 관측 순위가 10위이므로 공세 모드이며, 코드 제시에도 100·180·260을 유지하고 할인이 미적용임을 명시함
회복 일반가·할인가·초과 입찰 재안내·할인가 수락·공세 제시가는 실제 모델의 TEST ONLY 문맥 5개로 확인함
189개 단위 테스트와 배포 후 제출 검사가 통과했으며 기존 공식 25회 보고서는 이번에 다시 생성하지 않음
실제 kit 주문의 완료·검증된 리뷰 4건에서 솔직한 거래 후기 뒤의 광고·코드 안내 게시도 확인함
로그는 수정하지 않았고, 터널 DNS 지연과 중간 검증의 대화 중단도 별도 기록으로 남김
단계 전환 중 기존 거래의 견적을 보존하는 동작과 재시작 후의 순위 상태 복원은 구분됨
SDK task store는 메모리 저장소이므로 서버 재시작 간 진행 중인 A2A task 보존은 별도 지원하지 않음

검증 기록 커밋 `5212640`의 `git push origin week-07`은 GitHub GH001 검사에서 거절됨
기존 로컬 커밋 이력의 `logs/market-kit.log` 163.73MB가 GitHub 단일 파일 100MB 제한을 초과함
저장소의 이력 재작성·로그 편집 금지 규칙에 따라 이력을 변경하지 않고 로컬 커밋을 보존함
Market의 실행 중인 배포와 온라인 상태는 GitHub push 실패와 독립적이며 위 공개 검증은 완료됨
