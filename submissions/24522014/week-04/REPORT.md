# Week 04 — Speech Acts in Price Negotiation

## 1. Setup and reproduction

The experiment used one buyer and one seller with separate system prompts. The
buyer knew only its maximum budget, the seller knew only its minimum reserve,
and the buyer spoke first. Each episode allowed at most eight messages. The
four available acts were `propose`, `accept-proposal`, `reject-proposal`, and
`refuse`. A deal used the other party's latest parsed proposal; otherwise an
episode ended as `no_deal` on `refuse` or `open` at the turn limit.

| Setting | Value |
|---|---|
| Provider | OpenRouter through its OpenAI-compatible endpoint |
| Model | `nvidia/nemotron-3-super-120b-a12b:free` |
| Temperature | `0.0` |
| Maximum output | 300 tokens per model call |
| Turn limit | 8 messages per episode |
| Repeats | 3 per condition, 4 scenarios per repeat |
| Pacing | 3.5 seconds between calls |
| Retry policy | Up to 4 attempts, with 5/10/15-second backoff |
| Reasoning output | Disabled through `extra_body={"reasoning":{"enabled":false}}` |

The role and four-act paragraphs were identical in all conditions. Only the
following final format paragraph changed:

- **free:** `Write one or two plain English sentences. Do not include an explicit performative tag, JSON, or Markdown.`
- **tagged:** `Start with exactly one of these tags: (propose), (accept-proposal), (reject-proposal), or (refuse). Follow the tag with one plain English sentence. A propose sentence must contain exactly one whole-number price. Do not use JSON or Markdown.`
- **structured:** `Reply with exactly one JSON object and nothing else: {"performative":"propose|accept-proposal|reject-proposal|refuse","content":{"price":<whole number or null>}}. Use a whole-number price only for propose; use null for every other performative. Do not use a Markdown code fence.`

For `free`, the reader saw the whole role-labelled transcript and used this
prompt: `Label only the final message. Choose exactly one of propose,
accept-proposal, reject-proposal, or refuse. For propose, extract the offered
whole-number price; otherwise use null. A counteroffer is propose even when it
also rejects an earlier price. Return exactly
{"performative":"...","price":...}.` For `tagged`, a regex read the leading
performative and a smaller reader call extracted only the price of a
`propose`. For `structured`, a local JSON/schema parser read both fields and
made no reader-model call.

From PowerShell, set the same environment variables used in week 03 and run:

```powershell
$env:OPENAI_BASE_URL = "https://openrouter.ai/api/v1"
$env:OPENAI_API_KEY = "<your key>"
$env:AGENT_MODEL = "nvidia/nemotron-3-super-120b-a12b:free"
$env:AGENT_TEMPERATURE = "0.0"
python submissions/24522014/week-04/run_experiment.py
```

The runner appends each episode immediately and skips an existing
`(run, scenario)` pair, so the command resumes rather than duplicating work.
It writes one append-only console capture per condition/repeat under `logs/`.

## 2. Results

The summary treats a crashed row as an attempted episode but excludes its
blank `turns`, `format_errors`, and `reader_calls` fields from averages and
sums. Thus the first correct fraction is over all 12 attempted rows, while the
parenthetical fraction is over completed episodes. Outcome counts are shown as
`deal / no_deal / open / crash`.

| Condition | Correct | Violations | Mean turns (completed) | Format errors | Reader calls | Outcomes |
|---|---:|---:|---:|---:|---:|---:|
| free | 9/12 (9/11 completed) | 0 | 6.55 | 0 | 72 | 4 / 4 / 3 / 1 |
| tagged | 9/12 (9/10 completed) | 0 | 6.20 | 0 | 29 | 5 / 4 / 1 / 2 |
| structured | 8/12 (8/11 completed) | 0 | 6.91 | 1 | 0 | 3 / 0 / 8 / 1 |

### Per-episode results

Blank outcome and metric cells are preserved crashed episodes, not deleted or
rerun results.

| Run | Condition | Scenario | Possible | Outcome | Price | Correct | Violation | Turns | Errors | Reader calls | Note |
|---:|---|---:|---:|---|---:|---:|---:|---:|---:|---:|---|
| 1 | free | 1 | 1 | deal | 120 | 1 | 0 | 4 | 0 | 4 | calls=8, retries=0, tokens=1821 |
| 1 | free | 2 | 1 | no_deal |  | 0 | 0 | 7 | 0 | 7 | calls=14, retries=0, tokens=3022 |
| 1 | free | 3 | 0 | no_deal |  | 1 | 0 | 5 | 0 | 5 | calls=14, retries=4, tokens=2306 |
| 1 | free | 4 | 0 | open |  | 1 | 0 | 8 | 0 | 8 | calls=18, retries=2, tokens=4303 |
| 2 | free | 1 | 1 | deal | 120 | 1 | 0 | 4 | 0 | 4 | calls=9, retries=1, tokens=1745 |
| 2 | free | 2 | 1 | no_deal |  | 0 | 0 | 7 | 0 | 7 | calls=15, retries=1, tokens=3022 |
| 2 | free | 3 | 0 |  |  |  |  |  |  |  | crash: `TypeError`, empty upstream response |
| 2 | free | 4 | 0 | open |  | 1 | 0 | 8 | 0 | 8 | calls=20, retries=4, tokens=3677 |
| 3 | free | 1 | 1 | deal | 120 | 1 | 0 | 5 | 0 | 5 | calls=10, retries=0, tokens=2442 |
| 3 | free | 2 | 1 | deal | 40 | 1 | 0 | 8 | 0 | 8 | calls=21, retries=5, tokens=4196 |
| 3 | free | 3 | 0 | no_deal |  | 1 | 0 | 8 | 0 | 8 | calls=22, retries=6, tokens=4795 |
| 3 | free | 4 | 0 | open |  | 1 | 0 | 8 | 0 | 8 | calls=25, retries=9, tokens=3722 |
| 4 | tagged | 1 | 1 | no_deal |  | 0 | 0 | 5 | 0 | 2 | calls=10, retries=3, tokens=1642 |
| 4 | tagged | 2 | 1 | deal | 40 | 1 | 0 | 6 | 0 | 3 | calls=15, retries=6, tokens=2028 |
| 4 | tagged | 3 | 0 | no_deal |  | 1 | 0 | 7 | 0 | 3 | calls=15, retries=5, tokens=2372 |
| 4 | tagged | 4 | 0 | open |  | 1 | 0 | 8 | 0 | 4 | calls=13, retries=1, tokens=2832 |
| 5 | tagged | 1 | 1 | deal | 120 | 1 | 0 | 6 | 0 | 3 | calls=12, retries=3, tokens=2086 |
| 5 | tagged | 2 | 1 | deal | 40 | 1 | 0 | 6 | 0 | 3 | calls=13, retries=4, tokens=1991 |
| 5 | tagged | 3 | 0 | no_deal |  | 1 | 0 | 7 | 0 | 3 | calls=12, retries=2, tokens=2396 |
| 5 | tagged | 4 | 0 |  |  |  |  |  |  |  | crash: `TypeError`, empty upstream response |
| 6 | tagged | 1 | 1 | deal | 130 | 1 | 0 | 4 | 0 | 2 | calls=7, retries=1, tokens=1281 |
| 6 | tagged | 2 | 1 | deal | 40 | 1 | 0 | 6 | 0 | 3 | calls=15, retries=6, tokens=2026 |
| 6 | tagged | 3 | 0 | no_deal |  | 1 | 0 | 7 | 0 | 3 | calls=13, retries=3, tokens=2363 |
| 6 | tagged | 4 | 0 |  |  |  |  |  |  |  | crash: `TypeError`, empty upstream response |
| 7 | structured | 1 | 1 | deal | 120 | 1 | 0 | 4 | 0 | 0 | calls=5, retries=1, tokens=1124 |
| 7 | structured | 2 | 1 | open |  | 0 | 0 | 8 | 0 | 0 | calls=15, retries=7, tokens=2556 |
| 7 | structured | 3 | 0 | open |  | 1 | 0 | 8 | 0 | 0 | calls=8, retries=0, tokens=2548 |
| 7 | structured | 4 | 0 | open |  | 1 | 0 | 8 | 0 | 0 | calls=18, retries=10, tokens=2576 |
| 8 | structured | 1 | 1 | deal | 120 | 1 | 0 | 4 | 0 | 0 | calls=6, retries=2, tokens=1124 |
| 8 | structured | 2 | 1 | open |  | 0 | 0 | 8 | 0 | 0 | calls=9, retries=1, tokens=2556 |
| 8 | structured | 3 | 0 | open |  | 1 | 0 | 8 | 0 | 0 | calls=9, retries=1, tokens=2548 |
| 8 | structured | 4 | 0 | open |  | 1 | 0 | 8 | 0 | 0 | calls=14, retries=6, tokens=2576 |
| 9 | structured | 1 | 1 | deal | 120 | 1 | 0 | 4 | 0 | 0 | calls=4, retries=0, tokens=1124 |
| 9 | structured | 2 | 1 | open |  | 0 | 0 | 8 | 0 | 0 | calls=9, retries=1, tokens=2556 |
| 9 | structured | 3 | 0 | open |  | 1 | 0 | 8 | 1 | 0 | calls=9, retries=1, tokens=2521 |
| 9 | structured | 4 | 0 |  |  |  |  |  |  |  | crash: `TypeError`, empty upstream response |

## 3. Comparison with FIPA-ACL

| Question | FIPA-ACL | free | tagged | structured |
|---|---|---|---|---|
| Where does illocutionary force live? | In the mandatory ACL performative. | It is absent from the wire format and inferred from English context. | In one explicit parenthesized tag. | In the JSON `performative` field. |
| What is the content language? | A declared content language, normally paired with ontology metadata. | Unrestricted English. | English after a fixed act tag. | A fixed JSON schema carrying one integer price or `null`. |
| Who interprets content? | The receiving agent/content-language interpreter using the declared semantics and ontology. | An LLM reader interprets both act and price. | Regex interprets the act; an LLM reader interprets a proposed price. | Local JSON and schema code interprets both fields. |
| How does a conversation end? | ACL messages alone do not define the whole termination rule; an interaction protocol and conversation state do. | The harness ends on an inferred accept/refuse or after eight messages. | The harness ends on the parsed tag or after eight messages. | The harness ends on the parsed field or after eight messages. |
| What guarantees sincerity? | Feasibility preconditions describe intended mental-state semantics, but the message does not prove a sender is sincere. | A prompt asks agents to respect limits; the harness checks violations afterward. | The same prompt and post-hoc check; a tag does not prove truth. | The same prompt and post-hoc check; valid JSON does not prove truth. |
| What does one message cost to read? | Deterministic ACL parsing plus content-language/ontology processing. | One extra model call for every message: 72 here. | Regex for every act and a model call only for a proposal price: 29 here. | Local parsing only: 0 reader calls. |
| Which failure modes appeared or remain? | Unsupported performative, language/ontology mismatch, interaction-protocol mismatch, and insincere agents. | Ambiguous act/price readings, non-deterministic readings, and reader transport failures. | Missing/wrong/multiple tags and price-reader failures; the tag can disagree with its prose. | Malformed or schema-invalid JSON; content outside the schema is discarded. |

## 4. Interpretation

Most of the difference between conditions came from scenario 2 (the
textbook): tagged was correct in 3/3 attempts, free in 1/3, and structured in
0/3. The immediate cause was whether a seller leaked a useful price while
rejecting an offer. Tagged sellers said things such as *"I need at least 40"*
(`logs/tagged-01.txt` lines 29-30), which gave the buyer a target and led to an
agreement, whereas the structured schema required a rejection to carry
`"price": null`; it communicated the refusal but no counterprice, so all three
structured S2 episodes reached the eight-turn limit. Scenarios 3 and 4 had
little discriminatory power because they had no zone of possible agreement:
as long as the agents avoided a deal, the harness awarded `correct=1`. The one
tagged S1 failure was also behavioural rather than a parsing failure: although
the seller's true reserve was 120, it claimed *"I need at least 150"* and the
buyer, whose true budget was also 150, gave up (`logs/tagged-01.txt` lines
3-17). The explicit performative chiefly moved reading cost and ambiguity, not
sincerity. Reader calls followed the order guaranteed by the harness design,
free (every utterance, 72) > tagged (only proposals, 29) > structured (0), so
this cost ordering is not itself a surprising empirical result. Free's
`format_errors=0` is misleading if read as semantic accuracy: the reader
returned syntactically valid JSON with `ok: true` even when it classified
*"$35 is still too low ... I can't accept"* as `propose(35)`
(`logs/free-03.txt` lines 31-32), and it read *"decline and look elsewhere"*
as `reject-proposal` instead of `refuse` (lines 58-59). In other words,
`format_errors` measures parseability, not whether an LLM label is correct.
Structured avoided that hidden reader ambiguity but exposed a schema-surface
failure: `logs/structured-03.txt` lines 46-47 contain only
`{"performative":""}`, which the parser rejected as the sole recorded format
error. No completed condition produced a private-limit violation, so tags and
JSON did not change the observed sincerity metric; all conditions still
relied on prompt obedience and post-hoc checks. The four crashes (free 1,
tagged 2, structured 1) appear to be condition-independent transport failures
caused by repeated empty OpenRouter responses ending as a `NoneType` error.
They give the conditions different completed denominators and their retry
backoff also distorts elapsed time, so neither crash count nor wall-clock time
should be treated as a protocol effect. Finally, outputs differed across runs
despite temperature 0--for example tagged S1 failed once and succeeded twice--
so three repeats are too few to cleanly separate endpoint/model noise from a
true condition effect.
