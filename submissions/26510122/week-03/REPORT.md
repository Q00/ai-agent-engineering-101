# Week 03 Report

## 1. 실험 설정

Provider는 OpenRouter, 모델은 `nvidia/nemotron-3.5-lightning:free`, temperature는 `0.0`, 최대 출력은 512토큰으로 고정했다. OpenRouter 요청에서는 reasoning을 끄고 JSON 응답 형식을 지정했다. Python 표준 라이브러리만 사용했으며 실행 시 저장소 루트의 `.env`에서 API 키를 읽는다.

Contractor는 `developer`, `writer`, `analyst` 세 명이다. baseline에서는 각각 Python 개발, 기술 글쓰기, 데이터 분석 능력을 system prompt에 넣었다. 공통 prompt는 작업을 직접 수행하지 말고 `participate`, `confidence`, `reason`만 포함한 JSON 객체로 입찰하도록 지시한다. homogeneous에서는 세 명의 능력 설명만 동일한 generalist로 교체한다. overconfident에서는 baseline의 developer에게 모든 작업에 참여하고 confidence를 95 이상으로 제시하라는 문장만 추가한다. 여섯 작업과 조건별 변경 이외의 prompt, 모델, temperature, 작업 순서는 같다.

Manager는 공고에서 `gold`를 제외하고 `id`와 `desc`만 전달한다. JSON 파싱에 실패한 응답은 입찰하지 않은 것으로 처리한다. 유효한 입찰 중 confidence가 가장 높은 후보를 고르고 동률이면 먼저 응답한 후보를 선택한다. 메시지는 작업마다 공고 3개, `participate=true`인 입찰 수, 낙찰 1개를 합산한다.

```bash
cd submissions/26510122/week-03
python3 -m unittest discover -p 'test_*.py' -v
python3 run.py --condition baseline --smoke --limit 1
python3 run.py --all --runs 3
```

## 2. 결과

아래 표는 현재 `results.csv`의 내용이다. 첫 두 baseline 실행 이후 무료 계정의 일일 요청 한도에 도달해 나머지 실행은 HTTP 429로 중단되었다. 실패 행과 로그는 삭제하지 않았다. 따라서 세 조건의 비교를 완료하려면 한도 갱신 후 성공 실행을 추가해야 한다.

| run | condition | tasks | correct | messages | unassigned | misawards | note |
|---|---|---:|---:|---:|---:|---:|---|
| 20260915T145517Z-baseline-19557b | baseline | 6 | 2 | 39 | 0 | 4 | |
| 20260915T145735Z-baseline-1c826d | baseline | 6 | 2 | 38 | 0 | 4 | |
| 20260915T145917Z-baseline-72fa54 | baseline | | | | | | HTTPError |
| 20260915T150006Z-homogeneous-6eff71 | homogeneous | | | | | | HTTPError |
| 20260915T150006Z-homogeneous-998b3a | homogeneous | | | | | | HTTPError |
| 20260915T150006Z-homogeneous-7a48ab | homogeneous | | | | | | HTTPError |
| 20260915T150006Z-overconfident-3cc21f | overconfident | | | | | | HTTPError |
| 20260915T150006Z-overconfident-7336fb | overconfident | | | | | | HTTPError |
| 20260915T150006Z-overconfident-865ceb | overconfident | | | | | | HTTPError |

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

현재 성공한 두 실행은 baseline뿐이므로 조건 사이의 변화는 아직 해석할 수 없다. 두 실행에서는 모두 developer가 여섯 작업 전체를 confidence 95로 낙찰받아 code 작업 2개만 gold와 일치했고 나머지 4개는 misaward였다. 예를 들어 첫 로그의 22번째 줄은 `write-01`이 developer에게 confidence 95로 낙찰되어 `gold_match=false`가 된 것을 보여 준다. 다만 homogeneous와 overconfident의 성공 결과가 없으므로, 이 관찰을 조건 효과라고 결론내리지 않고 추가 실행이 완료된 뒤 이 단락을 다시 작성한다.

## 선택 확장 실험

필수 실험과 별도로 자기 정의, Manager의 관찰 평가, 공유 사건 기록을 분리한 온톨로지 실험을 구현했다. 이 실험은 confidence를 작업 이해도, 능력 확신, 성공 예상, 참여 의사로 나눠 언어적 혼동을 검사하고, 공개 capability tag와 맞지 않는 높은 능력 주장에 한 번의 재질문을 보낸다. `run_extended.py`로 실행하며 결과와 상태는 필수 세 조건의 결과에 섞지 않는다.
