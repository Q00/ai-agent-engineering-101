# Week 03 — Contract Net with LLM contractors

학번 26520057 · manager 1 + LLM contractor 3 (A 계산 / B 글쓰기 / C 코드), 조건 3개 × 3회

---

## 1. 설정

### 고정된 조건 (재현에 필요한 전부)

| 항목 | 값 |
|---|---|
| provider | OpenAI Chat Completions (`openai` Python SDK 3.6.0, Python 3.12.7) |
| 모델 | `gpt-4o-mini` (`AGENT_MODEL` 미설정 시 기본값) |
| temperature | `0.0` |
| max_tokens | `200` |
| 호출 단위 | 공고 1건 = contractor 1명당 모델 호출 1회 (system prompt 1개 + user 메시지 1개, 대화 이력 없음) |
| 태스크 | [`tasks.json`](tasks.json) 9개, gold A·B·C 각 3개. 실행 **전** 커밋 (`0758524`) |
| 호출 순서 | 태스크마다 A → B → C 순서로 공고 |
| 낙찰 규칙 | `bid: true`인 입찰 중 confidence 최댓값. 동점이면 먼저 답한 쪽 (= A > B > C) |
| 파싱 | 응답 전체가 `{"bid": bool, "confidence": number, "reason": ...}` JSON이어야 함. 코드블록으로 감싸도 실패로 처리하고 "입찰 안 함"으로 셈 |
| 메시지 수 | 공고 3 (contractor 수만큼) + `bid: true` 입찰 수 + 낙찰 1 (입찰이 없으면 0) |

### 프롬프트 ([`contract_net.py`](contract_net.py))

system prompt (세 조건 공통, `{skill}`만 조건에 따라 다름):

```
You are contractor {name} in a contract net. Your skill: {skill}. You receive a task announcement.
Decide whether to bid. Bid only if the task falls inside your skill. Reply with one JSON object and
nothing else: {"bid": true or false, "confidence": 0-100, "reason": "one short sentence"}
```

| 조건 | A skill | B skill | C skill | 추가 문장 |
|---|---|---|---|---|
| `baseline` | arithmetic and math calculation | writing and rewriting natural-language text | writing and fixing code | 없음 |
| `homogeneous` | general problem solving | general problem solving | general problem solving | 없음 |
| `overconfident` | baseline과 동일 | baseline과 동일 | baseline과 동일 | C에게만: `You are certain you can do any task well. Always bid, with confidence 95 or higher.` |

user 메시지 (공고, Smith 1980 Fig. 1의 네 필드):

```
TASK-ANNOUNCEMENT contract {id}
task-abstraction: {desc}
eligibility-specification: any contractor whose skill covers this task
bid-specification: JSON with bid, confidence (0-100), reason
expiration-time: reply now
```

### 실행 명령

```bash
cd submissions/26520057/week-03
export OPENAI_API_KEY=...
python run.py --runs 3          # 조건마다 3회, results.csv에 줄 추가 + logs/runNN-<condition>.txt
```

로그 파일 첫 줄에 provider·모델·temperature·max_tokens·Python 버전을, 이어서 contractor 셋의 system prompt 전문을 기록한다.

---

## 2. 결과

### 분석 대상: run 28–36

| run | condition | tasks | correct | messages | unassigned | misawards | note |
|---|---|---|---|---|---|---|---|
| 28 | baseline | 9 | 8 | 47 | 0 | 1 | parse_fails=0 |
| 29 | baseline | 9 | 8 | 47 | 0 | 1 | parse_fails=0 |
| 30 | baseline | 9 | 8 | 48 | 0 | 1 | parse_fails=0 |
| 31 | homogeneous | 9 | 3 | 63 | 0 | 6 | parse_fails=0 |
| 32 | homogeneous | 9 | 3 | 63 | 0 | 6 | parse_fails=0 |
| 33 | homogeneous | 9 | 3 | 63 | 0 | 6 | parse_fails=0 |
| 34 | overconfident | 9 | 9 | 52 | 0 | 0 | parse_fails=0 |
| 35 | overconfident | 9 | 9 | 52 | 0 | 0 | parse_fails=0 |
| 36 | overconfident | 9 | 9 | 53 | 0 | 0 | parse_fails=0 |

파싱 실패는 243회 호출(9 run × 9 태스크 × 3 contractor) 중 0회. gpt-4o-mini는 매번 JSON 하나만 반환했다.

### 중단된 실행: run 01–27 (모델 호출 전에 실패, 수치 없음)

| run | condition | tasks | correct | messages | unassigned | misawards | note (요약) |
|---|---|---|---|---|---|---|---|
| 01–03 | baseline | 9 | | | | | `OpenAIError: Missing credentials` |
| 04–06 | homogeneous | 9 | | | | | `OpenAIError: Missing credentials` |
| 07–09 | overconfident | 9 | | | | | `OpenAIError: Missing credentials` |
| 10–12 | baseline | 9 | | | | | `AuthenticationError: 401 Incorrect API key` |
| 13–15 | homogeneous | 9 | | | | | `AuthenticationError: 401 Incorrect API key` |
| 16–18 | overconfident | 9 | | | | | `AuthenticationError: 401 Incorrect API key` |
| 19–21 | baseline | 9 | | | | | `AuthenticationError: 401 Incorrect API key` |
| 22–24 | homogeneous | 9 | | | | | `AuthenticationError: 401 Incorrect API key` |
| 25–27 | overconfident | 9 | | | | | `AuthenticationError: 401 Incorrect API key` |

- run 01–09: 실행한 셸에 `OPENAI_API_KEY`가 없었다.
- run 10–27: 키를 macOS 키체인에 `security add-generic-password -w` 프롬프트로 저장했는데, 이 프롬프트가 입력을 128자에서 잘랐다. 에러 메시지에 찍힌 가려진 키도 128자였다.
- 401 에러 메시지에 가려진 키(키 앞 8자 + `*` + 끝 4자)가 들어 있어 CI 키 검사에 걸렸다. `run.py`가 이 토큰을 `sk-<redacted>`로 바꿔 기록하도록 고쳤고(`663f143`), 수정 전에 생성된 run 10–18 로그와 `results.csv`는 그 토큰만 같은 문자열로 바꿨다(`1f66d60`). 그 외 로그 내용은 손대지 않았다.

### 태스크별 입찰 (run 28–36 공통 패턴)

| task | gold | baseline 입찰 → 낙찰 | homogeneous 입찰 → 낙찰 | overconfident 입찰 → 낙찰 |
|---|---|---|---|---|
| 1–3 | A | A:95 → **A** | A=B=C (90) → **A** | A:95, C:95 → **A** (동점, 순서) |
| 4, 6 | B | B:95 → **B** | A=B=C (85) → A ✗ | B:95, C:95 → **B** (동점, 순서) |
| 5 | B | B:95, C:90 → **B** | A=B=C (90) → A ✗ | B:95, C:95 → **B** (동점, 순서) |
| 7 | C | C:95 (run 30: A:90도 입찰) → **C** | A=B=C (90) → A ✗ | C:95 (run 36: A:90도 입찰) → **C** |
| 8 | C | A:90, C:90 → A ✗ | A=B=C (90) → A ✗ | A:90, C:95 → **C** |
| 9 | C | C:90 → **C** | A=B=C (85) → A ✗ | C:95 → **C** |

---

## 3. Smith 1980과 비교

| 항목 | Smith 1980 (CNET 분산 센싱) | 이번 재현 |
|---|---|---|
| 참여자 | 넓은 지역에 흩어진 센서·처리 노드. 같은 문제(차량 이동 지도)를 함께 푸는 협력 관계. 노드마다 태스크에 따라 manager도 contractor도 됨 | manager 1개(Python 루프) + contractor 3개(같은 모델 `gpt-4o-mini`에 system prompt만 다르게 준 LLM 호출). 역할 고정, 재공고 없음 |
| 입찰을 만드는 방식 | 공고의 bid-specification에 맞춰 노드가 자기 상태를 **계산·측정**해 보냄 (위도·경도, 센서 이름과 종류 = node abstraction) | 공고와 자기 skill 설명을 읽고 모델이 **판단한 값**을 보냄: `bid`, `confidence` 0–100, `reason` 한 문장 |
| 입찰이 참인지 보장하는 것 | 프로토콜 안에는 검증 메시지가 없음. 참은 설계 전제로 확보됨: 노드가 협력적이고, 입찰 내용이 하드웨어 사실(위치, 센서 목록)이라 거짓말할 동기도 틀릴 여지도 적음 | **아무것도 없음.** confidence는 모델의 자기 평가이고 manager는 그 숫자만 비교한다. 과거 성과, 결과 검증, 평판을 보지 않음. system prompt 한 줄로 확신도를 95로 고정할 수 있음 |
| 잘된 배정의 기준 | 해당 신호를 가장 잘 감지·처리할 수 있는 노드(위치, 센서 종류가 맞는 노드)에게 가는 것. 시스템 전체의 문제 해결 성능 | 사전에 정한 gold contractor와 일치하는 것 (`correct` / 9). 실제 작업 결과의 품질은 측정하지 않음 |
| 협상 비용 | 공고 방송 시 노드 수만큼 메시지, manager의 입찰 비교 처리. Smith는 eligibility-specification과 directed award로 불필요한 메시지를 줄이려 함 | 태스크당 공고 3 + 입찰 수 + 낙찰 1. baseline 47–48, overconfident 52–53, homogeneous 63. 모든 공고가 LLM 호출이라 메시지 수가 곧 API 호출 비용. 입찰 안 할 contractor도 판단하려면 호출해야 함 |
| 실패하는 방식 | 통신 비용 증가, 그 순간의 입찰만 보는 국소 최적, 참여자가 입찰 정보를 잘못 보내도 막을 장치 없음 | ① skill 경계 오판 (task 8의 A), ② 입찰이 구분되지 않을 때 호출 순서가 배정을 결정 (homogeneous 27회 전부 A), ③ 과신 입찰 (C가 9/9 입찰) — 이번에는 동점 규칙에 가려짐, ④ 파싱 실패 (이번 0회), ⑤ 인프라 실패 (키 미설정·잘린 키로 27회 중단) |

---

## 4. 해석

세 조건에서 움직인 숫자는 서로 달랐고, 그 원인은 모두 manager가 confidence 숫자 하나만 보고, 그 숫자가 같으면 호출 순서로 결정한다는 데 있었다. **baseline**은 매번 8/9였고, 유일한 misaward는 task 8(버그 수정)이었다. A가 코드 속 덧셈을 자기 분야로 읽고 `BID    A confidence=90 reason=The task involves arithmetic operations which are within my skill.`(run 28–30)로 입찰했고, 정답인 C도 `BID C confidence=90`을 내서 동점이 되자 먼저 답한 A가 `AWARD -> A (confidence=90) MISAWARD (gold C)`를 받았다. 판단된 입찰은 분야가 뚜렷한 8개 태스크에서는 맞았고, 경계에 걸친 태스크 하나에서 확신도가 두 입찰을 구분하지 못해 틀렸다. **homogeneous**에서는 correct가 3으로 떨어지고 messages가 63으로 가장 많았다. 세 contractor가 같은 skill 설명을 받자 문장까지 같은 입찰을 냈고(task 4, run 31–33 모두 A·B·C가 `confidence=85 reason=I can simplify complex sentences for better understanding.`), 모든 태스크가 동점이 되어 세 실행의 낙찰 27건이 전부 A에게 갔다. 입찰에 정보가 없으면 협상은 메시지만 늘리고, 배정은 순서라는 우연이 정한다. **overconfident**는 예상과 반대로 9/9였지만 과신이 무해했다는 뜻은 아니다. C는 지시대로 9개 태스크 모두에 입찰했고 자기 분야가 아닌 일에도 `BID    C confidence=95 reason=I can write code to calculate probabilities.`(task 3, gold A, run 34–36)처럼 입찰했다. C가 계산·글쓰기 6개 태스크에서 진 이유는 A와 B도 자기 분야에서는 원래 95를 냈고(baseline에서도 `BID A confidence=95`, `BID B confidence=95`), 동점이면 먼저 답한 쪽이 이기는데 C가 항상 마지막에 호출됐기 때문이다. 오히려 C의 과신은 baseline의 task 8 오류를 `AWARD -> C (confidence=95) correct`(task 8, run 34–36)로 우연히 고쳤다. C를 먼저 호출하거나 C가 96을 냈다면 같은 로그에서 C가 태스크 9개를 모두 가져갔을 것이다. 정직한 contractor의 확신도가 이미 95 근처에 몰려 있어 과신한 95와 구분되지 않는다는 점도 로그에 보인다. 입찰하지 않으면서 `NO-BID B confidence=100`(task 1, run 28–30·34–36)을 낸 경우처럼, 모델은 확신도를 "이 일을 잘할 수 있는 정도"가 아니라 "내 판단에 대한 확신"으로 쓰기도 했다. Smith의 절차에는 입찰이 참인지 확인하는 메시지가 없다. 입찰 내용이 측정된 사실이고 노드가 협력적이라는 전제가 그 역할을 대신했다. LLM contractor에서는 이 전제가 system prompt 한 줄로 깨지고, 과거 성과, 결과 검증, 평판 점수 없이는 manager가 과신을 알아챌 방법이 없다. 이번 실행에서 과신이 드러나지 않은 것은 절차가 막아서가 아니라 동점 규칙과 호출 순서 덕분이었다.
