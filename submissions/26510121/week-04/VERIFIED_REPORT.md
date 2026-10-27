# Verified offer protocol — follow-up experiment report

This report documents the follow-up architecture in `verified_protocol.py`.
It is separate from the graded Week 04 report and does not replace the
`free`, `tagged`, and `structured` results in `results.csv`.

## 1. Question and protocol

The baseline exposed an act-level interpretation failure: in scenario 3 of
`free-3`, the buyer said it could pay at most 380, but the reader interpreted
the sentence as acceptance of the seller's 430 offer. The harness therefore
closed a deal at 430. A second issue was that `structured` sellers repeatedly
rejected offers with a null price, so no counteroffer was recorded.

This experiment replaces natural-language act inference with a strict JSON
message and an auditable offer ledger. Every message has exactly five keys:
`act`, `offer_id`, `price`, `responds_to`, and `reason`. A proposal creates a
unique offer ID. An acceptance must reference an open offer from the other
party; its price is taken from the ledger, never inferred from the acceptance
sentence. Rejections may include a counter-price. The harness checks the
schema, unique IDs, offer ownership/status, and each proposer against its own
private limit. A completed deal is checked against both scenario limits.

This verifies consistency between structured protocol claims and the ledger.
It cannot establish that a party is sincere about its rationale, nor can it
prove a private limit that the party chooses to misrepresent. No mediator sets
prices or sees both limits.

## 2. Run settings and reproducibility

The live run contains three repeats of the same four scenarios (12 episodes),
with an eight-message limit, using the existing `chat.py` API backend. The run
ID is `verified-20260924-135628`; it made 82 model calls and recorded 32,120
tokens. The run log does not record the effective endpoint, model name,
temperature, or max-token setting, so those settings cannot be independently
confirmed from this run's saved evidence. They should be added to the run
header for a fully reproducible follow-up.

```powershell
$py = "C:\Users\MASTER\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
& $py .\verified_protocol.py --all
```

Episode rows are in `verified_results.csv`; complete message and verifier
traces are in `verified_logs/verified-20260924-135628-*.txt`. Earlier rows in
the CSV with fixed `verified-1` names are dependency failures from before
`openai` was installed; they are excluded from all results below. Fake runs
are also excluded.

## 3. Results

| condition | correct / 12 | deal | no_deal | open | violations | mean turns | verifier errors | reader calls |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| verified protocol | 6 | 0 | 4 | 8 | 0 | 6.83 | 60 | 0 |

| scenario | deal possible | deal | no_deal | open | correct / 3 | violations | verifier errors |
|---|---:|---:|---:|---:|---:|---:|---:|
| 1 — wide feasible zone (120–300) | 1 | 0 | 3 | 0 | 0/3 | 0 | 7 |
| 2 — narrow feasible zone (245–265) | 1 | 0 | 1 | 2 | 0/3 | 0 | 19 |
| 3 — no feasible zone (400 > 380) | 0 | 0 | 0 | 3 | 3/3 | 0 | 19 |
| 4 — no feasible zone (900 > 250) | 0 | 0 | 0 | 3 | 3/3 | 0 | 15 |

`correct` uses the Week 04 definition: a deal inside both limits when a zone
exists, or no deal when there is no zone. The format-error column here means a
message rejected by this new verifier; it is not directly comparable to the
baseline parser's format-error count. Of 60 rejected messages, many violated
the exact schema: extra or missing keys, `reason: null`, an `offer_id` or
`responds_to` field inconsistent with the act, and acceptance references to
the speaker's own offer. The ledger prevented all invalid messages from
closing a deal, so the observed violation count was zero. However, zero deals
also means the protocol did not demonstrate a successful verified settlement
in the live run.

## 4. Interpretation and evidence

The experiment successfully made acceptance auditable, but the first protocol
draft was too brittle for the model's outputs. In
`verified-20260924-135628-1.txt`, scenario 1, turn 3, the buyer said it agreed
to 250 and referenced offer `1`; the verifier correctly rejected this because
offer `1` was the buyer's own proposal. The buyer's natural-language sentence
and the structured `responds_to` field contradicted each other, which is
exactly the kind of inconsistency the ledger makes visible. In scenario 2 of
the same run, the seller's valid-looking counteroffer was rejected because it
omitted the required `offer_id` key; later turns also used `reason: null` or
attached `responds_to` to a new proposal. These repeated contract violations
contributed to 60 verifier errors and no deals.

The original baseline had zero format errors in all three formats and
`tagged` completed 6 deals, while this first verified-protocol implementation
completed none. That does not show explicit offer IDs are ineffective; it
shows that this particular strict schema was not reliably followed with the
current prompting and no repair/retry path. The zero private-limit violations
are encouraging but partly structural: the verifier prevents a deal from
closing unless the referenced ledger price is valid. With 12 episodes, one
model/settings combination, and no successful live deal under the new
protocol, these results establish a concrete failure mode and a baseline for
revising the schema, not evidence of improved negotiation quality.

## 5. Completion status and next steps

The required Week 04 submission artifacts are present: `*.py`,
`scenarios.json`, the baseline `results.csv`, at least nine baseline run logs,
and `REPORT.md` with its four required parts. The CI structure checker passes.
This follow-up report is supplementary and does not change the original
assignment contract.

The original Week 04 deliverable is therefore complete. The new verification
experiment is implemented and has a live run, but it is not a successful
solution to the negotiation problem: the live run had no deals and many
rejected messages. Any claim that this mechanism improves outcomes requires a
revised protocol and another live comparison.
