# Public seller deployment

Status as of 2026-10-20: application and Linux images prepared; credential
placement and service activation are deferred by the owner. Do not transfer
model or tunnel credentials or start paid model calls under this decision.
The owner may request activation later. No automatic activation is scheduled.
The configured hostname `ax-week06.treasuresky.xyz` is not a verified live shop.
Public trades have not been run and `agent.json.deployed` has not been set.
This preparation does not constitute completion of the deployment extra credit.

## Architecture

Public HTTPS → dedicated Cloudflare Tunnel → NGINX → one Python seller process.
The public wrapper imports the existing Week 05-derived `Harness`, catalog and
A2A SDK seller. It does not replace their negotiation decisions or guards.
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

- `OPENAI_API_KEY`, optionally `OPENAI_BASE_URL`: existing model credentials.
- `AX_PUBLIC_URL=https://ax-week06.treasuresky.xyz/`.
- `AX_DAILY_MODEL_REQUESTS=600`: UTC daily maximum outbound model HTTP attempts,
  reserved atomically before sending and retained across process restarts.
- `AX_TUNNEL_CREDENTIALS_FILE`: private credential file for this tunnel only;
  it must be readable by the tunnel container's UID 65532.

For a later, explicitly requested activation, run from this submission directory
after arranging private credentials and confirming the runtime limits:

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
six-round function-tool loop with 450 output tokens per model call. Public
requests consume the owner's model API allowance and may incur API charges.
The daily limit counts attempts, not dollars; exhaustion prevents further model
requests until the next UTC day. GET discovery remains available.

## Validation and rollback

```bash
python verify_deployment.py --url https://ax-week06.treasuresky.xyz/ \
  --output logs/deployment/20261019/attempt-1
```

This fixed counterpart checks the public card, three successful purchases,
one below-reserve no-deal, exact offered-price receipts, task/context continuity,
version headers and finished-task errors. It is separate from the original
21-check kit. Use a new output directory for every actual invocation.

If public discovery or receipt validation fails, preserve the attempt logs before
changing the service. Stop only this Compose project, keep its budget volume and
restore the previous image/configuration before restarting. Do not restart or
change unrelated VPS services. There is no previous deployed seller release yet.
