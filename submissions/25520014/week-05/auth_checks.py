"""auth_checks.txt를 만든다. 모델 없이, 실행 중인 market server에 직접 HTTP로 묻는다.

  MARKET_ADMIN_KEY=... python market.py --port 8100 &
  MARKET_ADMIN_KEY=... python auth_checks.py --port 8100

네 줄, 한 줄에 한 검사:
  (1) 토큰 없는 요청 -> HTTP 상태와 WWW-Authenticate 헤더
  (2) 한 협상의 토큰으로 다른 협상에 수를 둠
  (3) 차례가 아닌 쪽이 수를 둠
  (4) server 조건에서 토큰 한도 밖의 수
"""
import argparse
import os
from pathlib import Path

import httpx2 as httpx

ap = argparse.ArgumentParser()
ap.add_argument("--port", type=int, default=8100)
BASE = f"http://127.0.0.1:{ap.parse_args().port}"
ADMIN = {"x-admin-key": os.environ["MARKET_ADMIN_KEY"]}
OUT = Path(__file__).resolve().parent / "auth_checks.txt"


def rpc(token, method, params, name=None):
    """MCP 2026-07-28 HTTP 요청 하나. 헤더와 _meta는 lab의 curl_checks.sh와 같다."""
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
               "MCP-Protocol-Version": "2026-07-28", "Mcp-Method": method}
    if name:
        headers["Mcp-Name"] = name
    if token:
        headers["Authorization"] = f"Bearer {token}"
    params = dict(params, _meta={"io.modelcontextprotocol/protocolVersion": "2026-07-28",
                                 "io.modelcontextprotocol/clientCapabilities": {}})
    return httpx.post(f"{BASE}/mcp", headers=headers,
                      json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params})


def call(token, tool, **args):
    r = rpc(token, "tools/call", {"name": tool, "arguments": args}, tool)
    res = r.json()["result"]
    return f"HTTP {r.status_code}, isError={str(res['isError']).lower()}, {res['content'][0]['text']!r}"


def open_negotiation(condition):
    """scenarios.json의 시나리오 3(reserve 90, budget 70)으로 연다."""
    enforce = condition.startswith("server")
    body = {"item": "a mechanical keyboard",
            "buyer_limit": 70 if enforce else None, "seller_limit": 90 if enforce else None,
            "raised": None}
    return httpx.post(f"{BASE}/admin/negotiations", headers=ADMIN, json=body).json()


a = open_negotiation("server")
b = open_negotiation("server")
buyer_a, seller_a = a["tokens"]["buyer"], a["tokens"]["seller"]

r = rpc(None, "tools/list", {})
line1 = (f"(1) no token: tools/list -> HTTP {r.status_code}, "
         f"WWW-Authenticate: {r.headers.get('www-authenticate')}")
line2 = (f"(2) buyer token of {a['negotiation_id']} on {b['negotiation_id']}: "
         f"propose(price=60) -> {call(buyer_a, 'propose', negotiation_id=b['negotiation_id'], price=60)}")
line3 = (f"(3) out of turn (buyer moves first, seller moves): "
         f"propose(price=100) -> {call(seller_a, 'propose', negotiation_id=a['negotiation_id'], price=100)}")
call(buyer_a, "propose", negotiation_id=a["negotiation_id"], price=60)       # buyer 60, seller 차례
call(seller_a, "propose", negotiation_id=a["negotiation_id"], price=100)     # seller 100, buyer 차례
line4 = (f"(4) server condition, buyer token limit 70, seller proposed 100: "
         f"accept_proposal -> {call(buyer_a, 'accept_proposal', negotiation_id=a['negotiation_id'])}")

OUT.write_text("\n".join([line1, line2, line3, line4]) + "\n", encoding="utf-8")
print(OUT.read_text(encoding="utf-8"), end="")
