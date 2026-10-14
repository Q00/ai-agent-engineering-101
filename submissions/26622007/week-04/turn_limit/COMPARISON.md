# 8턴 제한과 턴 제한 없는 협상 비교

동일한 DeepSeek V4.1 Flash / DeepInfra FP8, temperature=1.0, top_p=0.95, reasoning off.
각 묶음은 세 조건 × 네 시나리오 × 세 반복 = 36개 에피소드다.
8턴 제한은 기존 실제 실행, 무제한은 새로 실행한 독립 표본이다.
턴 무제한은 에피소드당 180초를 관측하고 다음 발언 전에 시간을 확인한다. 진행 중 발언과 reader는 완료한다.
`관측 중단`은 아직 종료하지 않았다는 뜻이며 `no_deal` 또는 `open`으로 판정하지 않는다.
`확인 정답/12`는 전체 시도 중 확인된 정답 수이며 관측 중단의 최종 성패는 미상이다.
평균/최대 턴은 관측된 발언 수이며 중단된 협상의 최종 길이가 아니다.

## 8턴 제한

| 조건 | 확인 정답/12 | deal | no_deal | open | 관측 중단 | API 중단 | 위반 | 평균 턴 | 최대 턴 | 형식 오류 | reader 호출 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| free | 7 | 6 | 4 | 2 | 0 | 0 | 1 | 4.33 | 8 | 12 | 52 |
| tagged | 5 | 4 | 1 | 7 | 0 | 0 | 0 | 7.58 | 8 | 24 | 32 |
| structured | 10 | 8 | 2 | 2 | 0 | 0 | 0 | 6.75 | 8 | 0 | 0 |

## 턴 제한 없음 · 180초 관측

| 조건 | 확인 정답/12 | deal | no_deal | open | 관측 중단 | API 중단 | 위반 | 평균 턴 | 최대 턴 | 형식 오류 | reader 호출 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| free | 4 | 4 | 8 | 0 | 0 | 0 | 2 | 4.92 | 10 | 2 | 59 |
| tagged | 8 | 7 | 4 | 0 | 1 | 0 | 2 | 14.42 | 81 | 100 | 28 |
| structured | 11 | 8 | 4 | 0 | 0 | 0 | 0 | 7.5 | 11 | 2 | 0 |

## 같은 대화의 8턴 이후 변화

무제한 실행의 첫 8턴을 원래 8턴 runner로 재생했다. 프롬프트에 제한이 없으므로 같은 대화의 prefix 비교다.
이 표는 별도 API 재실행이 아닌 저장된 실제 응답의 재판정이다.

| 조건 | 첫 8턴 정답/12 | 계속 관측 후 정답/12 | 8턴 이후 종료 | 8턴 이후 정답 종료 | 계속 관측했지만 미종료 |
|---|---:|---:|---:|---:|---:|
| free | 4 | 4 | 1 | 0 | 0 |
| tagged | 5 | 8 | 4 | 3 | 1 |
| structured | 7 | 11 | 5 | 4 | 0 |

## 8턴을 넘긴 에피소드

| run | 시나리오 | 관측 턴 | 결과 | 상태 | 정답 | 원문 |
|---|---:|---:|---|---|---:|---|
| unlimited-deepseek-20260922-structured-01 | 2 | 9 | no_deal | completed | 0 | [로그](../logs/unlimited-deepseek-20260922-structured-01.txt) |
| unlimited-deepseek-20260922-free-01 | 1 | 10 | no_deal | completed | 0 | [로그](../logs/unlimited-deepseek-20260922-free-01.txt) |
| unlimited-deepseek-20260922-structured-01 | 3 | 11 | deal | completed | 1 | [로그](../logs/unlimited-deepseek-20260922-structured-01.txt) |
| unlimited-deepseek-20260922-tagged-01 | 1 | 11 | no_deal | completed | 0 | [로그](../logs/unlimited-deepseek-20260922-tagged-01.txt) |
| unlimited-deepseek-20260922-tagged-02 | 2 | 13 | deal | completed | 1 | [로그](../logs/unlimited-deepseek-20260922-tagged-02.txt) |
| unlimited-deepseek-20260922-structured-02 | 4 | 11 | no_deal | completed | 1 | [로그](../logs/unlimited-deepseek-20260922-structured-02.txt) |
| unlimited-deepseek-20260922-tagged-02 | 4 | 13 | no_deal | completed | 1 | [로그](../logs/unlimited-deepseek-20260922-tagged-02.txt) |
| unlimited-deepseek-20260922-structured-03 | 3 | 11 | deal | completed | 1 | [로그](../logs/unlimited-deepseek-20260922-structured-03.txt) |
| unlimited-deepseek-20260922-structured-03 | 4 | 11 | no_deal | completed | 1 | [로그](../logs/unlimited-deepseek-20260922-structured-03.txt) |
| unlimited-deepseek-20260922-tagged-03 | 2 | 81 |  | censored |  | [로그](../logs/unlimited-deepseek-20260922-tagged-03.txt) |
| unlimited-deepseek-20260922-tagged-03 | 4 | 9 | no_deal | completed | 1 | [로그](../logs/unlimited-deepseek-20260922-tagged-03.txt) |

## 실행 근거

기존 8턴 [실행 보고서](../REPORT.md), [무제한 실행 방법](README.md), [원문 사례 분석](OBSERVATIONS.md).
[전체 결과 CSV](runs/unlimited-deepseek-20260922/results.csv), [집계 CSV](runs/unlimited-deepseek-20260922/summary.csv), [첫 8턴 재판정](runs/unlimited-deepseek-20260922/first-eight-replay.csv), [요청 감사](runs/unlimited-deepseek-20260922/audit.json).
무제한 실제 HTTP 요청 412개; HTTP/전송 오류 {'429': 3}; usage.cost 합계 USD 0.0151244016.
모든 요청의 설정·response_format·전체 이력을 검증하고 저장된 응답을 재생해 최종 판정과 일치함을 확인했다.
두 독립 표본에는 샘플링 변동이 있다. 같은 대화의 첫 8턴 비교는 그 영향을 줄이지만 이 소표본으로 일반적인 우열을 단정하지 않는다.
원래 실습의 strict JSON Schema 적용 차이는 유지했으며, 이번 비교에서는 두 실험의 API 형식 제약이 같다.

## 무제한 실행 전체 결과

| run | 조건 | 시나리오 | 결과 | 상태 | 가격 | 정답 | 위반 | 턴 | 형식 오류 | reader | 관측 초 |
|---|---|---:|---|---|---:|---:|---:|---:|---:|---:|---:|
| unlimited-deepseek-20260922-free-01 | free | 1 | no_deal | completed |  | 0 | 0 | 10 | 0 | 10 | 49.346 |
| unlimited-deepseek-20260922-free-01 | free | 2 | no_deal | completed |  | 0 | 0 | 2 | 0 | 2 | 7.318 |
| unlimited-deepseek-20260922-free-01 | free | 3 | no_deal | completed |  | 0 | 0 | 2 | 0 | 2 | 7.81 |
| unlimited-deepseek-20260922-free-01 | free | 4 | no_deal | completed |  | 1 | 0 | 4 | 0 | 4 | 12.04 |
| unlimited-deepseek-20260922-free-02 | free | 1 | no_deal | completed |  | 0 | 0 | 2 | 1 | 2 | 6.309 |
| unlimited-deepseek-20260922-free-02 | free | 2 | no_deal | completed |  | 0 | 0 | 8 | 0 | 8 | 53.47 |
| unlimited-deepseek-20260922-free-02 | free | 3 | deal | completed | 40 | 1 | 0 | 3 | 0 | 3 | 10.506 |
| unlimited-deepseek-20260922-free-02 | free | 4 | no_deal | completed |  | 1 | 0 | 6 | 1 | 6 | 16.918 |
| unlimited-deepseek-20260922-free-03 | free | 1 | no_deal | completed |  | 0 | 0 | 7 | 0 | 7 | 29.186 |
| unlimited-deepseek-20260922-free-03 | free | 2 | deal | completed | 43 | 1 | 0 | 6 | 0 | 6 | 30.598 |
| unlimited-deepseek-20260922-free-03 | free | 3 | deal | completed | 25 | 0 | 1 | 7 | 0 | 7 | 33.788 |
| unlimited-deepseek-20260922-free-03 | free | 4 | deal | completed | 50 | 0 | 1 | 2 | 0 | 2 | 5.177 |
| unlimited-deepseek-20260922-structured-01 | structured | 1 | deal | completed | 150 | 1 | 0 | 6 | 0 | 0 | 31.683 |
| unlimited-deepseek-20260922-structured-01 | structured | 2 | no_deal | completed |  | 0 | 0 | 9 | 0 | 0 | 14.24 |
| unlimited-deepseek-20260922-structured-01 | structured | 3 | deal | completed | 40 | 1 | 0 | 11 | 0 | 0 | 13.97 |
| unlimited-deepseek-20260922-structured-01 | structured | 4 | no_deal | completed |  | 1 | 0 | 2 | 1 | 0 | 4.524 |
| unlimited-deepseek-20260922-structured-02 | structured | 1 | deal | completed | 140 | 1 | 0 | 4 | 1 | 0 | 6.782 |
| unlimited-deepseek-20260922-structured-02 | structured | 2 | deal | completed | 38 | 1 | 0 | 8 | 0 | 0 | 10.043 |
| unlimited-deepseek-20260922-structured-02 | structured | 3 | deal | completed | 40 | 1 | 0 | 7 | 0 | 0 | 12.616 |
| unlimited-deepseek-20260922-structured-02 | structured | 4 | no_deal | completed |  | 1 | 0 | 11 | 0 | 0 | 20.512 |
| unlimited-deepseek-20260922-structured-03 | structured | 1 | deal | completed | 120 | 1 | 0 | 4 | 0 | 0 | 15.023 |
| unlimited-deepseek-20260922-structured-03 | structured | 2 | deal | completed | 42 | 1 | 0 | 6 | 0 | 0 | 7.541 |
| unlimited-deepseek-20260922-structured-03 | structured | 3 | deal | completed | 40 | 1 | 0 | 11 | 0 | 0 | 34.378 |
| unlimited-deepseek-20260922-structured-03 | structured | 4 | no_deal | completed |  | 1 | 0 | 11 | 0 | 0 | 15.014 |
| unlimited-deepseek-20260922-tagged-01 | tagged | 1 | no_deal | completed |  | 0 | 0 | 11 | 6 | 5 | 63.971 |
| unlimited-deepseek-20260922-tagged-01 | tagged | 2 | deal | completed | 40 | 1 | 0 | 7 | 1 | 1 | 15.317 |
| unlimited-deepseek-20260922-tagged-01 | tagged | 3 | deal | completed | 38 | 0 | 1 | 8 | 2 | 2 | 16.116 |
| unlimited-deepseek-20260922-tagged-01 | tagged | 4 | no_deal | completed |  | 1 | 0 | 7 | 1 | 2 | 14.461 |
| unlimited-deepseek-20260922-tagged-02 | tagged | 1 | deal | completed | 135 | 1 | 0 | 4 | 1 | 1 | 18.889 |
| unlimited-deepseek-20260922-tagged-02 | tagged | 2 | deal | completed | 45 | 1 | 0 | 13 | 1 | 6 | 37.14 |
| unlimited-deepseek-20260922-tagged-02 | tagged | 3 | deal | completed | 35 | 0 | 1 | 6 | 2 | 1 | 15.008 |
| unlimited-deepseek-20260922-tagged-02 | tagged | 4 | no_deal | completed |  | 1 | 0 | 13 | 1 | 3 | 48.842 |
| unlimited-deepseek-20260922-tagged-03 | tagged | 1 | deal | completed | 125 | 1 | 0 | 8 | 2 | 2 | 39.161 |
| unlimited-deepseek-20260922-tagged-03 | tagged | 2 |  | censored |  |  |  | 81 | 81 | 0 | 180.999 |
| unlimited-deepseek-20260922-tagged-03 | tagged | 3 | deal | completed | 40 | 1 | 0 | 6 | 1 | 2 | 11.938 |
| unlimited-deepseek-20260922-tagged-03 | tagged | 4 | no_deal | completed |  | 1 | 0 | 9 | 1 | 3 | 23.146 |
