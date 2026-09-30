# Week 03 Contract Net Report

## 1. Setup

This experiment implements a Contract Net protocol with one manager and three LLM contractors: calculator, writer, and coder.

- Provider: OpenRouter
- Model: `nvidia/nemotron-3.5-lightning:free`
- Temperature: `0.0`
- Task set: 6 tasks from `tasks.json`
- Conditions: baseline, homogeneous, and overconfident

For each task, the manager announces the task to all three contractors. Each contractor makes one LLM call and returns a bid. The manager awards the task to the highest valid confidence bid.

The task set was committed before the experiment runs. The final comparison uses the strict bid parser and the same task set, model, prompts, and temperature across conditions.

## 2. Results

The comparable completed runs are:

| Condition | Runs | Avg. correct | Avg. messages | Avg. unassigned | Avg. misawards | Avg. unparseable bids |
|---|---|---:|---:|---:|---:|---:|
| Baseline | 3, 10, 11 | 0.00 | 36.33 | 5.67 | 0.33 | 17.33 |
| Homogeneous | 12, 13, 14 | 0.00 | 36.00 | 6.00 | 0.00 | 18.00 |
| Overconfident | 15, 16, 17 | 0.00 | 36.00 | 6.00 | 0.00 | 18.00 |

The baseline runs were:
- Run 3: correct=0, messages=36, unassigned=6, misawards=0, unparseable_bids=17
- Run 10: correct=0, messages=37, unassigned=5, misawards=1, unparseable_bids=17
- Run 11: correct=0, messages=36, unassigned=6, misawards=0, unparseable_bids=18

The homogeneous runs 12, 13, and 14 each produced 18 unparseable bids, so all six tasks were unassigned in every run.

The overconfident runs 15, 16, and 17 also each produced 18 unparseable bids, so all six tasks were unassigned in every run.

Earlier runs were retained in `results.csv` and `logs/`. Run 1 failed because of authentication, runs 4-9 failed because of the OpenRouter free-tier daily rate limit, and run 2 was a pilot run before strict bid parsing.

## 3. Comparison with Smith (1980)

| Aspect | Smith (1980) | This experiment |
|---|---|---|
| Participants | Distributed problem-solving nodes | One manager and three LLM contractors |
| Task announcement | A manager announces available work | The manager sends each task to all contractors |
| Bid generation | Nodes evaluate their suitability | LLM contractors decide whether to bid and provide confidence |
| Award | The manager selects a contractor | The manager selects the highest valid confidence bid |
| Communication cost | Negotiation messages between nodes | Announcements, bids, and awards |
| Failure modes | Coordination or allocation failures | Unparseable bids, no awards, misawards, authentication failures, and rate limits |

## 4. Interpretation

The main finding is that the selected free OpenRouter model frequently failed to follow the required JSON-only bid format. After the parser was made strict, reasoning text or other non-JSON output was treated as an unparseable bid rather than being accepted as a valid bid.

This formatting behavior dominated the experiment. In the three comparable baseline runs, 52 of 54 bid attempts were unparseable. In the homogeneous condition, all 54 bid attempts were unparseable. In the overconfident condition, all 54 bid attempts were also unparseable.

Because valid bids were almost never produced, the manager usually had no contractor to award the task to. This produced zero correct allocations in all nine comparable completed runs. The homogeneous and overconfident conditions therefore did not show meaningful allocation differences under this model because the bid-format failure prevented the intended negotiation behavior from being observed.

The overconfident prompt instructed the programming contractor to bid on every task with very high confidence, including tasks outside programming. However, the model still returned responses that failed strict parsing. Therefore, the expected effect of overconfidence could not be evaluated from allocation outcomes in these runs.

The experiment also shows a practical limitation of using a free LLM provider for repeated multi-agent experiments. Several early runs were interrupted by authentication and daily rate-limit errors. These failures were preserved rather than deleted so that the experimental process remains reproducible.
