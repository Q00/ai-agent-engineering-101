# Week 03 보고서 — 계획 심사와 재귀 위임을 사용하는 현재 코드

보고서의 대상은 **현재 `extensions/peer_dag` 코드**다. 작업자 A/B/C가 계획·평가·실행·재위임·통합을 수행하고, 의존성이 없는 하위 작업을 병렬 실행한다. 사용자와 함께 설계한 질문은 비결정적인 LLM의 판단을 코드의 선택 규칙·의존성·검증으로 어디까지 통제할 수 있는가이다.

분석 자료는 출력 토큰 상한을 제거한 본 실험 **5개 작업 × 3개 조건 × 3회 = 45회**와, 그중 HTTP 429 실패만 별도로 복구한 **16회**다. 과거 배정 전용 코드의 9/15·5/15·3/15는 현재 코드의 결과가 아니다. [이전 배정 중심 보고서](REPORT_ALLOCATION_REFERENCE_20260922.md)에 원문을 보존했다.

이 문서는 현재 구현·관측 결과·근거를 정리한 작성본이다. 3절의 Smith 원문 비교와 4절의 최종 해석 문단은 작성·검토 대상으로 남겨 둔다. Codex가 사용자 설계에 따라 구현·실험·근거 집계를 보조했다.

## 1. 설정과 재현

### 역할과 작업 처리

[현재 에이전트 동작과 협업 상태 다이어그램](diagrams/CURRENT_STATES.md)은 동등한 동료의 계획·재귀 위임과 실행 코드가 관리하는 작업 상태를 두 그림으로 보여준다.

|Worker|baseline의 전문성|수행 가능한 역할|
|---|---|---|
|A|제품·기술: 오류 분석, 구현 의존성, 재현·회귀 테스트, 배포·롤백 판단|계획, 입찰, 동료 계획 심사, 직접 실행, 재위임, 통합|
|B|사업·분석: 수요, 손익, 불확실성, 사업 우선순위|위와 동일|
|C|운영·커뮤니케이션: 고객 안내, 상담 절차, 팀 간 일정·담당·전달|위와 동일|

고정 manager 전용 에이전트는 없다. 각 작업의 요청자가 관리자 역할을 맡으며, 최초 요청자는 A다. 선정된 Worker가 하위 계획을 만들면 그 하위 작업들의 요청자가 된다. 모든 호출에 조건에 맞는 팀 전체 역할표를 전달한다.

1. A/B/C가 각각 `bid`, `confidence`, `reason`, `plan`을 제출한다. 계획은 직접 실행 `execute` 또는 하위 위임 `delegate`다.
2. 요청 Worker가 확신도를 가린 후보의 이유·계획을 읽고 coverage, feasibility, verification을 각 0~2점으로 평가한다. 자기 계획도 후보에 포함된다.
3. 코드가 모든 기준 1점 이상인 후보만 남긴 뒤 **평가 합계 → 확신도 → 고정 순환 순서**로 선정한다. 루트 순서는 요청자부터, 하위 순서는 승인된 계획의 step 순서로 정한다. 완료 속도로 우선순위를 바꾸지 않는다.
4. 하위 작업은 `depends_on`으로 입력 의존성을, `reads`/`writes`로 논리적 자원 사용을 선언한다. 준비된 독립 작업은 병렬로 실행하고, 선행 작업이 실패하면 해당 후속 작업을 blocked로 남긴다.
5. 선정된 Worker가 완료한 하위 결과들을 받아 원자료와 대조하고 최종 `summary`, `facts`, `evidence`를 만든다. 같은 Worker의 독립 작업도 별도 세션에서 병렬 실행할 수 있다.

[현재 프롬프트](extensions/peer_dag/models.py), [선정·DAG·실행 코드](extensions/peer_dag/core.py), [단계별 JSON Schema](extensions/peer_dag/response_formats.py).
모든 요청은 두 메시지의 새 문맥이다. 선행 결과는 명시적 의존 관계로 전달하며, 장기 메모리는 사용하지 않는다. 이 실험은 제공된 합성 자료를 분석해 설계 문서를 생성한다. 웹 검색이나 실제 게임 실행·PG/DB 조작·배포는 하지 않는다.

### 세 조건과 다섯 작업

|조건|변경 사항|
|---|---|
|baseline|A/B/C의 전문성이 서로 다름|
|homogeneous|세 Worker의 전문성과 공통 역할표를 모두 같은 일반 문제 해결 능력으로 변경|
|overconfident|baseline의 C에게 propose 단계에서 항상 입찰하고 확신도 95 이상을 답하도록 추가 지시|

작업·원자료·평가 답안·모델·온도·공통 프로토콜·스키마·선정/병렬 규칙은 조건 간 동일하다.

|작업|산출물|사전 최초 책임자 gold|
|---|---|---|
|release-review|수익성·기술 위험 검토와 출시 권고|B|
|game-design-architecture|게임 기획, 코드 구조, 테스트·일정 설계|A|
|payment-redesign|결제 상태 전이, 중복 방지, 데이터 전환·운영 계획|A|
|growth-roadmap|대안별 손익, MVP, 코드·데이터 변경, 로드맵|B|
|launch-operations|운영 일정, 상담 용량, 고객 안내·비상 계획|C|

gold는 루트 책임자 일치 지표다. 다른 Worker도 업무 일부를 수행할 수 있으며, 하위 작업에는 사후 gold를 붙이지 않는다. homogeneous에서는 전문성이 같아지므로 기존 gold와의 비교는 원래 책임자 ID의 유지 정도로 읽는다. 최종 facts의 사후 평가 답안은 모델 입력에 넣지 않는다. [작업 정의](tasks.json), [산출물 요구사항과 평가 기준](extensions/peer_dag/cases/README.md).

### 실행 설정

- 모델 `deepseek/deepseek-v4.1-flash`, OpenRouter, `provider.only=["fireworks"]`, `require_parameters=true`, temperature=0, reasoning disabled.
- `max_tokens`와 `max_completion_tokens`를 보내지 않는다. 제공업체 자체 기본 한도는 적용될 수 있다.
- 모든 단계에 `response_format.type=json_schema`, `strict=true`를 전달하며 로컬 JSON·타입·업무 규칙 검증을 유지한다.
- `max_depth=5`, `max_tasks=12`, `max_steps=4`, `max_calls=64`, `max_parallel=3`. 루트 깊이는 0이다.
- 소켓 30초, 응답 수신 기한 90초, 응답 본문 2MB. 429는 동일 payload로 최대 6회, 누적 대기 300초이며 기타 재시도 대상 오류는 최대 2회다.
- 외부 실험 실행은 순차로 진행하고 사이에 15초를 둔다. 한 실행은 최대 600초다. 실행 내부 하위 작업의 병렬 처리와 구분한다.
- release-review는 최초 위임을 요구한다. 나머지 네 작업은 직접 실행/위임을 모델이 선택한다.

실험 소스 커밋은 `d3014d2b3fbda00c262d5fd5f56402e03a4019e0`이다. 당시 [manifest](extensions/peer_dag/conditions/20260922T012131-no-token-limit-9703d2/manifest.json)에 파일 해시와 실행 순서를 보존했다. 이후 보고서·사례 README 설명만 정리했고 분석 대상 런타임 코드는 같다. [사전 규약](extensions/peer_dag/conditions/SUITE_NO_TOKEN_LIMIT_PROTOCOL.md), [설정](extensions/peer_dag/config.json), [전체 원본 지표](extensions/peer_dag/conditions/20260922T012131-no-token-limit-9703d2/summary.json).

저장소 루트에서 다음 명령을 사용한다. API 키는 학번 폴더의 로컬 `.env`에 두며 제출하지 않는다. 현재 코드로 새로 실행하면 LLM 응답이 달라질 수 있다. 과거 원문 입력까지 재현하려면 위 소스 커밋을 사용한다.

```bash
# API 호출 없이 작업과 설정 확인
submissions/26622007/.venv/bin/python submissions/26622007/week-03/extensions/peer_dag/cli.py plan --case game-design-architecture

# 현재 코드의 45회 새 실험 실행: 새 ID에 기록
submissions/26622007/.venv/bin/python submissions/26622007/week-03/extensions/peer_dag/suite_study.py run

# API 호출 없이 이 보고서의 배정·위임 집계 재검증
submissions/26622007/.venv/bin/python submissions/26622007/week-03/extensions/peer_dag/conditions/20260922T012131-no-token-limit-9703d2/current_code_evidence.py --check
```

## 2. 현재 코드의 실제 결과

### 최초 배정과 최종 결과를 함께 평가

[현재 코드 배정·위임 근거](extensions/peer_dag/conditions/20260922T012131-no-token-limit-9703d2/CURRENT_CODE_EVIDENCE.json)는 원본 로그의 루트 award, 하위 award, 실제 실행 호출과 task_end를 대조한 파생 자료다. 행마다 원본 경로와 줄 번호가 있다.

|조건|실행 수|루트 gold 일치|루트 gold 불일치|루트 미배정|최종 facts 통과|
|---|---:|---:|---:|---:|---:|
|baseline|15|6|4|5|10|
|homogeneous|15|5|5|5|10|
|overconfident|15|1|9|5|9|
|합계|45|12|18|15|29|

루트 gold 불일치 18건 중 **17건은 최종 facts를 통과**했다. 그 17건 중 12건에서 요청자와 다른 Worker에게 맡긴 하위 작업의 실제 완료를 확인했다. 나머지 5건은 다른 Worker의 완료 없이 통과했다. 따라서 이 관측만으로 위임이 통과의 원인이라고 단정하지 않는다.

본 실험의 최종 실패 16건은 HTTP 429 소진 때문이었다. 15건은 최초 배정 전에 막혔고, 1건은 최초 배정 후 하위 작업 실패가 부모에 전파됐다. facts 통과 29/45와 개별 항목 306/486은 이 실패들도 포함한다. 미배정·배정 불일치·최종 결과 실패를 같은 수치로 취급하지 않는다.

### 실제 위임과 병렬 실행

|조건|루트가 delegate를 선택한 실행|다른 Worker의 하위 작업 완료가 있는 실행|실행 중첩이 있는 실행|동일 Worker 실행 중첩|
|---|---:|---:|---:|---:|
|baseline|6|6|4|0|
|homogeneous|4|4|3|1|
|overconfident|9|5|9|6|
|합계|19|15|16|7|

다른 Worker에게 맡겼다는 것은 하위 작업의 요청자와 낙찰자가 다르다는 뜻이며 전문성 적합도를 자동 판정한 것이 아니다. 실제 완료는 execute/synthesize의 call_start·call_end 및 succeeded task_end로 확인했다. 병렬성은 두 실행 호출의 시간 구간 중첩으로 확인하며 동시 입찰만으로 세지 않는다.

- **출시 검토 baseline 1회차:** 최초 A가 수익성 계산을 B에게 위임하고 기술 검토는 A가 수행했다. B와 A의 실행이 3.773초 중첩했고 두 결과가 완료된 후 출시 권고를 작성했다. [B 배정·실행](extensions/peer_dag/logs/20260922T012131-no-token-limit-9703d2-r1-release-review-baseline.jsonl#L69), [선행 결과 후 권고 시작](extensions/peer_dag/logs/20260922T012131-no-token-limit-9703d2-r1-release-review-baseline.jsonl#L107).
- **게임 overconfident 3회차:** 최초 C가 코드 구조와 게임 기획을 A에게 맡기고 예산·범위 및 통합을 수행했다. 두 A 작업도 서로 11.661초 중첩했다. [코드 구조 배정](extensions/peer_dag/logs/20260922T012131-no-token-limit-9703d2-r3-game-design-architecture-overconfident.jsonl#L109), [게임 기획 배정](extensions/peer_dag/logs/20260922T012131-no-token-limit-9703d2-r3-game-design-architecture-overconfident.jsonl#L139).
- **게임 overconfident 1회차:** 하위 작업까지 전부 C가 선정됐다. 작업 분해와 병렬 실행은 있었지만 다른 Worker로 전문 작업이 넘어가지는 않았다. [하위 배정](extensions/peer_dag/logs/20260922T012131-no-token-limit-9703d2-r1-game-design-architecture-overconfident.jsonl#L75).

실측 깊이는 본 실험에서 0인 실행 26회, 1인 실행 19회다. 허용 상한 5까지 실제로 내려갔다는 의미가 아니다. 루트 배정이 성립한 overconfident 10건은 모두 C였으나, 하위 단계에서는 위 사례처럼 다른 Worker가 선정되기도 했다.

### 429 복구와 응답 형식

본 45회에는 HTTP 요청 810회, 반환 응답 449개, 429 오류 361건이 있었다. 반환된 449개 응답은 모두 스키마 검사를 통과하고 stop으로 종료했다.

실패한 16개 슬롯만 새 ID로 각각 한 번 더 실행한 [별도 복구](extensions/peer_dag/conditions/20260922T012131-no-token-limit-9703d2/recovery_429/REPORT.md)에서는 16건 모두 문서가 생성됐고 facts는 15/16이 통과했다. HTTP 요청 420회 중 429 40건은 재시도로 해소됐으며 응답 380개도 모두 스키마를 준수하고 stop으로 종료했다. 복구의 최대 깊이는 2, 실행 중첩은 11회, 동일 Worker 중첩은 5회였다.

복구를 포함하면 원래 45개 슬롯 모두 최종 문서를 확보했고 44개는 필수 facts가 맞다. **44/45는 복구를 포함한 확보 수이며 최초 성공률이 아니다.** 선택된 실패만 재실행한 16건을 본 실험의 균형 잡힌 조건 비교에 합산하지 않는다.

전체 61회 실행의 실제 요청 1,230개에는 토큰 상한이 없었고 strict response_format이 있었다. 반환 응답 829개에서 JSON 형식 위반이나 length 종료는 없었다. [원본 재검증](extensions/peer_dag/conditions/20260922T012131-no-token-limit-9703d2/evidence_verification.json).

### 산출물 품질과 결정성

정성 점검은 사전에 정한 요구사항에 대한 코딩 보조 에이전트의 대조이며 맹검 심사나 실제 코드 실행 검증이 아니다.

|자료|충족|부분 충족|미충족|
|---|---:|---:|---:|
|본 45회|8|21|16|
|별도 복구 16회|2|14|0|

[본 실험의 문서·근거](extensions/peer_dag/conditions/20260922T012131-no-token-limit-9703d2/QUALITY_REVIEW.md), [복구 문서·근거](extensions/peer_dag/conditions/20260922T012131-no-token-limit-9703d2/recovery_429/QUALITY_REVIEW.md).
게임의 규칙과 타입 불일치, 결제 설계의 원자성·구버전 공존 누락 등은 수치가 맞아도 남았다. 복구의 한 오답은 [공헌이익과 개발 여유 시간의 키 의미 충돌](extensions/peer_dag/conditions/20260922T012131-no-token-limit-9703d2/recovery_429/FACT_KEY_COLLISION.md)로, 통합 결과가 48,000/240,000원 대신 16/-16시간 값을 선택한 사례다.

반복의 최초 루트 제안 입력은 같은 작업·조건·Worker마다 동일했다. 본 실험에서 결과가 있는 반복들의 필수 facts는 같은 작업·조건 안에서 일치했지만, 전체 문서와 계획은 달랐다. 출력 해시는 표현·작업 ID 차이도 반영하므로 의미 차이의 크기로 해석하지 않는다. 실패 때문에 결과가 하나뿐인 묶음도 있어 반복 일치를 일반적인 결정성으로 확대하지 않는다. [반복·과신·동점 상세](extensions/peer_dag/conditions/20260922T012131-no-token-limit-9703d2/FINAL_REPORT.md).

## 3. Smith (1980) 비교 — 원문 대조 작성란

현재 코드의 비교 대상을 아래와 같이 고정한다. Smith 열은 논문 원문과 페이지 근거를 확인한 뒤 작성한다. 기존 기본 배정 코드의 고정 관리자·확신도만으로 낙찰하는 설명을 현재 코드 열에 사용하지 않는다.

|비교 항목|Smith의 분산 센싱 시스템: 원문 확인 후 작성|현재 구현에서 확인한 내용|
|---|---|---|
|참여자|작성 필요|동일한 권한을 가진 A/B/C Worker. 작업마다 요청자가 관리자 역할을 맡음|
|입찰 생성|작성 필요|LLM이 입찰 여부·확신도·이유·직접 실행/위임 계획을 생성|
|입찰의 진실성 보장|작성 필요|확신도를 가린 계획 심사와 로컬 스키마·DAG 검증. 자기 평가·내용 진실성 보장은 없음|
|잘된 배정의 기준|작성 필요|최초 gold 일치, 하위 실제 위임, 최종 facts와 정성 품질을 함께 기록|
|협상 비용|작성 필요|각 작업의 3개 제안 호출, 요청자의 계획 심사, 낙찰 후 실행/통합. 하위 위임마다 협상이 반복됨|
|실패 방식|작성 필요|429 소진, 입찰/심사 부적합, 잘못된 의존성, 하위 실패 전파, 통합 시 값 의미 충돌. 본 실험에서 실제 관측한 실패와 코드상 가능한 실패를 구분|

## 4. 해석 — 현재 코드의 근거로 작성

최종 해석 문단은 아래 관측을 원본 로그와 함께 검토해 작성한다.

- 최초 gold 불일치 18건 중 17건이 필수 facts를 통과했다. 12건은 다른 Worker의 하위 작업 완료가 있었고, 5건은 없어도 통과했다. 최초 배정과 팀 전체 수행을 어떻게 함께 평가할 것인가?
- 과신 조건은 루트 배정이 성립한 10건 모두 C였다. 계획 심사는 있으나 점수 동점 뒤 확신도를 사용하는 선정 규칙의 영향을 검토한다. 하위에서는 C→A 위임과 C의 재독점이 모두 있었다.
- 코드가 결정하는 선택·의존성·실행 규칙과 LLM이 생성하는 계획·판정·문서 내용을 구분한다. 형식 준수와 실측 병렬성은 확인했지만 전체 결과의 완전한 결정성을 확인한 것은 아니다.
- 29/45와 복구 후 44/45를 구분하고, HTTP 장애·내용 오답·정성 품질 부족을 분리한다. 선택적 복구와 소수 반복만으로 조건 간 우열이나 위임의 인과 효과를 주장하지 않는다.

**최종 해석 문단: 작성 필요.**

### 참고 기록과 과제 파일의 범위

루트 [results.csv](results.csv)는 과거 기본 배정 실행기의 역사적 결과이며, 해당 CSV의 전체 표는 [이전 보고서](REPORT_ALLOCATION_REFERENCE_20260922.md)에 보존돼 있다. 현재 보고서 2절은 peer DAG의 [45회 CSV](extensions/peer_dag/conditions/20260922T012131-no-token-limit-9703d2/results.csv), 원본 로그 및 재집계 JSON을 근거로 작성했다. 서로 다른 실행기의 수치나 메시지 정의를 교체·합산하지 않았다.

현재 구조는 강의 README의 고정 manager 1명·contractor 3명 배정 실험을 발전시킨 형태다. 이 차이를 구현 설명에 명시하며, 자동 제출 형식 검사 통과를 구조의 동일성이나 보고서 해석의 완성으로 해석하지 않는다. 과거 2200토큰 실험과 실패 원본도 [이전 최종 기록](extensions/peer_dag/conditions/20260921T113158-suite-ab4e67/FINAL_REPORT.md)에 남아 있다.
