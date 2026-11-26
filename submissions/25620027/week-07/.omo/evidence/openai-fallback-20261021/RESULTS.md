# Free first, then explicitly authorized OpenAI daily fallback (Codex)

The user explicitly authorized the original OpenAI model when free service is exhausted/unavailable,at most5000 calls/day. The prior lifetime10000 stop was replaced by this provider-specific daily policy. The existing cumulative observed count and baseline remain telemetry,not a newly reset budget. Deadline2026-10-28 17:15KST remains. Auto buyer,rejoin and15-minute trend heartbeat remainOFF/PAUSED.

## Enforcement and scope

The primary route is MiniMax:free until its promotion expires,then openrouter/free. OpenRouter never falls through to a paid OpenRouter model. On unavailable free endpoints,connection failures or exhausted transient retries,the server can call gpt-4.1-nano-2025-04-14 directly at api.openai.com with its existing private OpenAI key. Other unexpected request errors still surface. A60-second free-service recheck avoids retrying a known unavailable free route on every incoming action.

Before each paid HTTP attempt,the service atomically reserves a slot in a0600 SQLite ledger under the persistent /data mount. The day changes at Korean midnight. Failed/interrupted paid attempts still consume slots. SDK retries are disabled for fallback,so they cannot bypass reservation. The limit applies to this AX7 fallback route from activation onward; it is not an account-wide OpenAI spending cap and excludes other apps and earlier experiments. The two provider keys stay in their separate original private files and are loaded into the authorized seller process without logging them.

Quota survives restarts/concurrent callers. A missing ledger with an existing initialization marker,missing schema,or invalid budget fails before paid network I/O. Hitting the daily paid quota returns a failure rather than another uncounted paid request. Free service can be retried; free-provider allowance is enforced separately by OpenRouter and shared across that account.

## Verification and deployment

- Full offline254/254,guard13/13 and scoped lint/types passed. Tests cover KST midnight,restart,concurrent reservations,ledger loss,maximum5000,paid failures without SDK retry,and free endpoint failure switching to the original OpenAI request with quota enforced. Paid fallback was driven through HTTP-level fakes,not forced against the paid production API.
- Isolated image preflight checked quota refusal and the one-week deadline with network disabled. Source7796d3b. Deployment at2026-10-21 17:30:09KST used image1.4.0-week-openai-daily5000,IDsha256:0f926ce52dafe626c0f55d3e7e040529920857925829674d366a5e5e174a7402.
- Runtime proof: healthy,deadline2026-10-28T17:15:00+09:00,limit_mode=openai_daily,paid_daily_limit5000,cumulative observed5291,baseline4354 and unchanged hash. The real paid ledger exists0600 and has no attempted paid requests yet. Hermes container ID/start/restarts are unchanged. [Runtime](after-runtime.log).
- One public lamp100 transaction completed on the primary free route and its receipt signature verified with the market-registered key. No market result or public review was posted. [Receipt](public-trade.json). This proves public transport/signing; it does not establish a sales or ranking improvement.
- Official course25-case live suite was not rerun for this provider/policy release. Earlier25/25 is historical evidence.

Rollback without paid calls: select the preserved1.4.0-openrouter-week-limit10000 image,which uses only the free route and its older cumulative stop,retaining all mounts and ledger. Do not silently fall back to the old OpenAI-only image. If state/signatures or the daily limiter fail,stop the affected seller rather than bypassing the budget. The guard-state deadline string was corrected to derive from the actual deadline timestamp in this release.
