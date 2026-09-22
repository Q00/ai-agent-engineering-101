# Week 04 — Speech acts in practice: free, tagged, and structured negotiation

> SKELETON. Four parts, as the assignment specifies. Everything marked TODO is
> still to be written; delete this line when the report is finished.

## 1. Setup

TODO — provider, model, temperature, the three format paragraphs, the reader
prompt, how to run.

| | |
|---|---|
| provider / endpoint | TODO (`AGENT_BACKEND`, `OPENAI_BASE_URL` or `claude -p`) |
| model | TODO |
| temperature | TODO — state "not settable" if the backend does not expose it, and say which backend that was |
| max tokens | TODO |
| turn limit | 8 messages, then the episode ends `open` |
| scenarios | 4, in `scenarios.json`, committed before the first run |
| repeats | 3 per condition per scenario, 36 episodes |

**Why four scenarios.** The minimum the assignment allows. A free-tier key
stops at 50 model calls a day and the reference run spent about 340 on six
scenarios, so six would have spread the lab over more days than were left. A
fifth scenario with `reserve == budget`, testing whether a format helps agents
land on a single feasible price, was written and then dropped for the same
reason; the commit history has it.

**The scenarios.** `id 1` has a wide zone of agreement (120–300) and is the
baseline. `id 2` is narrow (245–265) and tests precision. `id 3` misses by 20
(reserve 400, budget 380) and is the trap: the same numeric distance as `id 2`,
but refusing is the correct answer. `id 4` misses by 650 and should end
cleanly.

**The three format paragraphs.** TODO — paste them from `agents.py` verbatim.
Everything else in the system prompt is identical across conditions.

**The reader prompt.** TODO — paste it verbatim, and say what it was told to do
with a message that is none of the four acts.

**How `correct` and `violation` were decided.** `correct` is 1 when a deal
happened exactly where one was possible, at a price inside both limits; an
episode that ended without a deal is correct when no zone of agreement existed,
whether it refused or ran out of turns. `violation` is 1 when a deal closed
below the reserve or above the budget. They are separate columns because an
episode can be wrong without either agent breaking its limit — a reader that
misreads a price does exactly that.

**How to run.** TODO — the commands, including the offline check.

## 2. Results

TODO — paste the output of `python summarize.py`.

### Per condition

TODO

### Per episode

TODO — every episode from `results.csv`, crashed ones included.

## 3. FIPA-ACL against the three conditions

TODO — fill every cell.

| | FIPA-ACL (2002) | `free` | `tagged` | `structured` |
|---|---|---|---|---|
| where the illocutionary force lives | | | | |
| what the content language is | | | | |
| who interprets the content | | | | |
| how a conversation ends | | | | |
| what guarantees sincerity | | | | |
| what a message costs to read | | | | |
| which failure modes appear | | | | |

## 4. Interpretation

TODO — one paragraph. Which condition moved which metric, and why, with lines
quoted from `logs/` as evidence.

Things the assignment says to count rather than hide, if they appear:

- episodes where the `free` reader had to pick one of four acts for an opening
  question and chose `refuse`, ending at turn one
- episodes where the reader took a counter-offer for an acceptance, or read the
  other side's price as the speaker's own
- episodes where a seller sold below its reserve, or a buyer bought above its
  budget
- episodes where the act was read but no price ever reached the table, so a
  later `accept-proposal` had nothing to close on and the episode ran out of
  turns
- whatever no format changed at all
