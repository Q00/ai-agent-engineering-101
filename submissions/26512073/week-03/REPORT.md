# Week 03: Contract Net with LLM Contractors

Student ID: 26512073

## 1. Setup and reproduction

This experiment uses one Python manager and three LLM contractors.
It measures task allocation, not the quality of completed task solutions.
Contractors are instructed not to solve the tasks.

Settings:
- Provider: OpenAI
- Model: gpt-4o-mini
- Temperature: 0
- max_completion_tokens: 300
- Python: 3.14.7
- OpenAI Python package: 3.14.0
- Tools disabled; each bid uses a fresh conversation.
- Six tasks, committed before experimental runs:
  two arithmetic tasks (gold A), two English writing tasks (gold B),
  and two Python coding tasks (gold C).

| Condition | A | B | C |
|---|---|---|---|
| baseline | Arithmetic | English writing | Python coding |
| homogeneous | General problem solving | General problem solving | General problem solving |
| overconfident | Arithmetic | English writing | Python coding; instructed to always bid with confidence >=95 |

The prompt template and announcement format are in `prompts.py`.
Only the condition-specific skill descriptions and C's overconfidence
instruction change. Tasks, model, temperature, and selection rule stay fixed.
Full rendered prompts and raw responses are recorded in the logs.

The manager chooses the highest-confidence valid bidder.
Ties are resolved in A, B, C order. Gold labels are used only after selection.
Invalid JSON or invalid fields count as no bid and increment parse_failures.

Message counting uses one announcement per contractor, one per valid
bid=true response, and one per award. Refusals and invalid replies are logged
but are not counted as bids. API calls and protocol messages are different:
a complete run makes 18 model calls even when some contractors decline.

Install the dependency:

```powershell
python -m pip install openai==3.14.0
```

Set OPENAI_API_KEY securely in the environment; do not put it in a file
or commit it. In PowerShell, set the remaining environment variables:

```powershell
$env:AGENT_MODEL = "gpt-4o-mini"
$env:OPENAI_BASE_URL = "https://api.openai.com/v1"
Remove-Item Env:ANTHROPIC_API_KEY -ErrorAction SilentlyContinue
```

From the repository root, reproduce three runs per condition:

```powershell
python submissions/26512073/week-03/run_experiment.py baseline --runs 3
python submissions/26512073/week-03/run_experiment.py homogeneous --runs 3
python submissions/26512073/week-03/run_experiment.py overconfident --runs 3
```

The runner appends new results and creates separate timestamped logs.
Rerunning these commands adds experiments; it does not replace submitted runs.
Caught failures remain as rows with blank counts and an error note.
Temperature 0 does not guarantee identical responses across runs.

Development attempts are preserved in `development_logs/`.
Earlier requests failed with HTTP 400. A later local check found that the
environment contained only one character instead of the full API key.
After the full key was entered, the single-bid test succeeded.
The request parameter change and diagnostic changes remain in Git history.
The separate local tests use simulated replies, not experimental data.

## 2. Results

| run | condition | tasks | correct | messages | unassigned | misawards | note |
|---|---|---|---|---|---|---|---|
| 1 | baseline | 6 | 6 | 33 | 0 | 0 | parse_failures=0 |
| 2 | baseline | 6 | 6 | 33 | 0 | 0 | parse_failures=0 |
| 3 | baseline | 6 | 6 | 33 | 0 | 0 | parse_failures=0 |
| 4 | homogeneous | 6 | 2 | 41 | 0 | 4 | parse_failures=0 |
| 5 | homogeneous | 6 | 1 | 42 | 0 | 5 | parse_failures=0 |
| 6 | homogeneous | 6 | 1 | 42 | 0 | 5 | parse_failures=0 |
| 7 | overconfident | 6 | 6 | 35 | 0 | 0 | parse_failures=0 |
| 8 | overconfident | 6 | 6 | 35 | 0 | 0 | parse_failures=0 |
| 9 | overconfident | 6 | 6 | 35 | 0 | 0 | parse_failures=0 |

Baseline and overconfident each matched gold on 18/18 allocations.
Homogeneous matched gold on 4/18 allocations.
No official run crashed; no parsing failures or unassigned tasks occurred.

## 3. Comparison with Smith (1980)

| Aspect | Smith's distributed sensing example | This reproduction |
|---|---|---|
| Nodes | Distributed sensor and processor nodes; manager and contractor are dynamic roles. | One fixed Python manager and three prompted LLM contractors, called sequentially. |
| Bid production | Task-specific procedures evaluate eligibility; bids describe location and sensors. | An LLM produces a bid decision, confidence, and reason from text. |
| Bid honesty | Cooperative operation is assumed; negotiation itself is not a proof of truthful capability claims. | JSON validation checks structure, not honesty. Self-reported confidence is not calibrated or verified. |
| Allocation quality | Suitable sensor coverage, sensor types, and connections for distributed sensing. | Agreement with predefined gold contractor labels; execution quality is not measured. |
| Negotiation cost | Communication and bid processing; eligibility restrictions reduce unnecessary traffic. | Counted announcements, true bids, and awards, plus API tokens and latency. |
| Failure modes | Communication bottlenecks and node failures; failed contracts can be reannounced. | Observed gold mismatches, tie-order bias, and development API failures. Invalid JSON is handled but did not occur in official runs. |

Source: Reid G. Smith (1980), *The Contract Net Protocol: High-Level
Communication and Control in a Distributed Problem Solver*,
IEEE Transactions on Computers, C-29(12), 1104–1113, especially Sections III and VI.
https://www.reidgsmith.com/The_Contract_Net_Protocol_Dec-1980.pdf

## 4. Interpretation and evidence

Distinct skill prompts helped the baseline match all gold labels, although
skill boundaries overlapped: baseline run 1, task 1, shows A bidding at 95
and C at 90 before A wins (log lines 20, 46–47).
Homogeneous contractors submitted more bids, raising messages from 33 to
41–42, while gold matches fell to 1–2 per run. In homogeneous run 4,
task 3, A, B, and C all bid at 85 (lines 106, 119, 132), followed by
"[AWARD] task=3 winner=A" (line 133); the gold label was B.
Overconfidence increased messages to 35 without reducing gold matches.
In overconfident run 7, task 3, B and C both bid at 95 (lines 119, 132),
followed by "[AWARD] task=3 winner=B" (line 133).
C's new bids on the two writing tasks explain the two additional messages
relative to baseline. Alphabetical tie-breaking preserved correct allocation
in this case; it did not detect overconfidence. If C had bid above the
specialist's confidence, the same selection rule would have chosen C.
The announcement-bid-award structure alone provides no verification of
self-reported competence. Finally, homogeneous gold mismatches do not prove
inability: all three contractors had the same generalist description, while
the gold labels remained those of the original specialists. These findings
are limited to six tasks, three runs per condition, and this tie rule.

Evidence files:
- `logs/run_1_baseline_20260920_014248_908738.txt`
- `logs/run_4_homogeneous_20260920_014518_649378.txt`
- `logs/run_7_overconfident_20260920_014619_238909.txt`

AI assistance was used for step-by-step code scaffolding, debugging,
test design, and report wording. The experiment outputs are actual
recorded model responses; simulated test replies are kept separate.