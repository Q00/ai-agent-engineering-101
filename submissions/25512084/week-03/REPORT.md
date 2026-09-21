# Week 03 — Contract Net Experiment

## 1. Setup

Provider: OpenRouter  
Model: `nvidia/nemotron-3.5-lightning:free`  
Temperature: 0

The experiment uses one manager and three LLM contractors. In the baseline condition, contractor A specializes in arithmetic, B in writing, and C in coding. In the homogeneous condition, all three contractors use the skill `general problem solving`. In the overconfident condition, the baseline skills are kept, but contractor C receives an extra instruction to always bid with confidence 95 or higher.

Each contractor receives the same task announcement and must return JSON with `bid`, `confidence`, and `reason`. The manager selects the valid bidder with the highest confidence. Ties are resolved by first response order.

Run with: `python run_experiment.py`

## 2. Results

| run | condition | tasks | correct | messages | unassigned | misawards | note |
|---:|---|---:|---:|---:|---:|---:|---|
| 1 | baseline | 6 | 0 | 0 | 0 | 0 | partial run, TypeError crash |
| 2 | baseline | 6 | 5 | 36 | 0 | 1 | parse_fails=0 |
| 3 | baseline | 6 | 3 | 33 | 0 | 3 | parse_fails=3 |
| 4 | homogeneous | 6 | 0 | 0 | 0 | 0 | partial run, OpenRouter 429 rate limit |
| 5 | homogeneous | 6 | 0 | 0 | 0 | 0 | OpenRouter 429 rate limit |
| 6 | homogeneous | 6 | 0 | 0 | 0 | 0 | OpenRouter 429 rate limit |
| 7 | overconfident | 6 | 0 | 0 | 0 | 0 | OpenRouter 429 rate limit |
| 8 | overconfident | 6 | 0 | 0 | 0 | 0 | OpenRouter 429 rate limit |
| 9 | overconfident | 6 | 0 | 0 | 0 | 0 | OpenRouter 429 rate limit |

Failed runs were kept as experimental evidence. The free OpenRouter daily request limit was reached during homogeneous run 4, so later runs could not complete.

## 3. Comparison with Smith's Contract Net

| Item | Smith Contract Net | This experiment |
|---|---|---|
| Participants | Manager and multiple contractors | One manager and three LLM contractors |
| Bid production | Contractors evaluate announced tasks and submit bids | LLM contractors decide whether to bid and return confidence |
| Bid validity | Contractor follows the task and bidding protocol | Response must be valid JSON with `bid`, `confidence`, and `reason` |
| Assignment | Manager evaluates bids and selects a contractor | Highest-confidence valid bidder wins |
| Negotiation cost | Communication between manager and contractors | Measured using the `messages` metric |
| Failure | Missing or unsuitable bids can produce poor allocation | Parse failures, misawards, unassigned tasks, and API crashes |

## 4. Interpretation

The completed baseline runs show that confidence-based LLM bidding is unstable even when explicit specializations are given. Run 2 achieved 5 out of 6 correct assignments with one misaward, while run 3 achieved 3 out of 6 correct assignments with three misawards and three parse failures.

One clear failure occurred in run 2 on the Python reverse-list task. Contractor B incorrectly bid with confidence 95, while contractor C also bid with confidence 95. Because ties are resolved by first response order, B received the task even though C was the gold contractor.

The beginning of the homogeneous condition also showed reduced specialization. On the first arithmetic task all three general-purpose contractors bid, and both A and C reported confidence 100. This makes allocation depend more heavily on confidence and response order instead of specialized roles.

The homogeneous experiment was interrupted by the OpenRouter free-model daily rate limit. The remaining homogeneous runs and all overconfident runs therefore crashed with HTTP 429. These runs were kept in `results.csv` and `logs/` instead of being deleted. Because the overconfident condition did not successfully execute, no empirical conclusion about its effect can be made from these runs.
