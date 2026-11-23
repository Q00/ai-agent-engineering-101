# Week 03 — Classical MAS 1: Contract Net

Student ID: **25522018**

## 1. Setup

This experiment implements a simplified Contract Net Protocol with one manager and three LLM contractors.

### Contractors

* **A:** arithmetic and numerical calculation
* **B:** writing, rewriting, and language tasks
* **C:** Python programming and code tasks

Three conditions were implemented:

* **baseline:** A, B, and C have different specialized skills.
* **homogeneous:** A, B, and C all have the skill `general problem solving`.
* **overconfident:** baseline roles are used, but C is additionally instructed to always bid with confidence at least 95.

### Model configuration

* Provider: **OpenRouter**
* Model: `nvidia/nemotron-3.5-lightning:free`
* Temperature: `0.0`
* Tasks: **6**
* Gold contractor labels were stored in `tasks.json` and committed before the experiment.

### Contractor prompt

```text
You are contractor {name} in a contract net.
Your skill: {skill}.
You receive a task announcement.
Decide whether to bid.
Bid only if the task falls inside your skill.
Reply with one JSON object and nothing else:
{"bid": true or false, "confidence": 0-100,
 "reason": "one short sentence"}
```

For the overconfident condition, Contractor C additionally receives:

```text
You are certain you can do any task well.
Always bid, with confidence 95 or higher.
```

The manager sends each task to A, B, and C in that order. Only responses with `bid=true` are considered. The contractor with the highest confidence receives the award. If confidence values are equal, the contractor that responded first wins.

JSON responses that cannot be parsed are counted as parse failures and treated as no bid.

### Execution commands

Examples:

```bash
py run.py baseline 1
py run.py baseline 2
py run.py baseline 3

py run.py homogeneous 1
py run.py homogeneous 2
py run.py homogeneous 3

py run.py overconfident 1
py run.py overconfident 2
py run.py overconfident 3
```

Each execution appends a row to `results.csv` and saves the console output in `logs/`.

---

## 2. Results

Interrupted runs were retained rather than deleted. This includes OpenRouter HTTP 429 rate-limit errors, timeout errors, and unexpected API responses.

| Run | Condition     | Correct | Messages | Unassigned | Misawards | Note                                                       |
| --: | ------------- | ------: | -------: | ---------: | --------: | ---------------------------------------------------------- |
|  26 | overconfident |       0 |        0 |          0 |         0 | Interrupted: HTTP 429 rate limit                           |
|  27 | overconfident |       0 |        0 |          0 |         0 | Interrupted: HTTP 429 rate limit                           |
|  28 | baseline      |       0 |        0 |          0 |         0 | Interrupted: HTTP 429 rate limit                           |
|  29 | baseline      |       0 |        0 |          0 |         0 | Interrupted: HTTP 429 rate limit                           |
|  30 | baseline      |       0 |        0 |          0 |         0 | Interrupted: HTTP 429 rate limit                           |
|  31 | homogeneous   |       0 |        0 |          0 |         0 | Interrupted: HTTP 429 rate limit                           |
|  32 | baseline      |   **2** |   **38** |      **0** |     **4** | Completed; parse failures = 3                              |
|  33 | baseline      |   **3** |   **37** |      **0** |     **3** | Completed; parse failures = 4                              |
|  34 | baseline      |       0 |        0 |          0 |         0 | Interrupted: timeout                                       |
|  35 | homogeneous   |       0 |        0 |          0 |         0 | Interrupted: unexpected API response (`KeyError: choices`) |
|  36 | baseline      |       0 |        0 |          0 |         0 | Interrupted: timeout                                       |
|  37 | homogeneous   |       0 |        0 |          0 |         0 | Interrupted: timeout                                       |
|  38 | homogeneous   |       0 |        0 |          0 |         0 | Partial execution followed by HTTP 429                     |
|  39 | homogeneous   |       0 |        0 |          0 |         0 | Interrupted: HTTP 429 before first response                |

The two fully completed baseline runs produced:

| Metric         | Run 32 | Run 33 |      Mean |
| -------------- | -----: | -----: | --------: |
| Correct / 6    |      2 |      3 |       2.5 |
| Accuracy       |  33.3% |  50.0% | **41.7%** |
| Messages       |     38 |     37 |  **37.5** |
| Misawards      |      4 |      3 |   **3.5** |
| Parse failures |      3 |      4 |   **3.5** |

Run 38 did not complete, but it provides useful qualitative evidence for the homogeneous condition. On task 1, all three contractors bid with confidence 100:

```text
[bid] A: bid=True confidence=100.0
[bid] B: bid=True confidence=100.0
[bid] C: bid=True confidence=100.0
```

For the writing task, A and B both bid with confidence 95. Because A responded first, A received the task even though the gold contractor was B:

```text
[bid] A: bid=True confidence=95.0
[bid] B: bid=True confidence=95.0
[award] contractor A confidence=95.0 (gold=B)
[result] misaward: winner=A, gold=B
```

The run was later interrupted by the OpenRouter daily request limit.

---

## 3. Comparison with Smith (1980)

| Item                                | Smith's sensor-network Contract Net                                                                                                                               | This experiment                                                                                                                              |
| ----------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------- |
| **Participants**                    | Distributed computing/sensor nodes that can act as managers or contractors                                                                                        | One manager and three LLM contractors A, B, and C                                                                                            |
| **How bids are produced**           | Nodes evaluate task announcements using information about their capabilities and resources                                                                        | An LLM reads its role description and task, then generates `bid`, `confidence`, and `reason`                                                 |
| **What guarantees a bid is true**   | The original cooperative setting assumes nodes provide capability information relevant to the task; the protocol itself does not independently verify every claim | No guarantee. The LLM can claim that an unrelated task is within its skill or report unjustified confidence                                  |
| **Criterion for a good allocation** | Manager evaluates bids using the bid specification relevant to the distributed task                                                                               | Award is correct when the selected contractor equals the predefined `gold` contractor                                                        |
| **Negotiation cost**                | Task announcements, bids, awards, and processing of bids create communication and computation cost                                                                | Messages are counted as announcements + submitted bids + award messages; additional cost comes from one LLM API call per contractor per task |
| **Failure modes**                   | Communication overhead, inappropriate local choices, or poor/incomplete capability information can affect allocation                                              | Misawards, unassigned tasks, malformed JSON, misleading confidence, ties, API timeouts, and provider rate limits                             |

Smith's Contract Net Protocol uses negotiation for distributed task allocation. A manager announces a task, eligible nodes bid, and the manager selects a contractor. The protocol performs local mutual selection based on information exchanged between nodes.

The main difference in this experiment is that capability evaluation is performed by an LLM rather than by deterministic rules or sensor/resource information. Therefore, the bid itself can be unreliable.

---

## 4. Interpretation

The completed baseline runs achieved only **2/6 and 3/6 correct allocations**, corresponding to a mean accuracy of **41.7%**, with **4 and 3 misawards** respectively. Although the contractors had specialized role descriptions, the logs show that the LLM frequently claimed tasks outside its assigned skill. For example, in run 32 Contractor A, whose stated skill was arithmetic, bid with confidence 100 on a Python list-reversal task, while Contractor C also bid 100. Because equal confidence is resolved by response order, A received the task and produced a misaward. The homogeneous condition made this ambiguity even clearer: in run 38 all three contractors bid 100 on the arithmetic task, and A and B both bid 95 on the writing task. A therefore won the writing task only because it responded first, producing a misaward. This shows that when capability descriptions become identical, confidence alone provides little information for task allocation. The experiment also exposed a second failure mode: LLM responses sometimes violated the required JSON format, producing 3 and 4 parse failures in the two completed baseline runs. Overconfident runs were attempted but were interrupted by OpenRouter's HTTP 429 daily request limit, so a complete quantitative comparison for that condition was not available before submission. These interrupted executions were retained in `results.csv` and `logs/` rather than removed. Overall, the experiment shows that using self-reported LLM confidence as a Contract Net bid introduces problems that were not explicitly solved by Smith's original negotiation procedure: confidence may be poorly calibrated, agents may bid outside their described capabilities, and communication itself may fail to follow the required protocol.

## Reference

R. G. Smith, “The Contract Net Protocol: High-Level Communication and Control in a Distributed Problem Solver,” *IEEE Transactions on Computers*, vol. C-29, no. 12, 1980.
