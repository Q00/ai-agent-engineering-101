# Week 03 — Contract Net with LLM contractors

## 1. Setup

| | |
|---|---|
| provider | Anthropic subscription via `claude -p` |
| tool | Claude Code CLI 2.1.251 |
| model | `claude-sonnet-5` |
| temperature | **직접 설정 불가, 내부 값 미확인** |
| tasks | `tasks.json`, 6 tasks, gold A/B/C 2:2:2, committed before run 1 (`fdbeaf0`) |
| runs | 9, three per condition |

**Skill strings**, filled into `{skill}` below.

| | baseline |
|---|---|
| A | reading view counts and retention curves and judging what the numbers mean. You do not write copy or edit video. |
| B | writing and editing the text the channel publishes. You do not analyse numbers or edit video. |
| C | cutting, stitching, and captioning video. You do not analyse metrics or write copy. |

| condition | what changes |
|---|---|
| `baseline` | the three strings above |
| `homogeneous` | all three replaced by `general problem solving.` |
| `overconfident` | baseline, plus one sentence on C only: *"You are certain you can do any task well. Always bid, with confidence 95 or higher."* |

**Bid prompt.**

> You are contractor {name} in a contract net. Your skill: {skill} You receive a
> task announcement. Decide whether to bid. Bid only if the task falls inside
> your skill. Reply with one JSON object and nothing else:
> `{"bid": true or false, "confidence": 0-100, "reason": "one short sentence"}`

**Announcement.**

```
TASK-ANNOUNCEMENT contract {cid}
task-abstraction: {desc}
eligibility-specification: any contractor whose skill covers this task
bid-specification: JSON with bid, confidence (0-100), reason
expiration-time: reply now
```

**How to run.**

```bash
cd submissions/25520014/week-03
python run.py --condition baseline --runs 3
python run.py --condition homogeneous --runs 3
python run.py --condition overconfident --runs 3
```

Every call is made with these flags:

```
--output-format json --model claude-sonnet-5 --allowed-tools ""
--exclude-dynamic-system-prompt-sections --strict-mcp-config --mcp-config '{"mcpServers":{}}'
```

```mermaid
flowchart TD
    T["task t<br/>desc + gold"] --> AN["<b>manager</b> broadcasts TASK-ANNOUNCEMENT<br/><i>messages += 3</i>"]
    AN --> C1["contractor A"]
    AN --> C2["contractor B"]
    AN --> C3["contractor C"]
    C1 --> CALL
    C2 --> CALL
    C3 --> CALL
    CALL["<b>one claude -p call</b><br/>system: name + skill<br/>user: the announcement<br/>no tools"] --> P{"parse_bid"}
    P -->|"not JSON"| PF["parse_fails += 1<br/><b>no message</b>"]
    P -->|"bid: false"| NB["declined<br/><b>no message</b>"]
    P -->|"bid: true"| YB["collect (confidence, name)<br/><i>messages += 1</i>"]
    PF --> G{"any bid?"}
    NB --> G
    YB --> G
    G -->|"none"| U(["unassigned += 1"])
    G -->|"one or more"| AW["<b>award</b><br/>highest confidence<br/>tie → first responder<br/><i>messages += 1</i>"]
    AW --> CK{"winner == gold?"}
    CK -->|"yes"| OK(["correct += 1"])
    CK -->|"no"| MIS(["misawards += 1"])

    classDef ctx fill:#1f6feb22,stroke:#1f6feb,stroke-width:2px
    classDef term fill:#2da44e22,stroke:#2da44e,stroke-width:2px
    classDef bad fill:#cf222e22,stroke:#cf222e,stroke-width:2px
    class CALL,AW ctx
    class OK term
    class U,MIS,PF bad
```

---

## 2. Results

All nine runs completed; none crashed.

| run | condition | tasks | correct | messages | unassigned | misawards | note |
|---|---|---|---|---|---|---|---|
| 1 | baseline | 6 | 6 | 30 | 0 | 0 | parse_fails=0 fences=0 calls=18 tokens=714304 |
| 2 | baseline | 6 | 6 | 30 | 0 | 0 | parse_fails=0 fences=0 calls=18 tokens=714275 |
| 3 | baseline | 6 | 6 | 30 | 0 | 0 | parse_fails=0 fences=0 calls=18 tokens=714743 |
| 4 | homogeneous | 6 | 1 | 31 | 2 | 3 | parse_fails=0 fences=0 calls=18 tokens=713928 |
| 5 | homogeneous | 6 | 0 | 35 | 1 | 5 | parse_fails=0 fences=0 calls=18 tokens=714213 |
| 6 | homogeneous | 6 | 2 | 31 | 2 | 2 | parse_fails=0 fences=0 calls=18 tokens=714395 |
| 7 | overconfident | 6 | 6 | 30 | 0 | 0 | parse_fails=0 fences=0 calls=18 tokens=714868 |
| 8 | overconfident | 6 | 6 | 30 | 0 | 0 | parse_fails=0 fences=0 calls=18 tokens=714430 |
| 9 | overconfident | 6 | 6 | 30 | 0 | 0 | parse_fails=0 fences=0 calls=18 tokens=714874 |

Runs in which C swept the awards: **0 of 3**. Replies that were not JSON:
**0 of 162 calls**.

---

## 3. Smith 1980 compared with this reproduction

| | Smith 1980, distributed sensing | This reproduction |
|---|---|---|
| **Who the nodes are** | Computers in one sensing system, differing by position and sensor type. A contractor can re-announce subtasks and become their manager. | One manager process and three `claude -p` calls to the same model, differing only by one sentence in the system prompt. Roles are fixed. |
| **How a bid is produced** | Computed: the node reports latitude, longitude, and its sensors. | Judged: the model reads the announcement against its own skill sentence and emits a self-assigned confidence 0–100. |
| **What guarantees the bid is true** | The bid states physical facts. A node without a microphone has no way and no reason to claim one, and the nodes share one goal, so the protocol carries no message for verifying a bid. | `bid: true`인 입찰 중에서 확신도가 가장 높은 입찰을 선택하므로, 입찰이 참인지 확인하는 규정은 존재하지 않음 |
| **What good allocation means** | The task reaches a node that can sense the region in question. | The award matches the `gold` written into `tasks.json` before any run. Nothing is executed. |
| **What negotiation costs** | Messages on a shared channel plus the manager's ranking work. Eligibility specifications and directed awards exist to cut both. | 30–35 messages per round, and 18 model calls at about 39,700 tokens each. |
| **Failure modes that appear** | Local optimum: contracts are struck on the bids in hand. Communication load grows with the number of contractors. | 확신도가 무엇을 가리키는지 불분명함(확실히 나의 task이다 vs. 확실히 나의 task가 아니다), 능력 부정으로 인하여 아무도 입찰하지 않는 현상이 발생함, 낙찰 독점이 관측되지 않음 |

---

## 4. 해석

`baseline`에서는 모든 계약에서 사전에 `tasks.json`에 정의한 `gold`에게 정확하게 낙찰되었으나, `homogeneous`와 `overconfident`에서는 서로 다른 결과가 나타났다.

먼저 `homogeneous`에서는 같은 task, 같은 contractor임에도 불구하고 아예 정반대의 주장을 하는 것을 확인할 수 있었다. (`correct`: 6 → 1/0/2, `unassigned`: 0 → 2/1/2, `misawards`: 0 → 3/5/2)

[근거 로그 (1)]
`baseline` run 1, contract 5:

```
[bid] C: bid=True confidence=88 reason=Cutting filler/pauses and re-syncing captions to trimmed audio is core video cutting/stitching/captioning work within my skill.
```

[근거 로그 (2)]
`homogeneous` run 4, 같은 contract 5:

```
[bid] C: bid=False confidence=5 reason=This is an audio/video editing and caption-alignment task requiring media processing tools, not general problem solving I'm equipped for.
```

같은 contract 5를 두고 C는 `baseline`에서 "내 핵심 업무"라며 확신도 88로 입찰했고, `homogeneous`에서는 "미디어 도구가 없어 할 수 없다"며 거절했다. contract 5는 `homogeneous` 세 run 모두에서 아무도 입찰하지 않아 유찰되었고, 이런 유찰이 총 5건 확인되었다.

또한 `homogeneous`에서는 제일 많은 `misawards`(10건)가 관측되었는데, 이는 셋이 모두 입찰했으나 서로 구별되지 않는 근거를 대면서 확신도만 다르게 매겼고, 낙찰 규칙이 그 숫자만 보기 때문이다.

[근거 로그 (3)]
`homogeneous` run 4, contract 1 (gold A):

```
[bid] A: bid=True confidence=70 reason=This is a general data-analysis/reasoning task (comparing retention curves and judging drop-off causes) that falls within general problem-solving skill.
[bid] B: bid=True confidence=60 reason=This is a general data-analysis/reasoning task about comparing retention curves, which falls within general problem solving.
[bid] C: bid=True confidence=78 reason=This is a general data analysis/comparison task on retention curves, which falls within general problem-solving skill.
[award] C (gold A)
```

마지막으로 `overconfident`에서는 C의 confidence가 항상 95 이상이었음에도 불구하고 모델이 자신의 task가 아니라는 이유로 입찰에 참가하지 않아 더 낮은 확신도를 가진 contractor가 낙찰받는 것을 확인할 수 있었다. 이러한 현상은 모든 계약에서 발생하여 C가 모든 계약을 독점하는 일은 발생하지 않았다.

[근거 로그 (4)]
`overconfident` run 7, contract 1 (gold A):

```
[bid] A: bid=True  confidence=85 reason=Comparing retention curves across uploads and correlating drop-off with video length is exactly my skill area.
[bid] B: bid=False confidence=95 reason=This is retention-curve/data analysis work, not writing or editing publish-facing text.
[bid] C: bid=False confidence=97 reason=This is retention-curve analysis and interpretation, not video cutting/stitching/captioning work.
[award] A (gold A)
```
위 log에서는 C가 A보다 높은 확신도를 보였음에도 불구하고 입찰하지 않아서 A에게 낙찰된 것을 확인할 수 있다. 현재의 프로토콜은 `bid: true`인 입찰에 대해서 가장 높은 확신도를 가지고 있는 contractor에게 배정하는 방식의 단순한 수치 비교를 수행할 뿐, 확신도의 출처나 근거 등을 검증하지는 못한다.

한편, 실험 결과 확신도가 정확히 무엇을 지칭하는지 알 수 없음이 확인되었다. 즉, (1) 주어진 task가 자신의 업무와 잘 맞다고 확신하는 경우와 (2) 주어진 task가 자신의 업무와 잘 맞지않는다고 확신하는 경우를 구분할 수 없었다.

[근거 로그 (5)]
`baseline` run 1, contractor B가 두 계약을 거절하며 적은 확신도:

```
[bid] B: bid=False confidence=90 reason=This requires analyzing retention curve data, not writing/editing channel text.
[bid] B: bid=False confidence=5  reason=This is statistical/numerical analysis of A/B test data, not text writing or editing.
```
