# 한국어 협상과 통계 분석 계획

한국어 데이터를 보기 전에 고정한다. 영어 데이터는 이미 관찰한 탐색 자료이므로 정식 사전등록이라고 주장하지 않는다.

- 추가 실행: 한국어 8턴 36회 + 한국어 턴 무제한 36회. 각 조건/정책당 네 시나리오 × 세 반복.
- DeepSeek V4.1 Flash, DeepInfra FP8, temperature=1.0, top_p=0.95, reasoning off, 같은 전체 이력·파서·response_format.
- 역할·화행 설명·형식 지시·reader·물품명을 한국어로 번역한다. 가격 숫자와 단위 미지정은 유지한다.
  화행 값과 JSON 키는 영어 그대로다. structured는 자연어 발화가 없으므로 한국어 입력 지시의 효과를 본다.
- 원래 코드를 독립 모듈로 불러와 프롬프트 상수만 바꾼다. 영어 소스와 원본 로그는 수정하지 않는다.
- 기존과 같은 180초 무제한 관측 종료 정책. 진행 중 발언+reader는 완료하고 다음 턴 전에 확인한다.
  시간 중단은 censored, 전송 실패는 crashed, 한도 종료는 open으로 분리한다.
- 18개 독립 run의 실행 순서는 seed 20260922로 섞고 jobs=3. 샘플링 seed는 지정하지 않는다.
  영어와 한국어가 시간상 교차 무작위 배정된 실험은 아니므로 언어의 인과 효과를 확정하지 않는다.
- 표본 수를 결과에 따라 늘리지 않는다. 관측 중단/실패도 삭제하지 않는다.

## 검정과 해석

주 지표는 **관측 범위 안에 올바른 종료가 확인되었는지**다. correct=1만 1,
open/censored/crashed/incorrect는 0. 이것은 미종료의 궁극적인 성패를 단정하는 지표가 아니다.
확정 정답 수, 미종료 수와 가능한 최종 정답 수의 범위도 함께 표시한다.

1. 언어 비교: 조건 3 × 정책 2 = 6개 비교. 한국어 - 영어의 정답률 차이.
2. 형식 비교: 언어 2 × 정책 2 × 쌍 3 = 12개 비교.
3. 독립 실행의 턴 정책 비교: 언어 2 × 조건 3 = 6개 비교.

세 묶음의 **전체 24개** 양측 검정에 Holm 보정을 적용하고 alpha=0.05를 쓴다.
검정은 시나리오별 성공 수와 그룹 크기를 고정한 정확 조건부 순열 분포를 합성한다.
각 층에서 Hypergeometric(합계 성공 수, 합계 시도 수, A그룹 크기)을 열거하고
총 성공 수 차이의 절대값이 관측값 이상인 확률을 구한다. 모든 비교는 층당 3 vs 3이다.
독립 호출이 시나리오 안에서 교환 가능하다는 가정 아래 이 네 시나리오에 한정한 추론이다.
일반 협상 전체로 확장할 시나리오 표본은 4개뿐이다. 동일 run 안 상관과 API 시간 변화는 배제할 수 없다.

효과 크기는 정답률 차이(%p)다. 각 그룹의 네 시나리오 성공 확률을 별도로 추정하고
8개 확률에 Bonferroni를 적용한 Clopper–Pearson 구간에서 평균 차이의 보수적 95% 구간을 만든다.
이는 작은 n=3/시나리오에서 매우 넓을 수 있으며 다중 비교 보정 구간은 아니다.
통계적 비유의는 동등성이나 효과 부재의 증거로 해석하지 않는다.

무제한 대화의 첫 8턴과 최종 관측은 **같은 표본**이므로 독립 표본 검정에 넣지 않는다.
추가 정답/퇴행 개수와 정확 McNemar 양측 p를 별도 계산하고 6개에 Holm 보정한다.
성공하면 종료하는 구조상 정답이 퇴행할 수 없어 이 p는 중첩된 관측 시점의 보조 기술 통계다.
무작위 처리 효과의 확증으로 쓰지 않는다. 턴 수/형식 오류/reader 호출은 기술 통계만 보고한다.

## 근거와 재현

- [SciPy permutation_test](https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.permutation_test.html)
- [SciPy binomtest / exact CI](https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.binomtest.html)
- [statsmodels Holm](https://www.statsmodels.org/stable/generated/statsmodels.stats.multitest.multipletests.html)
- [statsmodels exact McNemar](https://www.statsmodels.org/stable/generated/statsmodels.stats.contingency_tables.mcnemar.html)

분석 코드·패키지 버전·전체 p값·요청 감사·실패 원문을 함께 저장한다.
