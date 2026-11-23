# 기본 배정과 최신 작업 수행의 비교

429 복구를 포함한 최종 45개 슬롯을 사용한다. 수치와 파일별 해시는 [comparison.json](comparison.json), 재계산 코드는 [compare_results.py](compare_results.py)에 있다. 기존 로그·산출물은 수정하지 않았다.

## 무엇이 개선됐는가

기본 실험은 **LLM 3명의 입찰을 받아 담당자를 고르는 단계까지만** 수행했다. 최신 실험은 담당자가 실제 문서를 작성하거나 작업을 재위임하고 통합한다. 따라서 실제 수행·협업·검증 근거가 추가됐다는 것은 확인할 수 있지만, 기본 실험에는 비교할 작업 결과물이 없어 품질 향상률을 계산할 수 없다.

|조건|기본 정답/15|최신 정답/15|기본 메시지|최신 메시지|
|---|---:|---:|---:|---:|
|baseline|9|7|102|371|
|homogeneous|5|6|105|301|
|overconfident|3|4|101|462|
|합계|17|17|308|1,134|

최초 gold 일치는 전체적으로 개선되지 않았다. 최신 메시지에는 하위 협상까지 포함되며, 심사·실행 호출과 중단 시도의 소비량은 이 표에서 제외한다. 모델·온도·5개 루트 gold는 같지만 프롬프트·계획 심사·출력 상한·실행 단위가 달라 선정 규칙 하나의 효과로 해석할 수 없다. [기본 실험 설정·원본](../task_sets/complex-final/20260921T123430-d7073d/manifest.json).

## 결과물의 양과 상세도

최신 완료 45건 중 직접 수행은 15건, 하위 위임은 30건이다. 최종 JSON 문서 45개와 하위 JSON 문서 117개, 총 **162개**를 생성했다. 계획·로그는 문서 수에 넣지 않았다.

아래는 같은 작업 종류 안의 관측 비교다. `summary` 문자열의 공백 포함 문자 수를 측정했으며, facts/evidence·JSON 직렬화는 제외했다. 조건과 실행 방식은 무작위 배정하지 않았고, 하위 내용과 최종 요약의 중복도 제거하지 않았다.

|작업|직접/위임 건수|최종 summary 평균 문자: 직접→위임|하위 포함 문서 평균: 직접→위임|LLM 호출 평균: 직접→위임|
|---|---:|---:|---:|---:|
|게임 기획·구조|5 / 4|2,447 → 2,830|1 → 4.50|5 → 22.50|
|사업 확장|3 / 6|2,653 → 2,733|1 → 4.83|5 → 24.17|
|출시 운영|2 / 7|2,344 → 2,093|1 → 6.57|5 → 32.86|
|결제 재설계|5 / 4|3,841 → 3,053|1 → 4.50|5 → 22.50|

출시 검토 9건은 입력에서 위임을 요구해 직접 수행 비교가 없다. 게임의 하위 포함 summary 평균은 2,447→9,976자로 늘었지만 최종 요약은 약 16% 증가에 그쳤다. 결제·출시 운영의 최종 요약은 오히려 짧았다. **검토 흔적과 중간 산출물은 늘었지만 문서 길이가 품질 향상을 뜻하지는 않는다.** 직접 수행에도 팀의 계획·심사 호출이 먼저 있으므로 이를 순수 단일 에이전트 대조군이라고 부를 수 없다.

## 추가로 고려된 사항과 남은 누락

- **구체화된 위험:** homogeneous 게임의 [2회 직접 수행 문서](../extensions/peer_dag/runs/20260922T012131-no-token-limit-9703d2-r2-game-design-architecture-homogeneous/artifacts/game-design-architecture.json)에는 없던 입력 큐와 저장 마이그레이션의 미확정 상태가 [3회 위임 문서](../extensions/peer_dag/runs/20260922T012131-no-token-limit-9703d2-r3-game-design-architecture-homogeneous/artifacts/game-design-architecture.json)에 명시됐다. 다만 위임 문서도 RNG 상태 복원 경로와 주차별 일정이 빠져 있었다. 서로 다른 회차의 예시이며 위임 덕분이라고 단정할 수 없다.
- **통합 중 교정:** 결제 복구 실행의 [상담 하위 결과](../extensions/peer_dag/runs/20260922T012131-no-token-limit-9703d2-recovery-r3-payment-redesign-overconfident/artifacts/payment-redesign/support_comms.json)는 `offline_backfill_fits=true`라고 했지만 [계산 결과](../extensions/peer_dag/runs/20260922T012131-no-token-limit-9703d2-recovery-r3-payment-redesign-overconfident/artifacts/payment-redesign/migration_calc.json)는 1,800초로 1,200초 중단 한도를 넘겼다. [최종 통합](../extensions/peer_dag/runs/20260922T012131-no-token-limit-9703d2-recovery-r3-payment-redesign-overconfident/artifacts/payment-redesign.json)은 충돌을 명시하고 false로 교정했다. 협업 중 상충하는 결과를 대조한 실제 사례다.
- **반대 사례:** 사업 확장 복구에서는 공헌이익과 개발 여유 시간을 같은 `margin`으로 해석해 [통합 오류](../extensions/peer_dag/conditions/20260922T012131-no-token-limit-9703d2/recovery_429/FACT_KEY_COLLISION.md)가 남았다. 위임은 오류를 잡을 기회를 주지만 새로운 의미 충돌도 만든다.

기존 정성 검토의 완료 문서 판정은 **충족 10·부분 충족 35**였다([최초 검토](../extensions/peer_dag/conditions/20260922T012131-no-token-limit-9703d2/QUALITY_REVIEW.md), [복구 검토](../extensions/peer_dag/conditions/20260922T012131-no-token-limit-9703d2/recovery_429/QUALITY_REVIEW.md)). facts 44/45와 별개이며, 블라인드 품질 평가나 실제 코드 실행 검증은 아니다. 단일 에이전트가 놓친 항목을 더 잘 찾는다는 결론에는 동일 입력·모델·평가 기준의 별도 대조 실험이 필요하다.

## 왜 깊이와 병렬도가 크지 않았는가

|지표|관측 분포|설정 상한|
|---|---|---:|
|최대 재귀 깊이|0: 15건 / 1: 25건 / 2: 5건|5|
|최대 동시 실행·통합 호출|1: 19건 / 2: 21건 / 3: 5건|3|
|계획·심사까지 포함한 최대 동시 호출|45건 모두 3|3|

전체 61개 시도에서 작업 수는 최대 9/12, 모델 호출은 최대 45/64여서 이 두 예산을 소진한 기록은 없다. 재귀 깊이는 40/45건에서 1 이하였으며, 제공 자료만 분석하고 외부 탐색·실제 구현을 수행하지 않는 작업 특성상 얕은 분해로 처리했을 가능성이 있다. 그러나 작업 복잡도를 조절한 실험이 아니므로 모델의 분해 선호나 프롬프트 영향과 구분할 수 없다. 실행 병렬도는 상한 3을 실제로 사용한 5건이 있어 단순히 복잡도가 낮아서 더 커지지 않았다고 말할 수 없다.

```bash
python3 submissions/26622007/week-03/submission/compare_results.py --check
```
