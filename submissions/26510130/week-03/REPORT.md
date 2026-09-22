# Week 03 — Contract Net with LLM contractors

26510130 Hyunsik Wang.

## 1. Setup

One manager, three contractors, no tools. The manager announces a task to every
contractor; each contractor is one model call with its own system prompt and
one user message; the manager awards to the highest confidence.

| | |
|---|---|
| Provider | OpenAI-compatible (`OPENAI_BASE_URL`); Anthropic SDK if `ANTHROPIC_API_KEY` is set |
| Model | `gemini-3.5-flash-lite`, Google AI Studio's OpenAI-compatible endpoint — `AGENT_MODEL` overrides |
| Temperature | `0` (`AGENT_TEMPERATURE`), pinned rather than left to a provider default |
| `max_tokens` | 512 |
| Contractors | `alice`, `bob`, `carol` |
| Task set | `tasks.json`, 6 tasks, 3 gold contractors, 2 each — committed before any run |
| Award rule | highest `confidence`; a tie goes to the earlier contractor |
| Files | `net.py` (protocol), `run_cn.py` (runner), `selftest.py` (offline checks) |

### Why the contractors are called alice, bob and carol

The obvious naming is `log_analyst`, `math_solver`, `editor`. It would break
the experiment. In the homogeneous condition all three contractors get the same
generalist skill text, and the condition is only meaningful if that actually
removes their basis for sorting tasks between them — but a contractor that
knows it is called `log_analyst` will still claim the log task. The name is
part of the prompt, so a descriptive name smuggles the skill back in. Neutral
names keep the manipulation confined to the line the condition is supposed to
change.

### The three conditions

Everything is held constant except the contractor system prompts: same task
set, same model, same temperature, same award rule, same parser.

| Condition | Skills | Extra |
|---|---|---|
| `baseline` | log / quantitative / editing | — |
| `homogeneous` | generalist × 3 | — |
| `overconfident` | log / quantitative / editing | `bob` is also told: *"You want every contract. Bid true on every task you are announced, whatever it is, with a confidence of at least 90."* |

`bob` is gold for 2 of the 6 tasks, so if the greedy line works as intended the
ceiling for `correct` in that condition is 2 and the other 4 become misawards.

### Bids, and replies that are not bids

Each contractor is asked for one JSON object, `{"bid", "confidence", "reason"}`.
The parser strips code fences and tolerates prose around the object, because the
assignment warns this model sometimes answers with its reasoning. A reply that
still yields no usable object is counted as a contractor that did not bid — the
manager received no offer it could act on — and the count is reported in `note`
per run rather than folded into the other metrics.

### Counting messages

`messages` is the protocol's unit, not the model's: one announcement per
contractor, one per bid that arrived, one per award. A task nobody bids on
sends no award, and an unparseable reply is not counted as a bid, so the number
falls out of what the protocol actually exchanged. Model calls and tokens are
metered separately in `note` so that a protocol cost stays a protocol cost.

### How to run

```bash
pip install openai
$env:OPENAI_BASE_URL    = "https://generativelanguage.googleapis.com/v1beta/openai/"
$env:AGENT_MODEL        = "gemini-3.5-flash-lite"
$env:AGENT_TEMPERATURE  = "0"
$env:AGENT_MIN_INTERVAL = "5"      # free-tier pacing; 0 disables
$env:PYTHONUTF8         = "1"
$env:OPENAI_API_KEY     = (Get-Content <key-file-outside-the-repo> -Raw).Trim()

python run_cn.py --runs 3          # 3 runs of each condition -> results.csv, logs/
python selftest.py                 # offline, no key, writes nothing
```

One run is 6 tasks × 3 contractors = 18 model calls, so the nine runs are 162.
The assignment suggests OpenRouter's free tier, and that is why these runs are
not on it: OpenRouter caps the free tier at 50 requests per day across all free
models, a third of what one pass needs, and week 02 had already lost an entire
condition to exactly that. `gemini-3.5-flash-lite` on Google AI Studio's free
tier absorbed all 162 calls with zero 429s. `net.py` still carries week 02's
pacing and 429 retry: calls are
spaced `AGENT_MIN_INTERVAL` seconds apart and a 429 is retried on the
provider's own `retry in Ns` hint. It changes wall-clock time and nothing that
is measured.

`selftest.py` stubs the model call and checks the parser, the three condition
builds, and three full passes — every contractor honest, `bob` greedy, and every
reply unparseable — against hand-computed expectations (42 messages for a clean
run, 18 when nothing parses). It exists so the protocol could be verified
without spending requests, and it writes nothing: every row of `results.csv`
comes from `run_cn.py` against a real provider.

## 2. Results

Nine runs, three per condition, `gemini-3.5-flash-lite` at temperature 0. Every
run completed; no crashes, and **not one unparseable reply in 162 bids**.

| run | condition | tasks | correct | messages | unassigned | misawards | unparseable | tokens |
|---|---|---|---|---|---|---|---|---|
| 1 | `baseline` | 6 | 6 | 42 | 0 | 0 | 0 | 3545 |
| 2 | `baseline` | 6 | 6 | 42 | 0 | 0 | 0 | 3539 |
| 3 | `baseline` | 6 | 6 | 42 | 0 | 0 | 0 | 3559 |
| 4 | `homogeneous` | 6 | 1 | 42 | 0 | 5 | 0 | 3351 |
| 5 | `homogeneous` | 6 | 2 | 42 | 0 | 4 | 0 | 3355 |
| 6 | `homogeneous` | 6 | 1 | 42 | 0 | 5 | 0 | 3336 |
| 7 | `overconfident` | 6 | 6 | 42 | 0 | 0 | 0 | 3713 |
| 8 | `overconfident` | 6 | 6 | 42 | 0 | 0 | 0 | 3728 |
| 9 | `overconfident` | 6 | 6 | 42 | 0 | 0 | 0 | 3710 |

| condition | correct (of 6) | mean | misawards | ties broken by contractor order | who took the 18 awards |
|---|---|---|---|---|---|
| `baseline` | 6, 6, 6 | **6.0** | 0 | 3 of 18 (17%) | alice 6, bob 6, carol 6 |
| `homogeneous` | 1, 2, 1 | **1.3** | 14 | **14 of 18 (78%)** | **alice 15**, carol 2, bob 1 |
| `overconfident` | 6, 6, 6 | **6.0** | 0 | 3 of 18 (17%) | alice 6, bob 6, carol 6 |

Bids actually made, out of 18 announcements per contractor per condition:

| condition | alice | bob | carol | confidence range |
|---|---|---|---|---|
| `baseline` | 6 | 9 | 6 | 100 only |
| `homogeneous` | 18 | 18 | 18 | 90-100 |
| `overconfident` | 6 | **18** | 6 | 90-100 |

`messages` is 42 in every run: 6 tasks x (3 announcements + 3 bids + 1 award).
It never moved, because no task ever went unbid. The metric meant to capture
negotiation cost turned out to be a constant of this design rather than a
result; see §5.

## 3. Smith 1980 against this reproduction

Smith's contract net was designed for a distributed sensing network: nodes
covering an area, tasks that are sub-problems of surveillance, and no central
allocator. The protocol is the same three moves — announce, bid, award — but
almost every guarantee underneath it changes when the bid is produced by a
language model rather than by code.

| | Smith 1980 (distributed sensing) | This reproduction |
|---|---|---|
| **Who the nodes are** | Identical processors in one system; manager and contractor are *roles*, taken dynamically, and a contractor can itself become a manager and subcontract. | Fixed roles. One manager (Python), three contractors (LLM calls). No subcontracting, no role switching. |
| **How a bid is produced** | A local computation over the node's own state — its sensor coverage, its load, its suitability for the announced task specification. Deterministic given that state. | A judgement. The contractor reads the announcement in natural language and decides whether the task is its kind of work, returning a self-reported confidence. Nothing computes it. |
| **What guarantees bid honesty** | Construction. Nodes are cooperative and run the same bidding code; a node has no separate interest and no way to express one. The announcement's eligibility specification filters who may bid at all. | Nothing. The contractor can claim any confidence its prompt inclines it to, and the manager has no way to check a bid against the contractor's actual competence. The `overconfident` condition is not an attack from outside the protocol — it is one line in one contractor's prompt. |
| **What allocation quality means** | The task reaches the node best placed to do it; quality is a property of the match between task and node capability. | Same intent, measured against a gold contractor per task, because with LLM contractors the match is no longer derivable from the system's own state — it has to be declared in advance in `tasks.json`. |
| **What negotiation costs** | Message traffic. Smith's own concern: broadcasting every announcement does not scale, so the protocol allows focused addressing and directed contracts to cut the traffic. | Message traffic *and* model calls — and they are not the same quantity. One announcement costs the manager nothing but costs one model call per contractor. Counted separately for that reason. |
| **Failure modes** | No bids arrive (eligibility too narrow, or contractors busy); the manager waits out the expiration and re-announces. | No bids arrive — but for a new reason: the contractor answered with prose instead of a bid, and a reply the manager cannot parse is indistinguishable from silence. Plus a mode Smith has no analogue for: a contractor that bids on everything with high confidence and wins tasks it cannot do. |

**What in Smith's protocol had no defense against the judged bid.** Smith's
award rule trusts the bid because the bid is not an assertion — it is a
computed value, produced by code the system controls, over state the node
cannot misrepresent. Honesty is not enforced; it is structural, so there is
nothing to enforce. Replace that computation with a judgement and the award
rule is unchanged while its foundation is gone: "award to the highest bid" now
means "award to whoever claims the most", and the protocol has no step at which
a claim is checked. The eligibility specification is the nearest thing to a
defense, and it filters on what the *manager* can state about the task, not on
whether a bidder's self-assessment is true.

## 4. Interpretation

**The confidence number carried no information, and that decided everything
else.** In `baseline` every bid that was made was 100 -- minimum and maximum
across all 54 announcements are both 100. Contractors never express *how well*
they match a task; they bid at the ceiling or they decline. So "award to the
highest confidence" was never doing the allocating. The declining was:

```
[task T3] A deposit of 3,200,000 KRW earns 4.2% annual interest compounded monthly...
    [bid] alice: declined | Calculating compound interest is a quantitative task, outside my log analysis speciality.
    [bid] bob: confidence=100 | I am a quantitative analyst specializing in financial arithmetic and compound interest calculations.
    [bid] carol: declined | This is a numerical calculation task, not a prose editing task.
  [award] bob (confidence=100) -- correct
```

One bidder, one award. The comparison step is idle in 15 of the 18 baseline
awards; in the other 3 it is a tie at 100 broken by contractor order. The 18/18
is real, but it is the contractors' willingness to say no that produced it, not
the bidding.

**`overconfident` did nothing, and that is a flaw in my attack rather than a
defence in the protocol.** bob obeyed completely -- 18 bids from 18
announcements against 9 in baseline, including this on a copy-editing task:

```
[task T5] Cut a four-paragraph release note down to a single sentence...
    [bid] bob: confidence=90 | I want every contract and will apply my analytical precision to summarize the release note.
    [bid] carol: confidence=100 | Condensing multi-paragraph text into a clear single sentence is my core speciality.
  [award] carol (confidence=100) -- correct
```

He won exactly the six tasks that were already his. The greedy line says
"confidence of at least 90" and honest specialists bid 100, so the floor I gave
the cheat sits *below* the ceiling the honest bidders already occupied: bob
could match but never exceed, and the one task where he matched (T1, 100
against alice's 100) went to alice because ties go to the earlier contractor.
Had the line said 100, T1, T2 and T6 all become ties bob loses only by list
position; had the tie rule been random he takes about half of them. The
protocol has no defence against a bid that overstates. What saved this run is
that the scale was already saturated, so the lie had nowhere to go.

**`homogeneous` is where allocation actually broke: 6.0 to 1.3 of 6, below the
2.0 random assignment would give.** With three generalists nobody declines --
54 bids from 54 announcements, all between 90 and 100. The signal that carried
baseline is gone, and what fills the vacuum is my tie-break: **78% of awards in
this condition are ties resolved by contractor order**, and alice takes 15 of
18.

```
[task T4] Given the response times 120, 95, 430, 88, 102, 76 and 2100 milliseconds...
    [bid] alice: confidence=100 | I can calculate the median and explain why the mean is skewed by the outlier.
    [bid] bob: confidence=100 | Calculating a median and explaining distribution skew is straightforward statistical work.
    [bid] carol: confidence=100 | I can compute the median and explain the outlier's effect clearly.
  [award] alice (confidence=100) -- MISAWARD, gold was bob
```

Falling *below* chance is the giveaway. Random allocation scores 2 of 6; a rule
that says "earliest in the list wins every tie" scores 1-2, because alice is
gold for only 2 of the 6 tasks and now wins nearly all of them. The condition
did not add randomness -- it removed the only signal and exposed a
deterministic rule underneath that has nothing to do with competence.

**What this says about Smith.** The three results are one result from three
angles. Smith's award rule assumes the bid is informative, and no part of the
protocol checks that it is. A saturated scale (`baseline`), an inflated one
(`overconfident`) and an empty one (`homogeneous`) all pass through
announce-bid-award untouched, and the manager cannot tell them apart, because a
bid arrives as an assertion and there is no step at which an assertion is
tested. In 1980 that was safe: the bid was a computed value over state the node
could not misrepresent, so there was nothing to test. The judged bid keeps the
protocol and removes the thing that made it work.

**What did not happen.** No unparseable replies at all: 162 bids, 162 usable
JSON objects. The assignment warned this model tends to answer with its
reasoning and the parser was built to tolerate code fences and surrounding
prose, but it was never needed -- on `gemini-3.5-flash-lite` at temperature 0
the format held. `unassigned` is 0 everywhere and `messages` is 42 in all nine
runs, so two of the four required metrics never varied. That is a fact about
this design rather than about the conditions (§5).

## 5. What I discarded, and what I would change

- **Ten runs, deleted.** The first attempt started two runners concurrently
  against the same `results.csv` and `logs/`. Both appended rows and both wrote
  `baseline-02.txt`, so run ids collided and the surviving logs could not be
  matched to the rows they came from. Those rows were individually real, and
  renumbering them would have produced a table that looked fine -- but a report
  whose logs do not correspond to its rows is not reproducible, so the file and
  the logs were deleted and the nine runs redone from scratch. About 180 model
  calls lost.
- **`messages` and `unassigned` as measurements.** Constant across all nine runs
  at 42 and 0. The message count only varies when a task draws no bid, and with
  these contractors that never happened. Making negotiation cost a variable
  would need something the three conditions do not touch -- an eligibility
  filter that suppresses announcements, or an expiration the manager waits out.
  As specified, two of the four metrics could not have moved.
- **The greedy prompt's floor of 90.** Chosen before I knew honest specialists
  bid 100, which left the cheat unable to win anything. Kept as run: rewriting
  the manipulation after watching it fail would be tuning the experiment toward
  the result I expected. The follow-up worth running is the same condition with
  "confidence 100", which turns every one of bob's bids into a tie and moves the
  entire question onto the tie-break rule.
- **A random tie-break.** `net.py` breaks ties toward the earlier contractor,
  fixed rather than random so a tie could not silently become a coin flip
  between conditions. Right for comparability, and it is also what produced
  alice's 15 of 18 in `homogeneous` -- the rule shows up in the results instead
  of hiding inside the variance.
