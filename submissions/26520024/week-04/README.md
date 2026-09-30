# Week 04: Free, tagged and structured negotiation

Student 26520024 (LeeUichann). A buyer and seller negotiate fictional GPU-slot
prices in integer credits. There are no real purchases, GPU jobs or image tests.
The independent variable is message format, not role instructions or model.

## Measured result

All 36 genuine episodes completed on 2026-09-28, with nine original run logs.
Each condition had 7/12 correct outcomes, zero limit violations and zero format
errors. Reader calls were 85 (free), 79 (tagged), and 0 (structured); mean turns
were 7.08, 7.25, and 7.50. Fifteen unresolved episodes remain in the data.
Structured messages removed interpretation calls, not negotiation failures.
See the Korean [report](REPORT.md) for the full table and log-based explanation.

## Reproduce in existing conda base

Requires Python 3.8+ and an already authenticated Codex CLI; tested setup is base
Python 3.8.19 and CLI 0.153.0 with ChatGPT login. No packages or SDK are installed,
and no API key is extracted or placed in an environment variable. CODEX_BIN can
select the CLI binary. Actor and reader use gpt-6-astra with low reasoning.

```bash
conda activate base
cd /nas/home/uichan/ai-agent-engineering-101/submissions/26520024/week-04
/home/uichan/miniconda3/bin/python -m unittest discover -v
/home/uichan/miniconda3/bin/python run_experiment.py --repetitions 3
/home/uichan/miniconda3/bin/python validate_results.py
cd ../../..
/home/uichan/miniconda3/bin/python scripts/check_week04.py submissions/26520024/week-04
```

The runner requires committed inputs/code and an exclusive lock. It appends
results and skips recorded (run, scenario) pairs, including failed episodes.
After completing three repeats, rerunning the command makes no model calls.
`--repetitions 4` adds a new full repeat without overwriting earlier evidence.
Run IDs are free-01, tagged-01, structured-01, then the next repetition.
Each of nine logs covers all four scenarios; CSV has at least 36 episode rows.
Transport crashes stop the batch after saving blank measured fields and the
error. Resume proceeds to unfinished pairs, not a replacement for the crash.
Rate-limit retries are bounded and logged; parse errors never trigger retries.

## Fixed experiment

[scenarios.json](scenarios.json), [prompts.json](prompts.json) and
[DESIGN.md](DESIGN.md) are committed before live calls. S1 has overlapping
limits, S2 equal limits, S3/S4 incompatible limits. Buyer opens; eight messages
maximum. Each actor sees its own private limit plus public history only.

| Condition | Actor format | Protocol layer |
|---|---|---|
| free | Plain English | Same-model observer labels every utterance |
| tagged | Parenthesized act + English | Regex reads act; observer reads propose price only |
| structured | JSON act + content.price | Strict parser; zero observer calls |

The observer receives the full public transcript but neither private system
prompt. Its system prompt is identical across free/tagged. In tagged, only its
price is used; its act label cannot override the explicit tag. Acceptance uses
the opponent's latest parsed proposal, not any price written in the acceptance.
Invalid messages are still delivered verbatim and consume a turn. A no-offer
acceptance is a protocol error, not a fabricated transaction.

Correct = valid-price deal for a feasible scenario, or explicit no_deal for an
infeasible one. Open is always incorrect. Violation counts only an agreed price
outside reserve/budget. The evaluator does not preempt unsafe agreements.
Turns count actor messages. reader_calls counts reader CLI attempts, including
explicit rate-limit retries; note also records actor calls, retry count, usage
and wall time. Tokens include CLI overhead; cached input is not double counted.
Failed transport attempts may lack provider usage and are not assigned fake tokens.

## CLI limitations and evidence

Temperature/max output tokens cannot be set by this adapter; internal values
are unknown. System/history are embedded in one fixed CLI request, not native
API roles. Fresh ephemeral, tool-disabled invocations avoid shared conversation
state; raw tool-action events are rejected. No JSON output schema is imposed.
See [official OpenAI documentation](https://learn.chatgpt.com/docs/non-interactive-mode)
for the non-interactive CLI interface. No model upgrade or provider substitution
is performed during the experiment.

- [REPORT.md](REPORT.md): Korean report with full results and interpretation.
- [results.csv](results.csv): measured episodes, including crashes if any.
- [logs/](logs/): append-only exact requests, raw model responses and protocol events.
- [PROCESS.md](PROCESS.md): implementation assistance and chronological decisions.
- `validate_results.py`: offline replay of prompts, traces, states and counts.
- `test_negotiation.py`: synthetic tests isolated in temporary directories.
- `test_validation.py`: replay and evidence-tampering regression tests.
- [verification/](verification/): offline test and actual-evidence validation output.

Course source: [assignment](../../../weeks/week-04/README.md) and
[lecture](../../../week-04.html). This implements the four-act exercise, not full
FIPA-ACL conformance or an enforceable contract. No pull, push or PR is performed.
