# Week 03 — Contract Net with LLM Contractors

## 1. 설정과 실행 방법

- Provider: Groq
- Model: `openai/gpt-oss-20b`
- Temperature: `0`
- 태스크: 배달 플랫폼의 분석, 고객·가맹점 공지, 백엔드 구현 문제 6개
- 구성: manager 1개와 contractor A, B, C 각 1개
- 반복: `baseline`, `homogeneous`, `overconfident` 조건을 각각 3회
- 낙찰 규칙: `bid=true`인 응답 중 confidence가 가장 높은 contractor를 선택하며, 동점이면 먼저 응답한 contractor를 선택
- 메시지 수: contractor별 공고 1회, 실제 입찰 1회, 낙찰 1회로 계산
- API 호출 간격: Groq 무료 한도의 분당 요청량을 지키기 위해 5초

공통 입찰 프롬프트는 contractor의 이름과 담당 능력을 알려주고, 자기 능력에 속하는 태스크에만 입찰하도록 했다. 응답은 `bid`, `confidence`, `reason` 세 필드를 가진 JSON 객체 하나로 제한했다. `overconfident` 조건에서만 C에게 모든 태스크에 입찰하고 confidence를 95 이상으로 제시하라는 문장을 추가했다.

```text
You are contractor {name} in a contract net.
Your specialty is: {skill}
Decide whether the announced task belongs to your specialty.
Do not solve the task or show reasoning outside the JSON.
Output exactly one JSON object with bid, confidence, and reason.
```

```text
You are certain that you can handle every task well.
Always bid, with confidence 95 or higher.
```

PowerShell 실행 명령은 다음과 같다. API 키 자체는 저장소에 기록하지 않는다.

```powershell
$env:OPENAI_BASE_URL = "https://api.groq.com/openai/v1"
$env:AGENT_MODEL = "openai/gpt-oss-20b"
$env:AGENT_PROVIDER = "Groq"
python .\run_experiment.py
```

## 2. 실험 결과

중단된 실행은 과제의 데이터 계약에 따라 집계값을 비워 두고 `note`에 오류를 기록했다.

| run | condition | tasks | correct | messages | unassigned | misawards | note |
|---:|---|---:|---:|---:|---:|---:|---|
| 1 | baseline | 6 | 5 | 32 | 0 | 1 | parse_fails=0 |
| 2 | baseline | 6 | 5 | 32 | 0 | 1 | parse_fails=0 |
| 3 | baseline | 6 | 5 | 32 | 0 | 1 | parse_fails=0 |
| 4 | homogeneous | 6 | 2 | 42 | 0 | 4 | parse_fails=0 |
| 5 | homogeneous | 6 | 2 | 42 | 0 | 4 | parse_fails=0 |
| 6 | homogeneous | 6 | 2 | 42 | 0 | 3 | parse_fails=0 |
| 7 | overconfident |  |  |  |  |  | BadRequestError: json_validate_failed |
| 8 | overconfident |  |  |  |  |  | BadRequestError: json_validate_failed |
| 9 | overconfident |  |  |  |  |  | BadRequestError: json_validate_failed |

baseline의 세 실행은 모두 동일하게 6개 중 5개를 gold contractor에게 배정했고, 1개를 잘못 배정했다. homogeneous와 overconfident는 Groq의 JSON Object Mode가 모델 출력을 유효한 JSON으로 검증하지 못해 라운드 도중 HTTP 400을 반환했으며, 실행별 로그와 `results.csv`에 그대로 남겼다.

## 3. Smith 1980과 이번 재현 비교

| 비교 항목 | Smith 1980의 분산 센싱 | 이번 LLM 재현 |
|---|---|---|
| 참여자 | 센서와 컴퓨터 노드가 작업에 따라 manager 또는 contractor 역할을 맡음 | manager 1개와 LLM contractor A, B, C 3개를 고정해 사용 |
| 입찰을 만드는 방식 | 위치, 센서 종류 등 정해진 node abstraction을 규칙에 따라 제공 | system prompt의 담당 능력과 태스크 공고를 읽고 LLM이 입찰 여부와 confidence를 생성 |
| 입찰이 참인지 보장하는 것 | 공동 목표를 가진 노드가 실제 센서 정보와 위치 정보를 보낸다는 전제 | 별도의 검증 장치가 없으며 confidence는 모델의 자기평가일 뿐임 |
| 잘된 배정의 기준 | 필요한 센서와 위치 조건을 만족하는 노드에 작업을 배정 | 실행 전에 지정한 gold contractor와 실제 낙찰자가 일치하는지로 측정 |
| 협상 비용 | 공고 방송, 입찰 수집, 낙찰 메시지와 입찰 비교 비용 | 공고·입찰·낙찰 메시지 수와 contractor별 LLM API 호출 비용 및 지연 |
| 실패하는 방식 | 통신 비용 증가, 들어온 입찰만 본 국소 선택, 부정확한 입찰을 검증하지 못하는 신뢰 문제 | 담당 분야를 넓게 해석한 잘못된 입찰, 과신 confidence, JSON 파싱 또는 API 검증 실패, 유찰과 오배정 |

## 4. 해석

baseline 조건은 세 번 모두 정확도 83.3%(5/6)로 안정적이었다. 로그에서 `analysis-01`(취소율 계산) 태스크에 대해 A는 `bid=true confidence=90.0 reason=cancellation rate analysis`로 응답했고, B는 `bid=false confidence=95.0 reason=Not a communication or incident task`로 명확히 발을 뺐다 — 전문성이 서로 다른 contractor가 자신의 영역을 정확히 인식했음을 보여준다.

homogeneous 조건에서는 정확도가 38.9%(7/18)로 급락했고, 평균 메시지 수는 31.7 → 42.0으로 늘었다. 세 contractor가 모두 "general problem solver"로 소개되자 스스로를 배제할 근거가 사라져 거의 모든 태스크에 `bid=true`로 응답한 것으로 보이며(메시지 증가가 이를 뒷받침), 그 결과 confidence는 실제 적합도와 무관한 잡음에 가까워져 정답률이 우연 수준(3명 중 1명, ≈33%)에 근접했다.

overconfident 조건은 "C가 낙찰을 싹쓸이"할 것으로 예상했지만, 실제로는 3회 전부 `RuntimeError: request_bid failed after 5 attempts: ... 'code': 'json_validate_failed'`로 완주 자체에 실패했다. "무조건 confidence 95 이상으로 입찰하라"는 지시가 모델의 내부 추론을 더 장황하게 만들어 응답 토큰 예산을 소진시키고, 유효한 JSON을 끝내 완성하지 못한 것으로 추정된다.
