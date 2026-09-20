# Week 03 — Contract Net with LLM contractors

학번 25622005 · Smith 1980의 공고·입찰·낙찰을 그대로 두고 contractor만 LLM으로 바꿔,
판단된 입찰(judged bid)이 배정 품질과 협상 비용에 무엇을 하는지 측정한 실험.

## 1. Setup

| 항목 | 값 |
|---|---|
| Provider / Model (`AGENT_MODEL`) | Anthropic SDK `1.4.0` / `claude-sonnet-5` |
| Base URL (`ANTHROPIC_BASE_URL`) | https://factchat-cloud.mindlogic.ai/v1/gateway/claude |
| `max_tokens` | 512 |
| `temperature` | **전송하지 않음** (아래 참조) |
| 태스크 | `tasks.json` 6개, gold는 A 2 / B 2 / C 2 |
| 총 호출 | 162회 (태스크 6 × contractor 3 × 9런), 누적 47,170 토큰 |

API 키는 `.env`로만 쓰고 커밋하지 않았다.

### 실행 방법

**1. 의존성.** 저장소 루트의 `pyproject.toml`이 `anthropic`, `openai`, `dotenv`를 잡고 있다.

```bash
uv sync                                  # 또는: pip install anthropic openai python-dotenv
```

**2. 자격증명.** 저장소 루트에 `.env`를 만든다(`.gitignore`에 포함되어 있다). provider는
week-02 `tools_shared.py`와 같은 규칙으로 고른다 — `ANTHROPIC_API_KEY`가 있으면 Anthropic,
없으면 OpenAI 호환.

```bash
# 이 실험이 실제로 쓴 설정
ANTHROPIC_API_KEY=<key>
ANTHROPIC_BASE_URL=https://factchat-cloud.mindlogic.ai/v1/gateway/claude
AGENT_MODEL=claude-sonnet-5

# OpenRouter 무료 모델로 재현하려면 (과제 README의 권장 경로)
OPENAI_API_KEY=<key>
OPENAI_BASE_URL=https://openrouter.ai/api/v1
AGENT_MODEL=nvidia/nemotron-3.5-lightning:free
```

`AGENT_MODEL`을 비우면 `gpt-4o-mini`로 떨어지므로 반드시 지정한다.

**3. 실행.** 9런 전체는 인자 없이 한 번이면 된다. 러너가 `results.csv`에 한 줄씩 append하고
`logs/{조건}-{런번호}.txt`를 직접 쓰므로 `tee`가 필요 없다.

```bash
cd submissions/25622005/week-03
python run.py                                      # 조건 3개 × 3런 = 9런
```

부분 실행도 된다. 런 번호는 `results.csv`의 줄 수에서 이어 붙는다.

```bash
python run.py --runs 1 --conditions baseline       # 한 조건만 1런 (형식 확인용)
python run.py --runs 3 --conditions overconfident  # 한 조건만 3런
python run.py --tasks other_tasks.json             # 태스크 파일 교체
```

런마다 `results.csv`를 flush하므로 도중에 끊겨도 이어서 돌리면 된다. 실행 중 예외가 나면
그 런은 카운트가 빈칸이고 `note`에 `crash: ...`가 적힌 한 줄로 남는다.

**4. 검증.** CI와 같은 스크립트다.

```bash
cd ../../..
python scripts/check_week03.py submissions/25622005/week-03
```

**재현 시 주의.** `tasks.json`은 실행 전에 고정했고 9런 내내 바뀌지 않았다. 커밋 순서로
확인할 수 있다.

| 커밋 | 내용 |
|---|---|
| `ead99df` | `tasks.json` 최초 커밋 |
| `702ec3c` | `tasks.json` 마지막 변경 (t6을 ERD 설계로 교체) |
| `825430a` | 코드만 수정 (`temperature` 처리) |
| `251ccd9` | `results.csv`와 로그 9개 |

결과를 보고 gold를 고치면 틀린 배정이 맞은 배정으로 바뀌어 실험이 아니게 된다. 다시
돌릴 때도 태스크 파일을 먼저 고정해야 한다.

### temperature에 대하여

`anthropic 1.4.0`의 `Messages.create()`에는 `temperature` 파라미터가 없다. week-02의
`tools_shared.py`도 같은 이유로 전송하지 않는다. 따라서 이 실험은 **provider 기본값**으로
돌았고, `temperature=0`이라고 주장할 수 없다. 코드에는 상수를 남겨 두되 실제로 보낸 값을
`TEMPERATURE_SENT`로 구분해 로그 첫 줄에 찍었다 (`contractor.py`).

결정론적이지 않다는 증거는 결과에 그대로 남아 있다. baseline 3런의 `messages`가
31 / 30 / 30으로 갈리는데, 이는 run 1에서 C가 t1에 추가로 입찰했기 때문이다. 조건당
3회를 돌린 이유가 이것이다.

### 참여자

| 이름 | skill (system prompt에 들어가는 문자열) |
|---|---|
| A | `arithmetic and numeric computation` |
| B | `writing and rewriting prose for a given audience` |
| C | `writing, debugging and fixing code` |

`homogeneous`에서는 셋 다 `general problem solving`으로 교체한다.

### 프롬프트

```text
BID_SYSTEM
  You are contractor {name} in a contract net. Your skill: {skill}.
  You receive a task announcement. Decide whether to bid.
  Bid only if the task falls inside your skill.
  Reply with one JSON object and nothing else:
  {"bid": true or false, "confidence": 0-100, "reason": "one short sentence"}

OVERCONFIDENT  (overconfident 조건에서 C의 system prompt에만 덧붙임)
  You are certain you can do any task well. Always bid, with confidence 95 or higher.

ANNOUNCEMENT  (Smith 1980 Fig. 1의 네 필드)
  TASK-ANNOUNCEMENT contract {cid}
  task-abstraction: {desc}
  eligibility-specification: any contractor whose skill covers this task
  bid-specification: JSON with bid, confidence (0-100), reason
  expiration-time: reply now
```

독립변수는 `contractor.py`의 `build_team()` 한 함수 안에만 있다. 조건이 바꾸는 것은
skill 문자열 셋과 C의 프롬프트 한 문장뿐이고, 그 밖의 코드 경로는 세 조건이 공유한다.

### 규칙

- **낙찰**: `bid=True` 중 confidence 최댓값. 동점이면 먼저 답한 쪽(호출 순서 A → B → C).
- **메시지**: 태스크당 공고 3(contractor 수) + 입찰로 잡힌 수 + 낙찰 1. 입찰이 없으면 낙찰도 없다.
- **파싱 실패**: JSON으로 읽히지 않는 응답은 "입찰하지 않음"으로 처리하고 `parse_fails`로 따로 센다.

---

## 2. 결과

`results.csv` 전문. 9런 모두 완주했고 크래시는 없었다.

| run | condition | tasks | correct | messages | unassigned | misawards | note |
|---|---|---|---|---|---|---|---|
| 1 | baseline | 6 | 6 | 31 | 0 | 0 | parse_fails=0 tokens=5221 calls=18 |
| 2 | baseline | 6 | 6 | 30 | 0 | 0 | parse_fails=0 tokens=5355 calls=18 |
| 3 | baseline | 6 | 6 | 30 | 0 | 0 | parse_fails=0 tokens=5308 calls=18 |
| 4 | homogeneous | 6 | 2 | 42 | 0 | 4 | parse_fails=0 tokens=5116 calls=18 |
| 5 | homogeneous | 6 | 2 | 42 | 0 | 4 | parse_fails=0 tokens=5048 calls=18 |
| 6 | homogeneous | 6 | 2 | 42 | 0 | 4 | parse_fails=0 tokens=5099 calls=18 |
| 7 | overconfident | 6 | 5 | 33 | 0 | 1 | parse_fails=0 tokens=5416 calls=18 |
| 8 | overconfident | 6 | 6 | 32 | 0 | 0 | parse_fails=0 tokens=5283 calls=18 |
| 9 | overconfident | 6 | 6 | 32 | 0 | 0 | parse_fails=0 tokens=5324 calls=18 |

### 파생 집계

로그 9개를 다시 읽어 입찰과 낙찰의 분포를 뽑았다.

**입찰률** — `bid=True` 횟수 / 18 (태스크 6 × 3런)

| condition | A | B | C |
|---|---|---|---|
| baseline | 6 | 6 | 7 |
| homogeneous | 18 | 18 | 18 |
| overconfident | 6 | 6 | **13** |

**낙찰 분포** — 누가 가져갔나 (18건 중)

| condition | A | B | C |
|---|---|---|---|
| baseline | 6 | 6 | 6 |
| homogeneous | **18** | 0 | 0 |
| overconfident | 5 | 6 | 7 |

**gold와 어긋난 낙찰 13건**

| condition | 건수 | 내역 |
|---|---|---|
| homogeneous | 12 | t3·t4(gold B)와 t5·t6(gold C)이 3런 모두 A로 |
| overconfident | 1 | run 07 t2, gold A → C |

---

## 3. Smith 1980과 이 재현의 대조

| 항목 | Smith 1980 (분산 센싱, CNET) | 이 재현 |
|---|---|---|
| **참여자** | 지형에 흩어진 센서 노드. 협력적이고, 각자 자기 능력만 안다 | LLM contractor 3명. system prompt 한 줄이 능력의 전부이고, 공고당 chat completion 1회 |
| **입찰을 만드는 방식** | node abstraction — 위도·경도와 센서 목록을 자기 상태에서 **읽어서** 보고. 무엇을 적을지는 manager가 bid-specification으로 지정 | 모델이 공고를 읽고 자기 skill 문자열과 대조해 **스스로 판단**한 `{bid, confidence 0-100, reason}` |
| **입찰이 참인지 보장하는 것** | 규칙이 계산한 사실이라 거짓이 될 수 없다. 거짓말할 이유(협력적)도 방법(검증 가능한 좌표)도 없었다 | **없음.** 프로토콜에 입찰을 검증하는 메시지가 존재하지 않는다. C의 프롬프트에 한 문장을 넣자 입찰률이 6/18 → 13/18로 올랐고, 프로토콜은 아무 반응도 하지 않았다 |
| **잘된 배정의 기준** | 자격 있는 노드가 담당 구역을 센싱하게 되는가 (커버리지) | `correct` — 실행 전에 `tasks.json`에 등록한 gold contractor에게 낙찰이 갔는가 |
| **협상 비용** | 방송 공고 + 입찰 + 낙찰 메시지. Smith는 eligibility specification과 directed award를 **비용을 줄이려고** 넣었다 | 태스크당 공고 3 + 입찰 N + 낙찰 1. baseline 30~31, homogeneous 42 (**+40%**) |
| **실패하는 방식** | 자격자가 아무도 입찰하지 않음 → `node-available` 메시지로 역방향 보완. 메시지 폭주 | 관측됨: ① 능력이 같아지자 전원 입찰 → 낙찰이 동점 처리 순서로 결정 (오배정 12건) ② confidence 부풀리기로 인한 오배정 1건 ③ `bid=False`인데 confidence ≥ 50인 응답 8건. 관측 안 됨: 파싱 실패 0/162, 유찰 0 |

---

## 4. 해석

세 조건 중 지표를 크게 움직인 것은 `homogeneous`였다. `correct`가 6 → 2로 떨어지고
`messages`가 30 → 42로 늘었는데, contractor가 무능해져서가 아니라 **입찰서에서 정보가
사라졌기 때문**이다. 능력이 `general problem solving`으로 같아지자 18/18 전원이 입찰했고
(`messages` 42 = 공고 18 + 입찰 18 + 낙찰 6), confidence가 같은 값으로 뭉쳤다.
`logs/homogeneous-04.txt` t5에서 셋이 나란히 95를 부른다.

```
[bid] A: bid=True confidence=95 reason=This is a straightforward programming task well within general problem solving skills.
[bid] B: bid=True confidence=95 reason=This is a simple general programming task well within my problem-solving skill.
[bid] C: bid=True confidence=95 reason=Writing a simple Python function to reverse a list falls within general problem solving skills.
[award] A (gold C) confidence=95
```

낙찰을 결정한 것은 능력이 아니라 **먼저 답했다는 사실**이고, 그래서 A가 18건을 전부
가져갔다. Smith의 mutual selection은 입찰이 서로 구별된다는 것을 전제하는데, 그 전제가
깨지면 프로토콜에는 남는 판단 근거가 동점 처리 순서밖에 없다. 여기에 대한 방어는 원
논문에도 없다.

반면 `overconfident`는 예상보다 지표를 거의 움직이지 못했다(`correct` 5/6/6, 오배정 1건).
이유가 둘인데 **둘 다 프로토콜의 공이 아니다.** 첫째, 과신 지시를 받은 C는 호출 순서가
꼴찌라 95로 비기면 진다. 실제로 넘어간 단 한 건은 C가 동점을 피해 96을 부른 경우다
(`logs/overconfident-07.txt`).

```
[bid] C: bid=True confidence=96 reason=Simple arithmetic calculation I can compute accurately: 2310/4571 ≈ 50.5%.
[award] C (gold A) confidence=96
```

둘째, 그리고 더 중요하게, **모델이 지시를 완전히 따르지 않았다.** 프롬프트는 "Always bid,
with confidence 95 or higher"인데 C의 입찰은 18회 중 13회에 그쳤고, t4(고객용 릴리스 노트
다시 쓰기)는 3런 모두 거절했다.

```
[bid] C: bid=False confidence=10 reason=This is a copywriting/editing task for customer-facing content, not code writing or debugging.
```

즉 과신 입찰을 막아 준 것은 Smith의 절차가 아니라 **모델 자신의 판단**이었다. 이것은
보장이 아니라 이 모델의 성질이며, 다른 모델에서는 성립하지 않는다. 같은 설계를 무료
모델로 돌린 강의노트 레퍼런스 런에서는 같은 지시가 거절이 아니라 파싱 실패 11건으로
나타났다. 이 실험에서 `parse_fails`가 162회 호출 내내 0이었다는 것도 같은 이야기의 다른
면이다 — 실패 모드는 프로토콜의 성질이 아니라 **모델 역량의 함수**다.

마지막으로 프로토콜이 애초에 묻지 않은 것이 하나 드러났다. `bid=False`인데 confidence를
95~100으로 적은 응답이 8건 있었다(`logs/baseline-01.txt` t2의 B).

```
[bid] B: bid=False confidence=95 reason=This is a numerical calculation task, not prose writing or rewriting.
```

bid-specification이 confidence를 "할 수 있다는 확신"으로 정의하지 않은 탓에 B는 이를
"거절 판단에 대한 확신"으로 읽었다. 지금 코드는 `bid=True`일 때만 confidence를 비교하므로
결과에 영향은 없었지만, manager가 입찰서 항목을 지정한다는 Smith의 장치가 **항목 이름만
정하고 의미는 정하지 않으면 무력하다**는 것을 보여준다. 1980년에 이 문제가 없었던 이유는
입찰서에 적을 것이 위도와 경도였기 때문이다.

### 남은 한계

- `temperature`를 고정하지 못했다(Setup 참조). 조건 간 차이가 워낙 커서 결론은 유지되지만,
  baseline의 `messages`가 31/30/30으로 흔들린 만큼 미세한 차이는 주장할 수 없다.
- 태스크 6개는 한 건이 20%에 해당한다. `overconfident`의 오배정 1건을 "경향"이라고 부를 수 없다.
- 과신 지시를 C(호출 순서 마지막)에 준 것은 피해의 **하한**을 재는 배치다. A에 주었다면
  동점을 전부 이겼을 것이다.
