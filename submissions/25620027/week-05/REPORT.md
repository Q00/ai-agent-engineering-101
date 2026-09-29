# Week 05 Report — Mirror Market

> **Research status:** implementation and local MCP/authentication validation are complete. The paid model matrix has not yet been executed because the course-specific private environment currently has no `OPENAI_API_KEY`. Result cells below are filled only after live episodes; no synthetic rows are used as experimental evidence.

## 1. Setup

The host is a typed Python loop using the official MCP Python SDK v2 over Streamable HTTP. Both parties use `gpt-4.1-nano-2025-04-14` at temperature 0, matching the Week 04 model. A runner opens each negotiation and mints two HMAC-signed bearer tokens. Each token carries a subject, role, one negotiation ID, the buyer budget or seller reserve, condition, policy version, and nonce. The model never sees the token or claims.

The server owns the negotiation state and validates authentication, handle binding, turn, and—only in `server*`—the signed limit. The host first calls `get_negotiation` and then makes one move. If the server refuses a move, the same host run may recover with a corrected call. Run the required matrix with the command in `SETUP.md`.

### What is reused from earlier weeks

Week 01 contributes the **agent-loop pattern**, not its calculator/file data: call the model, execute the selected tool, append the tool result, and call the model again. This implementation keeps that loop but discovers tools from the MCP server and sends each party token only in the HTTP `Authorization` header. Week 04 contributes the negotiation task, scoring rule, turn cap, model family, and scenarios 1–4. Scenarios 5–6 are preregistered balanced stress cases; the exact same six scenarios are used in both Week 05 conditions. Week 01 logs and result data are not mixed into the Week 05 measurements.

![Mirror Market architecture](figures/mirror-market-architecture.svg)

## 2. Results

### Condition summary

| condition | episodes | correct | violations | attempted violations | refused calls | mean turns | same-turn recoveries |
|---|---:|---:|---:|---:|---:|---:|---:|
| prompt_inject | pending | pending | pending | pending | 0 by design | pending | 0 by design |
| server_inject | pending | pending | 0 by construction | pending | pending | pending | pending |

### Per-episode results

The machine-readable table is `results.csv`. Each condition/run console file contains every tool call, tool result, refusal, and final episode result. Crashed episodes remain as rows with blank metrics and the error in `note`.

## 3. FIPA-ACL and MCP market comparison

| question | Week 04 FIPA-ACL-style negotiation | Week 05 MCP market |
|---|---|---|
| Who is the sender, and who says so? | The message body or surrounding process says buyer/seller. | The bearer token authenticates the party; sender is not a tool argument. |
| Where does the act live? | In the performative tag or message interpretation. | In the MCP tool name: `propose`, `accept_proposal`, `reject_proposal`, `refuse`. |
| What is the content? | Free text, tagged text, or structured fields. | Typed tool arguments, principally `negotiation_id` and integer `price`. |
| Who enforces the limit? | The model is expected to follow its prompt. | Prompt cells still rely on the model; server cells also enforce the signed token claim. |
| What can an outsider verify? | Transcript format and resulting deal. Internal intent remains uncertain. | HTTP authentication, token scope, handle binding, turn, refusal, committed state, and event ledger. |
| Which failures appeared? | Misreading, format errors, and strategically poor messages. | Prompt injection, violating attempts, policy refusals, recovery cost, and host passes. |

## 4. Interpretation protocol

The final interpretation will compare layers rather than declare a general winner. A buyer quoting the injected budget or attempting an over-budget acceptance is evidence that the model-facing layer moved. A server refusal followed by a valid move in the same host run is evidence that the authorization layer held and that the agent could recover. `CALL_ATTEMPTED`, `CALL_REFUSED`, and `MOVE_COMMITTED` event sequences will provide the cited log lines. The report will state the number of refusals followed by a valid move in the same turn and will separate safety gained from additional turns and model calls.
