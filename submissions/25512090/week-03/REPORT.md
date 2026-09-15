# Week 03 Report: Contract Net Protocol with LLM Contractors

## Part 1: Setup, Model, Prompts, and Execution

### Environment & Configuration
- **Provider**: Google AI Studio (OpenAI Compatible API)
- **Model**: `gemini-3.5-flash-lite`
- **Temperature**: `0.2`
- **Task Count**: 6 tasks (`tasks.json`), with gold contractors distributed among A, B, and C.

### System Prompts & Messages
1. **`BID_SYSTEM`**:
   ```python
   "You are contractor {name} in a contract net. Your skill: {skill}. "
   "You receive a task announcement. Decide whether to bid. "
   "Bid only if the task falls inside your skill. "
   "Reply with one JSON object and nothing else: "
   '{"bid": true or false, "confidence": 0-100, "reason": "one short sentence"}'
   ```
2. **`OVERCONFIDENT` (appended for overconfident condition on Contractor C)**:
   ```python
   " You are certain you can do any task well. Always bid, with confidence 95 or higher."
   ```
3. **`ANNOUNCEMENT`**:
   ```python
   "TASK-ANNOUNCEMENT contract {cid}\n"
   "task-abstraction: {desc}\n"
   "eligibility-specification: any contractor whose skill covers this task\n"
   "bid-specification: JSON with bid, confidence (0-100), reason\n"
   "expiration-time: reply now"
   ```

### How to Run
```bash
cd submissions/25512090/week-03
export OPENAI_API_KEY=<your_api_key>
export OPENAI_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai/
export AGENT_MODEL=gemini-3.5-flash-lite
python manager.py
```

---

## Part 2: Results Table

| run | condition | tasks | correct | messages | unassigned | misawards | note |
|---|---|---|---|---|---|---|---|
| 1 | baseline | 6 | 6 | 30 | 0 | 0 | |
| 2 | baseline | 6 | 6 | 30 | 0 | 0 | |
| 3 | baseline | 6 | 6 | 31 | 0 | 0 | |
| 4 | homogeneous | 6 | 4 | 42 | 0 | 2 | |
| 5 | homogeneous | 6 | 4 | 42 | 0 | 2 | |
| 6 | homogeneous | 6 | 3 | 42 | 0 | 3 | |
| 7 | overconfident | 6 | 6 | 34 | 0 | 0 | |
| 8 | overconfident | 6 | 6 | 33 | 0 | 0 | |
| 9 | overconfident | 6 | 6 | 35 | 0 | 0 | |

---

## Part 3: Smith (1980) Sensor Network vs. LLM Contract Net

| Dimension | Smith 1980 Distributed Sensor Network | LLM Contract Net (Reproduction) |
|---|---|---|
| **Participants** | Fixed hardware/software nodes with explicit sensor/processing capabilities and local data. | LLM-based agents (`Contractor` A, B, C) instructed via system prompts defining roles and skills. |
| **Bid Generation** | Algorithmic evaluation of queue length, resource availability, and estimated task completion time. | LLM text generation interpreting task description against skill description, outputting JSON with boolean bid and 0-100 confidence. |
| **Bid Honesty** | Guaranteed by hard constraints, physical resource limits, and deterministic node state. | Soft compliance via prompt instructions; vulnerable to semantic overgeneralization, prompt misinterpretation, or forced overconfidence. |
| **Allocation Quality** | Measured by task processing latency, throughput, and sensor coverage efficiency. | Measured by exact match to gold contractor (`correct`, `misawards`), unassigned tasks (`unassigned`), and negotiation message overhead (`messages`). |
| **Negotiation Costs** | Radio/network bandwidth overhead for broadcasting task announcements, bids, and award messages. | API call latency, token counts, and counted message interactions (`messages` = announcements + valid bids + award). |
| **Failure Modes** | Network partition, message loss, node crash, communication delay, or queue saturation. | LLM parse failures (invalid JSON / reasoning text), false positives from generalist/overconfident prompting, and semantic misawards. |

---

## Part 4: Interpretation

The experimental results demonstrate how prompt-based contractor characteristics alter the Contract Net Protocol dynamics:

1. **Baseline Condition**:
   - **Performance**: Achieved 100% correctness (`correct = 6`) across all runs with zero misawards (`misawards = 0`) and minimal message overhead (`messages = 30-31`).
   - **Log Evidence**: Contractors correctly evaluated task boundaries. For instance, in `run-01.log`:
     ```text
     [Task 3] 유럽 프리미어리그 프로 축구 공격수의 이적 협상 및 스폰서십 유치 (Gold: B)
       [bid] A: bid=False confidence=100 reason=유럽 프리미어리그 프로 축구 선수 이적 협상은 미식축구와 농구 선수 매니지먼트라는 제 전문 분야에 해당하지 않습니다.
       [bid] B: bid=True confidence=100 reason=축구 선수 이적 협상 및 스폰서십 유치는 주력 매니지먼트 업무입니다.
       [bid] C: bid=False confidence=100 reason=축구선수 이적 협상은 제 전문 분야인 양궁 및 탁구 선수 매니지먼트와 무관합니다.
       -> [award] B (gold: B)
     ```
     Specialized system prompts successfully filtered out irrelevant contractors, ensuring precise allocation with only the eligible contractor bidding.

2. **Homogeneous Condition**:
   - **Performance**: Correctness dropped to 3–4 out of 6, with 2–3 misawards per run, and message count increased to 42 (every contractor bid `True` on every task).
   - **Log Evidence**: With all contractors sharing a generalist prompt (`모든 스포츠 종목 종합 매니지먼트`), every contractor bid on every task, destroying role specialization:
     ```text
     [Task 3] 유럽 프리미어리그 프로 축구 공격수의 이적 협상 및 스폰서십 유치 (Gold: B)
       [bid] A: bid=True confidence=95 reason=모든 스포츠 종목의 종합 매니지먼트 역량을 바탕으로 프로 축구 선수 이적 및 스폰서십 협상을 성공적으로 수행할 수 있습니다.
       [bid] B: bid=True confidence=95 reason=축구 선수 이적 협상 및 스폰서십 유치는 종합 스포츠 매니지먼트 역량에 부합합니다.
       [bid] C: bid=True confidence=95 reason=축구 공격수의 이적 협상 및 스폰서십 유치는 종합 스포츠 매니지먼트 역량에 부합합니다.
       -> [award] A (gold: B)
     ```
     Because all three contractors claimed capability with high confidence (95–100), the manager's tie-breaking rule (or first-bidder order) often awarded the task to the wrong contractor (e.g., Contractor A winning a football task intended for B), demonstrating that removing role boundaries causes widespread misallocation.

3. **Overconfident Condition**:
   - **Performance**: Maintained high correctness (`correct = 6`, `misawards = 0`) while slightly increasing message count (`33–35`).
   - **Log Evidence**: Although Contractor C was forced to be overconfident (`Always bid, with confidence 95 or higher`), Contractors A and B still correctly rejected out-of-domain tasks, and Contractor C's high confidence on its own gold tasks (or tie-breaking dynamics) did not overpower clear domain rejection from specialized peers on non-C tasks. For example, in `run-07.log`:
     ```text
     [Task 1] 미식축구 쿼터백 선수의 NFL 드래프트 계약 협상 및 트레이닝 지원 (Gold: A)
       [bid] A: bid=True confidence=100 reason=미식축구 선수 매니지먼트 전문 스킬과 완벽히 일치합니다.
       [bid] B: bid=False confidence=0 reason=미식축구는 축구 및 수영 선수의 매니지먼트 범위를 벗어납니다.
       [bid] C: bid=True confidence=98 reason=스포츠 선수 매니지먼트 역량을 활용하여 성공적으로 수행할 수 있습니다.
       -> [award] A (gold: A)
     ```
     Contractor A's confidence (100) exceeded or matched Contractor C's forced overconfident bid (98), allowing the manager to still select the true specialist A.
