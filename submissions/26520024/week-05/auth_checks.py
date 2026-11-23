"""Real HTTP boundary probes, separate from scored model episodes."""
import argparse
import json
from pathlib import Path
from mcp_client import MCPClient, ServerProcess, http_post

SCENARIO = dict(id="AUTH", item="synthetic auth fixture", reserve=110, budget=90)


def check_boundaries(server):
    opened = server.admin("open", scenario=SCENARIO, condition="server_inject")
    other = server.admin("open", scenario=SCENARIO, condition="server_inject")
    nid = opened["negotiation_id"]
    buyer = MCPClient(server.url, opened["tokens"]["buyer"])
    seller = MCPClient(server.url, opened["tokens"]["seller"])
    body, headers = buyer.request_parts("tools/list")
    status, received, raw = http_post(server.url + "/mcp", None, body, headers)
    assert status == 401 and received.get("WWW-Authenticate", "").startswith("Bearer")
    lines = ["no_token: HTTP {} WWW-Authenticate={} body={}".format(status, received["WWW-Authenticate"], raw)]
    cases = [
        ("wrong_negotiation", buyer, "get_negotiation", dict(negotiation_id=other["negotiation_id"]), "wrong_negotiation"),
        ("out_of_turn", seller, "propose", dict(negotiation_id=nid, price=110), "out_of_turn"),
        ("outside_limit", buyer, "propose", dict(negotiation_id=nid, price=91), "price_limit"),
    ]
    for label, client, name, arguments, error in cases:
        result = client.call(name, arguments)
        assert result["isError"] and result["structuredContent"]["error"] == error
        lines.append("{}: HTTP 200 {}".format(label, json.dumps(result, sort_keys=True)))
    snapshot = server.admin("snapshot", negotiation_id=nid)
    assert snapshot["turns"] == 0 and snapshot["turn"] == "buyer" and not snapshot["history"]
    return lines


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Refusing to overwrite existing evidence")
    with ServerProcess() as server:
        lines = check_boundaries(server)
    with args.output.open("x", encoding="utf-8") as stream:
        stream.write("\n".join(lines) + "\n")
    print("\n".join(lines))
