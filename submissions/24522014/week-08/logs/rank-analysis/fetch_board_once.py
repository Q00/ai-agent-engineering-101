"""One public GET of /api/board for round 3, saved as logs/rank-analysis/board-<UTC>.json. No other call."""
from datetime import datetime, timezone
from pathlib import Path
import ssl

import httpx

stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
response = httpx.get("https://market.ourolabs.ai/api/board", params={"round": 3}, timeout=30, verify=ssl.create_default_context())
response.raise_for_status()
path = Path(__file__).resolve().parent / f"board-{stamp}.json"
path.write_bytes(response.content)
print(path.name, len(response.content))
