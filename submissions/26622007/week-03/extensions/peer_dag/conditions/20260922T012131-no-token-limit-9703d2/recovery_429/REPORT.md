# 429 실패의 별도 복구 확인

본 45회 종료 후 429로 실패한 16회를 각각 새 ID로 한 번씩 실행했다. 통과 15/16.
원래 실패와 본 실험의 45회 성공률은 변경하지 않았다. 아래 실행은 추가적인 복구 확인이다.
[규약](../HTTP429_RECOVERY_PROTOCOL.md), [상세 지표](summary.json), [최초 45회 결과](../summary.json).

|원래 실행|새 실행|결과|facts|429|
|---|---|---|---:|---:|
|20260922T012131-no-token-limit-9703d2-r1-growth-roadmap-baseline|20260922T012131-no-token-limit-9703d2-recovery-r1-growth-roadmap-baseline|succeeded / passed=False|14/16|4|
|20260922T012131-no-token-limit-9703d2-r1-growth-roadmap-homogeneous|20260922T012131-no-token-limit-9703d2-recovery-r1-growth-roadmap-homogeneous|succeeded / passed=True|16/16|0|
|20260922T012131-no-token-limit-9703d2-r1-growth-roadmap-overconfident|20260922T012131-no-token-limit-9703d2-recovery-r1-growth-roadmap-overconfident|succeeded / passed=True|16/16|2|
|20260922T012131-no-token-limit-9703d2-r1-launch-operations-baseline|20260922T012131-no-token-limit-9703d2-recovery-r1-launch-operations-baseline|succeeded / passed=True|8/8|0|
|20260922T012131-no-token-limit-9703d2-r1-launch-operations-homogeneous|20260922T012131-no-token-limit-9703d2-recovery-r1-launch-operations-homogeneous|succeeded / passed=True|8/8|2|
|20260922T012131-no-token-limit-9703d2-r1-launch-operations-overconfident|20260922T012131-no-token-limit-9703d2-recovery-r1-launch-operations-overconfident|succeeded / passed=True|8/8|3|
|20260922T012131-no-token-limit-9703d2-r3-payment-redesign-overconfident|20260922T012131-no-token-limit-9703d2-recovery-r3-payment-redesign-overconfident|succeeded / passed=True|8/8|4|
|20260922T012131-no-token-limit-9703d2-r3-payment-redesign-baseline|20260922T012131-no-token-limit-9703d2-recovery-r3-payment-redesign-baseline|succeeded / passed=True|8/8|5|
|20260922T012131-no-token-limit-9703d2-r3-payment-redesign-homogeneous|20260922T012131-no-token-limit-9703d2-recovery-r3-payment-redesign-homogeneous|succeeded / passed=True|8/8|0|
|20260922T012131-no-token-limit-9703d2-r3-growth-roadmap-overconfident|20260922T012131-no-token-limit-9703d2-recovery-r3-growth-roadmap-overconfident|succeeded / passed=True|16/16|0|
|20260922T012131-no-token-limit-9703d2-r3-growth-roadmap-baseline|20260922T012131-no-token-limit-9703d2-recovery-r3-growth-roadmap-baseline|succeeded / passed=True|16/16|0|
|20260922T012131-no-token-limit-9703d2-r3-growth-roadmap-homogeneous|20260922T012131-no-token-limit-9703d2-recovery-r3-growth-roadmap-homogeneous|succeeded / passed=True|16/16|0|
|20260922T012131-no-token-limit-9703d2-r3-launch-operations-overconfident|20260922T012131-no-token-limit-9703d2-recovery-r3-launch-operations-overconfident|succeeded / passed=True|8/8|6|
|20260922T012131-no-token-limit-9703d2-r3-launch-operations-baseline|20260922T012131-no-token-limit-9703d2-recovery-r3-launch-operations-baseline|succeeded / passed=True|8/8|2|
|20260922T012131-no-token-limit-9703d2-r3-launch-operations-homogeneous|20260922T012131-no-token-limit-9703d2-recovery-r3-launch-operations-homogeneous|succeeded / passed=True|8/8|12|
|20260922T012131-no-token-limit-9703d2-r3-release-review-overconfident|20260922T012131-no-token-limit-9703d2-recovery-r3-release-review-overconfident|succeeded / passed=True|12/12|0|

토큰 상한 부재=True, 요청 검사=True, 응답 검사=True, 실행 추적 검사=True.
HTTP 요청 420회, 응답 380개, 종료 사유 {'stop': 380}.
응답 보고 비용 합계 $0.41647225. 없는 응답의 비용은 추정하지 않는다.
facts 자동 검사는 산출물의 모든 설계·정책 내용이 맞다는 뜻이 아니다.
