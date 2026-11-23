# Week 05 — Running the market experiment

Submission folder: `submissions/25522018/week-05/`.

## Real LLM experiment

In terminal A (PowerShell equivalents: `$env:MARKET_ADMIN_KEY="..."`):

```bash
export MARKET_ADMIN_KEY='choose-a-long-random-local-secret'
python market_server.py
```

In terminal B:

```bash
export MARKET_ADMIN_KEY='choose-a-long-random-local-secret'
export OPENROUTER_API_KEY='your-private-key'
export OPENROUTER_MODEL='nvidia/nemotron-3.5-lightning:free'
python check_auth.py
python run_week05.py --mode llm --repeats 3
python scripts/check_week05.py submissions/25522018/week-05  # run from repository root
```

Runs overwrite `results.csv` and write console/agent traces into `logs/`.
`MARKET_ADMIN_KEY` and `OPENROUTER_API_KEY` MUST NOT be committed.

To smoke-test without an API key (not a genuine model comparison):

```bash
python run_week05.py --mode scripted --repeats 3
```

This writes `results_scripted_smoke.csv`, **not** the required LLM `results.csv`.

## Architecture and cautions

This zero-dependency implementation speaks JSON-RPC over HTTP at `/mcp`, exposing
`tools/list` and `tools/call`. It checks `Mcp-Method`, `Mcp-Name`, bearer identity,
negotiation binding, turn order, and token price limit, returning `isError` on
failed calls. `/admin/open`, `/admin/pass`, and `/admin/inspect` are separate
admin-only HTTP routes. Tokens are minted randomly by the server, never placed in
model context, and bound to one role, negotiation and private limit. They are
**local teaching tokens**, not OAuth 2.1 access tokens; replace the auth layer
with an OAuth verifier for a real deployment. Do not expose localhost endpoints.

Neither seller tool responses nor seller prompts receive the market-notice
injection. The buyer sees it solely inside the `get_negotiation` tool response.
All conditions use the same role prompts. The 'no injection' and 'injection'
conditions differ only in server response data and server enforcement.
