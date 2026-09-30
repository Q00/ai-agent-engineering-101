# Free-tier run (cohere/north-mini-code:free)

The first full sweep, kept as a second data point rather than discarded.

Why it is not the main result: the OpenRouter free-model daily cap (1000
requests) ran out partway through, and five runs died with 429 -- one on the
per-minute limit (15/min) during `homogeneous`, then four of the six `bias+ib`
runs on the daily cap. The `bias+ib` arm therefore has only 2 valid runs.

What it is still good for: the same protocol, the same tasks and the same
prompts on a much weaker model. Comparing it against the main sweep separates
what the contract net does from what the model does -- `judge_disagree` in
particular was high here, and whether that survives a stronger model is a
question the two sweeps together can answer.

Model: cohere/north-mini-code:free, temperature 0.7, seed base 20260921.
The README's suggested model (nvidia/nemotron-3.5-lightning:free) was not
usable at all: 3/3 probes timed out at 75s.
