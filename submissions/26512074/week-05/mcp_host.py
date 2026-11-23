"""Run the MCP 2.x negotiation market and admin routes in one process."""
from __future__ import annotations

import os
import uvicorn

from market_server import app


if __name__ == "__main__":
    host = os.environ.get("MARKET_HOST", "127.0.0.1")
    port = int(os.environ.get("MARKET_PORT", "8765"))
    uvicorn.run(app, host=host, port=port, log_level="warning", access_log=False)
