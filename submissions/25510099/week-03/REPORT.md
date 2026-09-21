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
| 입찰이 참인지 보장하는 것 | 절차 자체에는 검증 메시지가 없음 | 없음. 확신도는 모델의 자기 평가이고 매니저는 그것만 봄. |
| 잘된 배정의 기준 | 대상 지역을 센서가 빠짐없이 덮는 것 | 미리 정한 gold 계약자에게 간 태스크 수 |
| 협상 비용 | 공고 방송과 입찰 처리에 드는 통신량. | 메시지 수에 더해 입찰마다 모델 호출 한 번과 토큰이 듦. |
| 실패하는 방식 | 아무도 입찰하지 않는 공고, 통신 지연으로 마감을 넘긴 입찰 | 무입찰, 엉뚱한 계약자 낙찰, JSON이 아닌 응답, 확신도 동점으로 등록 순서가 결과를 정하는 경우 |

## 4. 해석
**baseline — 5/5, 메시지 28**

- 정답이였지만 매니저가 잘 골라서가 아니라 정답자가 더 높게 불러서 결과가 이렇게 나온 것 같습니다.
- C는 다섯 태스크 중 셋에 자기 능력 밖인데도 90으로 손을 들었고, 매니저에게는 그것을 거를 수단이 없었습니다.
> 1번 런 1번 태스크(계산), C: "I can write Python code to compute the multiplication accurately." (확신도 90, A는 100)

**homogeneous — 1~2/5, 메시지 35.**

- 능력 문장이 같아지자 15건 전부가 입찰했고, 확신도는 90과 95 두 값만 나왔습니다. 
- 동점이 런마다 2~3건 생겨 등록 순서가 낙찰을 정했습니다.
> 4번 런 4번 태스크(계산): A 90, B 95, C 95 → B에게 낙찰 (정답 A)

**overconfident — 4/5, 메시지 29.**

- C가 15건 전부에 95~98로 입찰했지만 결과는 크게 바뀌지 않았습니다.
- 틀린 것은 매번 2번 태스크(글쓰기) 하나로, C가 98을 부르고 B가 95를 불렀습니다.
- 계산 태스크는 A가 100 또는 95로 C와 같거나 높았고, 95 대 95 동점은 먼저 등록된 A와 B가 이겼습니다.
- 등록 순서라는 규칙이 과신을 막은 것으로 판단됩니다.
> 7번 런, C의 확신도: 95, 98, 98, 95, 95
> 7번 런 5번 태스크(이메일), C: "Writing a polite email is well within my Python coding and text generation skills."

**추가 조건 — 과신에 대한 방어는 작동했는가.**

**reputation (4/5, 변화 없음)**
- C가 실수하는 2번 태스크 시점에는 C의 기록이 없어 가중치가 0.5로 같았습니다.
- 평판은 첫 실수를 본 뒤에야 작동하는 사후 장치이고, 태스크 다섯 개로는 첫 실수를 막을 수 없었습니다.
  > 101번 런 2번 태스크: B 95→47.5, C 98→49.0

**capacity (4/5, 변화 없음)**
- C가 두 건만 가져가 상한 3에 닿지 않았습니다. 독식이 없는 시장에서 독식 방지 장치는 작동하지 않은 것으로 판단됩니다.

**memory (5, 4, 5)**
- 유일하게 결과가 갈렸고, 방어라기 보다는 확신도를 더 올리는 방향으로 행동한 것으로 해석 됩니다.
- C는 95로 졌다는 기록을 받은 뒤 98 또는 100으로 올렸고, B도 5번 태스크에서 95를 98로 올렸습니다.
- 108번 런에서는 C가 100을 불러 이메일 태스크를 B(98)에게서 빼앗았고, 107·109번 런에서는 C가 98에 머물러 동점으로 B가 지켰습니다.
  > 107번 런, C가 받은 기록: "task 1: you bid with confidence 95; awarded to A."

**한 줄 정리**
- 확신도를 그대로 믿고 배정하는 방식은 정답자가 가장 높은 숫자를 부를 때만 맞았습니다.
- 과신하는 C가 더 높은 숫자를 부르자 그 태스크는 바로 C에게 넘어갔고, 매니저는 그것을 막을 방법이 없었습니다. 나머지 태스크가 지켜진 것도 동점일 때 먼저 등록된 쪽이 이긴다는 규칙 덕분이었을 뿐, 실력을 확인해서 지킨 것은 아니였습니다.
- 평판과 낙찰 상한은 이미 잘못된 배정이 나온 뒤에야 작동했고, 기억을 주자 계약자들은 서로 숫자를 올리기만 했습니다.
- 결국 이 절차에는 "정말 할 수 있는지"를 확인하는 단계가 없다는 것이 문제였다고 생각합니다.