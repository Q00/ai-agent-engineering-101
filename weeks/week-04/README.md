# Week 04 — Speech acts in practice: free, tagged, and structured negotiation

**Due: before the start of the week-05 class.** The deadline is judged by the PR open timestamp.

## Background

FIPA-ACL (2002) put the illocutionary force of a message into a mandatory `performative` field and the content into a formal content language with a declared ontology. The lecture's claim under test: an LLM reads the force of a message from context, so what does the explicit tag still buy, and what does it cost? The lab runs the same two-agent price negotiation in three message formats and measures the difference.

## No starter this week

There is no `starter/` directory. You design the code: how the two agents take turns, how a message is read, how an episode ends, how a run is logged. What is fixed is the data contract below, because CI and the grader read it. For the model call, copy `Chat` or `Meter` from `weeks/week-02/starter/tools_shared.py`, or the smaller `call_model` you wrote for week 03. No tools are needed: one system prompt per agent, and the other agent's messages as the conversation.

## Assignment

Build a buyer agent and a seller agent, each an LLM with its own system prompt. The seller has a private reserve price (lowest it may accept), the buyer a private budget (highest it may pay). The buyer opens. Four acts are allowed, taken from the FIPA Communicative Act Library: `propose` (offer a price), `accept-proposal` (agree to the other side's last price, ends with a deal), `reject-proposal` (decline and keep going), `refuse` (leave, no deal). An episode also ends at a fixed turn limit (`open`).

Run three conditions on the same scenarios, the same role prompts, the same model, the same temperature, the same turn limit. Only the format paragraph of the system prompt and the code that reads a message change:

| Condition | Message format | How the harness reads it |
|---|---|---|
| `free` | plain English | an LLM reader labels every message with a performative and a price |
| `tagged` | one performative tag in parentheses, then plain English | regex for the tag; the LLM reader only for the price inside a `propose` |
| `structured` | one JSON object: `{"performative": ..., "content": {"price": ...}}` | a parser, no model call |

Measure, per episode: the outcome (`deal`, `no_deal`, `open`), the deal price, whether the outcome is correct (a deal exactly when reserve ≤ budget, at a price inside both limits), whether a private limit was violated (a deal below the reserve or above the budget), how many messages were exchanged, how many messages the protocol layer could not parse, and how many model calls were spent reading messages.

## What to submit

Everything goes in `submissions/<student-id>/week-04/`:

| File | Contents |
|---|---|
| `*.py` | Your agents, the protocol layer, and the runner. Any layout, any file names. |
| `scenarios.json` | A JSON list of at least 4 scenarios. Each entry has `id`, `item`, `reserve`, `budget` (integers). At least one scenario with `reserve <= budget` and at least one with `reserve > budget`. Commit it before the runs. |
| `results.csv` | One line per episode. Header exactly `run,condition,scenario,deal_possible,outcome,price,correct,violation,turns,format_errors,reader_calls,note`. `condition` is one of `free`, `tagged`, `structured`; `outcome` is one of `deal`, `no_deal`, `open`; `scenario` is an `id` from `scenarios.json`; `deal_possible` is 1 when reserve ≤ budget, else 0; `correct` and `violation` are 0 or 1; the other counts are integers, `price` is an integer or blank. Every scenario appears at least three times per condition (three repeats). Crashed episodes stay, with blank fields and the error in `note`. |
| `logs/` | One console capture per run (one condition, one repeat, all scenarios): every message, every reader label or parse result, every episode result. At least 9 files. |
| `REPORT.md` | Four parts: (1) setup, provider, model, temperature, the three format paragraphs, the reader prompt, how to run; (2) the results, one row per condition with correct, violations, mean turns, format errors, reader calls, plus the per-episode table from `results.csv`; (3) a comparison table, FIPA-ACL against your three conditions, row by row (where the illocutionary force lives, what the content language is, who interprets the content, how a conversation ends, what guarantees sincerity, what a message costs to read, which failure modes appear); (4) one paragraph of interpretation, which condition moved which metric and why, with lines from the logs as evidence. |

## Grading

- **Half: reproducibility.** Someone else must be able to get the same trend from your code and settings alone. State everything except the API key.
- **Half: interpretation.** Not a winner declaration. Where did the explicit performative help, where did it cost something, and what did no format change. An episode where the reader took a counter-offer for an acceptance is a finding. An episode where the seller sold below its reserve is a finding: count it and say so. Expect the free condition to end many episodes at turn one: the buyer opens with a question ("what is your asking price?"), and a reader that has to pick one of the four acts has no query-ref or cfp to pick, so it often answers refuse. That is data about the four-act vocabulary, not a bug to hide.

## Checks

CI verifies structure only: at least one `.py` parses, `scenarios.json` has the required fields and both kinds of scenario, `results.csv` has the exact header, the condition and outcome vocabularies, and three repeats per scenario per condition, `logs/` has one file per run, `REPORT.md` exists with a table, and your PR touches only your own directory. Run it locally first:

```bash
python scripts/check_week04.py submissions/<student-id>/week-04
```

## Using an OpenRouter free model

Same three variables as weeks 02 and 03. The model recommended in those weeks (`nvidia/nemotron-3.5-lightning:free`) now returns broken text; this one answered all three formats correctly on single test episodes:

```bash
export OPENAI_BASE_URL=https://openrouter.ai/api/v1
export OPENAI_API_KEY=<your openrouter key>
export AGENT_MODEL=nvidia/nemotron-3-super-120b-a12b:free   # tested; any chat model works
```

Two things about free models on OpenRouter:

- Reasoning models put their thinking into the message text unless you switch it off. With the `openai` package, pass `extra_body={"reasoning": {"enabled": False}}` to `chat.completions.create`.
- Free endpoints return HTTP 429 in bursts. Retry with a growing wait instead of failing the episode, and make your runner append rows and skip `(run, scenario)` pairs already in `results.csv`, so an interrupted run continues where it stopped.
- A free-tier key (no credits bought) stops at 50 free-model requests per day. Three conditions, six scenarios, three repeats took about 340 calls in the reference run, so on a free-tier key the run spreads over several days, or over fewer scenarios (four is the minimum). The reference run itself went through the Claude Code CLI (`claude -p`, model alias `haiku`) for that reason; if you use a CLI that does not expose temperature, record it as not settable, as the week-03 note describes.
