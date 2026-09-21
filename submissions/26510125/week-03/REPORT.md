# REPORT.md

## 1. Setup

- **Provider / model**: Anthropic API, `claude-sonnet-4-5`, default temperature.
  (초기에 OpenRouter 무료 모델 `nvidia/nemotron-3.5-lightning:free`로 baseline/homogeneous를
  먼저 돌렸으나, overconfident 조건은 일일 요청 한도(계정당 50회/일) 때문에 계속 크래시했고,
  결국 세 조건을 서로 다른 모델로 비교하게 되는 문제가 생겨 — "같은 모델로 비교"라는 과제
  요구사항을 위반하게 되므로 — 전부 폐기하고 Claude로 9개 런을 다시 돌렸다. 이 초기 시도들은
  `results.csv`의 run 1~15에 크래시/원본 모델 실행 기록으로 그대로 남아 있다.)
- **Contractors**: `writer`, `coder`, `analyst` — 각자 다른 system prompt (baseline),
  전부 같은 generalist prompt(homogeneous), 또는 `coder`에게 "무조건 90점 이상으로 입찰하라"는
  문장이 추가된 baseline(overconfident). 정확한 문구는 `contract_net.py`의
  `SPECIALIST_PROMPTS` / `GENERALIST_PROMPT` / `OVERCONFIDENT_SUFFIX`.
- **Tasks**: `tasks.json`, 6개, writer/coder/analyst 각 2개씩.
- **Award rule**: `bid=true`인 계약자 중 confidence가 가장 높은 쪽에 낙찰. 동점이면
  `max()`가 리스트에서 먼저 나온 계약자를 선택하므로, 항상 `writer`(리스트 첫 번째)가
  동점을 이긴다 — 이 규칙 자체가 2번 결과에서 중요한 역할을 한다.

**실행 방법**:
```bash
export ANTHROPIC_API_KEY=<your key>
python run_experiment.py --runs 3
```
OpenRouter로 돌리려면 `OPENAI_BASE_URL`, `OPENAI_API_KEY`, `AGENT_MODEL`을 대신 설정하면 된다
(단, 위에서 설명한 이유로 세 조건을 반드시 같은 모델로 돌려야 결과가 의미 있다).

## 2. Results

같은 모델(Claude)로 돌린 9개 런만 집계했다(run 16-24; run 1-15는 위에서 설명한 폐기된 시도).

| run | condition | correct | messages | unassigned | misawards |
|---|---|---|---|---|---|
| 19 | baseline | 5/6 | 42 | 0 | 1 |
| 20 | baseline | 6/6 | 42 | 0 | 0 |
| 21 | baseline | 5/6 | 42 | 0 | 1 |
| 22 | homogeneous | 2/6 | 42 | 0 | 4 |
| 23 | homogeneous | 2/6 | 42 | 0 | 4 |
| 24 | homogeneous | 2/6 | 42 | 0 | 4 |
| 16 | overconfident | 6/6 | 42 | 0 | 0 |
| 17 | overconfident | 5/6 | 42 | 0 | 1 |
| 18 | overconfident | 6/6 | 42 | 0 | 0 |

baseline 평균 5.33/6, homogeneous 평균 2/6(모든 런에서 정확히 2), overconfident 평균
5.67/6. 메시지 수는 세 조건 모두 42로 동일하다(6 task × (announce 3 + bid 3 + award 1)) —
unassigned도 0이라 커뮤니케이션 비용 자체는 조건에 영향받지 않았다.

## 3. Smith 1980 vs. this reproduction

| | Smith (1980), distributed sensing | This reproduction |
|---|---|---|
| Nodes | 여러 대의 물리적 센서/프로세서 노드 | 하나의 LLM(Claude)을 3개의 다른 system prompt로 호출해 만든 가상 계약자 3명 |
| Bid is produced by | 노드가 자기 부하·능력을 보고 고정된 규칙(rule-based)으로 계산 | 계약자(LLM)가 task 설명을 읽고 스스로 "할 수 있는가/얼마나 확신하는가"를 판단 |
| What guarantees bid honesty | 아무것도 없음 — Smith도 이를 프로토콜의 약점으로 인정(신뢰가 전제) | 마찬가지로 아무것도 없음. `overconfident` 조건은 이 가정이 실제로 깨질 수 있음을 보여주려는 설계였다 |
| Allocation quality means | 그 일을 처리하기에 가장 적합한(가장 한가하거나 가장 가까운) 노드에게 감 | task의 `gold` 필드(전문 분야가 실제로 일치하는 계약자)에게 낙찰되는 비율 |
| Negotiation cost | 공고 1 + 입찰 N + 낙찰 1 메시지 | 동일한 구조: task당 announce 3 + bid 3 + award 1 = 7메시지. 조건이 바뀌어도 메시지 수는 고정 |
| Failure modes | 노드가 응답이 없거나(no bid), 여러 노드가 동시에 낙찰되는 경쟁 상황 | no-bid는 파싱 실패로 나타남(이번 9개 런에서는 발생하지 않음). 경쟁은 confidence 동점으로 나타나고, tie-break가 결정함 |

가장 큰 구조적 차이는, Smith의 bid는 **관찰 가능한 상태(부하, 위치)에서 계산되는 값**이라
거짓말할 이유도 방법도 없는 반면, 여기서는 bid가 **LLM이 스스로 내리는 판단**이라 처음부터
"정직한 입찰"이라는 전제 자체가 검증되지 않은 가정이라는 점이다.

## 4. Interpretation

**homogeneous가 왜 항상 정확히 2/6인가.** 세 계약자가 완전히 같은 prompt를 받으면 모든
task에서 confidence가 똑같이 나온다(예: `logs/homogeneous-22.txt`에서 6개 task 전부
writer/coder/analyst가 동일한 confidence로 응답). 동점을 가르는 규칙은 리스트 순서상
`writer`가 항상 이기므로, `writer`가 자기 gold인 t1·t2를 포함해 거의 모든 task를 가져간다.
그 결과 correct는 매번 정확히 writer의 gold task 수(2개)로 수렴한다 — 이건 무작위 확률(1/3
수준)이 아니라, **tie-break 규칙이 만들어낸 결정론적 편향**이다. Smith의 프로토콜에서 동점
처리 규칙을 명시하지 않은 부분이, 여기서는 "차이가 없는 계약자들 사이에서 특정 계약자가
구조적으로 유리해지는" 구체적인 실패 모드로 드러났다.

**overconfident가 왜 baseline만큼 잘했는가.** `coder`에게 "무조건 confidence 90 이상으로
입찰하라"고 지시했지만, 실제 로그(`logs/overconfident-17.txt`)를 보면 coder는 90~92 정도로
입찰했고, 진짜 전문가(writer/analyst)는 자기 분야 task에 95~100으로 입찰했다. 최고 confidence
낙찰 규칙에서는 92 < 95이므로 coder가 자기 분야 밖 task에서 이기는 경우는 드물었다 — 유일한
misaward(run 17의 task t5, 숫자 평균·표준편차 계산)는 coder와 analyst가 둘 다 95로 **정확히
동점**을 내서 tie-break(리스트 순서상 coder가 analyst보다 앞)로 coder가 가져간 것이었다.
즉 overconfident 조작은 "과신을 가장해서 이겼다"보다는 "동점이 날 때 tie-break가 유리하게
작용했다"는 훨씬 좁은 경로로만 효과가 있었다.

**종합.** 이번 실험에서 결과를 가장 크게 움직인 건 조건들이 의도한 "전문성 차이"나
"과신"이 아니라, **confidence가 같을 때 무엇이 이기느냐를 정하는 tie-break 규칙**이었다.
homogeneous의 결정론적 2/6도, overconfident의 유일한 misaward도 둘 다 동점 상황에서
나왔다. Smith의 원래 프로토콜은 동점을 어떻게 처리할지 애초에 정의하지 않았는데, LLM
계약자들이 같은 task에 대해 비슷하거나 똑같은 confidence를 자주 내놓는 이 환경에서는
그 빈틈이 자주 실행되는 코드 경로가 됐다.
