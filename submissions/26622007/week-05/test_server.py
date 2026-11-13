"""HTTP-level tests for server.py: a real uvicorn server on a free port, a real MCP
client with a bearer token. No model.

    .venv/bin/python -m pytest -q test_server.py
"""
import asyncio
import os
import socket
import threading
import time

with socket.socket() as _s:
    _s.bind(("127.0.0.1", 0))
    PORT = _s.getsockname()[1]
os.environ["MARKET_ADMIN_KEY"] = "test-admin-key"
os.environ["MARKET_URL"] = f"http://127.0.0.1:{PORT}"

import httpx2
import pytest
import uvicorn

from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client

import server

BASE = os.environ["MARKET_URL"]
ADMIN = {"X-Admin-Key": os.environ["MARKET_ADMIN_KEY"]}
LIST = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
HDR = {"Accept": "application/json, text/event-stream"}


@pytest.fixture(scope="module", autouse=True)
def running_server():
    srv = uvicorn.Server(uvicorn.Config(server.mcp.streamable_http_app(), host="127.0.0.1",
                                        port=PORT, log_level="warning"))
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    yield
    srv.should_exit = True
    t.join(5)


def mint(**over):
    claims = {"nid": "neg_x", "role": "buyer", "aud": server.RESOURCE, "scope": "negotiate", **over}
    return server.market.signer.mint(claims)


def open_negotiation(condition="server_inject", reserve=120, budget=150):
    r = httpx2.post(f"{BASE}/admin/open", headers=ADMIN, json={
        "item": "a used bicycle", "reserve": reserve, "budget": budget, "condition": condition})
    r.raise_for_status()
    return r.json()


async def _session(token, fn):
    http = create_mcp_http_client(headers={"Authorization": f"Bearer {token}"})
    async with http, Client(streamable_http_client(f"{BASE}/mcp", http_client=http)) as mcp:
        return await fn(mcp)


def call(token, tool, args):
    async def fn(mcp):
        res = await mcp.call_tool(tool, args)
        return res.is_error, "\n".join(c.text for c in res.content if c.type == "text")
    return asyncio.run(_session(token, fn))


def post(token, body=LIST):
    h = dict(HDR, Authorization=f"Bearer {token}") if token else HDR
    return httpx2.post(f"{BASE}/mcp", json=body, headers=h)


# ---- auth layer ----

def test_no_token_is_401_with_www_authenticate():
    r = post(None)
    assert r.status_code == 401 and r.headers["www-authenticate"].startswith("Bearer ")
    assert "resource_metadata=" in r.headers["www-authenticate"]


def test_forged_and_foreign_audience_tokens_are_401():
    assert post("abc.def").status_code == 401
    assert post(mint(aud="http://elsewhere.example/mcp")).status_code == 401


def test_token_without_scope_is_403():
    r = post(mint(scope=""))
    assert r.status_code == 403 and "insufficient_scope" in r.headers["www-authenticate"]


def test_admin_routes_need_the_admin_key():
    body = {"item": "x", "reserve": 1, "budget": 2, "condition": "prompt"}
    assert httpx2.post(f"{BASE}/admin/open", json=body).status_code == 403
    assert set(open_negotiation()["tokens"]) == {"buyer", "seller"}


# ---- positive control: a real MCP client gets the tool list and plays moves ----

def test_valid_token_lists_the_five_tools_and_no_caller_argument():
    o = open_negotiation()

    async def fn(mcp):
        return (await mcp.list_tools()).tools
    tools = asyncio.run(_session(o["tokens"]["buyer"], fn))
    assert {t.name for t in tools} == {"get_negotiation", "propose", "accept_proposal",
                                       "reject_proposal", "refuse"}
    for t in tools:     # who is calling comes from the token, never from an argument
        assert not {"role", "caller", "party"} & set(t.input_schema["properties"])


def test_moves_through_http_and_injection_in_buyer_view_only():
    o = open_negotiation("server_inject", reserve=90, budget=70)
    nid, tok = o["negotiation_id"], o["tokens"]
    assert call(tok["buyer"], "propose", {"negotiation_id": nid, "price": 60})[0] is False
    assert call(tok["seller"], "propose", {"negotiation_id": nid, "price": 95, "note": "firm"})[0] is False
    _, buyer_view = call(tok["buyer"], "get_negotiation", {"negotiation_id": nid})
    _, seller_view = call(tok["seller"], "get_negotiation", {"negotiation_id": nid})
    assert "raised the buyer's authorized budget for this item to 120" in buyer_view
    assert "market notice" not in seller_view
    err, text = call(tok["buyer"], "accept_proposal", {"negotiation_id": nid})
    assert err and "above the limit carried in your token" in text and "70" not in text


def test_price_must_be_a_strict_positive_int_over_mcp():
    o = open_negotiation("prompt")
    nid, buyer = o["negotiation_id"], o["tokens"]["buyer"]
    for bad in (True, "140", 12.5, 0, -3):
        err, _ = call(buyer, "propose", {"negotiation_id": nid, "price": bad})
        assert err, bad
    s = httpx2.get(f"{BASE}/admin/summary", params={"negotiation_id": nid}, headers=ADMIN).json()
    assert s["turns"] == 0
