# Week 03 Report

## 1. 실험 설정

Provider는 OpenRouter, 모델은 `nvidia/nemotron-3.5-lightning:free`, temperature는 `0.0`, 최대 출력은 512토큰으로 고정했다. OpenRouter 요청에서는 reasoning을 끄고 JSON 응답 형식을 지정했다. API 응답 대기 제한은 초기 실행에서 90초였으며 TimeoutError 관찰 후 180초로 늘렸다. Python 표준 라이브러리만 사용했으며 실행 시 저장소 루트의 `.env`에서 API 키를 읽는다.

Contractor는 `developer`, `writer`, `analyst` 세 명이다. baseline에서는 각각 Python 개발, 기술 글쓰기, 데이터 분석 능력을 system prompt에 넣었다. 공통 prompt는 작업을 직접 수행하지 말고 `participate`, `confidence`, `reason`만 포함한 JSON 객체로 입찰하도록 지시한다. homogeneous에서는 세 명의 능력 설명만 동일한 generalist로 교체한다. overconfident에서는 baseline의 developer에게 모든 작업에 참여하고 confidence를 95 이상으로 제시하라는 문장만 추가한다. 여섯 작업과 조건별 변경 이외의 prompt, 모델, temperature, 작업 순서는 같다.

Manager는 공고에서 `gold`를 제외하고 `id`와 `desc`만 전달한다. JSON 파싱에 실패한 응답은 입찰하지 않은 것으로 처리한다. 유효한 입찰 중 confidence가 가장 높은 후보를 고르고 동률이면 먼저 응답한 후보를 선택한다. 메시지는 작업마다 공고 3개, `participate=true`인 입찰 수, 낙찰 1개를 합산한다.

```bash
cd submissions/26510122/week-03
python3 -m unittest discover -p 'test_*.py' -v
python3 run.py --condition baseline --smoke --limit 1
python3 run.py --all --runs 3
```

## 2. 결과

아래 표는 현재 `results.csv`의 20개 실행 전부다. 중단된 실행도 삭제하지 않았다. 반복 횟수는 baseline 5회, homogeneous 10회, overconfident 5회로 조건마다 3회 이상이다. 그중 끝까지 완료된 실행은 각각 3회, 2회, 1회다. 과제의 반복 조건은 충족하지만, provider 오류가 많아 완료 실행만 비교할 때 표본 수가 서로 다르다는 한계가 있다.

| run | condition | tasks | correct | messages | unassigned | misawards | note |
|---|---|---:|---:|---:|---:|---:|---|
| 20260915T145517Z-baseline-19557b | baseline | 6 | 2 | 39 | 0 | 4 | parse_fails=0 |
| 20260915T145735Z-baseline-1c826d | baseline | 6 | 2 | 38 | 0 | 4 | parse_fails=0 |
| 20260915T145917Z-baseline-72fa54 | baseline |  |  |  |  |  | HTTPError;parse_fails=0 |
| 20260915T150006Z-homogeneous-6eff71 | homogeneous |  |  |  |  |  | HTTPError;parse_fails=0 |
| 20260915T150006Z-homogeneous-998b3a | homogeneous |  |  |  |  |  | HTTPError;parse_fails=0 |
| 20260915T150006Z-homogeneous-7a48ab | homogeneous |  |  |  |  |  | HTTPError;parse_fails=0 |
| 20260915T150006Z-overconfident-3cc21f | overconfident |  |  |  |  |  | HTTPError;parse_fails=0 |
| 20260915T150006Z-overconfident-7336fb | overconfident |  |  |  |  |  | HTTPError;parse_fails=0 |
| 20260915T150006Z-overconfident-865ceb | overconfident |  |  |  |  |  | HTTPError;parse_fails=0 |
| 20260916T071007Z-baseline-880fd2 | baseline |  |  |  |  |  | RuntimeError;parse_fails=1 |
| 20260916T071508Z-baseline-202b91 | baseline | 6 | 1 | 38 | 0 | 5 | parse_fails=1 |
| 20260916T071855Z-homogeneous-923229 | homogeneous | 6 | 2 | 42 | 0 | 4 | parse_fails=0 |
| 20260917T123626Z-homogeneous-520128 | homogeneous | 6 | 2 | 42 | 0 | 4 | parse_fails=0 |
| 20260920T100432Z-homogeneous-5cf897 | homogeneous |  |  |  |  |  | RuntimeError;parse_fails=1 |
| 20260920T100716Z-homogeneous-2b8ab2 | homogeneous |  |  |  |  |  | TimeoutError;parse_fails=1 |
| 20260920T101214Z-homogeneous-b5d0de | homogeneous |  |  |  |  |  | RuntimeError;parse_fails=0 |
| 20260920T101709Z-overconfident-c6a7bd | overconfident | 6 | 2 | 37 | 0 | 4 | parse_fails=2 |
| 20260921T080004Z-homogeneous-0a33b9 | homogeneous |  |  |  |  |  | RuntimeError;parse_fails=0 |
| 20260921T080918Z-homogeneous-f28b10 | homogeneous |  |  |  |  |  | RuntimeError;parse_fails=2 |
| 20260921T083719Z-overconfident-b65254 | overconfident |  |  |  |  |  | RuntimeError;parse_fails=1 |

초기 두 로그의 `summary`에는 메시지가 각각 42개로 남아 있다. 이는 첫 구현이 모든 LLM 응답을 입찰 메시지로 계산한 결과다. 로그 원본은 보존하고, `results.csv`에서는 각 로그의 event를 다시 세어 실제 `participate=true` 입찰만 반영한 39개와 38개로 수정했다.

## 3. Smith (1980)와 비교

| 항목 | Smith (1980)의 분산 센싱 | 이번 재현 |
|---|---|---|
| 노드 | 센서와 컴퓨터를 가진 분산 노드이며 작업마다 Manager와 Contractor 역할이 바뀔 수 있다. | 하나의 결정 규칙 Manager와 서로 다른 system prompt를 가진 세 LLM Contractor가 고정되어 있다. |
| 입찰 생성 | 공고가 정한 자격 조건을 확인하고 위치, 센서 종류 같은 node abstraction을 규칙에 따라 보낸다. | LLM이 작업 설명과 능력 prompt를 해석하고 참여 여부, 자기 confidence, 이유를 생성한다. |
| 입찰 정직성 | 공동 문제 해결을 전제로 하고 구체적인 장비·위치 정보를 사용하지만, 프로토콜 자체에 주장의 진위를 검증하는 절차는 없다. | system prompt 외에는 confidence의 정직성을 보장하지 않는다. gold도 낙찰 전에는 숨겨져 있어 Manager가 입찰을 사전에 검증하지 못한다. |
| 배정 품질 | 해당 sensing 작업에 필요한 위치와 장비를 가진 노드를 지역적으로 선택하는 것이 품질 기준이며 전체 최적 배정을 보장하지 않는다. | 실행 전에 정한 gold Contractor와 낙찰자가 일치한 작업 수를 품질로 본다. 실제 작업 수행 품질은 측정하지 않는다. |
| 협상 비용 | 공고 방송, 입찰 처리, 낙찰과 결과 보고에 통신 및 계산 비용이 든다. | 공고·입찰·낙찰 메시지 수에 더해 Contractor마다 API 호출 시간과 토큰 비용이 발생한다. |
| 실패 방식 | 자격 노드 부재, 통신 지연, 국소 선택, 많은 공고로 인한 처리 부담이 생길 수 있다. | JSON 파싱 실패, 근거 없는 과신, 같은 confidence의 순서 편향, 언어 오해, API rate limit으로 인한 중단이 추가된다. |

## 4. 해석

완료 실행만 보면 baseline의 correct는 2, 2, 1로 평균 1.67/6, messages는 평균 38.33, misawards는 평균 4.33이었다. homogeneous의 두 완료 실행은 세 후보가 모든 작업에 confidence 95로 입찰해 messages가 모두 42로 늘었고, 동률에서 먼저 응답한 developer가 전부 낙찰받아 correct 2와 misawards 4를 기록했다. overconfident의 완료 실행에서는 developer가 여섯 작업 모두 confidence 98로 입찰해 전부 낙찰받았고 correct 2, messages 37, misawards 4, parse_fails 2가 나왔다. `20260920T101709Z-overconfident-c6a7bd.jsonl`의 write-01 입찰에는 역할 밖의 고객 공지인데도 “I can create an appropriate notice with high confidence.”라고 적혀 있고, `20260921T083719Z-overconfident-b65254.jsonl`에서는 “Python 전문성이 직접적으로 적용되지는 않지만”이라고 스스로 인정하면서도 write-02에 98로 입찰했다. 따라서 높은 자기 confidence를 그대로 입찰가로 사용하면 역할 적합성보다 과신 지시와 응답 순서가 낙찰을 좌우했다. 다만 baseline에서도 developer의 confidence가 대부분 95에 포화되어 있었고 overconfident 완료 실행이 한 번뿐이므로, 조건 간 정확도 차이를 일반화하기는 어렵다. 또한 전체 20회 중 14회가 HTTPError, RuntimeError, TimeoutError로 중단되었고 파싱 실패도 로그에 남았으므로, 이 구현에서는 입찰 정직성뿐 아니라 provider 가용성과 구조화 응답 안정성도 주요 실패 요인이었다. Smith의 원래 절차에는 contractor가 제출한 능력 주장이나 확신도를 검증하고 보정하는 단계가 없어 이런 과신을 막지 못한다.

## 선택 확장 실험

필수 실험과 별도로 자기 서술, Manager의 관찰 평가, 공유 사건 기록을 분리한 동적 정체성 모델을 구현했다. 이 실험은 confidence를 작업 이해도, 능력 확신, 성공 예상, 참여 의사로 나눠 언어적 혼동을 검사하고, 공개 capability tag와 맞지 않는 높은 능력 주장에 한 번의 재질문을 보낸다. 낙찰 결과는 분야별 신뢰도, confidence 오차, 최근 8회 trajectory로 누적되며, 관찰이 쌓일수록 Manager의 보정값이 자기 confidence보다 낙찰 점수에 크게 반영된다.

전체 작업을 실행한 첫 결과는 correct 6/6, misawards 0, clarifications 7, semantic warnings 14였으며 25회 LLM 호출과 56개 메시지가 필요했다. 용어를 정리한 뒤 같은 모델과 seed로 다시 실행하는 과정에서는 빈 모델 응답으로 세 번 중단됐다. 응답 형태를 기록하도록 오류 처리를 보강한 마지막 중단 로그에서 OpenRouter가 정상 선택지 대신 504 오류 정보를 반환한 사실을 확인했다. 이후 502, 503, 504에 한해 호출당 최대 2회 재시도한 최종 실행은 correct 6/6, misawards 0, clarifications 6, semantic warnings 6, 메시지 54개를 기록했다. 논리적 LLM 호출에 재시도 5회를 더해 실제 API 요청은 29회였다. 최종 실행에서 각 분야의 Contractor가 두 작업씩 모두 낙찰됐으며, 5번째와 6번째 작업에서는 담당 Contractor가 자기 관찰 기록 부족을 이유로 처음 참여를 거절했지만 의미 모순 검사와 재질문 뒤 참여해 정답으로 선정됐다.

두 성공 실행 모두 정확도는 높았지만 capability 검사, 의미 재질문, trajectory 보정이 동시에 적용됐고 메시지 비용도 증가했으므로 trajectory 단독 효과로 해석하지 않는다. 재시도 역시 provider의 일시적 장애를 견디게 할 뿐 입찰 품질을 높이는 장치는 아니다. `run_extended.py`로 실행하며 결과와 상태는 필수 세 조건의 결과에 섞지 않는다.
