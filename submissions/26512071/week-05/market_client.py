"""Shared plumbing: start the market server, call its admin routes, connect as a party."""

from __future__ import annotations

import contextlib
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path

import httpx2
from mcp import Client
from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client

HERE = Path(__file__).resolve().parent


@contextlib.contextmanager
def running_server(port: int = 8765, log_path: Path | None = None):
    """Start market_server.py as a child process with a fresh admin key. Yields (base_url, admin_key)."""
    admin_key = secrets.token_urlsafe(16)
    env = {**os.environ, "MARKET_ADMIN_KEY": admin_key}
    out = open(log_path, "a", encoding="utf-8") if log_path else subprocess.DEVNULL
    proc = subprocess.Popen([sys.executable, str(HERE / "market_server.py"), "--port", str(port)],
                            env=env, stdout=out, stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(100):
            if proc.poll() is not None:
                raise RuntimeError(f"market server exited with code {proc.returncode}")
            try:
                httpx2.get(base + "/admin/negotiations/ping", timeout=0.5)
                break
            except httpx2.TransportError:
                time.sleep(0.1)
        else:
            raise RuntimeError("market server did not start")
        yield base, admin_key
    finally:
        proc.terminate()
        proc.wait(timeout=10)
        if log_path:
            out.close()


def admin_open(base: str, admin_key: str, scenario: dict, condition: str, max_moves: int = 8) -> dict:
    r = httpx2.post(base + "/admin/negotiations", headers={"x-market-admin": admin_key}, json={
        "item": scenario["item"], "reserve": scenario["reserve"], "budget": scenario["budget"],
        "condition": condition, "max_moves": max_moves})
    r.raise_for_status()
    return r.json()


def admin_state(base: str, admin_key: str, nid: str) -> dict:
    r = httpx2.get(f"{base}/admin/negotiations/{nid}", headers={"x-market-admin": admin_key})
    r.raise_for_status()
    return r.json()


def party_client(base: str, token: str) -> Client:
    """An MCP client that sends the party's bearer token on every request."""
    http = create_mcp_http_client(headers={"Authorization": f"Bearer {token}"})
    return Client(streamable_http_client(base + "/mcp", http_client=http))


def result_text(result) -> str:
    chunks = [item.text for item in result.content if item.type == "text"]
    text = "\n".join(chunks) if chunks else str(result.content)
    return f"error: {text}" if result.is_error else text
