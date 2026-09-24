# Week 04 follow-up: mediator architecture

This is an exploratory extension. The graded three-condition run remains in
`results.csv` and `logs/`; this experiment writes only `mediator_results.csv`
and `mediator_logs/`.

## Why change the architecture?

The baseline's `correct` column mixes at least three mechanisms: strategic
convergence by the two LLMs, act/price interpretation by a reader, and the
eight-message cutoff. In the observed runs, `free` had 0 explicit acceptances
out of 12 episodes, and `structured` had 39/39 seller `reject-proposal`
messages with a null price. Those are strategy/action-space problems. The
reader is another independent source of error: in `free-3`, the buyer's
"I can only go up to 380. Would you accept that?" was read as acceptance of the
seller's standing 430 offer, producing a limit violation even though the
sentence stated 380.

The baseline's structured schema also has only one `content.price` slot and
the runner records prices only when the act is `propose`. A rejection carrying
a counter-price is therefore either not expressible in practice or discarded
by the protocol. This limits bargaining behavior. Finally, 19/36 episodes
were `open`, with mean turns near the eight-message limit; outcome quality is
therefore heavily censored by the horizon.

## New architecture

Buyer and seller still make LLM-generated price proposals, but communicate
through a neutral mediator. Each party sees its own private limit and the
previous offers, while the mediator knows both private limits. A deterministic
parser extracts an integer offer. The mediator rejects malformed offers and
offers beyond the proposer’s limit. Once both offers exist and buyer offer is
at least seller offer, it closes at the integer midpoint. If no overlap is
reached within eight messages, the result is `open`.

This is a centralized mechanism, not a like-for-like test of message formats.
It deliberately provides stronger safety guarantees by revealing private
limits to the mediator and imposing a settlement rule. Any improvement cannot
be attributed to tags or parsing alone. Conversely, it tests whether a
protocol-level intermediary can prevent reader misclassification and close a
feasible deal without asking one party to produce a natural-language
acceptance.

## Results

The mediator condition is intended to use the same four scenarios and three
repeats as the baseline. The result table is kept separate because its
`format_errors` column counts invalid mediator offers, whereas the baseline
column counts messages whose performative could not be read. Compare outcomes,
violations, and turns directly; do not interpret the two error columns as the
same measure.

| condition | correct | violations | mean turns | format errors | reader calls |
|---|---:|---:|---:|---:|---:|
| free (baseline) | 5/12 | 1 | 7.1 | 0 | 85 |
| tagged (baseline) | 12/12 | 0 | 6.8 | 0 | 39 |
| structured (baseline) | 9/12 | 0 | 7.0 | 0 | 0 |
| mediator, deterministic fake mechanics check | 12/12 | 0 | 7.75 | 0 | 0 |

The deterministic fake mechanics check produced 12/12 correct outcomes, no
limit violations, and 7.75 mean turns. The two feasible scenarios each
closed in seven or eight turns; the impossible scenarios correctly ended
without a deal. This confirms the harness and settlement rule for scripted
offers only. It says nothing about the LLM mediator design's effectiveness.
Per-episode output for that run is in `mediator_results_policy2.csv`; its full
transcripts are in `mediator_logs_policy2/`. Earlier offline attempts are also
retained in `mediator_results.csv` / `mediator_logs/` and
`mediator_results_retry.csv` / `mediator_logs_retry/`; the first exposed a
prompt-formatting bug and the second an overly slow fake offer schedule. They
are preserved as process evidence, not pooled into the final fake summary.

The live run is pending because this environment has no `OPENAI_API_KEY`.
The script defaults to the baseline runner's `gpt-4o-mini` API settings and
does not read a key from disk. Do not interpret the fake result as the
mediator's comparison against the baseline; run the live command below with
the same provider/model configuration first.

## Reproduction

Run the same environment setup used for `runner.py`, then:

```powershell
python mediator_experiment.py --all
```

The run is resumable by `(run, scenario)`. For a deterministic offline
mechanics check:

```powershell
python mediator_experiment.py --all --fake
```

Record provider, endpoint, model, temperature, and max token settings from the
run logs before drawing a comparison. This design adds mediator access to
both limits, offer validation, and a midpoint rule; those are explicit
confounds relative to the assignment's three conditions.
