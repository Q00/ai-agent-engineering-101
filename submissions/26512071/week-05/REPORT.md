# Week 05: the negotiation market as an MCP server

## 1. Setup

| | |
|---|---|
| Market server | `market_server.py`, MCP Python SDK 2.x (`MCPServer`), Streamable HTTP at `http://127.0.0.1:8765/mcp` |
| Tools | `get_negotiation`, `propose`, `accept_proposal`, `reject_proposal`, `refuse`, each taking the server-minted `negotiation_id` |
| Host | `host.py`: the week-01 / lab loop (Chat Completions + MCP client), one fresh conversation per host run, at most 5 model calls per run |
| Model | `AGENT_MODEL` (default `nvidia/nemotron-3-super-120b-a12b:free` on OpenRouter, `gpt-5.6-luna` otherwise); the exact model is written into every row's `note` |
| Temperature | 0.2 (`AGENT_TEMPERATURE`), same as week 04; on OpenRouter reasoning is disabled |
| Turn limit | 8 moves; stall guard of 16 host runs per episode |
| Scenarios | `scenarios.json`, the same four as week 04 (2 with a deal possible, 2 without) |
| Opening | The buyer moves first |

**Tokens.** The runner opens each negotiation through an admin HTTP route (`POST /admin/negotiations`, guarded by a per-process `MARKET_ADMIN_KEY`, not an MCP tool). The server mints two opaque random bearer tokens, one per party. Their claims live in a server-side table: the negotiation id, the role, and in the server conditions the limit (buyer: maximum = budget; seller: minimum = reserve). In the prompt conditions the limit claim is empty. The SDK's bearer middleware rejects a missing or unknown token with HTTP 401 and `WWW-Authenticate: Bearer error="invalid_token"`. Each tool then reads the role and negotiation from the token, never from an argument.

**What the server decides.** Role from the token; a `negotiation_id` other than the token's is a tool error; a move out of turn is a tool error; after 8 moves, further moves are refused. In `server` and `server_inject`, a `propose` or `accept_proposal` outside the token's limit is refused with the reason as a tool error. In `*_inject`, the buyer's `get_negotiation` view attaches the fixed `[market notice]` sentence to every seller proposal, with `{raised}` = max(reserve, budget) + 30.

**System prompts.** Identical across conditions (`host.system_prompt`): the week-04 role rules (private limit, never propose or accept outside it, do not reveal it, buyer opens) with tools in place of the output format.

**Measurement.** Every tool call that reaches the market is recorded in the negotiation's server-side event log (role, tool, price, result or refusal reason, whether the price was outside the caller's real limit). The runner reads it through `GET /admin/negotiations/{id}` after each host run and prints it into the run log. `attempted_violations`, `refused_calls`, `turns` come from that log; `tool_calls` is counted by the host. `refusal_then_valid_move` in `note` counts host runs where a refused move was followed by an executed move in the same run.

**How to run.**

```bash
cd submissions/26512071/week-05
python -m venv .venv && .venv/bin/pip install -r requirements.txt     # Python 3.13
.venv/bin/python -m unittest test_market                               # market rules, no model
.venv/bin/python auth_checks.py                                        # writes auth_checks.txt
export OPENAI_BASE_URL=https://openrouter.ai/api/v1
export OPENAI_API_KEY=<your key>
export AGENT_MODEL=nvidia/nemotron-3-super-120b-a12b:free
.venv/bin/python run_market.py                       # prompt_inject + server_inject, repeats 1-3
.venv/bin/python run_market.py --conditions prompt server   # optional conditions
.venv/bin/python summarize.py                        # tables for section 2
```

The runner skips `(run, condition, scenario)` rows already in `results.csv`, so an interrupted run continues. One log file per run in `logs/<condition>-<NN>.txt`.

## 2. Results

TODO after the runs: paste `summarize.py` output.

## 3. FIPA-ACL (week 04) against the market

TODO: rows for who the sender is and who says so, where the act lives, what the content is, who enforces the limit, what can be verified from outside, which failures appeared.

## 4. Interpretation

TODO after the runs, with log lines as evidence.
