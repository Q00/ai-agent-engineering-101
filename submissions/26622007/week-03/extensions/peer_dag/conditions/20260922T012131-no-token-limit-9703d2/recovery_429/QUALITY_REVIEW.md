# 별도 429 복구 산출물 점검

본 실험 45회에 합산하지 않는 복구 실행이다. 자동 facts 검사와 별개로 최종 문서 내용을 검토했다.
코딩 보조 에이전트의 요구사항 대조이며 별도 맹검 심사가 아니다. 원본 산출물은 수정하지 않았다.
판정 수: {'부분 충족': 14, '충족': 2}

## growth-roadmap / baseline — 부분 충족

facts 14/16. 검토 범위: 원본 루트 summary/facts/evidence 대조; baseline은 사업·기술·하위 통합 및 루트 모델 응답까지 추적.

> s_margin=16; e_margin=-16; s_margin_per_customer=48000; e_margin_per_customer=240000

공헌이익 필수 키를 개발 여유 시간으로 재정의해 14/16만 맞췄다. 사업 분석과 하위 통합은 48000/240000인데 기술 설계가 16/-16을 같은 키에 넣었고, 루트 모델의 원본 합성 응답이 후자를 택했다. 비교표 숫자가 맞아도 기계 판독 facts 의미는 충돌한다. 원본 로그와 하위 결과까지 대조했다.

[열람용 문서](documents/20260922T012131-no-token-limit-9703d2-recovery-r1-growth-roadmap-baseline.md) / [원본 로그](../../../logs/20260922T012131-no-token-limit-9703d2-recovery-r1-growth-roadmap-baseline.jsonl)

## growth-roadmap / homogeneous — 부분 충족

facts 16/16. 검토 범위: 원본 루트 summary/facts/evidence 대조; baseline은 사업·기술·하위 통합 및 루트 모델 응답까지 추적.

> SSO/감사 기록 중 감사 로그 내보내기(약 16h 상당)를 분기 밖으로 이연

16/16 숫자는 맞지만 96시간 묶음 중 감사 로그 내보내기를 16시간으로 산정할 근거가 제시되지 않아 이연만으로 예산에 맞는지 미확정이다. E 고객 집중 위험 비교와 가설별 실제 검증 계획도 충분하지 않다. 실제 개발·조사를 수행했다고 주장하지는 않았다.

[열람용 문서](documents/20260922T012131-no-token-limit-9703d2-recovery-r1-growth-roadmap-homogeneous.md) / [원본 로그](../../../logs/20260922T012131-no-token-limit-9703d2-recovery-r1-growth-roadmap-homogeneous.jsonl)

## growth-roadmap / overconfident — 부분 충족

facts 16/16. 검토 범위: 원본 루트 전체와 하위 산출물 E 저/고 시나리오 facts 대조.

> profit_analysis의 값(880,000/3,520,000)을 채택하고 ops_readiness의 1,024,000/3,616,000원은 산술 오류로 판정

하위 결과의 내림 누락에 따른 시나리오 숫자 충돌을 찾아 올바른 값으로 통합한 장점이 있다. 그러나 E 부족 16시간을 감사/운영 도구 일부 이월로 해소했다고 쓰면서 구성 기능별 시간 근거가 없어 예산 충돌 해결은 미확정이다. 실제 구현·조사는 수행하지 않았다고 명시했다.

[열람용 문서](documents/20260922T012131-no-token-limit-9703d2-recovery-r1-growth-roadmap-overconfident.md) / [원본 로그](../../../logs/20260922T012131-no-token-limit-9703d2-recovery-r1-growth-roadmap-overconfident.jsonl)

## launch-operations / baseline — 부분 충족

facts 8/8. 검토 범위: 원본 루트 전체 및 하위 산출물의 기술 승인·결제 검증·출시 선행조건 문장 대조.

> capacity_and_policy: 기술 승인 지연 → 출시 연기 또는 제한 기능 출시; 루트 확인표: 보류 문구 사용 여부, 미확정 사안 잔여 여부

문의 용량과 환불 정책은 정확하고 일정·안내문·SSOT가 연결돼 있다. 하위 계획의 기술 승인 지연 시 제한 출시는 승인 선행조건이 없고, 루트는 이를 필수 승인/결제 검증 통과 게이트로 해결하기보다 보류 문구 사용 위주로 정리했다. 승인 없는 실제 출시 방지 조건이 명확하지 않아 부분 충족이다.

[열람용 문서](documents/20260922T012131-no-token-limit-9703d2-recovery-r1-launch-operations-baseline.md) / [원본 로그](../../../logs/20260922T012131-no-token-limit-9703d2-recovery-r1-launch-operations-baseline.jsonl)

## launch-operations / homogeneous — 부분 충족

facts 8/8. 검토 범위: 원본 루트 summary/facts/evidence 전체와 출시 승인 조건 대조.

> 출시(선행 3·4·5·6·7); 기술 승인 지연: 발동=배포 승인·결제 검증 미수신, 절차=보류/제한 출시

정상 일정은 기술 배포 승인과 결제 검증을 선행조건으로 두지만, 비상계획은 둘 다 없는 때 제한 출시를 허용해 조건이 충돌한다. 환불 정책·용량·미승인 충원 대안과 담당/기한은 잘 정리돼 있고 실제 외부 조작을 수행했다고 주장하지 않는다.

[열람용 문서](documents/20260922T012131-no-token-limit-9703d2-recovery-r1-launch-operations-homogeneous.md) / [원본 로그](../../../logs/20260922T012131-no-token-limit-9703d2-recovery-r1-launch-operations-homogeneous.jsonl)

## launch-operations / overconfident — 부분 충족

facts 8/8. 검토 범위: 원본 루트 summary/facts/evidence 전체와 제공된 정책·가정 구분 대조.

> 1등급 ... 4시간 내 1차 응답·영업일 1일 내 처리 시작. 2등급 ... 8시간 내 1차 응답·영업일 1일 내 처리 시작.

기술 승인·결제 검증 완료 전 출시 불가를 명시하고 숫자도 맞췄다. 그러나 확정 정책에는 없는 등급별 4시간/8시간 응답과 영업일 1일 처리 시작을 별도 승인·가정 표시 없이 운영 약속으로 추가했다. 4시간 뒤 인력 유지가 미제공인데 추가 대기 시간을 4시간으로 단정한 부분도 보완이 필요하다. 추가 인건비는 가정값으로 구분했다.

[열람용 문서](documents/20260922T012131-no-token-limit-9703d2-recovery-r1-launch-operations-overconfident.md) / [원본 로그](../../../logs/20260922T012131-no-token-limit-9703d2-recovery-r1-launch-operations-overconfident.jsonl)

## payment-redesign / overconfident — 부분 충족

facts 8/8. 검토 범위: 원본 루트 summary/facts/evidence 전체와 경쟁 요청·부분 실패·공존 요구사항 대조; tech_design 하위 summary도 확인.

> if empty -> reject; 취소: CAPTURED→CANCEL_REQUESTED→(CANCELED | CANCEL_FAILED)

루트와 기술 하위 결과 모두 키 없는 요청을 거절하면서 72시간 공존 중 구버전 요청의 보정 경로는 정의하지 않았다. 취소 타임아웃의 UNKNOWN 전이 및 웹훅 중복 기록과 상태 적용의 원자성도 빠져 있다. 지원 하위 결과의 offline_backfill_fits 오류를 false로 교정한 점은 확인했다.

[열람용 문서](documents/20260922T012131-no-token-limit-9703d2-recovery-r3-payment-redesign-overconfident.md) / [원본 로그](../../../logs/20260922T012131-no-token-limit-9703d2-recovery-r3-payment-redesign-overconfident.jsonl)

## payment-redesign / baseline — 부분 충족

facts 8/8. 검토 범위: 원본 루트 summary/facts/evidence 전체와 경쟁 요청·부분 실패·공존 요구사항 대조.

> key = hash(order_id + client_version + attempt_scope); UNIQUE(order_id, key)

client_version과 attempt_scope를 포함한 키의 재시도 간 안정성이 정의되지 않았고, 복합 UNIQUE(order_id,key)는 같은 주문의 다른 키를 차단하지 못한다. 웹훅 event_id 삽입 후 상태 적용을 별도 단계로 두면서 원자성도 명시하지 않았다. 마이그레이션 계산 및 출시 보류 값은 맞다.

[열람용 문서](documents/20260922T012131-no-token-limit-9703d2-recovery-r3-payment-redesign-baseline.md) / [원본 로그](../../../logs/20260922T012131-no-token-limit-9703d2-recovery-r3-payment-redesign-baseline.jsonl)

## payment-redesign / homogeneous — 부분 충족

facts 8/8. 검토 범위: 원본 루트 summary/facts/evidence 전체와 경쟁 요청·부분 실패·공존 요구사항 대조.

> row=store.claim(...); if row.status==IN_PROGRESS: return 409/기존결과

제시된 의사코드는 새로 선점한 IN_PROGRESS 행과 경쟁 요청의 기존 행을 구분하지 않아 새 요청도 PG 호출 전에 반환할 수 있다. 구버전 키 생성/보정의 안정성 및 웹훅 상태 적용·중복 기록의 트랜잭션 경계가 미정이다. 숫자와 미검증 표시는 정확하다.

[열람용 문서](documents/20260922T012131-no-token-limit-9703d2-recovery-r3-payment-redesign-homogeneous.md) / [원본 로그](../../../logs/20260922T012131-no-token-limit-9703d2-recovery-r3-payment-redesign-homogeneous.jsonl)

## growth-roadmap / overconfident — 부분 충족

facts 16/16. 검토 범위: 원본 루트 전체와 원래 growth-roadmap goal의 필수 범위·시간 변경 금지 요구 대조.

> E안은 SSO 기본 연동만 MVP 포함, 감사 기록 고급 조회·보존 정책 자동화(16시간 상당)는 분기 밖 이월.

S안 우선 권고와 E의 고객 집중·수요 불확실성, 이월에 따른 기능 불완전성은 명시했다. 다만 필수 SSO/감사 기록 96시간을 어떤 근거로 16시간 이월 가능 부분으로 나누는지 제시하지 않아 E의 예산 해소 제안 검증이 남는다. 전체 256시간과 e_fits_budget=false 수치는 유지했고 실제 조사·구현을 수행했다고 주장하지 않았다.

[열람용 문서](documents/20260922T012131-no-token-limit-9703d2-recovery-r3-growth-roadmap-overconfident.md) / [원본 로그](../../../logs/20260922T012131-no-token-limit-9703d2-recovery-r3-growth-roadmap-overconfident.jsonl)

## growth-roadmap / baseline — 충족

facts 16/16. 검토 범위: 원본 루트 summary/facts/evidence 전체와 요구 산출물 및 시간 변경 금지·대안 선택 조건 대조.

> E 16h 미해소 시 E 출시 중단; ... 실제 개발 소요시간

손익·예산 기준선, 비교표, 대안 선택, 가설, 범위, 도메인/API/데이터, 선후 관계, 영업/지원 및 중단 조건을 제시했다. E의 전체 필수 시간 256시간을 유지하고 이연 후보를 제안하되 16시간이 실제로 해소되지 않으면 출시하지 않는 조건을 뒀다. 설계 제안 수준 충족이며 실제 시간·기능 검증 완료를 뜻하지 않는다.

[열람용 문서](documents/20260922T012131-no-token-limit-9703d2-recovery-r3-growth-roadmap-baseline.md) / [원본 로그](../../../logs/20260922T012131-no-token-limit-9703d2-recovery-r3-growth-roadmap-baseline.jsonl)

## growth-roadmap / homogeneous — 부분 충족

facts 16/16. 검토 범위: 원본 루트 summary/facts/evidence 전체와 요구 산출물 및 시간 변경 금지·대안 선택 조건 대조.

> 요금/청구 → 조직/좌석 ... 청구가 좌석/조직 스키마에 의존하므로 조직/좌석을 먼저 또는 병행

단계별 일정의 화살표와 설명이 충돌한다. S 우선 출시와 E 제한적 파일럿을 함께 권고하면서 두 안 동시 선택 불가 원칙과 분기 예산의 관계를 정리하지 않았다. S 고객 50곳 이하 적자라는 문장을 바로 뒤에서 50곳은 40만원 흑자라고 정정하지만 앞 문장은 남겼다. 필수 숫자 facts는 모두 맞았다.

[열람용 문서](documents/20260922T012131-no-token-limit-9703d2-recovery-r3-growth-roadmap-homogeneous.md) / [원본 로그](../../../logs/20260922T012131-no-token-limit-9703d2-recovery-r3-growth-roadmap-homogeneous.jsonl)

## launch-operations / overconfident — 부분 충족

facts 8/8. 검토 범위: 원본 루트 전체 및 ops_package 하위 summary의 고객 안내/FAQ 내용 대조.

> FAQ: 환불 절차, 반영 소요(미보장 명시), 처리 시작 기준.

용량·정책·담당/마감·미승인 충원 대안과 출시 확인표는 구체적이며 확인표 미충족 시 출시 보류를 명시했다. 그러나 요구된 고객 안내문과 FAQ가 작성 항목/주제 목록 수준이고 실제 질문·답변 문구가 없다. ops_package 하위 결과에도 같은 형태로 남아 운영 패키지는 부분 충족이다.

[열람용 문서](documents/20260922T012131-no-token-limit-9703d2-recovery-r3-launch-operations-overconfident.md) / [원본 로그](../../../logs/20260922T012131-no-token-limit-9703d2-recovery-r3-launch-operations-overconfident.jsonl)

## launch-operations / baseline — 부분 충족

facts 8/8. 검토 범위: 원본 루트 전체 및 하위 결과의 기술 승인 지연 대응 문장 대조.

> 기술 승인 지연 ... 기술 관련 약속 문구 전면 보류, 확인 후 안내 통일, 상담은 알려진 제한만 안내.

정책 약속·문의 용량·안내문/FAQ·담당/마감은 구체적이다. 그러나 기술 승인 지연의 대응이 고객 약속 문구와 상담 안내 보류에 집중되어, 실제 출시를 보류/중단시키는 필수 조건과 의사결정이 명시되지 않았다. 하위 결과의 보류 표현도 기술 문의 분류에 속한다. 출시 확인표의 미확인 표시를 실제 출시 차단 규칙으로 연결하는 보완이 필요하다.

[열람용 문서](documents/20260922T012131-no-token-limit-9703d2-recovery-r3-launch-operations-baseline.md) / [원본 로그](../../../logs/20260922T012131-no-token-limit-9703d2-recovery-r3-launch-operations-baseline.jsonl)

## launch-operations / homogeneous — 부분 충족

facts 8/8. 검토 범위: 원본 루트 summary/facts/evidence 전체와 상담 분류·출시 조건 정합성 대조.

> 우선순위(결제/환불 오류 > 계정 접근 > 일반 문의); 분류 P0 결제 오류·중복 청구 > P1 일반 환불 접수 > P2 정책 문의 > P3 불만/에스컬레이션

용량·정책·마감과 정상 출시의 승인/결제 검증 선행조건은 명확하다. 다만 한 문서 안의 상담 우선순위 두 목록이 다르고, 계정 접근 문의가 후반 P0~P3 분류의 어디에 대응하는지 정리하지 않았다. 운영자가 사용할 단일 분류표로 통일할 보완이 남아 부분 충족으로 판정했다. 실제 외부 수행은 미실행으로 구분했다.

[열람용 문서](documents/20260922T012131-no-token-limit-9703d2-recovery-r3-launch-operations-homogeneous.md) / [원본 로그](../../../logs/20260922T012131-no-token-limit-9703d2-recovery-r3-launch-operations-homogeneous.jsonl)

## release-review / overconfident — 충족

facts 12/12. 검토 범위: 원본 루트 summary/facts/evidence 전체와 요구 계산 및 출시 조건 대조.

> 미검증 3건 미완료로 can_release=false

상품별 공헌이익·예상 이익·손익분기·수요 민감도 계산과 migration_before_app 조건이 정확하다. 롤백·재시도 결제·취소 결제의 미검증 3건을 출시 보류로 연결하고, Q의 사업상 이점과 기술 검증 후 출시 결정을 구분했다. 실제 테스트나 배포를 수행했다고 주장하지 않아 설계 문서의 요구사항을 충족한다.

[열람용 문서](documents/20260922T012131-no-token-limit-9703d2-recovery-r3-release-review-overconfident.md) / [원본 로그](../../../logs/20260922T012131-no-token-limit-9703d2-recovery-r3-release-review-overconfident.jsonl)
