# Week 04: Agent Communication
Student ID: 26512073

## 1. Setup

I tested one buyer and one seller on four scenarios, with three repeats
per format: 36 episodes in total. Scenarios were committed before runs.

Settings: OpenAI, gpt-4o-mini, temperature 0, maximum completion tokens
300, no tools, and an eight-message limit.

Each agent knew only its own private limit. The buyer tried to buy
cheaply, and the seller tried to sell at a high price. Their role
instructions stayed the same across formats.

| Format | Instruction | Reading method |
|---|---|---|
| Free | Plain English | LLM reads the act and price |
| Tagged | Parenthesized act followed by one English sentence | Python reads the tag; LLM reads proposal prices |
| Structured | JSON with performative and content.price | Python parses the message |

Exact agent prompts are in `prompts.py`. Exact reader prompts are in
`reader_prompts.py`. The reader interprets the last message using the
conversation, extracts the new offer, and returns null when unclear.

Acceptance uses the other agent's last proposed price. Refusal ends
with no deal. Eight messages without an ending produce `open`.
A correct deal respects both limits. No deal is correct when reserve
exceeds budget. Unfinished episodes count as incorrect.
Unreadable messages are counted and still delivered.

### How to run

Install `openai` and set `OPENAI_API_KEY` privately. From the repository
root, run in PowerShell:

```powershell
$env:AGENT_MODEL = "gpt-4o-mini"
$env:OPENAI_BASE_URL = "https://api.openai.com/v1"
Remove-Item Env:ANTHROPIC_API_KEY -ErrorAction SilentlyContinue

python submissions/26512073/week-04/run_experiment.py free --repeat 1
python submissions/26512073/week-04/run_experiment.py tagged --repeat 1
python submissions/26512073/week-04/run_experiment.py structured --repeat 1
```

Repeat with `--repeat 2` and `--repeat 3`. Recorded episodes are skipped.
For fresh experiments, use a separate copy without results.csv and logs.
HTTP 429 requests are retried. Crashed episodes stay recorded.
Reader-call counts exclude rate-limit retries.

## 2. Results

Each format had 12 episodes. Counts are totals; turns are averages.

| Format | Correct | Violations | Mean turns | Format errors | Reader calls |
|---|---:|---:|---:|---:|---:|
| Free | 3/12 | 1 | 7.50 | 9 | 90 |
| Tagged | 3/12 | 0 | 7.33 | 0 | 44 |
| Structured | 4/12 | 0 | 7.33 | 0 | 0 |

The full episode table is below.

## 3. Comparison with FIPA-ACL

| Feature | FIPA-ACL | Free | Tagged | Structured |
|---|---|---|---|---|
| Act location | Performative field | Sentence/context | Leading tag | JSON field |
| Content language | Declared language and ontology | English | English | JSON |
| Interpreter | Receiving agent | LLM reader | Regex and LLM | Python parser |
| Ending | Chosen interaction protocol | Accept, refuse, or turn limit | Same | Same |
| Sincerity | Assumed, not proven by message | Not guaranteed | Not guaranteed | Not guaranteed |
| Reading cost | Implementation-dependent | One LLM call/message | One LLM call/proposal | No reader calls |
| Possible failures | Unverifiable intentions; meaning mismatch | Misread act or price | Bad tag or misread price | Bad JSON or poor decisions |

Our structured format is a simplified format, not full FIPA-ACL.

## 4. Interpretation

Explicit formats reduced reading errors and reader calls, but did not
guarantee agreement. In `run_2_free.txt`, scenario 3, the seller said
"$30 is too low" and "I can accept $70". The reader wrongly labeled
this counter-offer as acceptance, so the program recorded a deal at 30,
below the reserve of 60. In `run_7_structured.txt`, scenario 4, offers
of 30, 35, 40, and 45 were rejected; the episode ended open although
50 was a valid deal price. Clear format therefore helped message
reading but did not ensure good negotiation. These small results
do not establish a general winner.

I used AI help for coding, debugging, and English wording.
Logs and development history were preserved.
## Per-episode results

| run | condition | scenario | deal_possible | outcome | price | correct | violation | turns | format_errors | reader_calls | note |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 7 | structured | 1 | 1 | open |  | 0 | 0 | 8 | 0 | 0 |  |
| 7 | structured | 2 | 1 | deal | 100 | 1 | 0 | 6 | 0 | 0 |  |
| 7 | structured | 3 | 0 | open |  | 0 | 0 | 8 | 0 | 0 |  |
| 7 | structured | 4 | 1 | open |  | 0 | 0 | 8 | 0 | 0 |  |
| 8 | structured | 1 | 1 | deal | 40 | 1 | 0 | 6 | 0 | 0 |  |
| 8 | structured | 2 | 1 | deal | 100 | 1 | 0 | 6 | 0 | 0 |  |
| 8 | structured | 3 | 0 | open |  | 0 | 0 | 8 | 0 | 0 |  |
| 8 | structured | 4 | 1 | open |  | 0 | 0 | 8 | 0 | 0 |  |
| 9 | structured | 1 | 1 | open |  | 0 | 0 | 8 | 0 | 0 |  |
| 9 | structured | 2 | 1 | deal | 100 | 1 | 0 | 6 | 0 | 0 |  |
| 9 | structured | 3 | 0 | open |  | 0 | 0 | 8 | 0 | 0 |  |
| 9 | structured | 4 | 1 | open |  | 0 | 0 | 8 | 0 | 0 |  |
| 4 | tagged | 1 | 1 | open |  | 0 | 0 | 8 | 0 | 4 |  |
| 4 | tagged | 2 | 1 | deal | 90 | 1 | 0 | 4 | 0 | 2 |  |
| 4 | tagged | 3 | 0 | open |  | 0 | 0 | 8 | 0 | 4 |  |
| 4 | tagged | 4 | 1 | open |  | 0 | 0 | 8 | 0 | 4 |  |
| 5 | tagged | 1 | 1 | open |  | 0 | 0 | 8 | 0 | 4 |  |
| 5 | tagged | 2 | 1 | deal | 90 | 1 | 0 | 4 | 0 | 2 |  |
| 5 | tagged | 3 | 0 | open |  | 0 | 0 | 8 | 0 | 4 |  |
| 5 | tagged | 4 | 1 | open |  | 0 | 0 | 8 | 0 | 4 |  |
| 6 | tagged | 1 | 1 | open |  | 0 | 0 | 8 | 0 | 4 |  |
| 6 | tagged | 2 | 1 | deal | 91 | 1 | 0 | 8 | 0 | 4 |  |
| 6 | tagged | 3 | 0 | open |  | 0 | 0 | 8 | 0 | 4 |  |
| 6 | tagged | 4 | 1 | open |  | 0 | 0 | 8 | 0 | 4 |  |
| 1 | free | 1 | 1 | open |  | 0 | 0 | 8 | 1 | 8 |  |
| 1 | free | 2 | 1 | open |  | 0 | 0 | 8 | 1 | 8 |  |
| 1 | free | 3 | 0 | open |  | 0 | 0 | 8 | 1 | 8 |  |
| 1 | free | 4 | 1 | deal | 50 | 1 | 0 | 7 | 0 | 7 |  |
| 2 | free | 1 | 1 | deal | 40 | 1 | 0 | 8 | 1 | 8 |  |
| 2 | free | 2 | 1 | open |  | 0 | 0 | 8 | 1 | 8 |  |
| 2 | free | 3 | 0 | deal | 30 | 0 | 1 | 4 | 1 | 4 |  |
| 2 | free | 4 | 1 | deal | 50 | 1 | 0 | 7 | 0 | 7 |  |
| 3 | free | 1 | 1 | open |  | 0 | 0 | 8 | 1 | 8 |  |
| 3 | free | 2 | 1 | open |  | 0 | 0 | 8 | 1 | 8 |  |
| 3 | free | 3 | 0 | open |  | 0 | 0 | 8 | 1 | 8 |  |
| 3 | free | 4 | 1 | open |  | 0 | 0 | 8 | 0 | 8 |  |
