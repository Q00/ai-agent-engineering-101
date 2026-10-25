"""Connect real harness sellers and buyers from profile commands; not the full trade test."""

import argparse
from datetime import datetime, timezone
import json
import os
import signal
import socket
import subprocess
import time

import httpx

from agent_profiles import command_for, load_profile, PROFILES, ROOT


def verify(name):
    config = load_profile(name)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    evidence = ROOT / "logs/step8" / (stamp + "-" + name)
    evidence.mkdir(parents=True)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    server_command = command_for(config, "seller", port=port)
    report = {"scope": "one purchase and one insufficient-budget case per pairing",
              "profile": config, "seller_command": server_command, "passed": False, "cases": []}
    with (evidence / "seller.stdout.txt").open("wb") as stdout, (evidence / "seller.stderr.txt").open("wb") as stderr:
        server = subprocess.Popen(server_command, cwd=ROOT, stdout=stdout, stderr=stderr,
                                   start_new_session=(os.name != "nt"))
        try:
            with httpx.Client(headers={"A2A-Version": "1.0"}, timeout=1, trust_env=False) as client:
                deadline = time.monotonic() + 20
                while True:
                    if server.poll() is not None:
                        raise RuntimeError("Seller exited before readiness")
                    try:
                        response = client.get(url + "/.well-known/agent-card.json")
                        response.raise_for_status()
                        break
                    except httpx.HTTPError:
                        if time.monotonic() >= deadline:
                            raise TimeoutError("Seller startup timed out")
                        time.sleep(0.1)
                (evidence / "card.json").write_bytes(response.content)
                card = response.json()
                interface = card["supportedInterfaces"][0]
                assert interface["protocolBinding"] == "JSONRPC" and interface["protocolVersion"] == "1.0"
                assert interface["url"] == url + "/"
                assert config["seller"]["harness"] in card["description"]
                assert config["seller"]["model"] in card["description"]
            for item, budget in (("lamp", 120), ("bookshelf", 230)):
                command = command_for(config, "buyer", url=url, item=item, budget=budget)
                with (evidence / f"{item}.stdout.txt").open("wb") as out, (evidence / f"{item}.stderr.txt").open("wb") as err:
                    process = subprocess.run(command, cwd=ROOT, stdout=out, stderr=err, timeout=300)
                assert process.returncode == 0, f"{item}: buyer exited {process.returncode}"
                outcome = json.loads((evidence / f"{item}.stdout.txt").read_text(encoding="utf-8").strip())
                assert outcome["harness"] == config["buyer"]["harness"]
                assert outcome["model"] == config["buyer"]["model"]
                if item == "lamp":
                    assert outcome["outcome"] == "purchased" and outcome["state"] == "TASK_STATE_COMPLETED", outcome
                    assert outcome["receipt"]["item"] == item and 100 <= outcome["receipt"]["price"] <= budget, outcome
                else:
                    assert outcome["outcome"] in ("walked_away", "seller_rejected", "canceled"), outcome
                    assert outcome["receipt"] is None, outcome
                case = {"item": item, "budget": budget, "buyer_command": command, "outcome": outcome}
                report["cases"].append(case)
                print(json.dumps({"profile": name, **outcome}), flush=True)
            report["passed"] = True
        except Exception as error:
            report["error"] = str(error)
            raise
        finally:
            if server.poll() is None:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/PID", str(server.pid), "/T", "/F"],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
                else:
                    os.killpg(server.pid, signal.SIGTERM)
                if server.poll() is None:
                    server.wait(timeout=10)
            (evidence / "verification.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
            print(f"STEP 8 evidence: {evidence}", flush=True)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=(*PROFILES, "all"), required=True)
    args = parser.parse_args()
    for profile in PROFILES if args.profile == "all" else (args.profile,):
        verify(profile)
