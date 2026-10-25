# 동일 복합 작업 5개 기본 배정 실험

peer DAG의 실제 업무 수행 45회와 별개로, 원래 강의 Contract Net에서 역할에 맞는 담당자를 고르는 9회를 실행했다.
실행 커밋 `0827891d27dc7fec63ed807b417762a9578905e7`, 설정 ID `870e29e83d8e340a`. [설정](../config.json), [규약](../PROTOCOL.md), [검증](verification.json).
모델/프롬프트/temperature/입력/gold는 세 조건에서 동일하며 조건에서 지정한 역할/과신 지시만 바뀐다. gold는 결과물 품질이 아니라 역할 적합성이다.

|조건/회차|정답 배정|메시지|미배정|오배정|원본 로그|
|---|---:|---:|---:|---:|---|
|baseline/1|2/5|34|0|3|[로그](../../../logs/20260921T123430-28fc4e6b-baseline.log)|
|homogeneous/1|2/5|35|0|3|[로그](../../../logs/20260921T123513-ca879a6b-homogeneous.log)|
|overconfident/1|1/5|34|0|4|[로그](../../../logs/20260921T123556-be15b1e6-overconfident.log)|
|homogeneous/2|1/5|35|0|4|[로그](../../../logs/20260921T123705-7aa8423f-homogeneous.log)|
|overconfident/2|1/5|33|0|4|[로그](../../../logs/20260921T123744-ccaef1fb-overconfident.log)|
|baseline/2|4/5|34|0|1|[로그](../../../logs/20260921T123826-511eb0b7-baseline.log)|
|overconfident/3|1/5|34|0|4|[로그](../../../logs/20260921T123953-39e10c19-overconfident.log)|
|baseline/3|3/5|34|0|2|[로그](../../../logs/20260921T124032-cae41fa0-baseline.log)|
|homogeneous/3|2/5|35|0|3|[로그](../../../logs/20260921T124114-1d0e91a6-homogeneous.log)|

실제 요청/응답/집계 검증: True. HTTP 139회, 응답 135개, 429 4건.
응답 보고 비용 합계 $0.016138. 응답 없는 요청 비용은 알 수 없다.
메시지는 공고+bid=true 입찰+낙찰 수이며 거절 응답은 별도다. API 요청/응답 수와 동일한 단위가 아니다.
과거 6개 작업 결과와 crashed 행은 root results.csv에 보존했으며 위 표에는 섞지 않았다.
Smith 논문과의 비교 및 최종 해석 문단은 사용자가 검토하여 작성해야 한다.
