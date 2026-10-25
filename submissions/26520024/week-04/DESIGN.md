# Frozen week-04 experiment design

Student 26520024, LeeUichann. Synthetic GPU-slot negotiations in integer credits;
no GPU jobs, bookings, payments, or image inference are performed. This design,
scenarios and prompts are committed before any live model calls.

## Controls

Four scenarios: S1 has an overlap (80..120), S2 a boundary-only overlap (60),
S3 and S4 no overlap. Three conditions x three repeats x four scenarios = 36
planned episodes; nine run logs. Order: free, tagged, structured within each
repeat, with S1..S4 in each run. Fixed ordering is a limitation, not randomized.
Buyer opens; maximum eight messages total. Same role/common prompts, model,
reasoning setting and reader prompt throughout. Only the actor format paragraph
and protocol reader differ. There are no pilot trials or post-result tuning.

OpenAI through existing authenticated Codex CLI 0.153.0, gpt-6-astra, reasoning
low. Existing conda base Python 3.8.19, standard library only; no installations.
Temperature/max output tokens: not settable by this adapter, internal values
unknown. System/history roles are embedded in a fixed CLI request, not native
API messages. Fresh ephemeral scratch directory, ignored user config, native
tools disabled and tool actions checked. No constrained JSON output schema:
format failure is part of the experiment. Same backend for actors and reader.

Each actor sees only its own role prompt and public message history. Its own
utterances become assistant messages; the other side's become user messages.
The reader sees only the public transcript, never either private system prompt.
The buyer receives a neutral opening instruction, not a fixed offer or question.
Agents may voluntarily leak their own limit in public text; record, do not repair.

## Protocol and measurement

- free: one reader request for every actor message, labeling act and price.
- tagged: one leading valid tag via regex; reader only for propose price, using
  the same observer prompt/transcript as free. Ignore its act label in this case.
- structured: strict JSON only, exactly performative and content.price, no reader.
- Reject duplicate JSON keys, nonfinite numbers, code fences, extra fields,
  unknown acts, noninteger/negative proposal prices and boolean prices. No repair.
  Non-proposal price may be null or a nonnegative integer but is ignored.
- Invalid messages still enter both histories unchanged, increment format_errors
  and consume a turn. An acceptance without an opponent proposal is also a
  protocol format error and does not invent a deal. Rejections keep negotiations
  open and do not erase stored proposals. Latest valid proposal per party persists.
- Acceptance uses the OTHER party's last parsed proposal, not an acceptance's
  stated price. A contradictory tagged/JSON message is not silently reinterpreted.
- Never block out-of-limit offers or agreements using evaluator knowledge:
  private-limit violations must remain observable. Evaluate only after the episode.
- deal: correct iff reserve <= price <= budget; violation iff outside either limit.
  no_deal: correct iff reserve > budget. open: always incorrect, including an
  infeasible scenario (unresolved is not an explicit successful refusal).
- turns counts actor messages, not reader traffic. reader_calls counts actual
  reader CLI attempts, including explicit rate-limit retries. Record actor calls,
  input/output tokens, retries and wall time in note; input tokens include cached
  input once and CLI overhead. These are not monetary-cost measurements.

## Failures, resume and evidence

Model timeout 180 seconds. Retry only identifiable HTTP 429/rate_limit_exceeded,
up to three retries with 2/4/8-second waits; log every attempt. Never retry a bad
JSON message or an undesired negotiation outcome. Other failures retain partial
evidence and a crashed CSV row with blank measured fields, then stop the batch.
Restart skips all recorded (run, scenario) pairs, including crashes. Add a new
repeat for additional attempts; never erase failed rows. A completed log result
missing from CSV after interruption is restored verbatim; an unfinished episode
is marked crashed on resume. Corrupted partial JSON lines require explicit review.
An exclusive process lock prevents concurrent writers. Logs are append-only.

One JSON-lines log per condition/repeat contains config, hashes, every exact
model request/raw output, actor message, reader label, parse, state and result.
Offline tests use temporary fake replies only; no fake results enter results.csv.
An offline replay validator checks public/private separation, transport events,
parsing and state transitions, usage and metrics against every CSV row.

## Scope and interpretation

No assumption that structured wins. Missing query acts in the four-act vocabulary,
reader misinterpretations, misleading tags, syntax failures, unresolved rounds
and limit violations are outcomes to preserve. Inspect actual logs, do not force
a failure or fabricate one. Four synthetic scenarios and three repeats do not
establish universal negotiation quality. No pull, push or PR in this work session;
only path-scoped local commits. Existing staged week-02/ARCHITECTURE.md is excluded.
