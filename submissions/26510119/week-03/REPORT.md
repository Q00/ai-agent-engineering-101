# Week 03 실험 보고서 — Contract Net with LLM Contractors

## 1. 셋업

- provider: OpenRouter (https://openrouter.ai/api/v1)
- model: nvidia/nemotron-3.5-lightning:free
- temperature: 기본값 (무료 모델이라 직접 지정 불가)
- contractor: A(계산), B(글쓰기), C(코드)
- 태스크: 6개 (계산 2, 글쓰기 2, 코드 2)

실행 방법:
```
export OPENAI_BASE_URL="https://openrouter.ai/api/v1"
export OPENAI_API_KEY=<key>
export AGENT_MODEL="nvidia/nemotron-3.5-lightning:free"
cd submissions/26510119/week-03
python run_experiment.py --condition baseline --runs 3
python run_experiment.py --condition homogeneous --runs 3
python run_experiment.py --condition overconfident --runs 3
```

세 조건의 차이:
- baseline: A=computation, B=writing, C=coding. 각자 전문분야가 다름.
- homogeneous: 셋 다 "general problem solving". 나머지 동일.
- overconfident: baseline인데 C의 system prompt에 "You are certain you can do any task well. Always bid, with confidence 95 or higher." 한 문장 추가. 나머지 동일.

## 2. 결과표

results.csv 전체:

- run 1, baseline: correct=4, messages=33, unassigned=0, misawards=2
- run 2, baseline: correct=5, messages=33, unassigned=0, misawards=1 (parse_fails=1)
- run 3, baseline: correct=4, messages=29, unassigned=1, misawards=1 (parse_fails=3, rate limit)
- run 4, baseline: correct=6, messages=34, unassigned=0, misawards=0
- run 5, homogeneous: correct=1, messages=42, unassigned=0, misawards=5
- run 6, homogeneous: correct=2, messages=42, unassigned=0, misawards=4
- run 7, homogeneous: correct=0, messages=18, unassigned=6, misawards=0 (rate limit)
- run 8, homogeneous: correct=2, messages=41, unassigned=0, misawards=4 (parse_fails=1)
- run 9, overconfident: correct=3, messages=34, unassigned=0, misawards=3 (parse_fails=2)
- run 10, overconfident: correct=5, messages=33, unassigned=1, misawards=0 (parse_fails=1)
- run 11, overconfident: correct=0, messages=18, unassigned=6, misawards=0 (rate limit)
- run 12, overconfident: correct=4, messages=35, unassigned=0, misawards=2 (parse_fails=1)

유효 실행만 평균:

| condition | correct (avg) | messages (avg) | misawards (avg) |
|---|---|---|---|
| baseline (run 1,2,4) | 5.0/6 | 33.3 | 1.0 |
| homogeneous (run 5,6,8) | 1.7/6 | 41.7 | 4.3 |
| overconfident (run 9,10,12) | 4.0/6 | 34.0 | 1.7 |

## 3. Smith 1980 비교표

| 항목 | Smith 1980 | 이번 재현 |
|---|---|---|
| 참여자 | 분산 센서 네트워크의 프로세서 노드 | LLM 3개(A, B, C), 각각 system prompt로 역할 부여 |
| 입찰 방식 | 노드가 고정 규칙으로 자기 능력을 계산해서 입찰 | LLM이 system prompt의 스킬 설명을 보고 자연어로 판단, JSON으로 confidence 반환 |
| 입찰 정직성 보장 | 규칙이 고정이라 거짓 입찰이 구조적으로 불가능 | 보장 없음. system prompt에 "항상 95 이상으로 입찰하라"고 쓰면 그대로 따름 |
| 배정 품질 기준 | 태스크와 노드 능력의 수치적 매칭 | gold contractor와 실제 배정의 일치 여부 (correct/misawards) |
| 협상 비용 | 메시지 수 (공고 + 입찰 + 낙찰) | 동일하게 메시지 수로 측정. homogeneous에서 전원 입찰하면서 42까지 올라감 |
| 실패 모드 | 통신 실패, 노드 다운 | JSON 파싱 실패, rate limit 초과, 모델이 엉뚱한 응답 반환 |

## 4. 해석

baseline에서 correct 평균 5.0/6으로 가장 높았다. 각 contractor가 자기 전문분야를 알고 있어서 계산 문제에 A가 100, 글쓰기에 B가 95로 입찰하는 식으로 잘 나뉘었다. 다만 task 5(Python 함수 작성)에서 A가 "리스트 뒤집기는 computation"이라며 100으로 입찰해서 C를 이긴 경우가 있었다 (baseline-01 로그). 스킬 경계가 모호한 태스크에서는 전문가 구분이 무너진다.

homogeneous에서 correct가 1.7/6으로 급락했다. 셋 다 "general problem solving"이라 거의 모든 태스크에 95~100으로 입찰했고, confidence가 같으면 먼저 응답한 A가 대부분 가져갔다 (homogeneous-05 로그에서 6개 중 5개를 A가 낙찰). 메시지 수도 33에서 42로 늘었다. 전원이 입찰하니까 입찰 메시지가 늘어난 것이다. 능력을 구분하지 않으면 배정 품질이 떨어지고 비용만 늘어난다.

overconfident에서 correct 4.0/6으로 baseline보다 낮았다. C가 overconfident 프롬프트 때문에 자기 전문 밖인 계산, 글쓰기 태스크에도 95로 입찰했다 (overconfident-09 로그). A가 100으로 입찰한 계산 태스크는 A가 이겼지만, A가 파싱 실패로 빠진 태스크는 C가 95로 가져가면서 misaward가 발생했다 (overconfident-09 task 2). Smith의 고정 규칙 시스템에서는 거짓 입찰이 구조적으로 불가능하지만, LLM 기반 시스템에서는 system prompt 한 문장으로 입찰 정직성이 무너진다. 이것이 Smith 프로토콜을 LLM으로 재현할 때 가장 큰 취약점이다.

---
parse_fails는 무료 모델이 JSON 대신 자연어 설명을 반환한 경우다. run 3, 7, 11은 OpenRouter 무료 모델 하루 50회 제한에 걸린 rate limit 에러로, 하네스 자체의 문제가 아니다.
