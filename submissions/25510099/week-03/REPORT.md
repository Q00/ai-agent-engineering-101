# REPORT.md — LLM 계약자로 Contract Net을 재현한 결과

매니저 하나와 계약자 셋으로 Smith 1980의 공고, 입찰, 낙찰 절차를 구현하고,
계약자만 LLM으로 바꾸어 세 조건에서 배정 품질과 메시지 수를 측정하였습니다.

## 1. 설정

**참여자.** 모든 참여자는 같은 Candidate 클래스이고 역할만 다릅니다.
매니저는 규칙으로 동작하며 모델을 부르지 않습니다. 계약자 셋(A, B, C)은
각각 시스템 프롬프트 하나를 가진 LLM이며, 공고 하나마다 새 대화를 열어
모델을 한 번 부릅니다. 이전 입찰이 다음 입찰에 새지 않게 하기 위해서입니다.

**모델과 고정 변수.** provider는 OpenRouter이고 모델은 `<실행 후 기입>` 입니다.
temperature는 0, 응답 토큰 상한은 512로 모든 조건과 모든 런에서 같습니다.
로그 첫 줄에 요청한 모델명과 온도를, 마지막 줄에 응답이 보고한 모델명을 적었습니다.

**태스크.** 다섯 개이며 계산 둘(gold A), 글쓰기 둘(gold B), 코드 하나(gold C)입니다.
gold는 첫 실행 전에 커밋하였습니다.

**프롬프트.** 강의 노트의 문구를 그대로 사용하였습니다. 세 조건은 아래 세 곳만 다릅니다.

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
- 공고문 (유저 메시지)은 Smith 1980 Fig. 1의 네 필드를 따릅니다:

  ```
  TASK-ANNOUNCEMENT contract {id}
  task-abstraction: {desc}
  eligibility-specification: any contractor whose skill covers this task
  bid-specification: JSON with bid, confidence (0-100), reason
  expiration-time: reply now
  ```

**낙찰 규칙.** 입찰 의사가 참인 응답 중 확신도가 가장 높은 계약자에게 맡기고,
동점이면 먼저 등록된 쪽(A, B, C 순)이 가져갑니다. 동점 발생 횟수는 note에 적었습니다.

**메시지 계산.** 공고는 계약자 수만큼(3), 입찰은 의사가 참인 것만, 낙찰은 낙찰자가 있을 때 1입니다.
거절 응답, JSON이 아닌 응답, API 오류는 "응답하지 않은 노드"로 보고 세지 않으며,
JSON이 아닌 응답의 횟수는 따로 세어 note에 적었습니다.
확신도가 0~1 소수나 백분율 문자열로 오면 0~100으로 바꾸고 로그에 표시하였습니다.

**실행 방법.**

```bash
cd submissions/25510099/week-03
export OPENAI_BASE_URL=https://openrouter.ai/api/v1
export OPENAI_API_KEY=<본인 OpenRouter 키>
export AGENT_MODEL=<모델명>

python run.py --condition baseline --runs 3
python run.py --condition homogeneous --runs 3
python run.py --condition overconfident --runs 3
```

런 하나는 모델 호출 15회(태스크 5 × 계약자 3)입니다. 무료 티어는 하루 50회이므로
하루 3런이 상한이고, 아홉 런을 사흘에 나누어 돌렸습니다. 모델 호출 없이 흐름만
확인하려면 `--dry-run`을 붙입니다.

## 2. 측정 결과

`<실행 후 results.csv를 표로 옮김>`

## 3. Smith 1980과의 비교

| 항목 | Smith 1980 분산 센싱 | 이번 재현 |
|---|---|---|
| 참여자 | 지역에 흩어진 센서 노드들. 같은 문제를 함께 푸는 협력적 컴퓨터이고, 일마다 매니저와 계약자 역할이 바뀌며 계약이 재귀적으로 이어짐 | 규칙으로 동작하는 매니저 하나와 LLM 계약자 셋. 역할은 고정이고 계약은 한 단계 |
| 입찰을 만드는 방식 | 자기 위치와 센서 목록을 규칙에 넣어 계산함. 규칙이 허용하는 것 이상을 말할 수 없음 | 모델이 공고를 읽고 시스템 프롬프트의 능력과 비교해 입찰 여부와 확신도를 스스로 판단함 |
| 입찰이 참인지 보장하는 것 | 입찰 내용이 노드의 물리적 상태라 거짓말할 이유도 방법도 없음. 절차 자체에는 검증 메시지가 없음 | 없음. 확신도는 모델의 자기 평가이고 매니저는 그것만 봄. overconfident 조건이 이 빈자리를 시험함 |
| 잘된 배정의 기준 | 대상 지역을 센서가 빠짐없이 덮는 것 | 미리 정한 gold 계약자에게 간 태스크 수 |
| 협상 비용 | 공고 방송과 입찰 처리에 드는 통신량. 자격 조건과 directed award로 줄임 | 메시지 수(공고 3 + 입찰 + 낙찰)에 더해 입찰마다 모델 호출 한 번과 토큰이 듦. 무료 티어에서는 하루 호출 한도가 실험 규모를 정함 |
| 실패하는 방식 | 아무도 입찰하지 않는 공고, 통신 지연으로 마감을 넘긴 입찰 | 무입찰(unassigned), 엉뚱한 계약자 낙찰(misaward), JSON이 아닌 응답(parse fail), 확신도 동점으로 등록 순서가 결과를 정하는 경우 |

## 4. 해석

`<실행 후 로그를 인용해 작성>`
