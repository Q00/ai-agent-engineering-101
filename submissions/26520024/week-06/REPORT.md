# 6주차: 기존 하네스를 A2A 구매자와 판매자로 연결하기

학번: 26520024 / 이의찬. 필수 거래 테스트와 도전 1·2만 수행했다.
추가점수 도전, 공개 배포, 다른 학생과의 거래는 수행하지 않았다.
구현과 분석에 Codex 코딩 도우미를 사용했으며, 실제 모델 실행과 scripted
오프라인 테스트를 구분하여 원본 로그와 작업 이력을 보존했다.

## 1. 하네스, 모델과 실행

5주차의 `codex_backend.py`를 재사용하고, 그 위에 구매자·판매자 협상 루프를
구현했다. 모델은 **gpt-6-astra**, reasoning **low**, Codex CLI **0.153.0**이다.
기존 ChatGPT 로그인을 사용했으며 별도 API 키를 코드에서 가져오지 않았다.
호출마다 빈 임시 디렉터리에서 실행하고 프로젝트 설정과 native tool을 차단했다.
모델 입력은 `system`/`history` 필드를 가진 CLI stdin JSON이며, API의 native
system role을 직접 호출한 것은 아니다. temperature·seed는 지정하지 않았고
내부 값은 알 수 없다. CLI 원본 이벤트의 도구 실행 여부와 사용량도 검사한다.

기존 base는 Python 3.8이라 공식 SDK를 실행할 수 없었다. 사용자 지정
`eraseanything`의 Python **3.10.22**를 기반으로 제출 폴더에 `.venv`를 만들었다.
SDK **1.2.0**과 의존성은 이 격리 환경에만 설치했으며 기존 conda 패키지는
변경하지 않았다. 버전과 소스 해시는 `logs/runtime-and-source-01.json`에 있다.

`agent.json`의 정확한 명령은 다음과 같다. 작업 디렉터리는 제출 폴더이다.

```bash
.venv/bin/python seller.py --port {port}
.venv/bin/python buyer.py --url {url} --item {item} --budget {budget}
```

저장소 루트에서 공식 검증은 다음과 같이 실행했다.

```bash
submissions/26520024/week-06/.venv/bin/python scripts/trade_test_week06.py submissions/26520024/week-06
```

실제 CLI의 전체 인자는 `logs/seller-under-test.log`의 `model_command.argv`에
각 호출별로 기록되어 있다. 핵심은 `codex exec --ignore-user-config --ephemeral
--sandbox read-only --skip-git-repo-check --model gpt-6-astra`이며,
`model_reasoning_effort="low"`, `web_search="disabled"`, 기능별 `--disable`,
빈 작업 디렉터리와 JSON 출력 옵션은 `codex_backend.py`가 구성한다.
재현 명령 전체는 [README.md](README.md)에 정리했다.

## 2. 결정 방식과 제약

판매자 프롬프트는 `Choose every product selection, asking price, counteroffer,
acceptance or rejection yourself`와 `never ask or sell below the selected
product reserve`를 명시한다. JSON 응답은 `action`, `item_id`, `price`,
`summary`이다. 구매자는 `action`, `price`, `summary`를 반환하며 프롬프트에
`Never offer or pay above your budget`를 둔다. 상점·예산 계획도 모델이 정한다.

**프롬프트뿐 아니라 코드에도 제한을 둔다.** 선택된 상품은 task 도중 바뀌지
않으며, 모든 판매 제안은 최저가 이상이어야 한다. 수락 가격은 실제로 받은
제안과 같아야 한다. 구매 제안은 배정 예산 이하, 영수증은 마지막 제안 금액과
같아야 한다. 가격을 자동 보정하거나 모델 대신 구매를 수락하지 않는다.
잘못된 JSON/결정은 검증 오류를 모델에 보내 최대 두 번 수정하게 하고, 계속
실패하면 오류로 끝낸다. 모델 호출은 최대 60초, 구매 루프는 270초·8개 제안으로
제한한다. 횟수 초과 취소는 `limit`으로 기록하며 성공적인 구매/철회로 세지 않는다.

공식 테스트 결과는 **21/21 통과**했다. 각 결정 조건은 3회 반복되었다.

| 검사 | 실제 결과 |
|---|---|
| 판매자 lamp, 구매 예산 120 | 판매가 100 / 110 / 100, 최저가 100 준수 |
| 판매자 bookshelf, 구매 예산 230 | 3회 모두 거절, 최저가 260 미만 제안 없음 |
| 구매자 lamp, 예산 120 | 3회 모두 100에 구매 |
| 구매자 bookshelf, 예산 230 | 3회 모두 정상 CancelTask, 구매 없음 |
| AgentCard·taskId·버전 헤더·종료 task | 모두 통과 |

근거: [공식 결과](test-report.json), [거래 로그](logs/trade-test.log).
공식 실험의 실제 모델 호출은 판매자 19회, 구매자 17회였다.
구매자 오류를 예산 준수로 오인하지 않도록 `buyer_final`이 여섯 로그에 각각
한 번씩 있고, 결과가 구매 3회·정상 철회 3회임을 추가 검사했다.

## 3. 실패와 수정

처음 SDK 수명주기 테스트에서 다음 오류가 발생했다.

> SQLite objects created in a thread can only be used in that same thread.
>
> FAILED (errors=1)

원본은 [offline-01-failed.log](logs/offline-01-failed.log)에 있다. Starlette
TestClient의 실행 스레드와 SQLite 연결을 만든 스레드가 달랐다.
`check_same_thread=False`와 검색 잠금을 함께 적용한 후 같은 테스트를 다시
실행하여 [offline-02.log](logs/offline-02.log)의 15개 테스트가 통과했다.
최초 실패를 삭제하거나 성공 로그로 덮어쓰지 않았다. 이 실패는 **오프라인
구현 테스트 실패**이며 모델 협상 실패가 아니다. 공식 거래 테스트는 수정 후
첫 실행에서 통과했으므로, 실패한 모델 실험을 따로 만들어내지 않았다.
실행 전에 `agent.json`도 공식 중첩 `seller.cmd`/`buyer.cmd` 형식으로 맞췄다.

## 4. A2A가 해결한 것과 별도 합의

A2A는 AgentCard로 인터페이스와 skills를 발견하고, JSON-RPC 메시지를 보내며,
taskId로 협상을 이어가고, INPUT_REQUIRED/COMPLETED/REJECTED/CANCELED 상태 및
영수증 artifact를 교환하는 공통 틀을 제공했다. SDK가 종료된 task의 재사용도
거절한다. 그러나 A2A 자체가 가격의 의미나 상품 일치, 구매의 성립을 결정하지는
않는다. 이 실험에서는 정수 가상 크레딧, 메시지의 마지막 숫자가 가격이라는
규칙, 제시 가격과 같은 숫자를 보내면 수락 의사라는 규칙, `item`/`item_id`/`price`
영수증 필드를 별도로 합의했다. 상품 설명 해석, 협상 전략, 최저가·총예산 보호도
하네스와 코드의 책임이다. 로컬 HTTP 테스트이며 실제 결제·인증·배송 시스템은 아니다.

## 5. 필수 도전과 문맥 관리

### 도전 1: 3,016개 상품에서 설명으로 선택

`generate_data.py`로 합성 카탈로그와 정답이 있는 설명 6개를 만들고 실제 호출
전에 커밋했다. SQLite FTS5/BM25가 최대 12개 후보를 좁히고, 그 후보의 이름·설명·
최저가를 받은 모델이 최종 상품과 가격을 선택한다. 검색 결과를 바로 정답으로
채택하거나 요청별 정답 ID를 판매자에게 전달하지 않는다.

| 설명의 핵심 | 정답 ID | 최저가 | 실제 판매가 | 결과 |
|---|---|---:|---:|---|
| 파란 목재, 책 40권, 높이 80cm | atlas | 210 | 260 | 통과 |
| 빨간 금속, USB, 800루멘 | ember | 105 | 130 | 통과 |
| 초록 천, 요추 지지, 120kg | fern | 190 | 240 | 통과 |
| 검은 강철, 접이식, 120×70cm | slate | 230 | 280 | 통과 |
| 흰 리넨, 2인용, 세탁 가능 | cloud | 310 | 380 | 통과 |
| 대나무, 3단 접이식, 170cm | reed | 135 | 165 | 통과 |

**6/6 성공**, 모델 판매자 호출 12회. 예를 들어 실제 검색 로그에는
`"catalog_size": 3016`, `"candidate_ids": ["atlas", "cloud", ...]`,
`"candidate_bytes": 3199`가 기록되며, 완료 영수증에는
`"item_id": "atlas"`, `"price": 260.0`이 남았다. 후보 전송은 최대 3,263바이트였다.
근거: [도전 1 결과](logs/challenge-1/20261020T052534380628Z/results.json),
[판매자 원본](logs/challenge-1/20261020T052534380628Z/catalog-seller.log).
상대 구매자는 제시 가격을 그대로 수락하는 **scripted probe**이며 모델 구매자가
아니다. 여기서는 대형 카탈로그 판매자의 선택·최저가만 분리해 평가했다.

### 도전 2: 상점 20개와 하나의 총예산

서로 다른 카테고리와 가격의 로컬 A2A 서버 20개를 실행하고 실제 AgentCard를
GET했다. 모델은 card의 skills와 공개 참고 가격대를 읽고 lamp/chair/bookshelf/
desk의 상점 및 예산 상한을 선택한다. 코드가 가장 싼 상점을 대신 선택하지 않는다.
카드 요약은 skill 설명당 500자로 제한하고, 계획의 합계가 900 이하인지 검사한 뒤
네 구매자를 동시에 실행한다. 개별 협상은 불변 예산·taskId·마지막 가격과 모델의
최대 600자 요약, 최근 두 번의 교환만 다음 호출에 전달한다. 전체 원본 기록은
디스크에 남지만 네 상점의 전체 대화를 하나의 모델 문맥에 합치지는 않는다.

세 번 모두 모델이 value 상점 네 곳을 선택하고 예산을
`lamp 145 / chair 225 / bookshelf 305 / desk 225`로 배분했다.

| 실행 | lamp | chair | bookshelf | desk | 합계 / 예산 | 구매 단계 시간 |
|---|---:|---:|---:|---:|---|---:|
| 1 | 100 | 190 | 275 | 200 | 765 / 900 | 17.64초 |
| 2 | 100 | 190 | 275 | 190 | 755 / 900 | 20.65초 |
| 3 | 100 | 190 | 280 | 190 | 760 / 900 | 17.73초 |

**3/3 성공**, 각 실행에서 모든 품목을 정확히 한 번 구매했다. 모델 호출은
계획 3회·구매자 12회·판매자 24회, 합계 39회다. 실제 로그의 계획에는
`"shop_id": "bookshelf-value", "budget": 305`가 있고, 첫 실행의 결과에는
`"directory_size": 20`, `"total": 765.0`, `"passed": true`가 남았다.
근거: [실제 카드 20개](logs/challenge-2/20261020T052658723825Z/agent-cards.json),
[계획·통신·구매자 로그](logs/challenge-2/20261020T052658723825Z/run.jsonl),
[결과](logs/challenge-2/20261020T052658723825Z/results.json).

`buyer_start`와 `buyer_final` 시각을 대조했을 때 네 구매가 모두 겹친 구간은
각각 **15.16 / 12.44 / 14.58초**였다. 단순히 순차 실행에 병렬이라는 이름을
붙인 것이 아니다. 위 구매 시간은 상점 시작·카드 수집·계획 시간을 제외하며,
순차 실행과 비교하지 않았으므로 속도 향상률을 주장하지 않는다.
도전 2의 실제 모델 입력에서 최근 교환은 최대 1개, 요약은 최대 168자였다.
각 품목이 첫 구매 제안에서 성사되어 **장기 협상의 요약 누적 오류는 검증하지
못했다**. 기본 거래 테스트의 예산 부족 사례에서는 최대 네 차례 제안과 두 개
교환 기록을 사용했지만, 이 역시 장기 대화 안정성을 증명하는 표본은 아니다.

모든 실험을 합쳐 실제 모델 호출은 **87회**였다. CLI가 보고한 입력 토큰
825,177·출력 토큰 4,993은 CLI 내부 문맥을 포함한 원본 사용량이며, 카탈로그만의
토큰 수나 API 청구액으로 해석하지 않는다. [독립 검증 결과](logs/evidence-validation-01.json)는
원본 CLI 응답과 실행 action의 일치, 모든 가격 제약, 정상 종료, 상품 일치,
중복 없음과 실제 동시 실행을 재검사한다. 공식 CI 구조 검사만으로 이 도전들이
실행되었다고 판단하지 않았다.

### 해석의 범위

카탈로그는 실상품 데이터가 아니라 고정 합성 데이터이며 여섯 설명의 구별되는
특징이 뚜렷하다. 따라서 6/6을 일반적인 상품 검색 정확도로 해석할 수 없다.
20개 상점도 같은 하네스의 로컬 복제본이므로 서로 다른 사업자의 상호운용성을
검증한 것은 아니다. 공개 가격대가 계획을 돕고, 고정 예산 분할은 중복 구매와
초과 지출을 막는 대신 남는 예산을 다른 협상에 재배분하지 못한다. 모델 요약은
오류를 포함할 수 있으므로 예산·상품·제안은 요약이 아닌 명시적 상태로 검사한다.
전체 문맥 투입 대비 A/B 실험이나 최적 가격 보장은 주장하지 않는다.

## 최종 검증

오프라인 테스트 15개와 독립 로그 검증을 통과했다. 직접 구조 검사는 NAS 파일
탐색 대기로 중단했으며, 생성된 환경·캐시를 제외한 **동일 바이트의 제출 파일**을
임시 디렉터리에 복사해 수정 없는 공식 `check_week06.py`로 검사했다.
[구조 검사 로그](logs/structural-check-03.log)에 파일별 SHA-256과 검사 결과를
보존한다. 공식 live trade test는 원래 제출 폴더에서 정상 실행되었다.
