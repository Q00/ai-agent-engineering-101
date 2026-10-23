# LAB 1 Steps 3, 4, 5

2026-10-20, 학번 24522014 (kcwarni).

## Step 3: 명령 등록

`agent.json`에 `market_buyer.cmd`를 추가했다.

```text
python market_buyer.py --shops {shops} --item {item} --budget {budget}
```

기존 seller와 week-06 buyer 명령은 유지했다. 필수 플레이스홀더 3개를 확인했고 해당 작업을 별도 커밋했다.

## Step 4: 전체 market test

```powershell
python -X utf8 scripts/market_test_week07.py submissions/24522014/week-07
```

결과는 **22/25, 전체 판정 FAIL**이다. 실패는 LAB 2에서 구현할 `seller_signed_receipt` 3회뿐이며, 모두 receipt에 shop/task가 없다는 결과다. buyer 13개와 서명 외 seller 검사는 전부 통과했다. 강의 LAB 1 Step 4는 이 세 서명 실패를 허용하고 나머지가 통과하면 Step 5로 진행하도록 안내한다. 전체 market test 합격이나 과제 완료라고 보고하지 않는다.

`test-report.json`의 공식 테스트 해시와 현재 agent.json 명령 일치를 확인했다. 공식 로그는 `logs/market-test.log`, 콘솔 출력과 확인 결과는 `logs/lab1-steps345/`에 보존했다. 덮어써지는 이전 결과·로그는 실행 전에 `pre-market-test-*` 디렉터리에 원본 복사로 보존했다.

이 과정에서 직접 HTTPS 접속 경로의 인증서 오류를 발견했다. 기존 buyer는 localhost 프록시를 사용했지만 market_buyer는 가게에 직접 접속한다. `trust_env=False`가 SSL_CERT_FILE을 사용하지 않는 점을 확인해 market transport에 Windows 신뢰 저장소를 포함한 SSLContext를 명시적으로 전달했다. 인증서 검증은 유지한다. 실패와 성공 로그를 모두 보존했고, 실제 공개 가게의 카드 조회 성공 및 정책 검사 14개 통과를 확인했다. HTTP 5xx/408/429도 일시적 장애로 구분했다. 이 수정은 전체 테스트의 buyer 실행 전에 적용했다.

테스트로 생성된 `.market/buyer-memory.jsonl`은 `logs/lab1-steps345/market-test-buyer-memory.jsonl`로 옮겨 원본을 보존했다. 운영 기억에는 테스트 가게가 들어가지 않도록 분리했다.

## Step 5: 시장 키트 재시작

이전 PID 7284의 시장 키트와 자식 프로세스를 종료했다. 같은 `kcwarni` 이름과 기존 shop/buyer 키, GitHub claim code 환경변수로 재시작했다. claim code와 비밀키는 커밋하지 않는다.

- 새 키트 시작 시각: 2026-10-20 20:45:11 KST.
- 시작 PID: 34960. 로컬 상태는 `.market/process.json`.
- 가게 URL: https://minority-sophisticated-coating-where.trycloudflare.com
- 공개 카드 HTTP 200, shop/buyer 모두 owner `kcwarni`, alive true 확인.
- 재시작 전후 공개키 지문 일치 확인.
- Round 1 주문에 `running market_buyer`, `[choose]` 출력 확인.
- 원장 #6331: bookshelf 315, 예산 325, 5점, verified True, review accepted.
- 원장 #6345: bookshelf 315, 5점, verified True, review accepted.
- 원장 #6392: chair 199, 5점, verified True, review accepted.

`step5-registration-and-review-evidence.json`에 등록 상태와 후기 수락 로그를 저장했다. 운영 기억 스냅샷도 별도 보존했다. 이 후기는 구매한 **상대 가게**에 남긴 후기이며, 내 가게가 받은 후기와 구분한다.

키트는 백그라운드 실행을 유지한다. 컴퓨터 종료·절전 시 가동이 중단되고 재시작하면 터널 URL이 바뀐다. 현재 실행을 종료하려면 `.market/process.json`과 실행 프로세스를 확인한 뒤 `taskkill /PID 34960 /T /F`를 사용한다. 이후 LAB에서 agent.json 또는 seller를 바꾸면 키트도 다시 실행해야 한다.

## 받은 후기 0건 조사

이 작업 중 사용자가 제기한 가게 후기 0건 문제는 `REVIEW-ZERO.md`에 별도로 조사했다. Round 1 판매 5건 전부 상대 buyer가 stars null, review 빈 문자열을 제출한 것이 직접 원인이다. seller의 서명 누락은 별도 LAB 2 문제다.
