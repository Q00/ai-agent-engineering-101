# Public seller deployment

Status as of 2026-10-20: the owner requested a free API for the public deployment
only. The original OpenAI experiment configuration remains separate. Cloudflare
account sign-in and a scoped Workers AI credential are pending; the existing
tunnel credential does not authorize AI inference. No paid upgrade is authorized.
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

- `AX_PUBLIC_MODEL_API_KEY`: a dedicated private Workers AI API token, scoped to
  Workers AI Read/Edit on the intended account. Do not reuse the original OpenAI
  experiment key or the Cloudflare account/tunnel-management credential.
- `AX_PUBLIC_MODEL_BASE_URL`: the explicit
  `https://api.cloudflare.com/client/v4/accounts/<account-id>/ai/v1` endpoint.
- `AX_PUBLIC_MODEL=@cf/zai-org/glm-4.7-flash`: the proposed public-only model.
- `AX_PUBLIC_URL=https://ax-week06.treasuresky.xyz/`.
- `AX_DAILY_MODEL_REQUESTS=600`: UTC daily maximum outbound model HTTP attempts,
  reserved atomically before sending and retained across process restarts.
- `AX_TUNNEL_CREDENTIALS_FILE`: private credential file for this tunnel only;
  it must be readable by the tunnel container's UID 65532.

Before activation, verify that the account uses **Workers Free** and that the
chosen model remains available on that plan. Then run from this submission
directory after arranging private credentials and confirming the runtime limits:

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
not tokens or Neurons; exhaustion prevents further model requests until the next
UTC day. GET discovery remains available.
For direct execution, unset `OPENAI_CUSTOM_HEADERS`; public startup rejects it
before creating an HTTP client. Ambient OpenAI organization/project settings are
also excluded, and model HTTP redirects are refused. Compose passes only the
dedicated public configuration.

[Cloudflare Workers Free](https://developers.cloudflare.com/workers-ai/platform/pricing/)
allows 10,000 Neurons per day, resetting at 00:00 UTC (09:00 KST); over-limit
operations fail on that plan. This is an account-wide allowance, not a guaranteed
number of negotiations. Do not upgrade to Workers Paid or enable paid fallback.
The [July 2026 Free-plan notice](https://developers.cloudflare.com/changelog/post/2026-07-28-models-require-workers-paid/)
explicitly includes GLM-4.7-Flash; its
[model schema](https://developers.cloudflare.com/workers-ai/models/glm-4.7-flash/)
documents the required tool-calling fields. Live compatibility and account plan
have not yet been verified. Free quota exhaustion can make the shop unavailable
until the next reset, so public availability is not guaranteed by these limits.

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
