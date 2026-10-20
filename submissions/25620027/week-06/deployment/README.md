# Public seller deployment

Status as of 2026-10-20: the owner requested a free API for the public deployment
only. The owner selected the existing OpenRouter credential. Authentication and
one real free-model tool call succeeded; copying that key to the VPS still needs
explicit destination approval. The original OpenAI experiment configuration
remains separate. No Cloudflare Workers AI account sign-in is needed.
The configured hostname `ax-week06.treasuresky.xyz` is not a verified live shop.
Public trades have not been run and `agent.json.deployed` has not been set.
This preparation does not constitute completion of the deployment extra credit.

## Architecture

Public HTTPS → dedicated Cloudflare Tunnel → NGINX → one Python seller process.
The public wrapper imports the existing Week 05-derived `Harness`, catalog and
A2A SDK seller. It does not replace their negotiation decisions or guards.
The wrapper supplies its own model; the original command-line seller and buyer
retain the original `gpt-4.1-nano-2025-04-14` default.
The browser landing page and `/.well-known/agent-card.json` make no model calls.

The service uses an existing VPS, independently of the workstation. Compose
`restart: unless-stopped` restarts its containers after failure or reboot. The
in-memory task store requires one process/replica and loses open negotiations on
restart. Only the daily model-request budget is persisted in a Docker volume.
Keep the service available until grading is complete; stop it explicitly afterward.

## Private configuration

Keep the model environment and tunnel credential files outside this repository.
Never commit them, include them in a build context, or print expanded Compose
configuration. Required deployment variables:

- `AX_PUBLIC_MODEL_API_KEY`: the private OpenRouter key selected by the owner.
  Copy only that value into private server configuration, not the source env
  file or unrelated credentials. Do not put it in a browser or source file.
- `AX_PUBLIC_MODEL_BASE_URL=https://openrouter.ai/api/v1`.
- `AX_PUBLIC_MODEL=cohere/north-mini-code:free`: the selected public-only model.
- `AX_PUBLIC_URL=https://ax-week06.treasuresky.xyz/`.
- `AX_DAILY_MODEL_REQUESTS=50`: UTC daily maximum outbound model HTTP attempts,
  reserved atomically before sending and retained across process restarts.
- `AX_TUNNEL_CREDENTIALS_FILE`: private credential file for this tunnel only;
  it must be readable by the tunnel container's UID 65532.

Before activation, check the exact model's zero input/output price in the
[official catalog](https://openrouter.ai/api/v1/models) and authenticate with
`GET /api/v1/key` without logging the key. The existing account reported a daily
free-model limit of 50. Then run from this submission directory after arranging
private credentials and confirming the runtime limits:

```bash
docker compose --env-file /private/path/model.env \
  -p ax-week06-25620027 \
  -f deployment/compose.yaml -f deployment/compose.tunnel.yaml config --quiet
docker compose --env-file /private/path/model.env \
  -p ax-week06-25620027 \
  -f deployment/compose.yaml -f deployment/compose.tunnel.yaml up -d --build
```

The seller binds host loopback for administration. NGINX and the tunnel publish
no host ports. NGINX limits request bodies to 16 KiB, permits at most eight active
connections and admits 30 requests/minute with a burst of 30. The model wrapper
also uses one Uvicorn worker, at most eight active requests and the existing
six-round function-tool loop with 450 output tokens per model call. The public
entrypoint requires its own explicit model configuration and has no fallback to
the paid OpenAI experiment settings. The local daily limit counts HTTP attempts,
not transactions or tokens; exhaustion prevents further model requests until the next
UTC day. GET discovery remains available.
For direct execution, unset `OPENAI_CUSTOM_HEADERS`; public startup rejects it
before creating an HTTP client. Ambient OpenAI organization/project settings are
also excluded, and model HTTP redirects are refused. Compose passes only the
dedicated public configuration.

[OpenRouter free variants](https://openrouter.ai/docs/guides/routing/model-variants/free)
have their own zero-priced catalog entries. The public runtime accepts only an
explicit fixed model ID ending in `:free`, with no paid fallback list, plugins or
dynamic router. Unknown configuration fields are rejected. A paid counterpart,
OpenAI endpoint or model without the free suffix cannot start this public app.

[OpenRouter's free usage limits](https://openrouter.ai/docs/api_reference/limits)
include 20 requests/minute and the account's daily ceiling. One negotiation can
need several model requests. The local SQLite allowance counts server HTTP
attempts and cannot account for calls made elsewhere using the same key. Quota
exhaustion or provider congestion can interrupt negotiations; no credits purchase
or paid fallback is authorized. Discovery GETs remain available.

The [selected endpoint metadata](https://openrouter.ai/api/v1/models/cohere/north-mini-code:free/endpoints)
advertises tools, required tool selection and `max_tokens`, but not
`max_completion_tokens` or `parallel_tool_calls`. With the unchanged harness
arguments, one real local A2A lamp negotiation completed at 100 after buyer
offers of 60, 80 and 100; its receipt and finished-task error passed validation.
The [local wire logs](../logs/deployment/20261020/local-openrouter-2/) retain five
model calls and the actual JSON-RPC messages. This does not establish native
enforcement of every optional parameter or reliable public availability.

An earlier Liquid free-model probe returned a valid tool call and reported cost
0, but its full local A2A trial failed on repeated upstream HTTP 504 responses.
Those [failed logs](../logs/deployment/20261020/local-openrouter-1/) are preserved.
Full public trades remain unverified.

## Validation and rollback

```bash
python verify_deployment.py --url https://ax-week06.treasuresky.xyz/ \
  --output logs/deployment/20261020/attempt-1
```

This fixed counterpart checks the public card, three successful purchases,
one below-reserve no-deal, exact offered-price receipts, task/context continuity,
version headers and finished-task errors. It is separate from the original
21-check kit. Use a new output directory for every actual invocation.

If public discovery or receipt validation fails, preserve the attempt logs before
changing the service. Stop only this Compose project, keep its budget volume and
restore the previous image/configuration before restarting. Do not restart or
change unrelated VPS services. There is no previous deployed seller release yet.
