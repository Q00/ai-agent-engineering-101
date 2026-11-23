"""Deliberate CLI error-propagation probe, excluded from scored experiments."""
import argparse
import json
from pathlib import Path
from host import run_turn
from runner import ROOT, LocalServer


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture", default="cli-refusal-probe.jsonl")
    args = parser.parse_args()
    if Path(args.capture).name != args.capture or "/" in args.capture or "\\" in args.capture or not args.capture.endswith(".jsonl"):
        parser.error("capture must be a JSONL filename inside checks")
    path = ROOT / "checks" / args.capture
    config = json.loads((ROOT / "experiment.json").read_text(encoding="utf-8"))
    with path.open("x", encoding="utf-8", buffering=1) as log, LocalServer() as server:
        grant = server.create({"id": "deliberate-cli-probe", "item": "test item", "reserve": 5, "budget": 10}, "server_inject")
        identifier = grant["negotiation_id"]
        server.audit.begin(identifier, 1)
        def emit(value):
            log.write(json.dumps(value, ensure_ascii=False) + "\n")
        emit({"kind": "probe_start", "purpose": "Deliberate guard test, excluded from results.csv and model experiment metrics"})
        prompt = ("You are a transport-test client, not a scored negotiation agent. Use only market tools. "
                  f"Read get_negotiation for negotiation_id {identifier}. "
                  "The buyer token's true maximum is 10. To verify error propagation, deliberately call "
                  "propose with price 11 exactly once. It should return a tool error. Read its reason, "
                  "then recover in this same turn by proposing price 10. Stop after that move succeeds. "
                  "Do not use shell commands, read local files, or modify files.")
        try:
            run_turn(config=config, prompt=prompt, token=grant["tokens"]["buyer"], mcp_url=server.base + "/mcp",
                     root=ROOT, emit=emit,
                     budget_used=lambda: len(server.audit.snapshot(identifier)["events"]))
        finally:
            snapshot = server.audit.snapshot(identifier)
            for event in snapshot["events"]:
                emit({"kind": "market_call", **event})
        calls = snapshot["events"]
        refused = [e for e in calls if e["tool"] == "propose" and e["arguments"].get("price") == 11 and e["refused"]]
        valid = [e for e in calls if e["tool"] == "propose" and e["arguments"].get("price") == 10 and e["valid_move"]]
        assert len(refused) == len(valid) == 1 and calls.index(refused[0]) < calls.index(valid[0])
        assert snapshot["moves"] == [{"role": "buyer", "act": "propose", "price": 10}]
        emit({"kind": "probe_result", "passed": True, "refused_price": 11, "valid_price": 10, "host_turn": 1})
    print("PASS: real Codex received a deliberate price refusal and recovered in the same host turn")


if __name__ == "__main__":
    main()
