# Public deployment verification — 2026-10-20

Status: LIVE; public HTTPS discovery and all four bounded scenarios passed.
Observation time: 2026-10-20 14:11 KST. Grading points remain the instructor's
decision; this records the observed deployment requirements and transactions.

## Authorization and activation

The owner explicitly approved transferring only the existing OpenRouter key and
the assignment's dedicated tunnel credential to the private VPS directory,
starting the free public seller and checking four virtual-product negotiations.
The key was separated from its source env file in memory and sent over SSH;
neither the whole env file nor the original paid OpenAI key was copied.
The private directory is mode 0700; model configuration is 0600; the tunnel file
is 0400 and readable by its container UID. No credential values are in Git,
images, browser content or these transaction logs.

The first startup used an incorrect Compose project directory: the seller became
healthy, but NGINX could not bind its config file. The accidentally created empty
directory was removed and the project directory corrected to `deployment/`.
The seller, ingress and tunnel are now running with restart count 0 and
`restart: unless-stopped`. Seller health is healthy. Only 127.0.0.1:9998 is bound
on the VPS; the tunnel supplies public HTTPS. The existing unrelated service's
container ID and start time were unchanged.

## Actual public results

- URL: https://ax-week06.treasuresky.xyz/
- Agent Card: https://ax-week06.treasuresky.xyz/.well-known/agent-card.json
- Card name: Sungjin Shop; first interface: JSONRPC, protocolVersion 1.0, same
  public RPC URL; one catalog-negotiation skill.
- Public-only model: `cohere/north-mini-code:free` via OpenRouter.
- Original seller/buyer commands and OpenAI 21/21 evidence are unchanged.

| Scenario | Budget | Reserve | Observed result |
|---|---:|---:|---|
| lamp | 120 | 100 | completed, exact offered-price receipt 100 |
| chair | 200 | 180 | completed, exact offered-price receipt 180 |
| bookshelf | 300 | 260 | completed, exact offered-price receipt 260 |
| bookshelf | 230 | 260 | no receipt/no deal after eight offers; still INPUT_REQUIRED |

All three completed tasks returned error -32004 on a further message. The
verifier checked task/context continuity, exact product/SKU, reserve/budget
bounds, receipt price equal to the actual last buyer offer and version headers.
It exited 0: **PASS, four cases**, with 25 HTTP responses, 24 POST payloads and
25 valid A2A-Version headers. The below-reserve case was not terminally rejected;
it remained an open negotiation without a receipt.

Raw request/response bodies, transcript and immutable summary are in
`../../../logs/deployment/20261020/public-attempt-1/`. This is the project's
public verification, not a rerun of the official 21-check kit on Cohere.

## Exact client invocation

The existing verifier and scenarios were unchanged. Its HTTP pre-request hook
waited four seconds to respect the free-provider rate limit. This is not a load
test. The command ran the following code from the submission directory:

```python
from pathlib import Path
import anyio, httpx2, typer
import verify_deployment as verifier

unpaced = verifier.create_client

async def pace(_request: httpx2.Request) -> None:
    await anyio.sleep(4)

def paced_client(*, model: bool = False) -> httpx2.AsyncClient:
    client = unpaced(model=model)
    client.event_hooks["request"].append(pace)
    return client

verifier.create_client = paced_client
try:
    verifier.main(
        url="https://ax-week06.treasuresky.xyz/",
        output=Path("logs/deployment/20261020/public-attempt-1"),
    )
except typer.Exit as exc:
    raise SystemExit(exc.exit_code)
```

Use a new output directory for any explicitly requested repeat; the invocation
above has already run. No additional model trades were run after these four.

## Runtime evidence and limits

The seller's filtered runtime logs showed 25 successful MODEL_USAGE entries for
the fixed free Cohere model, zero MODEL_RETRY entries and the three matching
receipt records. Persistent SQLite budget usage was 25 for UTC 2026-10-20.
The account counter changed from used 7 / remaining 43 to used 32 / remaining 18
of its 50-request UTC daily allowance; reported usage_daily remained 0. This is
an API-reported observation, not an invoice audit. The model catalog again showed
zero input/output prices before activation.

One negotiation needs several model calls. Other uses of the same account share
its quota; exhaustion or upstream congestion can interrupt trading while GET
discovery remains available. The service is configured to run until grades are
out, but future availability is not established by this one verification.
The in-memory task store resets on process restart. No paid fallback is enabled.
Earlier Liquid failure and local Cohere success logs remain preserved.
