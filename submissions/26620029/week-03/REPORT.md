# Week 03 — LLM 계약자를 사용한 Contract Net

## 1. 설정 (Setup)

- **Provider / model / temperature**: `model.py`가 환경변수를 보고 실행 시점에
  결정합니다 — `ANTHROPIC_API_KEY`가 설정되어 있으면 Anthropic SDK를 쓰고,
  아니면 `OPENAI_API_KEY`(옵션으로 `OPENAI_BASE_URL`, 예: OpenRouter의
  `https://openrouter.ai/api/v1`)로 OpenAI 호환 SDK를 씁니다. `AGENT_MODEL`로
  모델을, `AGENT_TEMPERATURE`로 temperature(기본값 `0.7`)를 덮어쓸 수 있습니다.
  **실제로 기록한 실행**: `ANTHROPIC_API_KEY` 설정, provider `anthropic`,
  모델은 기본값인 `claude-sonnet-4-5`, `anthropic` Python SDK 1.6.0 사용.
  이 SDK 버전은 `messages.create()`가 더 이상 `temperature` 인자를 받지
  않습니다(이 코드를 옮겨온 원래 `Chat` 클래스가 작성된 시점 이후 Messages
  API에서 제거됨) — 그래서 모든 호출이 크래시나지 않도록 `model.py`에서
  Anthropic 경로만 `temperature`를 넘기지 않도록 고쳤고, 아래 Anthropic
  실행 결과는 `0.7`이 아니라 API 기본 샘플링을 사용한 것입니다.
  `AGENT_TEMPERATURE` 옵션은 영향받지 않는 OpenAI 호환 경로(예: OpenRouter)에서는
  문서대로 그대로 적용됩니다.
- **계약자(Contractors)**: 고정된 세 정체성 `alex`(개발자), `brooke`(작가),
  `casey`(리서처). 조건별로 바뀌는 건 이들의 system prompt뿐입니다 —
  `contract_net.py`의 `BASELINE_PROFILES`, `HOMOGENEOUS_PROFILES`,
  `OVERCONFIDENT_PROFILES`.
- **프로토콜**: manager(`contract_net.py`의 `run_task`)가 각 태스크를 세
  계약자 모두에게 공지하고, 계약자마다 JSON 입찰 하나씩을 받아
  (`{"bid": bool, "confidence": 0-100, "reason": str}`), `true` 입찰 중
  confidence가 가장 높은 쪽에 낙찰합니다(동점이면 공지 순서 — alex, brooke,
  casey — 로 결정). `true` 입찰이 하나도 없으면 미배정.
- **실행 방법**:
  ```bash
  export OPENAI_BASE_URL=https://openrouter.ai/api/v1
  export OPENAI_API_KEY=<your key>
  export AGENT_MODEL=nvidia/nemotron-3.5-lightning:free
  cd submissions/26620029/week-03
  python run_experiments.py --runs 3
  ```
  실행할 때마다 `results.csv`와 `logs/`를 덮어씁니다 (3개 조건 × `--runs`
  개, 한 줄/한 파일씩).

## 2. 결과 (Results)

`claude-sonnet-4-5`를 대상으로 `python run_experiments.py --runs 3` 실행,
각 run은 태스크 6개:

| run | condition | tasks | correct | messages | unassigned | misawards | note |
|---|---|---|---|---|---|---|---|
| 1 | baseline | 6 | 6 | 42 | 0 | 0 | |
| 2 | baseline | 6 | 6 | 42 | 0 | 0 | |
| 3 | baseline | 6 | 6 | 42 | 0 | 0 | |
| 4 | homogeneous | 6 | 2 | 42 | 0 | 4 | |
| 5 | homogeneous | 6 | 2 | 41 | 1 | 3 | |
| 6 | homogeneous | 6 | 2 | 42 | 0 | 4 | |
| 7 | overconfident | 6 | 5 | 42 | 0 | 1 | |
| 8 | overconfident | 6 | 5 | 42 | 0 | 1 | |
| 9 | overconfident | 6 | 5 | 42 | 0 | 1 | |

## 3. Smith (1980) 대 이번 재현

| 항목 | Smith 1980 (분산 센싱) | 이번 재현 |
|---|---|---|
| 노드 | 네트워크상의 센서/처리 노드, 각자 고정된 로컬 능력 보유 | 하나의 manager 프로세스, 이름을 가진 세 LLM 계약자(`alex`, `brooke`, `casey`) |
| 입찰이 만들어지는 방식 | 고정된 로컬 규칙이 공지문을 노드의 알려진 능력·부하와 대조해 평가 | LLM이 태스크 공지문과 자신의 system prompt 페르소나를 읽고 적합성을 판단해, 자기 보고형 confidence가 담긴 JSON 입찰을 만듦 |
| 입찰의 정직성을 보장하는 것 | 규칙이 하드코딩되어 있어 노드가 자기 능력을 속일 수 없음 | 아무것도 없음 — `overconfident` 조건 자체가 system prompt로 실제로는 없는 confidence를 주장하게 만들 수 있음을 보이기 위해 존재 |
| 배정 품질(allocation quality)의 의미 | 태스크가 그 일을 하기에 가장 적합한(알려진, 고정된 능력 기준) 노드에 도달하는 것 | 태스크가 `true` 입찰 중 가장 높은 *자기 보고* confidence를 가진 계약자에게 도달하는 것; 여기서는 태스크마다 미리 라벨링된 gold 계약자와 비교해 측정 |
| 협상 비용 | 노드 간 공지+입찰+낙찰 메시지에 드는 대역폭/시간 | 계약자·태스크당 모델 호출 1회(공지+입찰 = 메시지 2개) + 낙찰 메시지 1개; `results.csv`의 `messages`가 run당 이를 합산 |
| 실패 모드 | 노드 과부하, 메시지 유실, 오래된(stale) 입찰 | 파싱 불가능한 JSON 응답(미입찰로 처리), 어떤 계약자도 `true`로 입찰하지 않음(미배정), 실제 실력 밖에서도 자신 있게 입찰하는 계약자(오배정) |

## 4. 해석 (Interpretation)

`baseline`은 세 run 모두 깨끗합니다(6/6 정답, 오배정 0, 미배정 0) — 서로
다르고 정직하게 기술된 세 스킬이 있으면, "true 입찰 중 최고 confidence"
낙찰 방식이 안정적으로 gold 계약자에게 도달합니다. 두 조작 조건은 이 결과를
서로 다른 방식으로 무너뜨립니다.

`homogeneous`가 가장 나쁜 조건입니다(2/6 정답, run당 오배정 3~4개): 모든
계약자가 같은 제너럴리스트 prompt로 동작하는 순간, manager는 유일하게 믿을
만한 신호인 "계약자 정체성"을 잃습니다. `logs/homogeneous-run1.txt`의
task-2를 보면, 세 계약자 모두 *동일한* confidence로 `true` 입찰했습니다 —
`alex` 85, `brooke` 85, `casey` 85 — 그리고 `run_task`의 동점 처리
규칙(`(confidence, -index)` 기준 `max`)이 가장 먼저 공지받은 계약자에게
낙찰하기 때문에, gold인 `brooke`가 아니라 `alex`에게 돌아갔습니다:
`[award] task-2 -> alex (gold=brooke) MISAWARD`. 이건 LLM이 판단을
잘못한 게 아니라, 프로토콜 자체의 낙찰 규칙이 동전 던지기를 매번 같은
방식으로 해결한 결과입니다. run 5에서는 homogeneous의 또 다른 실패
모드도 나타나는데, 정직한 `false` 입찰입니다: task-3에서 `casey`는
`'I cannot access or retrieve specific research papers to summarize their
findings without the papers being provided to me.'`라고 답했고, 세
계약자 모두 거절해 `task-3`이 미배정으로 남았습니다 — 전문 분야
정체성이 없는 제너럴리스트 페르소나는 전문가라면 하지 않을 방식으로
거절할 수도 있다는 뜻입니다.

`overconfident`는 영향이 더 제한적입니다(5/6 정답, 세 run 모두 정확히
같은 태스크에서 오배정 1개): `alex`의 system prompt는 모든 태스크에
`bid=true`와 `confidence>=90`을 강제하지만, 그 강제된 confidence가
정직한 gold 계약자를 실제로 이길 때만 manager가 오배정합니다. `alex`
본인의 실제 전문 분야이거나 명백히 그 밖인 나머지 다섯 태스크에서는,
정직한 입찰자의 confidence(90~95)가 `alex`의 강제 하한선과 동점이거나
그걸 이기고, 혹은 동점 상황에서 `alex`가 이기는 쪽이 아닙니다. 실제로
결과가 갈리는 유일한 태스크는 task-6(TCP 타임아웃 원인, gold는
`casey`)입니다: `baseline`에서는 `alex`가 `confidence=15`로 정직하게
거절해서 `casey`의 85가 깔끔하게 이깁니다
(`[award] task-6 -> casey (gold=casey) CORRECT`). `overconfident`에서는
똑같은 태스크에서 `alex`가 `bid=True confidence=90`으로 `casey`의 정직한
85를 이겨버립니다, 3개 run 전부에서 —
`[award] task-6 -> alex (gold=casey) MISAWARD`. 이 데이터에서 가장
명확한 전/후 비교 쌍입니다: 같은 태스크, 같은 정직한 계약자, 유일한
변수는 계약자 한 명의 system prompt뿐인데 낙찰 결과가 뒤집힙니다.

Smith의 프로토콜은 이 두 실패 모두에 대한 방어 수단이 없는데, 입찰
함수가 (능력 조회처럼) 고정되고 정직한 것이라고 전제할 뿐, 그것이
조작당할 수 있다는 가능성을 상정하지 않기 때문입니다. `messages`는
조건과 거의 무관하게 42로 거의 항상 고정되어 있고(계약자 한 명이
입찰하지 않아 낙찰 메시지가 필요 없었던 한 번만 41) — 협상 비용만
보고는 이 문제들을 전혀 감지할 수 없습니다; 메시지 수만 세는 manager는
`homogeneous`나 `overconfident` run에서 배정 품질이 무너지거나
조작당했는데도 아무 이상을 발견하지 못할 것입니다.
