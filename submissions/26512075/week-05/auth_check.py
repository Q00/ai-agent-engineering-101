"""
Write auth_check.txt against a running market server
"""

from __future__ import annotations

import argparse
import json

from pathlib import Path

import httpx

MCP_BODY_LIST = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "tools/list",
    "params": {
        "_meta": {
            "io.modelcontextprotocol/clientCapabilities": {}
        }
    },
}

def open_session(base: str, token: str) -> str:
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "Authorization": f"Bearer {token}",
    }
    init = httpx.post(
        f"{base}/mcp",
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "auth_check", "version": "0.0.1"},
            },
        },
        timeout=15.0,
    )
    init.raise_for_status()
    sid = init.headers.get("mcp-session-id")
    if not sid:
        raise RuntimeError(f"no session: {init.status_code} {init.text[:200]}")
    httpx.post(
        f"{base}/mcp",
        headers={**headers, "Mcp-Session-Id": sid},
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        timeout=15.0,
    )
    return sid

def post_mcp(base: str, token: str | None, method: str, params: dict, extra_headers: dict | None = None):

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "Mcp-Method": method,
    }

    if token:
        headers["Authorization"] = f"Bearer {token}"
    if extra_headers:
        headers.update(extra_headers)

    body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}

    # return httpx.post(f"{base}/mcp/", headers=headers, json=body, timeout=15.0, follow_redirects = True)
    return httpx.post(f"{base}/mcp", headers=headers, json=body, timeout=15.0)

def main() -> None:

    parser = argparse.ArgumentParser(description="Check auth against market server")
    parser.add_argument("--base", default="http://127.0.0.1:8001", help="Base URL of the market server")
    parser.add_argument("--out", default="auth_checks.txt")
    args = parser.parse_args()
    base = args.base.rstrip("/")
    lines: list[str] = []

    # no tokens -> 401 + WWW-Authenticate
    r1 = httpx.post(
        f"{base}/mcp",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Mcp-Method": "tools/list",
        },
        json=MCP_BODY_LIST,
        timeout=15.0,
    )
    lines.append(
        f"(1) no token: HTTP {r1.status_code}; WWW-Authenticate={r1.headers.get('www-authenticate')!r}"
    )

    opened = httpx.post(
        f"{base}/admin/open",
        json={
            "item": "probe",
            "seller_reserve": 70,
            "buyer_budget": 90,
            "condition": "server",
            "inject": False,
        },
        timeout=15.0,
    ).json()
    buyer = opened["buyer_token"]
    seller = opened["seller_token"]
    nid = opened["negotiation_id"]

    other = httpx.post(
        f"{base}/admin/open",
        json={
            "item": "other",
            "seller_reserve": 10,
            "buyer_budget": 20,
            "condition": "server",
            "inject": False,
        },
        timeout=15.0,
    ).json()

    #  tokens for negotiation_id  A, call with negotiation_id B
    seller_sid = open_session(base, seller)
    buyer_sid = open_session(base, buyer)

    r2 = post_mcp(
        base,
        seller,
        "tools/call",
        {"name": "propose", "arguments": {"nid": other["negotiation_id"], "price": 18}},
        extra_headers={"Mcp-Session-Id": seller_sid},
    )

    lines.append(
        f"(2) POST {base}/open -> HTTP : {r2.status_code}; body : {r2.text[:500]!r}"
    )

    # seller opens, due to ouf of turn, buyer moving first should fail
    r3 = post_mcp(
        base,
        seller,
        "tools/call",
        {"name": "propose", "arguments": {"nid": nid, "price": 70}},
        extra_headers={"Mcp-Session-Id": seller_sid},
    )

    lines.append(
        f"(3) POST {base}/open -> HTTP : {r3.status_code}; body : {r3.text[:500]!r}"
    )

    # server condition, price outside token limit
    r4 = post_mcp(
        base,
        buyer,
        "tools/call",
        {"name": "propose", "arguments": {"nid": nid, "price": 91}},
        extra_headers={"Mcp-Session-Id": buyer_sid},
    )
    
    lines.append(
        f"(4) POST {base}/open -> HTTP: {r4.status_code}; body: {r4.text[:500]!r}"
    )


    Path(args.out).write_text("\n".join(lines) + "\n", encoding = "utf-8")
    print("\n".join(lines))
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
