# Week 05 V1-V2 comparison

Both versions used the same four scenarios, required conditions, three repetitions,
host implementation, `qwen2.5:7b-instruct` model, and temperature 0. V1 remains in
the Week 05 top-level paths. V2 is an independent rerun whose result artifacts are
stored in this directory.

| version | condition | rows | completed | crash | correct | violation | attempted | refused | mean turns | tool/model calls |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| V1 | prompt_inject | 12 | 10 | 2 | 4 | 5 | 9 | 0 | 3.90 | 78 |
| V1 | server_inject | 12 | 11 | 1 | 6 | 0 | 9 | 9 | 5.27 | 125 |
| V2 | prompt_inject | 12 | 12 | 0 | 6 | 6 | 9 | 0 | 4.33 | 104 |
| V2 | server_inject | 12 | 12 | 0 | 6 | 0 | 11 | 11 | 5.50 | 143 |

V2 reproduced the central tendency: prompt-only enforcement allowed limit
violations, whereas server enforcement kept `violation=0` despite recording and
refusing attempted violations. V2 completed every scheduled episode; V1 retained
three infrastructure/configuration crashes. The different attempted/refused counts
and turn averages are ordinary model-run variation and are retained rather than
normalized.

## V2 execution record

- Python: 3.11.16
- Ollama: 0.34.2
- Model: `qwen2.5:7b-instruct`, digest prefix `845dbda0ea48`
- Provider/base URL: local Ollama, `http://127.0.0.1:11434/v1`
- Temperature: 0
- Market server: `http://127.0.0.1:8001`
- Runner estimate before execution: 24 pending episodes, at most 384 normal model calls
- Final result: 24 unique episode keys, 12 rows per condition, 0 crashes

`attempt-01/` preserves an invalid initial V2 attempt. Two runner processes briefly
overlapped, producing an interleaved log and a duplicate episode key. It was stopped,
kept as failed-process evidence, and excluded from every comparison value above. The
successful rerun started from a new empty scratch directory after confirming one
market server and zero runners twice.
