# Week 03 보고서 — 실제 실험 기록과 해석 초안

2026-09-22: 사용자 요청에 따라 기본/peer 설정의 출력 토큰 상한을 제거하고 [새 규약](extensions/peer_dag/conditions/SUITE_NO_TOKEN_LIMIT_PROTOCOL.md)으로 45회를 재실험했다.
[최신 결과](extensions/peer_dag/conditions/20260922T012131-no-token-limit-9703d2/FINAL_REPORT.md)는 최초 facts 통과 29/45, 별도 429 복구 15/16이다.
이전 `max_tokens=2200`은 구현 에이전트가 임의로 넣은 설정이었다. 그 상한에서 잘린 17회와 과거 28/45 기록을 최신 결과와 구분한다.
기본 배정 9회의 과거 512토큰 설정과 결과도 그대로 보존한다.

**동일 5개 작업의 기본 배정 9회, 출력 상한 제거 후 peer DAG 수행 45회와 별도 복구 16회를 완료했다. 아래는 관측 기록이며 3·4절의 논문 비교와 최종 해석은 사용자 작성·검토가 남아 있다.**
Codex가 사용자의 설계 지시에 따라 실행기와 합성 사례를 구현하고 로그를 보존했다. 자동 형식 검사 통과를 보고서 해석의 완성으로 보지 않는다.

## 1. 설정과 재현

- 기본 실험: 고정 manager 코드와 A 제품·기술, B 사업·분석, C 운영·커뮤니케이션의 독립 입찰. 계획/작업 수행은 하지 않는다.
- 모델 deepseek/deepseek-v4.1-flash, OpenRouter, provider.only=[fireworks], require_parameters=true, temperature=0, max_tokens=512, reasoning disabled.
- 모든 입찰의 실제 payload에 strict JSON Schema response_format을 전달했다. 135개 응답을 스키마 검증했다.
- 최고 확신도를 선택하고 동점은 A→B→C 순이다. 작업×에이전트마다 새 두 메시지 문맥이며 기억·정답·결과 피드백은 입력에 없다.
- baseline은 전문성이 다른 세 역할, homogeneous는 같은 일반 역할, overconfident는 baseline의 C에 전 작업 고확신 입찰 지시만 추가한다.
- 현재 tasks.json은 5개 복합 작업이고 gold는 A 2개, B 2개, C 1개다. 입력/gold는 실제 실행 전에 커밋했다.
- 최종 배정의 별도 설정: 요청 30초, 응답 수신 기한 90초, 일반 오류 최대 2회, 429 최대 6회·누적 대기 300초, 전체 HTTP 상한 810회. 세 조건에서 동일하다.
- 실행 순서 BHO/HOB/OBH, 회차 사이 15초. 실패 교체 없이 9회 모두 보존했다.
- 실행 코드 커밋 `0827891d27dc7fec63ed807b417762a9578905e7`, 설정 ID `870e29e83d8e340a`. [실제 설정과 전체 프롬프트](task_sets/complex-final/20260921T123430-d7073d/manifest.json).
- 실행: `submissions/26622007/.venv/bin/python -u submissions/26622007/week-03/task_sets/complex-final/allocation_study.py run` (저장소 루트).
- [설정](task_sets/complex-final/config.json), [사전 규약](task_sets/complex-final/PROTOCOL.md), [검증](task_sets/complex-final/20260921T123430-d7073d/verification.json).

## 2. 실제 결과

|조건|반복별 정답/5|정답 합계|메시지 합계|미배정|오배정|
|---|---|---:|---:|---:|---:|
|baseline|2, 4, 3|9/15|102|0|6|
|homogeneous|2, 1, 2|5/15|105|0|10|
|overconfident|1, 1, 1|3/15|101|0|12|

실제 HTTP 요청 139회, 응답 135개. 429 4건을 재시도했고 9회 모두 완료했다. 응답 보고 비용 합계 $0.016138446.
정답 배정은 사전 역할 일치이며 실제 결과물의 정확도와 다르다. 메시지는 공고+bid=true의 유효 입찰+낙찰이고 거절은 별도다. 전체 API 요청/응답 수와 혼동하지 않는다.

### results.csv의 전체 기록

새 9회와 과거 5행을 모두 표시한다. 과거 행은 입력과 설정이 다르므로 위 합계에 섞지 않는다. —는 crashed 행의 빈 수치다.

|범위|run / 로그|조건|작업|정답|메시지|미배정|오배정|상태|
|---|---|---|---:|---:|---:|---:|---:|---|
|과거 설정|[20260915T121020-423c5498](logs/20260915T121020-423c5498-baseline.log)|baseline|—|—|—|—|—|crashed: CallError: HTTP 429|
|과거 설정|[20260915T121106-1358bb2f](logs/20260915T121106-1358bb2f-baseline.log)|baseline|—|—|—|—|—|crashed: CallError: HTTP 429|
|과거 설정|[20260915T121151-75d78906](logs/20260915T121151-75d78906-baseline.log)|baseline|—|—|—|—|—|crashed: CallError: HTTP 429|
|과거 설정|[20260915T121417-95790cf5](logs/20260915T121417-95790cf5-baseline.log)|baseline|6|6|36|0|0|completed|
|과거 설정|[20260915T121722-fb1a7bb5](logs/20260915T121722-fb1a7bb5-baseline.log)|baseline|6|6|35|0|0|completed|
|최종 5개|[20260921T123430-28fc4e6b](logs/20260921T123430-28fc4e6b-baseline.log)|baseline|5|2|34|0|3|completed|
|최종 5개|[20260921T123513-ca879a6b](logs/20260921T123513-ca879a6b-homogeneous.log)|homogeneous|5|2|35|0|3|completed|
|최종 5개|[20260921T123556-be15b1e6](logs/20260921T123556-be15b1e6-overconfident.log)|overconfident|5|1|34|0|4|completed|
|최종 5개|[20260921T123705-7aa8423f](logs/20260921T123705-7aa8423f-homogeneous.log)|homogeneous|5|1|35|0|4|completed|
|최종 5개|[20260921T123744-ccaef1fb](logs/20260921T123744-ccaef1fb-overconfident.log)|overconfident|5|1|33|0|4|completed|
|최종 5개|[20260921T123826-511eb0b7](logs/20260921T123826-511eb0b7-baseline.log)|baseline|5|4|34|0|1|completed|
|최종 5개|[20260921T123953-39e10c19](logs/20260921T123953-39e10c19-overconfident.log)|overconfident|5|1|34|0|4|completed|
|최종 5개|[20260921T124032-cae41fa0](logs/20260921T124032-cae41fa0-baseline.log)|baseline|5|3|34|0|2|completed|
|최종 5개|[20260921T124114-1d0e91a6](logs/20260921T124114-1d0e91a6-homogeneous.log)|homogeneous|5|2|35|0|3|completed|

[이전 보고서 원본](REPORT_LEGACY_20260915.md)과 [이전 6개 작업](task_sets/launch-v1/tasks.json)도 보존했다.

### 최신 확장: 출력 상한 제거 후 peer DAG 45회와 429 복구

[45회 최종 보고서](extensions/peer_dag/conditions/20260922T012131-no-token-limit-9703d2/FINAL_REPORT.md), [정성 점검](extensions/peer_dag/conditions/20260922T012131-no-token-limit-9703d2/QUALITY_REVIEW.md), [61회 증거 검증](extensions/peer_dag/conditions/20260922T012131-no-token-limit-9703d2/evidence_verification.json).
기존 작업·프롬프트·스키마·실행 순서를 유지하고 API 요청의 `max_tokens`/`max_completion_tokens`를 생략했다. 모든 단계는 strict JSON Schema를 사용한다.
baseline 10/15, homogeneous 10/15, overconfident 9/15가 필수 facts를 통과했다. 총 29/45, 306/486 fields이며 나머지 16회는 HTTP 429 소진 실패다.
응답 449개는 모두 스키마를 준수하고 stop으로 종료됐다. 실행 중첩은 16회, 동일 Worker 중첩은 7회, 실제 최대 깊이는 1이다. 설정의 허용 깊이 5와 구분한다.

429 실패 16회만 새 ID로 각각 한 번 [복구 실행](extensions/peer_dag/conditions/20260922T012131-no-token-limit-9703d2/recovery_429/REPORT.md)했다. 16개 문서를 생성했고 facts는 15/16이 통과했다.
복구의 응답 380개도 모두 스키마를 준수하고 stop으로 종료됐다. 복구의 실제 최대 깊이는 2이며 실행 중첩 11회, 동일 Worker 중첩 5회다.
복구까지 포함해 원래 45개 슬롯 모두 문서를 확보했고 44개는 필수 facts가 맞다. 남은 한 건은 [공헌이익과 개발 여유 시간의 키 의미 충돌](extensions/peer_dag/conditions/20260922T012131-no-token-limit-9703d2/recovery_429/FACT_KEY_COLLISION.md)로 14/16이다.
44/45는 최초 성공률이 아니며, 실패만 선택한 복구 16회를 균형 잡힌 조건 비교 표에 합산하지 않는다.
문서 정성 점검은 본 실험 충족 8·부분 충족 21·미충족 16, [복구 문서](extensions/peer_dag/conditions/20260922T012131-no-token-limit-9703d2/recovery_429/QUALITY_REVIEW.md) 충족 2·부분 충족 14다. 자동 facts 통과를 문서의 모든 설계가 맞다는 뜻으로 해석하지 않는다.

### 과거 확장: 2200토큰 상한의 peer DAG 작업 수행 45회

[최종 확장 보고서](extensions/peer_dag/conditions/20260921T113158-suite-ab4e67/FINAL_REPORT.md)와 [산출물 품질 점검](extensions/peer_dag/conditions/20260921T113158-suite-ab4e67/QUALITY_REVIEW.md)을 참고한다.
baseline 8/15, homogeneous 11/15, overconfident 9/15가 필수 facts를 통과했다. 총 28/45, 290/486 fields. 실패 17회는 2200토큰 잘림이며 부분 결과도 보존했다.
실행 중첩 20회, 동일 Worker 중첩 12회, 실제 최대 깊이 2. 고정 응답 직렬·병렬 재생 10회는 상태·평가·전체 하위 산출물·호출 수가 모두 일치했다.
작업 수행과 문서 품질 실험이므로 기본 Contract Net 배정 결과에 합산하지 않는다. 최대 토큰·프롬프트·프로토콜도 다르므로 두 표를 직접 성능 향상 비교로 사용하지 않는다.

## 3. Smith (1980) 비교 — 사용자가 작성

필수 논문을 읽고 원문 근거와 실제 구현을 대조한다.

| 비교 항목 | Smith의 분산 센싱 시스템 | 이번 구현에서 확인한 내용 |
|---|---|---|
| 참여자 | 작성 필요 | 작성 필요 |
| 입찰 생성 | 작성 필요 | 작성 필요 |
| 입찰의 진실성 보장 | 작성 필요 | 작성 필요 |
| 잘된 배정의 기준 | 작성 필요 | 작성 필요 |
| 협상 비용 | 작성 필요 | 작성 필요 |
| 실패 방식 | 작성 필요 | 작성 필요 |

## 4. 해석 — 사용자가 작성

baseline 대비 어떤 지표가 변했는가? 대표 로그의 파일·줄 번호를 인용한다.
C의 과신 지시가 실제 입찰에 나타났는가? 낙찰이 유지됐다면 동점·확신도·파싱 실패 중 무엇이 설명하는가?
메시지 비용과 HTTP 요청·토큰 비용은 어떻게 다른가?
여기서 gold는 실제 업무 수행 성능이 아니라 사전 역할 정의에 따른 책임 담당자다. 이 평가 범위와 5개 복합 합성 작업의 한계를 명시한다.
