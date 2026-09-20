# Week 03: Contract Net
Student ID: 26512073

## 1. Setup

I used one Python manager and three LLM contractors:
- A: arithmetic
- B: English writing
- C: Python coding

There are six tasks: two for each skill. Their correct contractors
(gold labels) were saved and committed before the experiments.

Settings: OpenAI, gpt-4o-mini, temperature 0,
max_completion_tokens 300, Python 3.14.7, openai package 3.14.0.

Each contractor receives a fresh conversation with tools disabled.
The prompts in `prompts.py` ask it to return a JSON bid decision,
confidence from 0 to 100, and a short reason—not solve the task.

I tested three conditions:
- baseline: three different skills.
- homogeneous: all three have general problem-solving skills.
- overconfident: baseline skills, but C is told to always bid
  with confidence 95 or higher.

Everything else stays the same. The manager chooses the highest
confidence among valid bids. Ties go in A, B, C order.
The manager does not use gold labels when choosing.

I count one message per announcement to a contractor, one per
accepted-format bid=true reply, and one per award.
Refusals and invalid replies are logged but not counted as bids.
Invalid replies also increase the parsing-failure count.

### How to run

Install the package:

```powershell
python -m pip install openai==3.14.0
```

Set OPENAI_API_KEY securely in the environment. Never commit the key.
In PowerShell, set:

```powershell
$env:AGENT_MODEL = "gpt-4o-mini"
$env:OPENAI_BASE_URL = "https://api.openai.com/v1"
Remove-Item Env:ANTHROPIC_API_KEY -ErrorAction SilentlyContinue
```

From the repository root:

```powershell
python submissions/26512073/week-03/run_experiment.py baseline --runs 3
python submissions/26512073/week-03/run_experiment.py homogeneous --runs 3
python submissions/26512073/week-03/run_experiment.py overconfident --runs 3
```

Each complete run makes 18 model calls. New runs are added to
`results.csv`, with separate files in `logs/`. Failed runs keep
blank counts and an error note. Earlier development attempts are
saved in `development_logs/`. Exact answers may vary between runs.

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

All nine experiments completed without parsing failures.

## 3. Comparison with Smith (1980)

| Question | Smith's sensing system | My experiment |
|---|---|---|
| Who are the nodes? | Sensor and processor nodes; roles can change. | One fixed manager and three LLM contractors. |
| How is a bid made? | Programmed procedures use information such as location and available sensors. | An LLM reads a prompt and reports confidence. |
| Are bids guaranteed honest? | Cooperative nodes are assumed; negotiation alone does not prove honesty. | No. Checking JSON does not check whether confidence is true. |
| What is a good allocation? | Suitable sensors and connections for sensing tasks. | Choosing the contractor matching the gold label. |
| What does negotiation cost? | Messages and processing time. | Messages, API tokens, and waiting time. |
| What can go wrong? | Node failures and communication problems; tasks can be announced again. | Wrong awards, tie-order bias, API errors, or invalid JSON. |

Source: [Smith (1980), The Contract Net Protocol](https://www.reidgsmith.com/The_Contract_Net_Protocol_Dec-1980.pdf),
especially Sections III and VI.

## 4. What I learned

Baseline matched all gold labels. Homogeneous produced more bids,
so messages increased from 33 to 41–42, but correct awards fell to
1–2 per run. In homogeneous run 4, task 3, all three contractors
bid at 85, so A won even though gold was B. Overconfident kept
6 correct awards but increased messages to 35 because C also bid
on the two writing tasks. In run 7, task 3, B and C both bid at 95.
B won because it comes before C. There was no special rule against
overconfidence: C would win if its confidence were higher.
The protocol does not check whether confidence is truthful.
Also, matching gold is not the same as solving a task correctly.
This matters especially for the generalists, who all had the same
skill description. My findings cover only six tasks and three runs
per condition.

Log evidence:
- `logs/run_4_homogeneous_20260920_014518_649378.txt`,
  lines 106, 119, 132: all bid at 85.
  Line 133: `[AWARD] task=3 winner=A`.
- `logs/run_7_overconfident_20260920_014619_238909.txt`,
  lines 119, 132: B and C bid at 95.
  Line 133: `[AWARD] task=3 winner=B`.

I used AI help for coding, debugging, tests, and English wording.
The results come from actual model calls. Local tests used separate
simulated replies. Failed development attempts remain in the logs
and Git history.