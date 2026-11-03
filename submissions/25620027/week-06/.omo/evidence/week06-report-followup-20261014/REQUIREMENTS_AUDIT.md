# Week 06 과제 요건 독립 대조

최종 실행 근거 판정: **codeQualityStatus WATCH / recommendation APPROVE / blockers 없음**. 일반 C2 attempt-6과 stress replanning-attempt-3의 실제 성공으로 이전 H1을 해소했다. 각 고정 사례의 관찰 결과에 한정하며, PR은 아직 생성되지 않았다.

2026-10-14. 검토 기준: 최초 HEAD `2dd0daba497e7e2281975c77fbf99b27c19292b9`, 후속 구현 `68b5feebf6baeaea0ea46ecc427736ef1970a9d6`, `640ad7f`, 프롬프트 보완 `8bb9fc7`, 수정된 REPORT. 소스·로그 수정 및 유료 API 재실행 없음. 실패 후 재계획 실행 3회 및 일반 C2 attempt-5/6을 원문과 대조했다. 마지막 두 성공 실행의 invocation 내 소스27개 SHA-256은 각각 8bb9fc7 커밋과 전부 일치했다.

## 명세의 최신성

GitHub connector로 Q00/ai-agent-engineering-101 기본 브랜치의 파일을 직접 읽었다. 아래 Git blob SHA는 로컬 HEAD와 일치했다.

| 공식 자료 | 현행 공개 파일 blob SHA | 로컬 대조 |
|---|---|---|
| [weeks/week-06/README.md](https://github.com/Q00/ai-agent-engineering-101/blob/main/weeks/week-06/README.md) | `6630d1c7832b4e45ed94d445c575d8906eb7e997` | HEAD 및 보고서 기준 3defe07과 동일 |
| [강의 HTML 원본](https://github.com/Q00/ai-agent-engineering-101/blob/main/week-06.html) | `96fe49bb9d3a255c678bb8256f20b31d8d6b9e36` | HEAD와 동일 |
| [trade_test_week06.py](https://github.com/Q00/ai-agent-engineering-101/blob/main/scripts/trade_test_week06.py) | `79fb07b07d273ed1d060734ba9e3303692f76a0d` | HEAD와 동일 |

README의 최신 변경 커밋은 `096c9c31544594e9cc7bc3277ed6aaae2a728ddf`(2026-10-13T14:09:35Z)다. 파일 내용 기준으로 요구조건 변경은 없다. wpti.dev 배포 URL 직접 읽기는 web fetch 실패 및 로컬 DNS 실패로 확인하지 못했다. 따라서 **GitHub 공식 강의 원본 확인**과 **wpti 배포 상태 확인**을 구별한다.

## 과제 요건표

경로는 `submissions/25620027/week-06/` 기준. 공식 조건의 출처는 위 README이며 강의 HTML 1588–1622행도 같은 구분이다.

| 구분 | 실제 필수 조건 | 확인 근거 | 판정 및 범위 |
|---|---|---|---|
| 판매자 | A2A 1.0 JSONRPC 카드, lamp/chair/bookshelf, reserve 100/180/260, INPUT_REQUIRED 숫자 호가, 동일 taskId 협상, COMPLETED 숫자 영수증, 완료 task 오류 | `test-report.json`, `logs/trade-test.log`, `market_a2a/seller_turn.py` | 기존 실제 기본 실행 PASS |
| 구매자 | 카드 조회, A2A-Version 1.0, taskId 후속 요청, 예산 내 구매 또는 중단, 300초 내 종료, 모델이 제안·중단 결정 | 21결과 중 buyer 12개, `market_a2a/buyer.py:129` | PASS. 코드가 초과 제안을 차단하며 자발적 중단 능력을 입증하지 않음 |
| 하네스 | 구매자·판매자 모델 판단; 빈 작업 디렉터리 실행; prompt/format 및 제한의 위치 명시 | `harness_app.py`, `agent.json`, REPORT §1–2 | 문서·구현 확인. reserve/budget 코드 가드는 명시적으로 허용 |
| 기본 검사 | 현행 스크립트+agent.json으로 생성, 모든 회차 통과 | `test-report.json`: 21/21, kit SHA-256 직접 대조 일치 | PASS. 이번 감사는 기존 유료 실행 증거 재검증이며 새 거래 실행 아님 |
| C1 규모·검색 | 수천 상품의 이름/설명/reserve, catalog 커밋, 이름 아닌 설명 요청, 모델에 노출할 후보 축소 설계 | `catalog.json`: 3,008개, `logs/challenge-1/attempt-3/` | 고정 6사례 PASS. 7회 검색 도구 관찰 모두 후보 길이1 확인 |
| C1 성공 | 올바른 품목이며 해당 reserve 아래 호가·판매 없음 | 위 `summary.json`, `server.log`, `wire.log` | 6/6 기록 유효. 복수 후보 판별·미사용 표현 일반화는 미측정 |
| C2 입력·행동 | 약20카드 skills, 쇼핑 목록, 총예산, 여러 가게 동시 협상, 협상별 상태 요약 | `cards/[00..19].json`:20개 고유 URL, `directory.json`, `buyer.log`, `shopping.py`, `buyer.py:115` | 20개 카드/5개 가게 거래 확인. `*-http.json`20개는 응답 메타자료이며 별도 가게 아님 |
| C2 성공 | 목록 전부 구매, 총예산 이하, 품목 중복 없음 | 기존 attempt-4 PASS, attempt-5 FAIL 보존. 최신 일반 attempt-6:5품목1757/2200 PASS | 현행 일반 C2 관찰 요건 충족. 상세는 후속 검증 절 |
| 보고서 | ①하네스·모델·정확명령 ②프롬프트·응답형식·한도강제 ③실패 인용·수정 ④A2A 및 별도 합의 ⑤C1/C2 문맥관리 로그 인용 | REPORT §1–5 | 요구된 항목 존재. 후속 수정은 평가 한계와 테스트 범위를 더 정확히 밝힘 |
| 파일·구조 | source,agent.json,test-report.json,기본·C1·C2 logs,REPORT 최소600자 | 이번 감사자가 `python3 scripts/check_week06.py submissions/25620027/week-06` 실행 | all checks passed: source38개 parse,21 runs pass,필수파일존재 |
| 제출 | 자기 학번 폴더 커밋·upstream PR, 7주차 수업 전 PR open 시간 | 아직 PR 미생성이라는 REPORT 상태 | 로컬 검증과 실제 제출은 다름. 제출 완료라고 표현하면 안 됨 |
| 과정 보존 | 실패 커밋·실행 콘솔 보존, squash 금지, 키 미커밋 | 실패 attempt 폴더와 REPORT 인용 존재 | 이번 감사는 전체 Git 비밀검사나 전체 이력 재감사 아님 |
| 선택 | C3 다중숫자/적대문구, 공개배포, 다른 학생 하네스 교차거래 | REPORT 미실시 명시 | 필수 미충족으로 볼 수 없음 |

## 이번 사용자 요청과 공식 필수의 차이

세 한계의 명시(모든 후보1개, 가드12회/WALK0, 첫 계획 성공 및 평가기 테스트의 범위), 실제 실패 후 재계획 검증, Obsidian 경과 기록은 이번 후속 요청이다. 공식 C2 조건에 실패를 일부러 유발해야 한다는 조건은 없다. 후보 복수개 비교, 20개 가게 모두에서 구매, 최저가 보장, 호가의 단조 감소, 독립 클라우드 20곳, 다른 학생 판매자 필수 등의 조건을 새로 추가하면 안 된다.

REPORT 현재 수정에서 후보6사례 전체1개 및 특징9개의 시험용5SKU 편중, 예산 초과 가드4×3=12 및 WALK0, 기존 재계획 테스트가 `shopping_summary()` 합성로그 평가기 테스트라는 사실을 확인했다. 기존 가드 수와 후보 수는 원문 로그·catalog에서 독립 계산했다. 기존 성공 증거는 그대로 두고 해석을 좁힌 변경은 타당하다.

## 스킬 관점 및 위험

`omo:remove-ai-slops`, `omo:programming` 및 Python reference를 읽고 관점 검사를 수행했다. 최초 diff는 REPORT 수정뿐이었고, 이어서 `68b5fee`의 구현·회귀 테스트 전체 diff를 읽었다. 실패 가게·cap·공개 관찰을 typed CheckoutAttempt로 계획에 전달하고 비싼 첫 가게 시나리오를 실행하는 추가는 요청에 직접 필요하다. 불필요한 데이터 추출·정규화나 untyped escape hatch 추가는 발견하지 않았다. 문구를 고정하는 테스트를 추가할 필요가 없고, 실제 기능 경로와 회계 위반을 관찰하는 검증이 적합하다. 기존 `test_challenge_replanning.py`는 변조된 원장의 거부/정상 재시도 원장 수용을 확인하여 동어반복·삭제전용 테스트는 아니다. 다만 `shop()` 실행 회귀라고 보고하면 테스트 역할을 과장하게 된다. 검색 속성 해석 자체는 공식 목표에 필요하므로 불필요한 파싱으로 일괄 판단하지 않았다.

CRITICAL: 없음.

HIGH: 현재 잔존 없음. 이전 H1(일반 C2 attempt-5 실패)은 8bb9fc7 보완 후 일반 attempt-6 성공으로 해소했다. 과거 실패와 성공 전 보류 판단은 아래에 이력으로 보존한다.

MEDIUM: 기존 `catalog_constraints.py:28`은 부정 표현을 긍정 특징으로 읽을 수 있다. `shopping.py`의 구매 예외는 여전히 task group을 종료시킬 수 있다. 실패 가게/cap/실패 사유 누락은 `68b5fee`가 반환된 미체결 결과에 대해 보완했다. 전송 오류의 체결 여부가 불명인 경우까지 재구매하지 않은 것은 타당하다. 새로운 REPORT 문구를 통해 이런 미검증 복구·일반화 능력을 입증했다고 주장하지 않아야 한다. 명세에 없는 오류복구 능력을 새 필수로 만들지는 않는다.

LOW: wpti 배포본은 직접 접근 불가. GitHub 공식 원본은 최신 파일 해시가 로컬과 일치하여 확인 범위를 명시할 수 있다.

스킬 복기: 재사용 가능한 새로운 사용자 소유 스킬 개선 근거 없음. 관리형 스킬을 수정하지 않았다.

## 독립 회귀 실행

키를 unset하고 bytecode·pytest cache 쓰기를 끈 상태에서 `tests/test_current_buyer.py tests/test_challenge_replanning.py`를 별도 실행했다: 초기 변경에서 **19 passed in 0.17s**, 반복 미체결 제한 추가 후 **23 passed in 0.18s**. 새 `test_shop_replans_only_failed_item_with_observed_failure`는 실제 shop 함수와 fake 외부 checkout/모델을 사용한다. 완료 품목 1회만 구매, 실패 관찰 전달, 잔여예산280, 두 번째 계획에서 seat만 남음, 최종합계300 및 reserved0을 검사한다. 고정 prose prompt 문자열이나 구현 상수만 확인하는 테스트가 아니다. 모델 실제 재계획 성능은 이 오프라인 테스트만으로 주장하지 않는다.

## 초기 후속 실행 결과와 당시 판단(이력)

`logs/challenge-2/replanning-attempt-1/` 및 `replanning-attempt-2/`의 buyer/server/summary/invocation을 읽었다. 둘 다 실제 OpenAI 모델과 로컬 A2A 거래이며, 첫 가게 reserve에 5000을 추가한 주입 시나리오다. 테스트 fake 결과와 구별한다.

| 실행 | 코드 커밋 | 계획 수 | 실제 구매 | 실제 지출 | 미체결 checkout | 최종 예약금 | 복구 결과 |
|---|---|---:|---:|---:|---:|---:|---|
| replanning-attempt-1 | 68b5fee | 3 | 3/5 | 1198/2200 | 6 | 0 | FAIL: 실패 가게·같은 cap 반복 |
| replanning-attempt-2 | 640ad7f | 3 | 4/5 | 1536/2200 | 3 | 0 | FAIL: 다른 가게로 옮겼지만 lamp 구매 못함 |

각 invocation의 모든 source_sha256을 해당 커밋의 `git show` 바이트와 대조해 **불일치0**을 확인했다. 실패 시 summary는 빈 필드로 생성되므로 summary의 total0/receipts[]/planning_rounds0을 실제 지출·구매·계획 없음으로 읽으면 안 된다. 위 수치는 buyer 원문의 `[SHOPPING PLAN]`, `[CHECKOUT ATTEMPT]`, `[BUDGET LEDGER]`를 직접 파싱해 확인했다. 첫 실행에서 두 번째 상태는 실패2건·지출1198·잔여1002·미구매2품목, 세 번째 상태는 실패4건을 모델에 전달했다.

두 번째 실행에서는 같은 가게·같거나 낮은 cap 반복을 코드가 거부한다. 이 정책은 모델의 자율 개선 능력과 구별해야 하며 가격 사유를 별도로 분류하지 않아 모든 반환된 미체결 이력에 적용된다. 최종 lamp는 shop00 cap300 → shop10 cap150 → shop16 cap150을 택했지만 미구매로 끝났다. 따라서 실패정보 전달과 재계획 경로의 작동은 실측했지만, 전체 구매 복구 성공과 안정성은 입증하지 못했다. 이 stress 결과를 성공이라고 보고하면 안 된다.

공식 기본 C2의 일반 구성은 별도다. 주입된 실패 시나리오의 FAIL을 기존 일반 C2 PASS 취소나 명세에 없는 필수 복구조건 위반으로 자동 판정하지 않는다. 그러나 **주입 없는 현재 코드 일반 C2 attempt-5도 실패했다**. `logs/challenge-2/attempt-5/summary.json`은 pass=false다. buyer 원문은 4/5품목 구매, 지출1506/2200, 최종 reserved0이며 storage는 shop14 cap200→shop19 cap150에서 미구매였다. 세 번째 planning은 6회의 모델 도구 수정으로도 승인 가능한 계획을 내지 못해 `HarnessError: Six model rounds ended without an authorized action`으로 종료했다. 이전 attempt-4의 5/5 PASS는 역사적 성공 증거로 보존되지만, 현재 코드의 필수 C2 재검증 통과로 대신할 수 없다.

**당시 HIGH H1 — 일반 C2 실패(후속 attempt-6에서 해소):** `logs/challenge-2/attempt-5/buyer.log`, `market_a2a/shopping.py`의 계획 선택 및 `harness.py:143`의 제한 종료. stress 실패와 달리 공식 C2의 모든 품목 구매 조건을 직접 충족하지 못한 최신 실행이다. 당시에는 제출물에 대한 무조건 승인 또는 검증 완료 선언을 차단했다. 후속 성공 증거가 나오거나 현행 C2 미완료를 최종 상태에 명시해야 한다. 조건 점검 자체는 완료할 수 있지만 과제 성공 승인과 구별한다.

## 성공 재검증 전 판단 이력

REPORT 머리·말미가 현재 제출 완료 판정 보류로 바뀐 것을 재확인했다. 새 계획 프롬프트는 cap과 지불가격·잔여예산을 분명히 하고 하나 남은 품목의 잔여한도를 안내하지만, 해당 보완의 실제 일반 C2 성공은 아직 미검증이다. root VALIDATION은 전체89tests/Ruff/format/strict types 통과 기록을 제시한다. 이는 root 실행 근거이며 독립검사자의 23개 관련테스트와 구별한다. VALIDATION의 최초 raw-artifact JSON 집계 칸은 traceback이어서 root에 startswith 이벤트 판별로 고치도록 알렸다. 본 감사의 부분지출 수치는 로그를 독립 집계한 값이다.

Obsidian 경과 반영은 root 담당이며 본 감사자는 Vault 완료 여부를 검증하거나 수정하지 않았다. 추가 유료 실행은 승인 대기이며 실행했다고 주장하지 않는다. 당시 과제성공승인 blocker는 일반C2 전품목구매 통과 증거의 부재였으며, 아래 최종 실행으로 해소했다. 두stress실패와범위를정직하게기록한보고서정정자체에는blocker없다.


## 최종 추가 성공 실행의 독립 검증

승인된 추가 실행 결과를 읽었으며 검토자는 API를 호출하지 않았다. `logs/challenge-2/replanning-attempt-3/`와 `logs/challenge-2/attempt-6/`의 summary, invocation, buyer 원문 및 실제 A2A response를 직접 대조했다.

| 최종 실행 | 설정 | 계획 수 | 실제 구매 | 합계 | 실패 checkout | 최종 예약금 | 판정 |
|---|---|---:|---:|---:|---:|---:|---|
| replanning-attempt-3 | 첫 가게 reserve+5000 | 2 | 5/5 | 1686/2200 | 1 | 0 | PASS |
| attempt-6 | 가격 실패 주입 없는 일반 C2 | 2 | 5/5 | 1757/2200 | 1 | 0 | PASS |

두 실행 모두 invocation.source_sha256의 **27개 파일 전부**를 `git show 8bb9fc7:<파일>` 바이트와 비교하여 불일치0을 확인했다. 각 buyer 원문의 A2A RESPONSE에서 COMPLETED task는5개, 영수증 SKU도5개 고유하며 실제 영수증 금액 합계는 각각1686,1757이다. HTTP 관찰29개가 모두 A2A-Version1.0이다. 전체 correlations는 실패 task를 포함해 **각6개**이고, 성공 영수증 task가5개라는 뜻과 구별한다. 원장6행은 실패1회+성공5회를 반영하므로 중복 구매가 아니다.

stress3은 reading-light를 shop00 cap150에서 사지 못한 뒤, 나머지4품목 지출1536/잔액664/완료품목4개와 실패관찰1개를 다음 모델 상태에 전달했다. 다음 계획에는 reading-light만 남았고 shop05 cap150에서 price150으로 성사했다. baseline6은 secure-storage shop04 cap200에서 실패한 뒤, 다른4품목 지출1354/잔액846을 유지하고 미구매품목만 shop14 cap846으로 재계획해403에 구매했다. 최종 잔액은 각각514,443이다. 이미 구매한4품목의 checkout을 다시 실행하지 않은 원문을 확인했다.

따라서 실제 실패→예약 해제→실패 관찰 전달→미구매품목만 재계획→전 품목 구매 완료 경로가 **두 실제 실행에서 관찰되었다**. stress3의 cap150은 한 품목만 남으면 잔여예산 전체를 cap으로 잡으라는 지침과 다르므로, 모델이 모든 지침을 항상 지켰다는 주장은 하지 않는다. 대체가게 구매 성공·예산 안전성과 그 지침 준수는 별개다. 더 낮은 총액이나 가게 선택 이유를 최저가 탐색·인과적 프롬프트 효과 입증으로 확대하지 않는다.

## 최종 승인 범위

기본21/21, 기존 C1 6/6, 현행 일반 C2 5/5 및 추가 실제 복구 성공의 관찰 근거로 로컬 과제 요건 부합을 승인한다. 이전 stress1/2와 baseline5 실패는 로그·커밋 그대로 보존하고 한 번의 재검증 성공을 반복 안정성 보장으로 바꾸지 않는다. C1의 후보1개 편중, 영어/특징 파서의 부정표현 한계, 가격·카드순서 결합, 네트워크 오류의 체결 여부 불명 복구 미검증은 잔여 MEDIUM/WATCH다. 현재 scope의 새로운 코드·테스트에 slop/과적합 테스트 위반은 발견하지 않았다.

PR 미생성: **로컬 요건 부합 ≠ 과제 제출 완료**. 실제 PR 생성 및 제출시각 확인은 별도다. Obsidian 현행화도 root 담당이며 이 검토자의 파일변경 범위 밖이다. REPORT 및 root VALIDATION의 최종 결과 갱신은 별도로 확인한다.

최종 문서 대조 완료: REPORT 머리·말미와 VALIDATION의 Observable verdict가 기본21/21, C1고정6/6, 최신 일반C2 1757/2200, 추가실패복구1686/2200, 과거실패보존, 고정사례범위 및 PR미생성을 일관되게 명시한다. 현재 결과와 모순되는 완료/보류 상태는 해당 최종 요약에서 발견하지 않았다.
