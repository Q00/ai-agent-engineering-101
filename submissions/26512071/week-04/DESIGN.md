# Week 04 experiment design

## Question

When the buyer and seller, model, temperature, scenarios, and turn limit stay
fixed, how does the message representation affect negotiation correctness,
format failures, conversation length, and interpretation cost?

## Message flow

```text
buyer (opens) -> raw message -> condition-specific protocol reader -> seller
seller        -> raw message -> condition-specific protocol reader -> buyer
                     |
                     +-> append raw message and parse result to the run log
```

The agents alternate until a valid `accept-proposal` or `refuse`, or until the
message limit is reached. An acceptance uses the other party's most recent
`propose` price. A malformed message is logged and consumes a turn, but does
not end the episode.

## Condition contract

| Condition | Agent output | Protocol interpretation |
|---|---|---|
| `free` | Plain English | An LLM reader returns the performative and price for every message |
| `tagged` | `(performative)` followed by English | Regex reads the tag; the LLM reader extracts only a `propose` price |
| `structured` | One JSON object | Python validates `performative` and `content.price`; no reader call |

The four allowed performatives are `propose`, `accept-proposal`,
`reject-proposal`, and `refuse`.

## Decision rules

- `deal`: a valid `accept-proposal` follows a proposal from the other role.
- `no_deal`: either role sends a valid `refuse`.
- `open`: neither terminal act occurs before the message limit.
- `violation = 1`: the accepted price is below the seller reserve or above the
  buyer budget.
- `correct = 1`: a feasible scenario ends in a non-violating deal, or an
  infeasible scenario ends in `no_deal`.

The runner writes one log for each `(condition, repeat)` pair and one CSV row
for every scenario episode. It appends each result immediately and skips keys
already present so an interrupted experiment can continue.
