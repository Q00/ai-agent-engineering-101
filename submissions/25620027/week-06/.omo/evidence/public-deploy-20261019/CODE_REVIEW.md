# Public seller predeployment code review

- codeQualityStatus: CLEAR
- recommendation: APPROVE
- Scope: public_seller.py, tests/test_public_seller.py, verify_deployment.py, deployment/Dockerfile, Dockerfile.dockerignore, compose.yaml, compose.tunnel.yaml, cloudflared.yml, nginx.conf. All are new untracked files; inspected complete contents rather than relying on git diff (which omits them).
- Goal: publish the existing seller with HTTPS discovery, negotiation continuity within one process, persistent outbound request allowance, bounded ingress, and truthful live verification.
- ULW status: `omo-agent-toolkit ulw-loop status --json` returned ULW_LOOP_PLAN_MISSING. Report uses the parent's explicitly assigned submission-local artifact location; no Documents-root artifacts created.
- Evidence inspected: ../public-seller-20261019/VERIFICATION.txt, RECORD.md, pytest.txt, ruff.txt, basedpyright.txt, structure.txt. RECORD.md served as implementation notepad. Inspected evidence supports 19 targeted passes, lint/type/structure checks. Parent's separate 98-test/full-build claims were not independently verified here and are not approval evidence.

## CRITICAL
None.

## HIGH
None.

## MEDIUM
No open findings. The following finding is closed after re-review.

1. **RESOLVED** — **Deployment verifier accepts a receipt above the offered price** — `verify_deployment.py:128`, `verify_deployment.py:165-169`. The negotiated offer is discarded and receipt validation checks only reserve/budget/product bounds. A seller can quote 100, receive the buyer's offer of 100, complete at 120, and obtain PASS for the lamp/120 scenario. This weakens the live validation's commercial-authorization claim. Preserve the final offer and require exact equality with the receipt price (the existing `market_a2a.remote.purchase` boundary already demonstrates this contract). Add a focused adverse wire-response case that distinguishes an exact-price receipt from an in-budget overcharge.

Independent reproduction (no API calls): invoked real `verify_case`, `Remote`, and `Recorder` using httpx2.MockTransport. Responses: input-required quote 100; completed lamp receipt 120; terminal error -32004. Observed output:

```json
{"buyer_offer":"100","verifier_result":{"case":"lamp-120","pass":true,"state":"TASK_STATE_COMPLETED","task_id":"t","context_id":"c","receipt":{"price":120,"item":"lamp","sku":"lamp"},"terminal_error":-32004}}
```

## LOW
None requiring action for this bounded review.

## Skill-perspective check
Loaded and consulted both provider-managed `remove-ai-slops/SKILL.md` and `programming/SKILL.md`. Ran the review perspective over all new production code and tests. No deletion-only tests, prompt phrase pins, implementation-constant mirroring, untyped escapes, or unnecessary production parsing were found. Settings parsing is at the environment boundary; SQLite serialization is necessary for the request budget. Tests exercise real ASGI routing and SQLite plus a narrow HTTP fake; the restart test uses a distinct subprocess. The new verifier lacks the adverse receipt behavior check identified above. No material slop/maintainability violation otherwise found. No provider-managed skills modified; no reusable skill correction identified.

## Deployment assessment and limits
The SQLite conditional UPSERT reserves before HTTP send, commits under SQLite's writer lock, and persists through the named Docker volume. Concurrent reservation, restart, failed upstream attempt, and date rollover are supported by inspected tests/evidence. The limit is a request count, not a monetary ceiling. Transport connection retries do not imply additional billable HTTP completions; model retries and redirects traverse request hooks. Failure to reserve fails closed.

One uvicorn worker retains existing task/context state; `Remote.send` checks identity continuity. Task state resets on restart as the landing page discloses. Docker runs non-root with a writable named data volume, read-only application filesystem, and loopback-only host publishing. Nginx provides the declared body/rate/concurrency limits. Health probes validate discovery only, not model credentials, quota, or trades. No SSH, secret reads, API requests, live deployment, container runtime checks, or full test repeats performed in this review. A live readiness claim still requires actual HTTPS/card/trade evidence; this is a predeployment code review only.

## Fix verification
Re-read the changed `negotiate` and `verify_case` functions plus the complete new `tests/test_public_verifier.py`. The negotiator now returns the last offer; receipt validation requires exact equality in addition to reserve/budget/product bounds. Independently ran:

```text
/Users/sungjinho/.cache/uv/archive-v0/k_F7EUrQE1oYyHVUjw4SH/bin/python -m pytest tests/test_public_verifier.py -q
2 passed in 0.11s
```

The two HTTP-level cases exercise real Remote/Recorder parsing: a 100 receipt passes and an in-budget 120 receipt after a 100 offer raises HarnessError. They address observable authorization behavior rather than pinning implementation constants or prose. Both skill perspectives remain clear. This closes the sole MEDIUM finding. No full-suite or live API checks were repeated. Approval is for the bounded predeployment code scope, not a claim of live readiness.

## Blockers before approval
None.
