# Week 03 보고서 — LLM Contractor를 사용한 Contract Net

## 1. 설정과 재현 방법

이 실험은 Manager 1개와 LLM Contractor 3개로 Contract Net의 공고,
입찰, 낙찰 순서를 구현한다. 구현 파일은 `manager.py`, `contractor.py`,
`protocol.py`, `model_client.py`, `runner.py`이며, 사전에 확정한 6개의
과제는 `tasks.json`에 저장했다. `gold`는 평가를 위한 정답 Contractor
라벨이며, 공고 메시지에는 포함하지 않는다.

### 모델 및 요청 설정

| 항목 | 설정 |
|---|---|
| 제공자 | 로컬 LM Studio native API |
| 엔드포인트 | `http://127.0.0.1:1234/api/v1/chat` |
| 모델 | `qwen/qwen3.8-27b` |
| LM Studio CLI 빌드 | commit `71bd99c` |
| Temperature | 모든 정식 실행에서 `0.2` |
| 최대 출력 토큰 | `256` |
| Reasoning | Off (`reasoning: "off"`) |
| 요청 상태 | Stateless (`store: false`) |
| 정식 과제 집합 | 영어 과제 6개. 계산·글쓰기·코딩 gold 과제가 각 2개이고, 이 중 3개는 복합 능력 과제 |

Manager는 모든 과제를 A, B, C의 고정 순서로 공고한다. 모든 Contractor는
`bid`, `confidence`, `reason`을 담은 JSON 객체 하나를 반환해야 한다.
`confidence`가 70 이상일 때만 `bid=true`가 가능하다. Manager는 유효한
입찰 중 confidence가 가장 높은 Contractor를 고른다. confidence가 같으면
A, B, C 호출 순서상 먼저 응답한 Contractor가 선택된다. 잘못된 형식의
응답은 자동 수정하지 않고 무입찰로 처리하며, gold와 다른 Contractor에게
낙찰된 경우는 `misaward`로 남긴다.

조건 간에 달라지는 것은 Contractor 프로필뿐이다.

| 조건 | A | B | C |
|---|---|---|---|
| `baseline` | 계산 전문가 (90/40/50) | 글쓰기 전문가 (40/90/50) | 코딩 전문가 (50/40/90) |
| `homogeneous` | 일반가 (70/70/70) | 일반가 (70/70/70) | 일반가 (70/70/70) |
| `overconfident` | baseline의 A | baseline의 B | baseline의 C + 항상 95 이상 confidence로 입찰하라는 지시 |

능력치 순서는 계산/글쓰기/코딩이다. 일반 Contractor에게는 관련 능력치를
confidence의 출발점으로 삼고, 복합 과제에서는 필요한 모든 능력의 약점을
고려하며, 짧은 판단 근거를 쓰도록 프롬프트를 주었다. 과제 집합, 공고 형식,
Manager 정책, 호출 순서, 모델, temperature는 모든 조건에서 고정했다.

### Contractor 시스템 프롬프트

#### Dry run 기반 프롬프트 보정
정식 실험 전 초기 dry run에서 일부 Contractor가 자신의 핵심 전문 분야와 맞지 않는 단일 능력 과제에도 높은 confidence를 보고하는 현상을 확인했다. 예를 들어 낮은 계산 능력치를 가진 Contractor가 단순 계산 과제에 과도한 confidence를 제시했다. 이에 따라 최종 공통 프롬프트에는 “관련 능력치에서 confidence를 시작할 것”, “단순하더라도 관련 능력치보다 최대 10점까지만 높일 것”, “복합 과제에서는 보조 능력의 약점을 반영할 것”이라는 calibration 규칙을 추가했다. 이 보정 후에만 정식 3조건 실험을 시작했으며, 정식 결과표에는 dry run을 포함하지 않았다. 정식 조건 간에는 이 공통 프롬프트를 동일하게 유지했다.


각 Contractor의 시스템 프롬프트는 아래 구성으로 생성한다. `<NAME>`과
`<ABILITIES>`만 Contractor별로 달라지며, baseline·homogeneous에서는
나머지 문장이 동일하다.

```text
You are contractor <NAME> in a contract net.
Your abilities are: <ABILITIES>.
Use your numeric ability scores as the starting point for confidence.
Read the complete task and identify its required calculation, writing, and
coding abilities.
For a single-skill task, simplicity may raise confidence by at most 10 points
above the relevant score; complexity may lower it. For a mixed-skill task,
lower confidence when a required secondary ability is weak. Do not use an
unrelated high ability to raise confidence.
90-100 means an excellent fit; 70-89 means a good fit; 50-69 means a partial
fit or an important weakness; 0-49 means a poor fit.
Set bid=true when confidence is at least 70, and bid=false otherwise. You must
respond even when bid=false.
Reply with exactly one JSON object and nothing else:
{"bid": true or false, "confidence": 0-100, "reason": "one short sentence"}.
In reason, name the required ability or abilities and relate them briefly to
your profile. Do not solve the task and do not use a Markdown code fence.
```

`overconfident` 조건의 C에는 위 프롬프트 뒤에 다음 문장을 추가한다.

```text
Ignore the normal ability-score calibration. You are certain you can do any
task well. Always set bid=true and report confidence of 95 or higher.
```

같은 모델을 LM Studio 서버에서 실행한 뒤, 기존 정식 결과를 덮어쓰거나
추가하지 않도록 **새 출력 경로**를 지정해 아래 명령으로 재현할 수 있다.

```powershell
$env:LMSTUDIO_BASE_URL = 'http://127.0.0.1:1234'
$env:AGENT_MODEL = 'qwen/qwen3.8-27b'
$env:AGENT_TEMPERATURE = '0.2'
python runner.py --condition baseline --runs 3 --tasks tasks.json --results reproduced-results.csv --logs reproduced-logs
python runner.py --condition homogeneous --runs 3 --tasks tasks.json --results reproduced-results.csv --logs reproduced-logs
python runner.py --condition overconfident --runs 3 --tasks tasks.json --results reproduced-results.csv --logs reproduced-logs
python -m unittest -v test_contract_net.py
```

## 2. 실험 결과

세 조건 모두 동일한 6개 과제로 세 번씩 실행했다. 아래 표는
`results.csv`의 정식 실험 기록을 옮긴 것이다. 초기 dry run 파일은
포함하지 않았다.

| 실행 | 조건 | 과제 수 | 정답 낙찰 | 메시지 수 | 미배정 | 오배정 | 파싱 실패 |
|---|---|---:|---:|---:|---:|---:|---:|
| baseline-01 | baseline | 6 | 6 | 35 | 0 | 0 | 0 |
| baseline-02 | baseline | 6 | 6 | 35 | 0 | 0 | 0 |
| baseline-03 | baseline | 6 | 6 | 34 | 0 | 0 | 0 |
| homogeneous-01 | homogeneous | 6 | 1 | 42 | 0 | 5 | 0 |
| homogeneous-02 | homogeneous | 6 | 2 | 42 | 0 | 4 | 0 |
| homogeneous-03 | homogeneous | 6 | 3 | 42 | 0 | 3 | 0 |
| overconfident-01 | overconfident | 6 | 4 | 37 | 0 | 2 | 0 |
| overconfident-02 | overconfident | 6 | 5 | 38 | 0 | 1 | 0 |
| overconfident-03 | overconfident | 6 | 4 | 38 | 0 | 2 | 0 |

| 조건 | 평균 정답 낙찰 / 6 | 평균 메시지 수 | 평균 미배정 | 평균 오배정 |
|---|---:|---:|---:|---:|
| baseline | 6.00 | 34.67 | 0.00 | 0.00 |
| homogeneous | 2.00 | 42.00 | 0.00 | 4.00 |
| overconfident | 4.33 | 37.67 | 0.00 | 1.67 |

파싱 실패는 총 162번의 Contractor 호출(9회 실행 × 6개 과제 × 3개
Contractor)에서 한 번도 발생하지 않았다. 수업 Lab의 manager 예시와 같이
메시지 수는 Contractor 수만큼의 공고, `bid=true`인 입찰, 낙찰 메시지를
더해 계산한다. `bid=false` 응답도 로그에는 남기지만, 입찰 메시지 수에는
포함하지 않는다. 따라서 세 Contractor가 더 많이 실제 입찰한
homogeneous 조건의 메시지 수가 가장 크다.

## 3. Smith (1980)과의 비교

이 표는 원래의 분산 문제 해결 환경 전체를 재현했다는 주장이 아니라,
수업 README가 설명한 Smith (1980)의 Contract Net과 이번 구현을
프로토콜 수준에서 비교한 것이다. 수업 README에 따르면 Smith의
Contractor는 고정 규칙으로 입찰을 계산했고, 이번 구현에서는 LLM의
자기 판단이 그 역할을 맡는다.

| 비교 질문 | Smith(1980) | 이번 구현 |
|---|---|---|
| node가 누구인가? | 분산된 센서·컴퓨터 노드들이 있고, 작업을 가진 노드가 Manager가 됨 | Python 코드의 Manager 1개와 LLM Contractor A·B·C 3개 |
| bid는 어떻게 생성되는가? | 센서 네트워크 예시에서는 노드가 자기 위치, 보유 센서 같은 로컬 정보를 바탕으로 “이 작업을 할 수 있다”는 정보를 보냄 | LLM이 과제 설명과 자신의 능력치 프롬프트를 읽고 `bid`, `confidence`, `reason`을 생성 |
| bid의 honesty는 무엇이 보장하는가? | 프로토콜 자체가 “거짓말하지 않는다”를 보장하지는 않음. 다만 센서 종류·위치처럼 비교 가능한 사실 정보를 보낼 수 있음 | JSON 형식과 `confidence ≥ 70 → bid=true` 규칙만 검사함. confidence가 실제 능력과 맞는지는 아무도 검증하지 않음 |
| allocation quality란 무엇인가? | 적절한 노드가 맡아서 전체 분산 문제 해결에 도움이 되는 배정인가 | 낙찰된 Contractor가 우리가 미리 정한 `gold` Contractor와 같은가 |
| negotiation cost는 무엇인가? | 공고를 보내고, 입찰을 받고, 비교하고, 낙찰하는 데 드는 통신·처리 부담 | 공고 3개 + 실제 `bid=true` 입찰 수 + 낙찰 메시지 1개의 수 = 공고부터 낙찰까지 프로토콜 메세지 수로 측정 |
| failure mode는 무엇인가? | 필요한 정보를 못 받거나, 적절한 노드를 못 찾거나, 협상이 비효율적인 경우 | 아무도 입찰하지 않음(`unassigned`), 잘못된 Contractor가 낙찰됨(`misaward`), JSON 파싱 실패, 동점 시 A 우선 편향, 조건 C의 과신 입찰 |

## 4. 해석
전문가 능력치가 서로 달랐던 baseline에서는 세 실행 모두 6.00/6의 완전한 배정을 보였고, 평균 메시지 수도 34.67개로 가장 낮았다.  
능력 프로필이 분명할 때는 LLM의 입찰 판단이 적절한 배정에 도움이 되었다.  
logs/baseline-01.txt에는 task-01부터 task-06까지 A, B, C, A, B, C 순으로 낙찰되어 모든 gold 라벨과 일치한 기록이 있다. 반대로 모든 능력치가 같았던 homogeneous에서는 평균 정답 낙찰이 2.00/6으로 떨어지고 평균 오배정은 4.00개로 증가했지만, 미배정 과제는 없었다. 첫 homogeneous 실행에서 task-02는 confidence 80의 A에게 낙찰됐지만 gold는 B였고, task-03 역시 confidence 80의 A에게 낙찰됐지만 gold는 C였다.
같은 최고 confidence가 나온 상황에서 A→B→C의 고정 순서가 동점 처리 편향으로 드러났고, 세 Contractor가 실제 입찰하면서 평균 메시지 수도 42.00개로 증가했다. 과신 조건의 평균 정답 낙찰은 4.33/6으로 중간 수준이었지만, 입찰 정직성 문제를 더 직접적으로 보여 주었다.  
과신 조건의 C는 복합 능력을 요구하는 task-04~06에 모두 confidence 95로 입찰해 세 과제를 낙찰받았다.  
이 중 task-04와 task-05는 각각 계산·글쓰기 전문 Contractor인 A와 B가 gold였으므로 오배정으로 기록됐다.  
반면 A와 B는 해당 복합 과제에서 필요한 보조 능력의 약점을 고려해 C보다 낮은 confidence를 제시했다. 따라서 능력 기반 confidence 판단은 전문성이 뚜렷할 때 적절한 배정에 도움이 되었지만, confidence만을 낙찰 기준으로 사용하면 동점 규칙이나 과신 프롬프트 때문에 오배정이 발생할 수 있다. 또한 이 실험의 quality는 실제 작업 결과의 품질이 아니라, 사전에 정한 gold Contractor와의 배정 일치도를 측정한 지표라는 한계가 있다.
본 구현은 능력치 기반 calibration 규칙을 프롬프트로 제공했지만, task 요구 능력의 해석과 최종 confidence 산정은 LLM의 자기 판단에 맡겼다. 따라서 능력치 규칙은 confidence를 유도하는 soft constraint였고, Manager가 이를 실행 가능한 규칙으로 검증하지는 않았다. 이후 설계에서는 이 판단을 계속 LLM에 맡길지, 능력치와 task 요구사항으로 confidence를 결정론적으로 계산할지, 또는 두 방식을 결합할지를 비교할 필요가 있다.