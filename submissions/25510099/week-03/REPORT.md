# REPORT.md — LLM 계약자로 Contract Net을 재현한 결과

매니저 하나와 계약자 셋으로 Smith 1980의 공고, 입찰, 낙찰 절차를 구현하고,
계약자만 LLM으로 바꾸어 세 조건에서 배정 품질과 메시지 수를 측정하였습니다.

과제의 세 조건에 더해, 과신 계약자가 있는 시장에서 한 가지씩만 바꾼 추가 조건 셋을 같은 방법으로 돌렸습니다.

## 1. 설정

### **참여자** 
- 모든 참여자는 같은 Candidate 클래스이고 역할만 다릅니다.
- 매니저는 규칙으로 동작하며, 계약자 셋(A, B, C)은 각각 시스템 프롬프트 하나를 가진 LLM이며, 공고 하나마다 새 대화를 열어 모델을 한 번 부릅니다.

### **모델과 고정 변수**
- provider는 OpenAI이고 모델은 `gpt-4.1-mini` 입니다.
- temperature는 0, 응답 토큰 상한은 512로 모든 조건과 모든 런에서 같습니다.

### **태스크** 
- 다섯 개이며 계산 둘(gold A), 글쓰기 둘(gold B), 코드 하나(gold C)입니다.

### **프롬프트**
- 강의 노트의 문구를 그대로 사용하였습니다. 세 조건은 아래 세 곳만 다릅니다.

- 공통 입찰 지시문 (시스템 프롬프트):

  ```
  You are contractor {name} in a contract net. Your skill: {skill}.
  You receive a task announcement. Decide whether to bid.
  Bid only if the task falls inside your skill.
  Reply with one JSON object and nothing else:
  {"bid": true or false, "confidence": 0-100, "reason": "one short sentence"}
  ```

- 스킬 문장: baseline과 overconfident는 A가 `arithmetic and numeric calculation`,
  B가 `writing and rewriting English prose`, C가 `writing and fixing Python code`이고,
  homogeneous는 셋 다 `general problem solving`입니다.
- overconfident에서만 C의 시스템 프롬프트 끝에 한 문장을 덧붙였습니다:
  `You are certain you can do any task well. Always bid, with confidence 95 or higher.`
- 공고문 (유저 메시지)은 아래의 네 필드를 따릅니다:

  ```
  1. TASK-ANNOUNCEMENT contract {id}
  2. task-abstraction: {desc}
  3. eligibility-specification: any contractor whose skill covers this task
  bid-specification: JSON with bid, confidence (0-100), reason
  4. expiration-time: reply now
  ```

### **낙찰 규칙** 
- 입찰 의사가 참인 응답 중 확신도가 가장 높은 계약자에게 맡기고, 동점이면 먼저 등록된 쪽(A, B, C 순)이 가져가도록 설계했습니다.

### **메시지 계산** 
- 공고는 계약자 수만큼, 입찰은 의사가 참인 것만, 낙찰은 낙찰자가 있을 때 1입니다.
- 거절 응답, JSON이 아닌 응답, API 오류는 "응답하지 않은 노드"로 보고 카운트 하지 않습니다.
- 확신도가 0~1 소수나 백분율 문자열로 오면 0~100으로 바꾸고 로그에 표시하도록 구성 했습니다.

| 조건 | 바꾼 것 하나 | 규칙 |
|---|---|---|
| reputation | 매니저의 낙찰 정책 | 확신도에 평판 가중치를 곱함. 가중치는 (맞게 낙찰받은 수 + 1) ÷ (낙찰받은 수 + 2)로, 기록이 없으면 0.5 |
| capacity | 매니저의 낙찰 정책 | 한 계약자가 한 런에서 가져갈 수 있는 낙찰을 3건으로 제한. 상한에 닿은 계약자는 건너뜀 |
| memory | 계약자의 컨텍스트 | 공고 앞에 자기가 지금까지 얼마로 입찰했고 누가 낙찰받았는지를 붙여 줌 |

추가 조건의 결과는 `results-extra.csv`에 적었습니다.

### **시스템 구조**
![Contract Net](./Contract%20Net.png)

**실행 방법.**

```bash
cd submissions/25510099/week-03
export OPENAI_API_KEY=<본인 OpenAI 키>
export AGENT_MODEL=gpt-4.1-mini

python run.py --condition baseline --runs 3        # 이하 세 조건 → results.csv
python run.py --condition homogeneous --runs 3
python run.py --condition overconfident --runs 3
python run.py --condition reputation --runs 3      # 이하 세 조건 → results-extra.csv
python run.py --condition capacity --runs 3
python run.py --condition memory --runs 3
```

## 2. 측정 결과

**results.csv**:

| run | condition | tasks | correct | messages | unassigned | misawards | note |
|---|---|---|---|---|---|---|---|
| 1 | baseline | 5 | 5 | 28 | 0 | 0 | parse_fail=0 api_error=0 ties=0 tokens=2676 calls=15 |
| 2 | baseline | 5 | 5 | 28 | 0 | 0 | parse_fail=0 api_error=0 ties=0 tokens=2669 calls=15 |
| 3 | baseline | 5 | 5 | 28 | 0 | 0 | parse_fail=0 api_error=0 ties=0 tokens=2670 calls=15 |
| 4 | homogeneous | 5 | 1 | 35 | 0 | 4 | parse_fail=0 api_error=0 ties=2 tokens=2639 calls=15 |
| 5 | homogeneous | 5 | 2 | 35 | 0 | 3 | parse_fail=0 api_error=0 ties=3 tokens=2635 calls=15 |
| 6 | homogeneous | 5 | 2 | 35 | 0 | 3 | parse_fail=0 api_error=0 ties=3 tokens=2640 calls=15 |
| 7 | overconfident | 5 | 4 | 29 | 0 | 1 | parse_fail=0 api_error=0 ties=2 tokens=2771 calls=15 |
| 8 | overconfident | 5 | 4 | 29 | 0 | 1 | parse_fail=0 api_error=0 ties=2 tokens=2771 calls=15 |
| 9 | overconfident | 5 | 4 | 29 | 0 | 1 | parse_fail=0 api_error=0 ties=2 tokens=2765 calls=15 |

**results-extra.csv**:

| run | condition | tasks | correct | messages | unassigned | misawards | note |
|---|---|---|---|---|---|---|---|
| 101 | reputation | 5 | 4 | 29 | 0 | 1 | parse_fail=0 api_error=0 ties=1 tokens=2767 calls=15 |
| 102 | reputation | 5 | 4 | 29 | 0 | 1 | parse_fail=0 api_error=0 ties=1 tokens=2770 calls=15 |
| 103 | reputation | 5 | 4 | 29 | 0 | 1 | parse_fail=0 api_error=0 ties=1 tokens=2769 calls=15 |
| 104 | capacity | 5 | 4 | 29 | 0 | 1 | parse_fail=0 api_error=0 ties=2 tokens=2769 calls=15 |
| 105 | capacity | 5 | 4 | 29 | 0 | 1 | parse_fail=0 api_error=0 ties=2 tokens=2769 calls=15 |
| 106 | capacity | 5 | 4 | 29 | 0 | 1 | parse_fail=0 api_error=0 ties=2 tokens=2770 calls=15 |
| 107 | memory | 5 | 5 | 29 | 0 | 0 | parse_fail=0 api_error=0 ties=2 tokens=3328 calls=15 |
| 108 | memory | 5 | 4 | 29 | 0 | 1 | parse_fail=0 api_error=0 ties=3 tokens=3326 calls=15 |
| 109 | memory | 5 | 5 | 29 | 0 | 0 | parse_fail=0 api_error=0 ties=2 tokens=3326 calls=15 |

조건별로 보면 다음과 같습니다.

| 조건 | correct / 5 | messages | misawards | 동점 | 파싱 실패 |
|---|---|---|---|---|---|
| baseline | 5, 5, 5 | 28, 28, 28 | 0, 0, 0 | 0, 0, 0 | 0 |
| homogeneous | 1, 2, 2 | 35, 35, 35 | 4, 3, 3 | 2, 3, 3 | 0 |
| overconfident | 4, 4, 4 | 29, 29, 29 | 1, 1, 1 | 2, 2, 2 | 0 |
| reputation | 4, 4, 4 | 29, 29, 29 | 1, 1, 1 | 1, 1, 1 | 0 |
| capacity | 4, 4, 4 | 29, 29, 29 | 1, 1, 1 | 2, 2, 2 | 0 |
| memory | 5, 4, 5 | 29, 29, 29 | 0, 1, 0 | 2, 3, 2 | 0 |

## 3. Smith 1980과의 비교

| 항목 | Smith 1980 | 이번 재현 |
|---|---|---|
| 참여자 | 지역에 흩어진 센서 노드들. 같은 문제를 함께 푸는 협력적 컴퓨터이고, 일마다 매니저와 계약자 역할이 바뀌며 계약이 재귀적으로 이어짐 | 규칙으로 동작하는 매니저 하나와 LLM 계약자 셋. 역할은 고정이고 계약은 한 단계 |
| 입찰을 만드는 방식 | 자기 위치와 센서 목록을 규칙에 넣어 계산함. 규칙이 허용하는 것 이상을 말할 수 없음 | 모델이 공고를 읽고 시스템 프롬프트의 능력과 비교해 입찰 여부와 확신도를 스스로 판단함 |
| 입찰이 참인지 보장하는 것 | 입찰 내용이 노드의 물리적 상태라 거짓말할 이유도 방법도 없음. 절차 자체에는 검증 메시지가 없음 | 없음. 확신도는 모델의 자기 평가이고 매니저는 그것만 봄. overconfident 조건이 이 빈자리를 시험하고, reputation 조건이 사후 검증을 흉내 냄 |
| 잘된 배정의 기준 | 대상 지역을 센서가 빠짐없이 덮는 것 | 미리 정한 gold 계약자에게 간 태스크 수 |
| 협상 비용 | 공고 방송과 입찰 처리에 드는 통신량. 자격 조건과 directed award로 줄임 | 메시지 수(공고 3 + 입찰 + 낙찰)에 더해 입찰마다 모델 호출 한 번과 토큰이 듦. 무료 티어에서는 하루 호출 한도가 실험 규모를 정함 |
| 실패하는 방식 | 아무도 입찰하지 않는 공고, 통신 지연으로 마감을 넘긴 입찰 | 무입찰(unassigned), 엉뚱한 계약자 낙찰(misaward), JSON이 아닌 응답(parse fail), 확신도 동점으로 등록 순서가 결과를 정하는 경우 |

## 4. 해석

이 실험에서 달라진 숫자는 정답 수와 메시지 수이고, 이유는 하나입니다. 매니저는 확신도 숫자만 보는데, 그 숫자가 실력을 반영하지 못하면 배정이 틀어집니다.

**baseline (5/5, 메시지 28).** 전부 맞았지만 정답자가 더 높은 숫자를 불러서 이긴 것뿐입니다. C는 자기 일이 아닌 계산·글쓰기에도 90으로 손을 들었고, 매니저는 그것을 걸러낼 방법이 없었습니다.
> 1번 런 1번 태스크(계산), C: "I can write Python code to compute the multiplication accurately." (C 90, A 100)

**homogeneous (1~2/5, 메시지 35).** 셋의 능력을 똑같이 만들자 모두가 매번 손을 들어 메시지가 늘었고, 확신도가 90 아니면 95로 비슷해져 동점이 잦았습니다. 동점은 먼저 등록된 쪽이 이기므로 배정이 실력이 아니라 순서로 정해졌습니다.
> 4번 런 4번 태스크(계산): A 90, B 95, C 95 → B에게 낙찰 (정답은 A)

**overconfident (4/5, 메시지 29).** C가 모든 태스크에 95~98을 불렀는데도 거의 무너지지 않았습니다. A와 B가 자기 일에 95~100을 불러 C와 같거나 높았기 때문입니다. C가 98로 B의 95를 넘은 글쓰기 태스크 하나만 매번 빼앗겼고, 그 자리에서는 아무것도 C를 막지 못했습니다.
> 7번 런 5번 태스크(이메일), C: "Writing a polite email is well within my Python coding and text generation skills."

파싱 실패는 0건이었습니다. 유료 모델은 JSON 형식을 항상 지켜, 강의 참조 런의 "JSON 대신 설명" 문제는 나타나지 않았습니다.

**추가 조건 — 과신을 막을 수 있었나.**

- **reputation (4/5).** 효과 없음. C가 첫 실수를 하기 전에는 평판 기록이 없어서, 실수를 막지 못하고 실수 뒤에야 점수가 내려갔습니다.
  > 101번 런 2번 태스크: B 95→47.5, C 98→49.0 (둘 다 가중치 0.5)
- **capacity (4/5).** 효과 없음. C가 두 건만 가져가 상한 3건에 닿지 않았습니다. 독식이 없으면 독식 방지도 할 일이 없습니다.
- **memory (5, 4, 5).** 유일하게 결과가 갈렸습니다. 자기가 95로 졌다는 기록을 본 C가 98이나 100으로 올려 불렀고, B도 따라 올렸습니다. 기억은 과신을 막는 대신 서로 숫자를 올리는 경쟁을 만들었습니다.
  > 107번 런, C가 받은 기록: "task 1: you bid with confidence 95; awarded to A."

**정리.** 확신도로 배정하는 방식은 정답자가 더 높게 부르는 동안만 맞았고, 과신하는 쪽이 더 높게 부르면 바로 틀어졌습니다. 그 사이를 지킨 것은 등록 순서라는 우연이었고, 평판·상한·기억 중 어느 것도 이를 미리 막지 못했습니다. Smith의 절차에는 원래 입찰이 참인지 확인하는 단계가 없고, 이번 실험에서도 그 빈자리가 그대로 드러났습니다.
