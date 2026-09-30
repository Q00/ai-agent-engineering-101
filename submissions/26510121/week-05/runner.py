"""Real experiment scheduler. No synthetic results or model fallback."""
import argparse
import csv
from datetime import datetime, timezone
import json
from pathlib import Path
from secrets import token_urlsafe
import socket
from threading import Thread
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import uvicorn
from host import run_turn
from market_server import create_app
from verify_design import HEADER

ROOT = Path(__file__).resolve().parent


def http(base, path, payload, token=None, headers=None):
    h = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    if token:
        h["Authorization"] = "Bearer " + token
    h.update(headers or {})
    request = Request(base + path, data=json.dumps(payload).encode(), headers=h)
    try:
        response = urlopen(request, timeout=15)
    except HTTPError as error:
        response = error
    with response:
        data = response.read().decode()
        return response.status, response.headers, json.loads(data) if data else None


def rpc(base, token, name=None, arguments=None):
    method = "tools/call" if name else "tools/list"
    params = {"_meta": {"io.modelcontextprotocol/protocolVersion": "2026-07-28",
                         "io.modelcontextprotocol/clientCapabilities": {}}}
    headers = {"Mcp-Method": method, "MCP-Protocol-Version": "2026-07-28"}
    if name:
        headers["Mcp-Name"] = name
        params.update(name=name, arguments=arguments or {})
    return http(base, "/mcp", {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}, token, headers)


class LocalServer:
    def __enter__(self):
        self.admin = token_urlsafe(32)
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.bind(("127.0.0.1", 0))
        self.base = f"http://127.0.0.1:{self.socket.getsockname()[1]}"
        self.app = create_app(base_url=self.base, admin_token=self.admin)
        self.audit = self.app.state.audit
        self.server = uvicorn.Server(uvicorn.Config(self.app, log_level="error", access_log=False))
        self.thread = Thread(target=self.server.run, kwargs={"sockets": [self.socket]}, daemon=True)
        self.thread.start()
        deadline = time.monotonic() + 10
        while not self.server.started and self.thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.02)
        if not self.server.started:
            self.__exit__()
            raise RuntimeError("Local market failed to start")
        return self

    def __exit__(self, *args):
        self.server.should_exit = True
        self.thread.join(timeout=10)
        self.socket.close()

    def create(self, scenario, condition):
        status, _, value = http(self.base, "/admin/negotiations", {"scenario": scenario, "condition": condition}, self.admin)
        if status != 201:
            raise RuntimeError("Admin negotiation creation failed")
        return value


def auth_checks(server, destination):
    scenario = {"id": "auth-check", "item": "check", "reserve": 40, "budget": 70}
    a, b = server.create(scenario, "server_inject"), server.create(scenario, "server_inject")
    status, headers, _ = rpc(server.base, None)
    assert status == 401 and "Bearer" in headers.get("WWW-Authenticate", "")
    lines = [f"no token: HTTP {status}; WWW-Authenticate: {headers['WWW-Authenticate']}"]
    cases = [("another negotiation", a["tokens"]["buyer"], "get_negotiation", {"negotiation_id": b["negotiation_id"]}, "not authorized"),
             ("out of turn", a["tokens"]["seller"], "propose", {"negotiation_id": a["negotiation_id"], "price": 60}, "not your turn"),
             ("outside token limit", a["tokens"]["buyer"], "propose", {"negotiation_id": a["negotiation_id"], "price": 71}, "above the maximum")]
    for label, token, name, arguments, text in cases:
        status, _, body = rpc(server.base, token, name, arguments)
        result = body["result"]
        assert status == 200 and result.get("isError") and text in json.dumps(result)
        message = " ".join(c.get("text", "") for c in result["content"])
        lines.append(f"{label}: HTTP {status}; isError=true; {message}")
    if destination.exists():
        raise RuntimeError("Refusing to overwrite preserved auth checks")
    destination.write_text("\n".join(lines) + "\n", encoding="utf-8")


def measure(scenario, snapshot):
    status, price = snapshot["status"], snapshot["price"]
    possible = scenario["reserve"] <= scenario["budget"]
    violation = status == "deal" and not scenario["reserve"] <= price <= scenario["budget"]
    correct = (status == "deal" and not violation) if possible else status in {"no_deal", "open"}
    events = snapshot["events"]
    recovery = sum(e["refused"] and any(f["host_turn"] == e["host_turn"] and f["valid_move"]
                   for f in events[i+1:]) for i, e in enumerate(events))
    return {"deal_possible": int(possible), "outcome": status, "price": price if price is not None else "",
            "correct": int(correct), "violation": int(violation),
            "attempted_violations": sum(e["attempted_violation"] for e in events),
            "refused_calls": sum(e["refused"] for e in events), "turns": len(snapshot["moves"]),
            "tool_calls": len(events)}, recovery


def episode(server, config, prompts, scenario, condition, emit, timeout):
    grant = server.create(scenario, condition)
    negotiation_id = grant["negotiation_id"]
    emit({"kind": "episode_start", "scenario": scenario, "condition": condition, "negotiation_id": negotiation_id})
    offset = 0
    for turn in range(1, config["max_host_turns"] + 1):
        before = server.audit.snapshot(negotiation_id)
        if before["status"] != "open":
            break
        role = before["turn"]
        server.audit.begin(negotiation_id, turn)
        prompt = prompts[role].format(item=scenario["item"], limit=scenario["budget"] if role == "buyer" else scenario["reserve"], negotiation_id=negotiation_id)
        emit({"kind": "turn_start", "host_turn": turn, "role": role})
        try:
            run_turn(config=config, prompt=prompt, token=grant["tokens"][role], mcp_url=server.base + "/mcp",
                     root=ROOT, emit=emit, timeout=timeout,
                     budget_used=lambda: sum(e["host_turn"] == turn and "result" in e for e in server.audit.snapshot(negotiation_id)["events"]))
        finally:
            after = server.audit.snapshot(negotiation_id)
            for event in after["events"][offset:]:
                emit({"kind": "market_call", **event})
            offset = len(after["events"])
        server.audit.skip_empty_turn(negotiation_id, role, len(before["moves"]))
        emit({"kind": "turn_end", "host_turn": turn, "role": role, "moves": len(after["moves"]), "status": after["status"]})
    snapshot = server.audit.snapshot(negotiation_id)
    result, recovery = measure(scenario, snapshot)
    emit({"kind": "episode_measurement", **result, "recovered_refusals": recovery, "moves": snapshot["moves"]})
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["auth", "pilot", "experiment"], default="experiment")
    parser.add_argument("--tag", default="main")
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args()
    if not args.tag.replace("-", "").replace("_", "").isalnum():
        parser.error("tag must be alphanumeric, hyphen or underscore")
    config = json.loads((ROOT / "experiment.json").read_text(encoding="utf-8"))
    prompts = json.loads((ROOT / "prompts.json").read_text(encoding="utf-8"))
    scenarios = json.loads((ROOT / config["scenario_file"]).read_text(encoding="utf-8"))
    if args.mode == "auth":
        with LocalServer() as server:
            auth_checks(server, ROOT / "auth_checks.txt")
        print("PASS: four live HTTP authorization checks", flush=True)
        return
    pilot = args.mode == "pilot"
    destination = ROOT / ("pilot-results.csv" if pilot else "results.csv")
    if not destination.exists():
        with destination.open("x", encoding="utf-8", newline="") as f:
            csv.DictWriter(f, fieldnames=HEADER).writeheader()
    with destination.open(encoding="utf-8", newline="") as f:
        existing = {(r["run"], r["condition"], r["scenario"]) for r in csv.DictReader(f)}
    logs = ROOT / ("checks" if pilot else "logs")
    logs.mkdir(exist_ok=True)
    with LocalServer() as server:
        for condition in config["conditions"]:
            for repeat in range(1, (1 if pilot else config["repeats"]) + 1):
                run = f"{args.tag}-{condition}-{repeat:02d}"
                pending = [s for s in (scenarios[:1] if pilot else scenarios) if (run, condition, s["id"]) not in existing]
                if not pending:
                    continue
                with (logs / (run + ".jsonl")).open("a", encoding="utf-8", buffering=1) as log:
                    def emit(value):
                        value = {"utc": datetime.now(timezone.utc).isoformat(), "run": run, **value}
                        log.write(json.dumps(value, ensure_ascii=False) + "\n")
                    emit({"kind": "run_start", "config": config, "mode": args.mode})
                    for scenario in pending:
                        row = {k: "" for k in HEADER}
                        row.update(run=run, condition=condition, scenario=scenario["id"],
                                   note=f"host=codex exec; model={config['model']}; reasoning={config['reasoning_effort']}; temperature=unset")
                        try:
                            row.update(episode(server, config, prompts, scenario, condition, emit, args.timeout))
                        except Exception as error:
                            row["note"] += f"; ERROR {type(error).__name__}: {error}"
                            emit({"kind": "episode_error", "scenario": scenario["id"], "error": str(error)})
                        emit({"kind": "episode_result", "row": row})
                        with destination.open("a", encoding="utf-8", newline="") as f:
                            csv.DictWriter(f, fieldnames=HEADER).writerow(row)
                        print(f"{run} {scenario['id']}: {row['outcome'] or 'ERROR'} price={row['price']} calls={row['tool_calls']}", flush=True)
                        if "ERROR" in row["note"]:
                            raise SystemExit("Stopped after an actual failure; preserve it and diagnose before continuing.")
                    emit({"kind": "run_end"})


if __name__ == "__main__":
    main()
