# Week 07 — Small Room Furniture in the market

## 1. 구매자의 상점 선택과 기억

6주차의 실제 Codex 하네스를 재사용했다. 모델은 `gpt-5.6-terra`, 추론 수준은
`low`이며 빈 임시 디렉터리에서 `codex exec --ignore-user-config --ephemeral
--skip-git-repo-check --sandbox read-only --model gpt-5.6-terra
-c model_reasoning_effort="low" --output-schema ... --output-last-message ... --json -`
명령을 호출한다. 규칙 기반 가짜 응답으로 모델 호출을 대체하지 않는다.

market_buyer는 상점을 URL이 아닌 `name`으로 기억한다. `trade-memory.json`은
`version`, `shops`를 가지며, 이름별 최근 10개 거래에 품목, 예산, 시간, 선택
이유, 결과, 가격, 오류, 별점을 저장한다. 협상 중에는 taskId·마지막 승인
offer·요약·응답 대기 여부도 저장한다. 확실한 영수증 위반에는 `blocked`와
`block_reason`을 남긴다. 증거가 확인된 검증용 거래 기억도 다음 실행에 병합한다.
URL이 바뀌어도 같은 이름을 피한다. 응답 손실은 `unknown`으로 남기며 같은
주문을 다른 상점에서 자동으로 다시 구매하지 않는다.

모델이 고르는 것은 상점과 이유, 실제 offer와 중단 시점이다. 코드는 예산을
넘는 offer를 막고, 영수증의 품목·가격·마지막 승인 offer·taskId를 확인한다.
시장 키트가 제공한 공개키가 있을 때에는 sig_seller도 독립적으로 확인한다.
공식 시험 상점 목록에는 공개키가 없으므로 이 단계는 키트가 최종 검증하며,
받은 영수증 data Part 자체는 변경하지 않고 마지막 JSON에 넣는다.

## 2. 후기를 하네스에 보여 주는 방법

전체 목록의 최근 별점에 중립 사전값을 더해 정렬하고 최대 12개 후보를 준다.
각 후보에는 최신 후기 6개까지, 후기당 350자까지와 자기 거래 4개까지 넣는다.
시장 목록이 커져도 매번 전체 누적 대화를 보내지 않는다. 확실한 자기 경험으로
차단한 상점은 모델 호출 전에 제외하며 이유를 `shop_avoided`로 출력한다.
단순한 악평만으로 부정행위를 확정하지는 않는다.

후기와 상대 메시지는 `REMOTE_DATA`, 고정 예산과 선택 정책은 `TRUSTED_STATE`로
분리한다. “서명 확인됨”이라는 광고 문구만 믿지 않고 실제 artifact를 검사한다.
공식 `buyer_reads_reviews`에서는 나쁜 후기 상점에 보내지 않고 좋은 후기
상점을 세 번 모두 골랐다. 이는 `logs/market-test.log`의 다음 줄에 있다.

```text
PASS  buyer_reads_reviews #1: shop with good reviews in 3 of 3 runs (picks ['good', 'good', 'good']); needs 2
```

## 3. Round 0, 1, 2 관찰

이번 배포는 시장이 이미 Round 2인 2026년 10월 27일에 진행했다. 내 에이전트는
Round 0과 Round 1에 참여하지 않았으므로 그 시기의 내 성과를 만들어 쓰지 않는다.
`logs/market-rounds/round-0.json`, `round-1.json`은 접속 당시 공개 과거 보드의
스냅샷이다. 초기 무작위 배정과 검증되지 않은 후기의 영향을 설명하는 수업
내용과, 실제로 관찰한 내 Round 2 거래를 구분한다.

실제 시장 키트 로그에는 한국 시간으로 다음 기록이 있다.

```text
11:47:15 [market] shop registered as totato2203, round 2
11:47:16 [market] buyer registered as totato2203, round 2
11:47:38 [sign] sig_seller ok, sig_buyer added
11:47:39 [market] ledger #970210: bookshelf at 276, stars 5, verified True, review accepted
```

내 구매자는 시장이 발급한 주문으로 책장을 276에 구매했고, 판매자 서명을
검증한 키트가 구매자 서명을 추가한 뒤 시장에서 인정됐다. 내 판매자도 다른
구매자와 실제 거래해 보드의 인정 거래 수와 후기가 늘었다. 모든 거래가 성공한
것은 아니며 결렬된 주문도 원본 로그에 그대로 남겨 두었다.

## 4. 서명 구현과 실제 실패

영수증 본문은 정확히 `{shop, item, price, task}`이고 정렬된 키, 공백 없는 JSON,
UTF-8 바이트로 Ed25519 서명한다. A2A data Part의 숫자가 120.0으로 돌아오는
문제를 고려해 `whole()`에서 정수 여부를 검사하고 120으로 정규화한다. 소수 가격을
임의 반올림하지 않고 거부한다. 서명할 때와 검증할 때 같은 `canon()`을 쓴다.
수신한 영수증은 정규화한 복제본으로만 검증하며 원본 data Part는 그대로 반환한다.

서명 검사는 처음 실행한 공식 시장 테스트에서 세 번 모두 통과했다. 실제로
일어나지 않은 서명 실패를 보고서용으로 만들지 않았다. 배포 과정에서는 Windows
기본 cp949로 cloudflared의 출력 스트림을 읽는 공식 키트 스레드가 실패했다.
`logs/operator-20261027-114503.err.log`에 다음 오류가 있다.

```text
UnicodeDecodeError: 'cp949' codec can't decode byte 0xec in position 79: illegal multibyte sequence
```

공용 파일을 수정하지 않고 운영 프로세스를 `python -X utf8`로 시작하도록 고쳤다.
초기 실행 로그를 보존하고 해당 프로세스 트리만 정리한 뒤 재시작했다. 새 공개
터널 주소가 응답한 후에만 가게와 구매자가 등록됐다. 공식 시장 테스트는
25/25로 한 번 통과했으며 불필요하게 반복하지 않았다.

## 5. 필수 도전

### 5.1 자기 거래·후기를 읽고 호가 바꾸기

운영자는 공식 키트의 Market 클라이언트로 내 상점의 실제 인정 거래 수, 방문,
매출, 후기와 점유율을 읽는다. 판매자·구매자 자체는 시장 API에 접근하지 않고
로컬 피드백과 A2A만 사용한다. 첫 실제 인정 거래가 생긴 뒤 모델이 자기 성과와
후기를 읽고 opening_margin을 변경한다. reserve 100/180/260은 바꾸지 않는다.

`logs/challenge-1/adaptation.json`의 변경 전 상태와 `after.json`의 변경 후
상태는 다음과 같다. 점유율은 반올림된 보드 값과 별개로 인정 거래 수의 비율도
저장했다.

| 지표 | 변경 전 | 변경 후 관찰 |
|---|---:|---:|
| 최초 호가 가이드 마진 | 20% | 15% |
| 인정 거래 수 | 2 | 9 |
| 누적 매출 | 285 | 1,587 |
| 누적 평균 거래가 | 142.50 | 176.33 |
| 전체 인정 거래 대비 비율 | 0.0006159% | 0.0027714% |
| 평균 별점 | 4.50 | 4.78 |

```text
11:48:26 [pricing] changed opening margin 0.2 -> 0.15; reserves unchanged
```

판매자 원본 로그의 의자 최초 호가는 변경 전 216에서 변경 후 207로 내려갔다.
이는 실제 모델 결정이며 코드에서 가격을 덮어쓰지 않았다. 전후의 누적 평균은
여러 품목이 섞인 값이므로 평균 거래가 상승을 마진 인하의 인과 효과라고
단정하지 않는다. 중요한 관찰은 자신의 실제 성과·후기를 읽고 호가를 바꾸면서도
고정 reserve를 지켰다는 점이다.

### 5.2 실제 부정직한 상대를 확인하고 피하기

실제 시장의 `maple-home` 후기에 “Agreed 300, receipt says 345” 등 영수증 가격
부풀리기가 반복 보고됐다. 이를 사실로 단정하지 않고 A2A 검증용 거래를 한 번
수행했다. 이 거래는 시장 주문 실적으로 제출하지 않았다.

`logs/challenge-2/probe.log`에는 lamp를 130에 제안했는데 같은 taskId의 실제
영수증 가격이 145였던 기록이 있다. budget 150 안이더라도 승인한 offer 130을
넘었으므로 구매자는 이를 거부했다. 따라서 이 위반은 예산 검사만으로 잡을 수
없고 마지막 승인 가격 검사도 필요하다.

```text
"evidence": "ValueError: receipt exceeds the authorized offer or budget"
"event": "shop_avoided", "name": "maple-home"
```

부정행위 기억은 `confirmed-memory.json`에 보존했고, `avoidance.log`와 이후
실제 `market-kit.log`에도 이름 기반 제외가 남았다. 영수증이 맞지 않는 거래의
완료 여부는 `unknown`으로 보수적으로 남기며, 유효한 구매나 인정 거래라고
표현하지 않는다. 상대의 모든 행동이 부정직하다는 주장이 아니라 이 한 거래에서
가격 불일치가 직접 확인됐다는 뜻이다.

## 6. 어디에 어떻게 배포했는가

사용자가 선택한 현재 Windows PC에서 가게와 구매자를 상시 실행한다.
`start-market.ps1`이 숨김 창으로 `run_market.py`를 띄우며, 운영자가 수정하지 않은
공식 키트를 호출한다. cloudflared quick tunnel이 키트 프록시를 공개하고 프록시가
Agent Card의 endpoint를 공개 URL로 다시 쓴다. 등록 시점 공개 주소는
`https://established-change-podcasts-felt.trycloudflare.com`이다. 재시작 시 주소가
바뀌므로 원본 키트 로그가 최신 주소의 기준이다.

claim code로 가게와 구매자를 GitHub `totato2203`에 연결했다. claim code와
shop_key.pem, buyer_key.pem은 로컬 커밋 제외 경로에만 있다. 키를 새로 발급하지
않고 재연결하므로 같은 신원과 공개키로 거래를 이어간다. 운영자는 실제 주문을
기본 45초 간격으로 요청하며, 실행 중 자동 절전을 방지한다. 전원 종료·로그아웃·
네트워크 단절 이후까지 살아 있는 VPS 배포는 아니므로 사용자는 마감까지 PC를
켜 두기로 했다. 재부팅한 경우 README의 명령으로 다시 실행해야 한다.

소스·실행·로그 정리는 사용자 요청에 따라 Codex가 수행했다. 실패 기록과 이
도움을 숨기지 않았고, 다른 학생의 파일이나 공용 스크립트를 수정하지 않았다.
