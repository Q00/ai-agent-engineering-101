# Funding and local free seller validation (Codex)

User reported purchasing$20 credits. The existing key independently reported total_credits20,total_usage0,is_free_tier=false and free requests2/1000 before the local seller checks. No further purchase or auto top-up was performed.

The original MiniMax free route passed actual local A2A negotiations for lamp100,chair180,bookshelf260. Each rejected a below-reserve bid,accepted the actual valid price and produced a verified receipt signed with an ephemeral test key. The final run used12 successful model calls. No market result or public review was submitted.

The first local check completed lamp100 correctly but the verifier wrongly expected a terminal task on replay; the SDK actually rejects reusing a completed task. That failed trace is preserved. The corrected verifier asserts the error and no additional model call. This was an in-process test with an ephemeral key,not a duplicate public market trade.

The original guard test invocation collided with previously retained evidence directories. Using its existing VPS_TEST_EVIDENCE setting with a new folder produced12/12 passes without deleting old evidence. Full offline suite242/242 and scoped lint/types passed at this stage. Later weekly routing and paid fallback revisions have their own results.

Initial VPS key provisioning failed because AX6 used AX_PUBLIC_MODEL_API_KEY instead of the two expected aliases. After inspecting only variable names,the helper copied that one existing OpenRouter value internally into an exclusive0600/UID10001 AX7 file. The OpenAI rollback file and other credentials were preserved. Same-account server GET later confirmed20 credits,0 usage and1000 free requests/day. Secret values are absent from evidence and Git.

The temporary same-day free image was built but never activated; the subsequent user request extended the release for a week. See [weekly evidence](../openrouter-week-20261021/RESULTS.md) and [final paid fallback policy](../openai-fallback-20261021/RESULTS.md).
