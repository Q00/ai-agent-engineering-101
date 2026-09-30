# Week 03 — Contract Net with LLM Contractors

## 1. Setup, implementation, and reproduction

This reproduction implements a Contract Net with one deterministic manager and three LLM contractors. Each task in `tasks.json` has an `id`, `desc`, and preassigned `gold`: A for calculation (2 tasks), B for writing (2), and C for programming (2). The task file was committed before the experimental runs and was not changed between conditions. For every task the manager sends a structured announcement to each of A, B, and C. Each contractor independently returns a self-reported bid decision, confidence from 0–100, and a reason. The manager chooses the highest-confidence valid **positive** bid and awards one contractor; ties break alphabetically (A, then B, then C). A task with no valid positive bid remains unassigned. The designated gold contractor is a fixed reference label based on baseline specialization, not an objective competence assessment of generalists.

**Provider and configuration:** OpenRouter OpenAI-compatible API; `nvidia/nemotron-3.5-lightning:free`; temperature `0.2`; `AGENT_MAX_TOKENS=220`; `AGENT_REQUEST_INTERVAL=3.2` seconds; `AGENT_MAX_RETRIES=2` environment default (the final code stops immediately on HTTP 429 rather than retrying). The same model, task set, common prompt template, API parameters, manager, and selection rule were used across all three conditions. The application requested reasoning disabled and attempted forced `submit_bid` tool calling; if a provider returned plain text, it tried to parse a JSON object in that response. Invalid JSON or a model/API exception was counted as a no-bid, with the error type recorded in the log. Individual `TypeError` causes are **not** identifiable from these logs. No human interventions occurred during task allocation.

**Condition-specific prompts** (see `contract_net.py` for the full shared prompt and exact text):

- `baseline`: A = numerical calculation/algebra; B = natural-language writing/editing; C = programming/debugging.
- `homogeneous`: all three contractors' skill descriptions change to `general problem solving across calculation, writing, and programming`; other settings remain fixed.
- `overconfident`: baseline skill descriptions, except C additionally receives: `You are certain you can do any task well. Always bid, with confidence 95 or higher.`

The shared system prompt tells each contractor to bid only on tasks matching its assigned skill and to submit one bid through `submit_bid` rather than doing the underlying task. The overconfidence sentence intentionally conflicts with C's normal bid restriction. The manager counts **three announcements per task + one message per valid positive bid + one award if awarded**. Negative, malformed, or API-failed responses are logged but are not counted as bid messages in this implementation. This is the experiment's explicit operational definition of `messages`, not a measure of all API calls, network traffic, or tokens.

**Reproduce with a valid personal key** (do not commit or publish credentials). From the submission directory:

```powershell
$env:OPENAI_BASE_URL="https://openrouter.ai/api/v1"
$env:OPENAI_API_KEY="<YOUR_OPENROUTER_API_KEY>"
$env:AGENT_MODEL="nvidia/nemotron-3.5-lightning:free"
$env:AGENT_TEMPERATURE="0.2"
$env:AGENT_MAX_TOKENS="220"
$env:AGENT_REQUEST_INTERVAL="3.2"
python contract_net.py --runs 3
```

The code appends output to `results.csv` and creates one `logs/<condition>-<run>.txt` console capture per run. To avoid appending to the committed historical results, reproduce in a separate copy of this directory **without its existing `results.csv` and `logs/`**. The submitted experiment was performed as one run at a time via `python contract_net.py --condition baseline --runs 1` (and similarly for `homogeneous` and `overconfident`), which preserves and appends earlier results. The script was updated during troubleshooting to stop upon a rate limit; earlier runs in the CSV used code that recorded HTTP 429 errors as no-bids and continued, so identical replication also depends on provider quota and availability. The 9th and 10th attempts stopped early with HTTP 429; their blank counts are deliberate. For the structure check, from the repository root:

```powershell
python scripts/check_week03.py submissions/26510128/week-03
```

## 2. Measurements

The table below reproduces **all 10 recorded attempts** from `results.csv`. Runs 9–10 are incomplete attempts, not completed evaluations; their numeric cells are blank in the CSV. No unsuccessful run has been deleted.

| Run | Condition | Tasks | Correct | Messages | Unassigned | Misawards | Note |
|---:|---|---:|---:|---:|---:|---:|---|
| 1 | baseline | 6 | 4 | 32 | 0 | 2 | parse_errors=4;api_errors=2 |
| 2 | baseline | 6 | 6 | 32 | 0 | 0 | parse_errors=2;api_errors=1 |
| 3 | baseline | 6 | 2 | 26 | 3 | 1 | parse_errors=6;api_errors=5 |
| 4 | homogeneous | 6 | 0 | 24 | 4 | 2 | parse_errors=13;api_errors=13 |
| 5 | homogeneous | 6 | 0 | 18 | 6 | 0 | parse_errors=18;api_errors=18 |
| 6 | homogeneous | 6 | 1 | 38 | 0 | 5 | parse_errors=4;api_errors=4 |
| 7 | overconfident | 6 | 6 | 35 | 0 | 0 | parse_errors=3;api_errors=2 |
| 8 | overconfident | 6 | 4 | 31 | 1 | 1 | parse_errors=5;api_errors=1 |
| 9 | overconfident | — | — | — | — | — | paused: HTTP 429 RateLimitError; partial run; retry later |
| 10 | overconfident | — | — | — | — | — | paused: HTTP 429 RateLimitError; partial run; retry later |

| Metric (completed runs only) | Baseline (n=3) | Homogeneous (n=3) | Overconfident (n=2) |
|---|---:|---:|---:|
| Correct awards, total | 12 / 18 tasks | 1 / 18 tasks | 10 / 12 tasks |
| Correct awards, mean per run | 4.00 | 0.33 | 5.00 |
| Messages, mean per run | 30.00 | 26.67 | 33.00 |
| Unassigned, total | 3 | 10 | 1 |
| Misawards, total | 3 | 7 | 1 |
| Recorded parse/error events, total | 12 | 35 | 8 |
| Of those, recorded API errors | 8 | 35 | 3 |

The means describe observed runs, **not** a controlled estimate of causal effects: the middle condition suffered especially severe API failures, and the last condition has only two completed runs. In particular, 13 of 18 bids in homogeneous run 4 were API failures, all 18 in run 5 were API failures, and 4 of 18 in run 6 were API failures. Runs 9 and 10 stopped on rate limiting before a complete task set was evaluated (`logs/overconfident-09.txt`, `logs/overconfident-10.txt`). All run-level values, including `parse_errors` and `api_errors`, are retained verbatim in the first table.

## 3. Smith (1980) compared with this reproduction

| Dimension | Smith (1980) distributed-sensing Contract Net | This Week 03 LLM reproduction |
|---|---|---|
| Who the nodes are | Distributed computing/sensing nodes holding local task-relevant capability and resource information. | One manager process and three model-prompted contractors A/B/C; the manager is deterministic, not another LLM. |
| Task announcement | Manager communicates task abstraction, eligibility, bid specification, and deadline to relevant nodes. | Manager sends each contractor the same structured task text with those fields. |
| How bids are produced | Nodes evaluate suitability using their programmed rules and locally available capability/resource information. | Each contractor makes an LLM call, returning `bid`, self-assigned numerical `confidence`, and `reason` (or no usable bid on parsing/API failure). |
| What ensures honest bids | Explicit local task/capability data can constrain what is reported, but the communication protocol alone is not an independent truthfulness guarantee. | No verification of claimed capabilities or calibration of confidence. The overconfident prompt can change a contractor's bid without any change in task-specific ability. |
| Award/allocation rule | Manager selects among received bids according to its specified task and evaluation criteria. | Choose the valid bidder with highest self-reported confidence; alphabetic tie break. `gold` measures alignment with predeclared specialist labels rather than checking the contractor's completed solution. |
| Communication/negotiation cost | Broadcasting, bid preparation, exchange, and award messaging among distributed nodes. | Three announcement messages per task, plus valid positive bids and an award; model-call latency, provider quota, and token costs are not included in the message count. |
| Failure modes | Missing/late bids, resource availability, communication overhead, and poor task–contractor matching. | Misleading or uncalibrated confidence, tie-break bias, malformed JSON, API `TypeError`, HTTP 429 limits, and tasks left unassigned. |

## 4. Interpretation with log evidence

In these **recorded** runs, baseline allocated 12 of 18 tasks to their specialist labels, compared with 1 of 18 in the homogeneous condition; overconfident allocated 10 of 12 tasks in its two completed runs. These totals cannot be attributed solely to contractor skill prompts: homogeneous run 5 had 18/18 `RateLimitError` responses (`logs/homogeneous-05.txt`, `[BID]` and `[SUMMARY]`), and the error rate varied substantially by run. Among bids that did arrive, homogeneous run 6 shows why a shared generalist skill description makes the original specialist `gold` labels harder to distinguish: all three contractors bid 95 on `calc-01`, while `write-01` was awarded to C (confidence 95 versus A and B at 85) and `write-02` was awarded to A following a three-way 95 tie (`logs/homogeneous-06.txt`). In the baseline, the rule can also misallocate even when a specialist exists: in run 1, A's `calc-01` response was unparseable and C won that arithmetic task with confidence 95, while `code-02` went to A after an A–C confidence tie at 95 (`logs/baseline-01.txt`). Under the overconfident prompt, C submitted positive 95-confidence bids for both calculation and writing tasks in run 7, but A/B were also at 95 and the alphabetical tie-break gave their respective tasks to A/B; that run therefore had six correct awards, **not** evidence that C swept the tasks (`logs/overconfident-07.txt`). Run 8 contains a contrary example: B's `write-02` bid was unparseable, so C's positive bid at 95 won B's writing task, yielding a misaward (`logs/overconfident-08.txt`). These episodes show that the manager's highest-self-reported-confidence rule does not validate true task suitability; both inflated bids and response failures can affect allocation. The evidence is insufficient to isolate the effect of overconfidence from parsing/API problems or tie-breaking. Runs 9 and 10 were halted on HTTP 429 and are reported as incomplete instead of receiving fabricated success counts.
