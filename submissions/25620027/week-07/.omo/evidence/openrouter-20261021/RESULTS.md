# OpenRouter free-model integration preparation (Codex)

The user asked to adopt the OpenRouter/MiniMax approach described in a supplied chat screenshot. This preparation does not purchase credits, extend the seller deadline, switch the production model, or resume monitoring.

## Verified facts

- Official catalog GET `https://openrouter.ai/api/v1/models` on2026-10-21 lists `minimax/minimax-m3.1-flash:free`, zero prompt/completion pricing and tools/tool_choice support. Its supported parameters omit parallel_tool_calls and max_completion_tokens.
- The official model page `https://openrouter.ai/minimax/minimax-m3.1-flash:free` states that this launch promotion ends2026-10-22 12:00UTC (21:00KST); the free variant is retired then. It is not a permanent free-model promise. The base slug is paid and is not configured as fallback.
- Official limits `https://openrouter.ai/docs/api_reference/limits` and pricing `https://openrouter.ai/pricing` describe20 requests/minute,50/day before at least$10 lifetime credit purchase and1000/day after. A purchase is prepaid credit, with checkout fees separately applicable, not unlimited usage.
- Read-only `GET /api/v1/key` using the existing local OpenRouter credential returned `is_free_tier=true`, daily requests used0/limit50/remaining50 and usage_daily0 before the smoke tests. No key value was printed, copied or committed. This is not the post-test remaining balance; failed attempts can consume quota.

## Implementation and observed behavior

`AX_MODEL_PROVIDER=openrouter-free` explicitly selects the fixed free MiniMax ID and the official OpenRouter endpoint with `OPENROUTER_API_KEY`. OpenAI remains the default. Unknown provider, missing free key, a paid OpenRouter model and a free model sent to the wrong host fail closed. No arbitrary model fallback list, plugins or paid variant is configured. Requests additionally cap provider prompt, completion and request prices at0.

The first real attempt failed404 because this live endpoint did not accept tool_choice=required, despite tool_choice being advertised. [Original failure](live-tool-check.log) is preserved. The free path now uses tool_choice=auto; the existing bounded model loop and action guards still require an actual authorized tool action before a negotiation can commit. Its output allocation is4096 with low reasoning effort, rather than the OpenAI450-token request. MiniMax's unsupported parallel_tool_calls option is omitted.

With that compatibility change, the actual model called lookup, received fictional price100, then called accept with100. Exactly2 successful calls reported1034 input tokens and43 output tokens. This was a local tool-loop test using fictional data, not a market order, seller-performance comparison, signature test, or full25-case course test. [Real trace](live-tool-compatible.log), [reproducible smoke script](../../../tests/manual_openrouter_tools.py).

SDK request serialization tests cover both endpoints, key separation, zero price filters, absence of paid model fallback, model/host mismatch and unknown/missing configuration. Full offline suite passed242/242 in offline-release.log; Ruff passed in lint-release-clean.log and scoped type checking reported0 errors/0 warnings in types-release-clean.log. Initial diagnostics are preserved. These checks do not establish the free endpoint's long-term latency, capacity or seller negotiation quality.

## Operational decision still required

Production remains seller1.4.0 with OpenAI nano. Its2026-10-21 21:00KST/10000 observed-call guard, baseline4354, auto-buyerOFF and paused15-minute monitoring remain. This local provider preparation is not deployed. The existing free-tier50/day is too small to promise continuous market availability; even1000/day is a request quota, not a transaction count or uptime guarantee.

Before switching production: decide whether to purchase credits, confirm the provider's remaining allowance, run the seller/signature acceptance checks with the selected provider under an agreed allowance, use a private env configuration and preserve the current deployment as rollback. Any24-hour extension must name its new deadline/budget explicitly. After MiniMax's promotion expires, select and validate another zero-priced endpoint or deliberately authorize paid inference; this integration will not select a paid replacement automatically.
