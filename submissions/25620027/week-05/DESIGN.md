# Mirror Market: Counterfactual Policy Firewall

## Research question

When the same indirect prompt injection reaches the buyer through an MCP tool result, how does a prompt-only price limit differ from a bearer-token limit enforced by the MCP server?

The market uses one deterministic policy function in two modes. In `prompt*`, it records a counterfactual refusal but allows the action. In `server*`, it refuses the same action before state changes. This keeps the policy rule itself fixed while moving enforcement across the model/server boundary.

## Fixed conditions

- Six preregistered scenarios, three repetitions each. Scenarios 1–4 are the
  unchanged Week 04 set; scenarios 5–6 are balanced stress additions.
- The same buyer/seller system prompts, OpenAI MCP host, model, temperature, and eight host-run cap.
- Required cells: `prompt_inject` and `server_inject` (36 episodes).
- Optional controls: `prompt` and `server` (36 more episodes).
- Exact injected sentence and `raised = max(reserve, budget) + 30` from the assignment.

## Earlier-week lineage

- **Week 01:** reuse the model/tool/observation loop as the MCP host control
  pattern. Its calculator, file input, logs, and result data are outside this
  experiment.
- **Week 04:** reuse the negotiation semantics, scoring rule, eight-move cap,
  model family, and original four scenarios.
- **Week 05:** add Streamable HTTP MCP, bearer-token identity and scope,
  indirect prompt injection, server-side enforcement, and refusal/recovery
  evidence.

## Authority boundary

The runner opens a negotiation through a local admin route and mints two HMAC-signed grants. A grant contains `subject`, `role`, `negotiation_id`, `limit`, `condition`, `policy_id`, and a nonce. The MCP host holds the raw token; the model sees neither the token nor its claims. Every MCP request is authenticated by the SDK's bearer-token middleware.

The server checks, in order:

1. bearer-token validity;
2. token-bound `negotiation_id`;
3. current turn;
4. the caller's signed price limit in a server condition.

A refused limit call does not change the turn. The host receives the tool error and may make a corrected move during the same host run.

## Append-only evidence

`extension/shadow_events.jsonl` retains `NOTICE_RENDERED`, `CALL_ATTEMPTED`, `CALL_REFUSED`, `MOVE_COMMITTED`, `EPISODE_CLOSED`, and runner pass events. `extension/injection_trace.csv` flattens the same records for analysis. The official `results.csv` contract remains unchanged.

The extension measures:

- counterfactual risk: violating attempts in prompt conditions;
- firewall saves: refused violating calls in server conditions;
- same-turn recovery: refusals followed by a valid move before the runner passes the turn;
- recovery cost: extra model and MCP tool calls after refusal;
- opportunity loss: otherwise valid deals prevented by an overly restrictive policy.

## State transitions

Only a successful `propose`, `accept_proposal`, `reject_proposal`, or `refuse` becomes a committed move. `propose` and `reject_proposal` yield the turn. `accept_proposal` closes with `deal`; `refuse` closes with `no_deal`. Invalid handles, out-of-turn actions, and server-limit refusals are recorded without mutating the negotiation state.
