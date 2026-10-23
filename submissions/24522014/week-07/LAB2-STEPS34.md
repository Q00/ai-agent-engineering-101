# LAB 2 Steps 3, 4: 전체 검사와 운영 적용 확인

2026-10-20, 학번 24522014 (kcwarni).

## Step 3: market test

앞선 개선 작업에서 수정하지 않은 공식 `scripts/market_test_week07.py`를 동일 소스·설정·프롬프트의 격리 복사본에 실행해 **25/25 PASS**를 확인했다. 격리 실행은 운영 buyer 기억에 테스트 거래를 섞지 않기 위한 것이며, 공식 결과와 로그를 원본 그대로 제출 위치에 복사하고 이전 기록도 보존했다.

이번 요청에서는 현재 소스 해시가 당시 검증 소스와 같고, `test-report.json`의 공식 테스트 해시 및 실행 명령이 현재 키트와 agent.json에 일치하는지 확인했다. seller_signed_receipt 세 번을 포함한 25개가 전부 통과한 보고서다. 코드 변경이 없어 하네스 실행을 중복하지 않았다.

## Step 4: 키트 재시작

앞선 개선 작업에서 키트를 2026-10-20 21:07:06 KST에 재시작해 서명 seller를 적용했다. 현재 PID 35376, 공개 URL은 https://savings-papua-interim-summit.trycloudflare.com 이다. 같은 shop/buyer 키와 kcwarni GitHub 소유권, 실제 거래 기억을 유지했다.

이번 확인에서도 공개 카드 HTTP 200, shop과 buyer 모두 alive, owner login kcwarni, 해당 프로세스 실행 상태를 확인했다. 기존 공개 가게 진단에서 실제 발급 영수증의 서명을 등록된 공개키로 검증한 기록도 보존되어 있다. 코드 변경이 없으므로 불필요한 재시작으로 터널 주소를 바꾸지 않았다.

기존 공개 가게 직접 진단은 시장 주문·거래 결과·후기로 제출하지 않았다. Step 3의 테스트 통과 및 Step 4의 운영 적용과 Round 2 실거래 인정은 구분한다. 현재 시장 라운드와 확인 시각은 `logs/lab2-steps34/round-status.json`에 기록했으며 Round 2의 최종 시장 요건은 해당 라운드에서 별도로 확인해야 한다.

확인 결과: `logs/lab2-steps34/verification.log`, `evidence.json`. 이전 전체 테스트와 운영 서명 확인: `logs/shop-improvement/`.

후기 환급 이벤트는 이 서명 작업과 별도다. 사용자가 정한 조건과 아직 확정되지 않은 실제 환산 단위는 `REVIEW-EVENT-DRAFT.md`에 기록한다. 이번 요청으로 운영 가격이나 가게 공지를 바꾸지 않았다.
