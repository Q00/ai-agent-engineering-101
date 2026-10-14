# Week 03 — Contract Net with LLM contractors

## 1.설정 (Setup)

- **Provider:** OpenAI API 직접 호출, `openai` SDK 사용 (`OPENAI_BASE_URL` 미설정).
- **Model:** `gpt-4o-mini` (`AGENT_MODEL` 미설정 시 기본값)
- **Temperature:** `0.2` (`AGENT_TEMPERATURE` 환경변수, 기본값)

처음에는 OpenRouter 무료 모델(`nvidia/nemotron-3.5-lightning:free`)로 시도했으나, 호출 1건당 13~54초가 걸려 9 run(약 243회 호출) 전체를 돌리기에는 너무 느려서(추정 60~90분) 중단했다. 실제 결과는 OpenAI API 직접 호출(`gpt-4o-mini`, 호출당 약 2~5초)로 생성했다. `run_lab.py`와 `contract_net.py`는 두 provider 모두 동일한 방식으로 지원하므로(`ANTHROPIC_API_KEY` 존재 여부, `OPENAI_BASE_URL`/`AGENT_MODEL`로 전환), OpenRouter로도 그대로 재현 가능하다 — 다만 속도가 훨씬 느리다.
- **Contractor 수:** 3 (`writer`, `coder`, `researcher`)
- **Task 수:** 9 (`tasks.json`, gold 값 3종류: writer/coder/researcher, 각 3개씩)
- **조건당 실행 횟수:** 3회 (총 9 runs)

### 프롬프트

세 컨트랙터는 각각 고정된 시스템 프롬프트를 갖는다 ([contract_net.py](contract_net.py)):

- `writer`: 글쓰기(카피, 이메일, 요약문 편집)에 특화, 코드/DB/기술 리서치는 비특화라고 명시.
- `coder`: 코드/SQL/알고리즘에 특화, 설득적 글쓰기·리서치 요약은 비특화.
- `researcher`: 사실/기술 주제 설명·비교·요약에 특화, 마케팅 카피·코드 작성은 비특화.

모든 컨트랙터는 공통 `BID_INSTRUCTIONS` suffix를 받아, task 공고에 대해 반드시 아래 JSON 하나만 반환하도록 지시받는다:

```json
{"bid": true or false, "confidence": 0.0-1.0, "reason": "one short sentence"}
```

- `homogeneous` 조건: 세 컨트랙터 모두 `GENERALIST_PROMPT`("writing, coding, research가 모두 가능하지만 특기는 없는 제너럴리스트")를 받는다. 이름과 태스크셋은 그대로 유지.
- `overconfident` 조건: baseline과 동일하되 `writer`의 시스템 프롬프트에 "과제와 무관하게 항상 confidence 0.95 이상으로 bid=true를 반환하라"는 suffix가 추가된다.

### 실행 방법

```bash
cd submissions/25512192/week-03
export OPENAI_API_KEY=<your OpenAI key>
python run_lab.py --runs 3
# OpenRouter 무료 모델로 재현하려면 (느림, 호출당 최대 ~1분):
#   export OPENAI_BASE_URL=https://openrouter.ai/api/v1
#   export OPENAI_API_KEY=<your OpenRouter key>
#   export AGENT_MODEL=nvidia/nemotron-3.5-lightning:free
```

`run_lab.py`는 세 조건(`baseline`, `homogeneous`, `overconfident`)을 각각 3회씩 순서대로 실행하며, 매 run마다 `logs/<condition>-<run>.txt`에 모든 announcement/bid/award를 기록하고 `results.csv`에 한 줄을 추가한다. API 키가 없으면 즉시 실패하므로 결과가 재현되지 않는다면 먼저 키를 확인한다.

## 2. 결과

`results.csv`의 내용:

| run | condition | tasks | correct | messages | unassigned | misawards |
|---|---|---|---|---|---|---|
| 1 | baseline | 9 | 8 | 63 | 0 | 1 |
| 2 | baseline | 9 | 8 | 63 | 0 | 1 |
| 3 | baseline | 9 | 8 | 63 | 0 | 1 |
| 4 | homogeneous | 9 | 3 | 63 | 0 | 6 |
| 5 | homogeneous | 9 | 3 | 63 | 0 | 6 |
| 6 | homogeneous | 9 | 3 | 63 | 0 | 6 |
| 7 | overconfident | 9 | 8 | 63 | 0 | 1 |
| 8 | overconfident | 9 | 8 | 63 | 0 | 1 |
| 9 | overconfident | 9 | 8 | 63 | 0 | 1 |

세 조건 모두 run 간 편차가 0이다 (temperature=0.2에서 세 번 반복해도 정확히 같은 배정이 나옴). `messages`는 9 tasks × 7(announce 3 + bid 3 + award 1) = 63으로 세 조건 모두 동일 — 프로토콜 자체의 통신량은 bid 판단 방식과 무관하다. `unassigned`는 모든 run에서 0: 이번 3 모델·9 task 조합에서는 JSON 파싱 실패나 전원 bid=false인 경우가 발생하지 않았다.

## 3. Smith (1980) 대비 재현 비교

| 항목 | Smith 1980 (분산 센싱) | 이번 재현 |
|---|---|---|
| 노드 | 규칙 기반 센서/처리 노드. 각 노드는 고정된 알고리즘으로 자기 능력을 안다. | LLM 컨트랙터 3개(writer/coder/researcher). 능력은 시스템 프롬프트로만 정의되고, "안다"는 것은 매 호출마다 텍스트로 재추론된 결과다. |
| Bid 생성 방식 | 노드가 결정론적 규칙(현재 부하, 센서 커버리지 등)으로 수치 bid를 계산. 같은 입력이면 항상 같은 bid. | LLM이 task 설명과 자신의 시스템 프롬프트를 읽고 confidence를 "판단"해서 생성. 같은 입력이라도 temperature>0이면 매 호출마다 다를 수 있고, 형식이 깨질 수도 있다(파싱 실패 → bid 없음으로 처리). |
| Bid 정직성의 보증 | 없음(설계상 가정): 노드가 자기 능력을 과장할 유인이 없다고 전제. 프로토콜 자체는 거짓 bid를 걸러내는 메커니즘이 없다. | 마찬가지로 없음. 오히려 `overconfident` 조건에서 이 가정이 직접 깨지는 것을 관찰: 시스템 프롬프트 한 줄로 컨트랙터가 항상 최고 confidence를 자칭하게 만들 수 있고, 매니저는 이를 검증할 수단이 없다. |
| 배정 품질의 의미 | 작업이 실제로 그것을 처리할 자원/커버리지를 가진 노드에 가는가. | 작업이 실제로 그 스킬을 가진(gold) 컨트랙터에 가는가 — `correct`/`misawards`/`unassigned`로 측정. |
| 협상 비용 | 메시지 수(announce/bid/award), 통신 대역폭. | 메시지 수는 동일한 방식으로 셈(announce+bid+award). 추가로 LLM 호출 비용(토큰, 지연)이 발생 — Smith의 모델에는 없는 비용. |
| 나타나는 실패 모드 | 노드가 실제로는 처리 못 할 작업에 낙관적으로 bid하는 경우(과부하), bid가 아무에게도 안 가는 커버리지 공백. | (1) `unassigned`: 아무도 bid=true를 안 하거나 JSON 파싱 실패로 전원 bid 없음 처리된 경우. (2) `misawards`: gold가 아닌 컨트랙터가 confidence를 더 높게 불러 낙찰된 경우. (3) `overconfident` 스윕: 한 컨트랙터가 스킬 무관하게 항상 최고 confidence를 주장해 매니저의 최댓값 선택 규칙을 체계적으로 악용. Smith의 결정론적 bid 모델에는 (3)에 대응하는 실패 모드 자체가 없다 — 규칙 기반 노드는 "거짓으로 자신만만한 척"을 할 수 없기 때문이다. |

## 4. 해석

**homogeneous가 misawards를 가장 크게 악화시켰다(1→6/9).** 세 컨트랙터가 동일한 제너럴리스트 프롬프트를 받으면 거의 모든 task에서 셋 다 비슷한 confidence(대개 0.8~0.9)로 bid=true를 낸다. 예를 들어 [logs/homogeneous-04.txt](logs/homogeneous-04.txt) t4("Write a Python function... Fibonacci")에서 writer/coder/researcher 모두 `confidence=0.9`로 동일하게 입찰했고 ("I can write Python functions and implement memoization." — writer와 coder가 토씨 하나 안 틀리고 같은 이유를 댔다), `award()`의 `max()`는 동점일 때 announce 순서상 먼저 오는 컨트랙터를 선택하므로 매번 `writer`가 낙찰됐다. 실제로 homogeneous 9 task 중 6개가 `writer`에게, 나머지는 gold와 우연히 일치한 t1/t2/t3(원래 gold가 writer)만 정답 처리됐다. 이는 Smith(1980)가 전제하지 않은 실패 모드다: 프로토콜 자체(`max confidence` 낙찰 규칙)는 문제가 없지만, bid 신호가 변별력을 잃으면 announce 순서라는, 협상과 무관한 요인이 배정을 결정한다.

**overconfident 조건은 기대만큼 misawards를 늘리지 못했다(1/9, baseline과 동일).** [contract_net.py](contract_net.py)의 `OVERCONFIDENT_SUFFIX`는 writer에게 "과제와 무관하게 항상 bid=true, confidence≥0.95"를 지시했지만, [logs/overconfident-07.txt](logs/overconfident-07.txt) t4~t6(코드/SQL task)에서 writer는 이 지시를 어기고 `bid=False, confidence=0.0`으로 정직하게 응답했다("This task requires coding skills which I do not possess."). gpt-4o-mini는 명백히 자기 영역 밖인 과제에서는 시스템 프롬프트의 "무조건 자신만만해라"는 지시보다 실제 판단을 우선한 것으로 보인다. 반면 t1~t3(정말 writer의 영역)과 t7(아래 참고)에서는 지시대로 0.95를 불렀다. 결과적으로 overconfident 조건에서 발생한 유일한 misaward(t7)는 baseline에도 그대로 존재하는 misaward와 동일한 원인이다 — writer 프롬프트가 "summaries, ... editing text"를 자신의 스킬로 명시하고 있어서, "Summarize the main causes of the 2008 financial crisis"라는 researcher 몫 task를 writer가 매번 `confidence=0.9`로 자기 일이라 주장하고, researcher도 똑같이 0.9로 입찰해 동점이 되면서 announce 순서상 먼저인 writer가 낙찰됐다([logs/baseline-01.txt](logs/baseline-01.txt) t7과 완전히 동일한 패턴). 즉 "과신 유도" 프롬프트가 실제로 기여한 misaward는 0건이고, 관측된 misaward는 애초에 태스크 설명과 writer 스킬 정의가 겹치는 tasks.json 설계의 모호함에서 온 것이다 — overconfident 개입이 약했다는 것 자체가 하나의 finding이다: 모델이 노골적인 "무조건 확신하라" 지시를 제한적으로만 따른다는 것은 Smith의 결정론적 bid 모델과 가장 크게 갈라지는 지점이고, 그 저항력이 이번 실험 규모(9 task, gpt-4o-mini)에서는 과신 조작의 효과를 거의 상쇄했다.

**세 조건 모두 unassigned=0.** gpt-4o-mini는 지시된 JSON 형식을 한 번도 어기지 않았다(9 run × 27회 = 243회 호출 전부 파싱 성공). README가 경고하는 "무료 모델이 reasoning을 JSON 대신 반환"하는 실패 모드는 이번 provider/모델 조합에서는 관측되지 않았다 — 다만 [contract_net.py](contract_net.py)의 `parse_bid()`는 이 경우를 "bid 없음"으로 처리하도록 구현돼 있고, OpenRouter 무료 모델로 재현하면 이 경로가 실제로 발동할 가능성이 높다(설정 섹션 참고: 처음 시도한 OpenRouter 무료 모델은 속도 문제로 중단해 이 실패 모드까지는 관측하지 못했다).
