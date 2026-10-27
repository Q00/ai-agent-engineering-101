# OpenRouter activation preflight — 2026-10-20

Current status: LIVE; the owner subsequently approved both private credential
transfers and free public operation. All four real public cases passed. This
file preserves the earlier preflight and approval boundary; final results are
in PUBLIC_VALIDATION.md.

The owner selected OpenRouter and explicitly directed the executor to the
existing private environment configuration. No credential value is in this record.

## Direct observations

- The existing configuration has an OpenRouter base URL and an OpenRouter key
  stored under the generic OPENAI_API_KEY name. That is not the original paid
  OpenAI experiment key.
- Authenticated `GET https://openrouter.ai/api/v1/key` succeeded; is_free_tier=true,
  free-model daily requests used=0, limit=50, remaining=50 before inference.
- The public catalog and selected endpoint reported zero input/output prices for
  `liquid/lfm-2.5-2.6b:free` and required tool selection support.
- One real `chat.completions.create` request used the exact original generation
  arguments: temperature=0, tool_choice=required, parallel_tool_calls=false,
  max_completion_tokens=450. The fictional lamp quote returned `quote(price=130)`;
  input=112, output=59, reported cost=0. The printed response metadata is preserved
  in `../../../logs/deployment/20261020/openrouter-tool-probe.json`.
- Parallel-tool control is not advertised in endpoint metadata. A successful
  request proves only this observed call, not universal provider compliance.
- Worker full suite: 140 passed. Independent review: CLEAR / APPROVE, 49 focused
  tests passed. Original Harness implementation and experiment model defaults
  were not edited. Official checker passed: 43 source files, original 21-run
  current-kit report, required log and REPORT.

## Credential-transfer boundary

Automatic review rejected transferring the whole private env file. The executor
then minimized the payload to only the authenticated OpenRouter key, with an
OpenRouter host/prefix check and SSH stdin into a mode-0600 file inside the
service's mode-0700 VPS directory. Automatic review rejected that attempt too:
the human had not explicitly named the destination VPS for secret transfer.
Neither transfer executed. No alternative route is being used to move the key.

Live activation therefore requires explicit approval for that one key and the
already-created, dedicated Cloudflare Tunnel credential into the assignment's
private directory on the existing VPS. Cloudflare Workers AI sign-in/key creation
is no longer needed. No paid API fallback or plan upgrade is authorized.

Public transactions and agent.json.deployed remain unverified/unset at this stage.

## Real local A2A trial 1: Liquid failure

The public app was exercised through its real in-memory ASGI JSON-RPC surface,
using the real free OpenRouter model and the existing fixed lamp scenario.
The credential stayed local. An outbound-attempt budget of 12 was applied.
This was not a deployed/public-Internet transaction.

The model first encountered HTTP 504, then successfully called search_catalog
(755 input / 85 output tokens). The following model step, after receiving the
six actual catalog candidates, returned 504 three times and exhausted the
existing retry policy. A2A returned TASK_STATE_FAILED; the verifier exited 1
with `seller did not open`. No purchase or receipt was produced.

The original request, response and model/tool/retry transcript are retained in
`../../../logs/deployment/20261020/local-openrouter-1/`. This failure is not
overwritten or counted as a successful free-model trade.

## Real local A2A trial 2: Cohere success

After checking the zero-priced Cohere endpoint and required tool support, the
same actual local A2A lamp scenario ran once with `cohere/north-mini-code:free`.
It had a 12-attempt budget and a 240-second overall deadline. Generation settings
and the original Harness were unchanged.

The seller opened at 130; buyer offers 60, 80 and 100 led to a completed receipt
for item=lamp, sku=lamp, price=100. The verifier checked exact offered-price
authorization, reserve/budget bounds, task/context continuity and terminal error
-32004. Five model calls used 3505 input and 1302 output tokens.

The selected public runtime model is now Cohere. The receipt summary and raw
messages are under `../../../logs/deployment/20261020/local-openrouter-2/`.
This is one successful local negotiation, not public deployment or a measured
success rate. Cohere metadata does not advertise max_completion_tokens or
parallel_tool_calls; observed compatibility is limited to this run.

## Linux runtime readiness

Public source commit `c01f18e` was archived with Git, excluding environment files
and credentials, and built on the existing VPS as
`ax-week06-25620027:openrouter-20261020`. The manifest-list digest is
`sha256:d8f0e18612808df62b5359a4fdacca6a1a3d12a0343735bac17e40cf371679c8`.
A network-disabled, read-only container imported the app successfully as UID
10001 with Python 3.12.11 and the unchanged original OpenAI model default.

This is an image/import check only. Neither credential transfer ran; no
long-running public seller or tunnel was activated. The shared assignment note,
academic current_state, academic source_manifest and course RAW source_manifest
record the Liquid failure, Cohere local lamp success, selected free model and
pending VPS credential-transfer approval. Their recorder reread raw A2A results
and verified all four records with exit 0, PASS 4/4.
