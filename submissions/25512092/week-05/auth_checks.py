"""auth_checks.py — 실행 중인 market server에 4가지를 확인하고 auth_checks.txt를 쓴다.

실행:  python auth_checks.py      (runner와 같은 8001 포트를 쓰므로 실험이 끝난 뒤에)
  (1) 토큰 없는 요청 -> HTTP 상태와 WWW-Authenticate 헤더
  (2) 다른 협상의 negotiation_id로 부른 결과
  (3) 차례가 아닌 수의 결과
  (4) server 조건에서 토큰의 한도 밖 수의 결과
"""
import json
from pathlib import Path
from urllib import request as urlreq
from urllib.error import HTTPError

from runner import BASE, admin, start_server

HERE = Path(__file__).resolve().parent
META = {"io.modelcontextprotocol/protocolVersion": "2026-07-28",
        "io.modelcontextprotocol/clientCapabilities": {}}


def post_mcp(name, args, token=None):
    """tools/call 을 직접 보낸다. (HTTP 상태, 헤더, 응답 본문) 을 돌려준다."""
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": name, "arguments": args, "_meta": META}}
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
               "Mcp-Method": "tools/call", "Mcp-Name": name, "MCP-Protocol-Version": "2026-07-28"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urlreq.Request(BASE + "/mcp", data=json.dumps(body).encode(), headers=headers, method="POST")
    try:
        with urlreq.urlopen(req, timeout=30) as r:
            return r.status, dict(r.headers), r.read().decode()
    except HTTPError as e:
        return e.code, dict(e.headers), e.read().decode()


def tool_text(raw):
    """응답(JSON 또는 SSE)에서 isError와 텍스트를 꺼낸다."""
    if "data:" in raw:
        raw = [l[5:] for l in raw.splitlines() if l.startswith("data:")][-1]
    res = json.loads(raw)["result"]
    text = " ".join(c.get("text", "") for c in res.get("content", []))
    return f"isError={str(res.get('isError', False)).lower()} \"{text}\""


def main():
    proc, admin_token = start_server()
    try:
        keyboard = {"id": 4, "item": "a mechanical keyboard", "reserve": 90, "budget": 70}
        lamp = {"id": 2, "item": "a desk lamp", "reserve": 30, "budget": 45}
        a = admin("POST", "/admin/open", admin_token, {"scenario": keyboard, "condition": "server"})
        b = admin("POST", "/admin/open", admin_token, {"scenario": lamp, "condition": "server"})

        lines = []
        status, headers, _ = post_mcp("get_negotiation", {"negotiation_id": a["negotiation_id"]})
        www = next((v for k, v in headers.items() if k.lower() == "www-authenticate"), "(none)")
        lines.append(f"(1) no token: tools/call get_negotiation -> HTTP {status}, WWW-Authenticate: {www}")

        _, _, raw = post_mcp("get_negotiation", {"negotiation_id": b["negotiation_id"]}, a["buyer"])
        lines.append(f"(2) wrong negotiation: buyer token of {a['negotiation_id']} calls get_negotiation"
                     f"({b['negotiation_id']}) -> {tool_text(raw)}")

        _, _, raw = post_mcp("propose", {"negotiation_id": a["negotiation_id"], "price": 95}, a["seller"])
        lines.append(f"(3) not your turn: seller calls propose(95) on {a['negotiation_id']} while it is "
                     f"the buyer's turn -> {tool_text(raw)}")

        _, _, raw = post_mcp("propose", {"negotiation_id": a["negotiation_id"], "price": 80}, a["buyer"])
        lines.append(f"(4) outside the limit (server condition, buyer budget 70): buyer calls propose(80) "
                     f"on {a['negotiation_id']} -> {tool_text(raw)}")

        (HERE / "auth_checks.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
        print("\n".join(lines))
    finally:
        proc.terminate()


if __name__ == "__main__":
    main()