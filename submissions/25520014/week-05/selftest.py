"""market server의 규칙을 모델 없이 확인한다. 본 실행 전에 돌린다.

  python selftest.py          # server를 직접 띄우고 끈다

첫 전체 실행에서 reject_proposal이 늘 거절되는 버그를 모델 로그를 보고서야 알았다
(attempt-01-reject-bug/). 시험 실행이 reject를 한 번도 부르지 않았기 때문이다. 그래서 다섯
tool의 정상 경로와 거절 경로를 모두 여기서 부른다.
"""
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path

import httpx2 as httpx

HERE = Path(__file__).resolve().parent
PORT = 8199
BASE = f"http://127.0.0.1:{PORT}"
KEY = secrets.token_hex(8)
ADMIN = {"x-admin-key": KEY}
failures = 0


def call(token, tool, **args):
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
               "MCP-Protocol-Version": "2026-07-28", "Mcp-Method": "tools/call", "Mcp-Name": tool,
               "Authorization": f"Bearer {token}"}
    params = {"name": tool, "arguments": args,
              "_meta": {"io.modelcontextprotocol/protocolVersion": "2026-07-28",
                        "io.modelcontextprotocol/clientCapabilities": {}}}
    res = httpx.post(f"{BASE}/mcp", headers=headers,
                     json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": params}).json()
    return not res["result"]["isError"], res["result"]["content"][0]["text"]


def expect(label, got, ok, contains=""):
    global failures
    good = got[0] == ok and contains in got[1]
    failures += not good
    print(f"{'ok  ' if good else 'FAIL'} {label}: {'accepted' if got[0] else got[1]}")


def opened(buyer_limit=None, seller_limit=None):
    o = httpx.post(f"{BASE}/admin/negotiations", headers=ADMIN,
                   json={"item": "x", "buyer_limit": buyer_limit, "seller_limit": seller_limit,
                         "raised": None}).json()
    return o["negotiation_id"], o["tokens"]["buyer"], o["tokens"]["seller"]


def state(nid):
    return httpx.get(f"{BASE}/admin/negotiations/{nid}", headers=ADMIN).json()


server = subprocess.Popen([sys.executable, str(HERE / "market.py"), "--port", str(PORT)],
                          env=dict(os.environ, MARKET_ADMIN_KEY=KEY),
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    time.sleep(3)
    n, b, s = opened()
    expect("buyer reject before any seller offer", call(b, "reject_proposal", negotiation_id=n), False, "not proposed")
    expect("buyer accept before any seller offer", call(b, "accept_proposal", negotiation_id=n), False, "not proposed")
    expect("buyer propose 60", call(b, "propose", negotiation_id=n, price=60), True)
    expect("seller reject buyer's 60", call(s, "reject_proposal", negotiation_id=n), True)
    expect("seller moves again (out of turn)", call(s, "propose", negotiation_id=n, price=100), False, "not your turn")
    expect("buyer reject before any seller offer", call(b, "reject_proposal", negotiation_id=n), False, "not proposed")
    expect("buyer propose 70", call(b, "propose", negotiation_id=n, price=70), True)
    expect("seller propose 100", call(s, "propose", negotiation_id=n, price=100), True)
    expect("buyer reject seller's 100", call(b, "reject_proposal", negotiation_id=n), True)
    expect("seller propose 80", call(s, "propose", negotiation_id=n, price=80), True)
    expect("buyer accept seller's 80", call(b, "accept_proposal", negotiation_id=n), True)
    st = state(n)
    expect("deal at 80", (st["status"] == "deal" and st["price"] == 80, str(st["status"])), True)
    expect("move after close", call(s, "propose", negotiation_id=n, price=90), False, "closed")

    n, b, s = opened()
    call(b, "propose", negotiation_id=n, price=10)
    expect("seller refuse", call(s, "refuse", negotiation_id=n), True)
    expect("status no_deal", (state(n)["status"] == "no_deal", ""), True)

    n, b, s = opened(buyer_limit=70, seller_limit=90)
    expect("buyer propose 75 over token limit 70", call(b, "propose", negotiation_id=n, price=75), False, "outside the limit")
    expect("buyer propose 70 at limit", call(b, "propose", negotiation_id=n, price=70), True)
    expect("seller propose 85 under token limit 90", call(s, "propose", negotiation_id=n, price=85), False, "outside the limit")
    expect("seller accept buyer's 70 under limit", call(s, "accept_proposal", negotiation_id=n), False, "outside the limit")
    expect("seller reject buyer's 70 (a reject carries no price)", call(s, "reject_proposal", negotiation_id=n), True)
    st = state(n)
    expect("refusals reset after a valid move", (st["refusals_in_turn"] == 0, ""), True)

    n, b, s = opened(buyer_limit=70, seller_limit=90)
    call(b, "propose", negotiation_id=n, price=60)
    call(s, "propose", negotiation_id=n, price=100)
    for i in range(3):
        expect(f"buyer accept 100, refusal {i + 1}", call(b, "accept_proposal", negotiation_id=n), False, "outside the limit")
    expect("buyer accept 100, refusal 4 halts", call(b, "accept_proposal", negotiation_id=n), False, "halted")
    expect("halted is recorded", (bool(state(n)["halted"]), ""), True)
finally:
    server.terminate()

print(f"\n{failures} failure(s)")
sys.exit(1 if failures else 0)
