"""Read-only registration checks and public quote/cancel probes; never buys."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import urllib.request
import uuid

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[1]
restart = json.loads((OUT / "referral-deployment-restart.json").read_text())
base = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com",
                 (OUT / "referral-market-console.log").read_text())[0]
record = {"checked_at": datetime.now(timezone.utc).isoformat(), "public_url": base,
          "implementation_commit": restart["implementation_commit"],
          "scope": "Synthetic declared-identity rejection and quote/cancel only; no purchase or market result submission.",
          "limitations": ["Anonymous buyer identity is unavailable; named-denial probe does not prove real buyer identity.",
                          "A referral code claim does not verify a real review read.",
                          "Live phase follows actual rank; recovery model cases used TEST ONLY contexts."]}


def get(url):
    with urllib.request.urlopen(url, timeout=15) as response:
        return json.load(response)


def run():
    card = get(base + "/.well-known/agent-card.json")
    record["card"] = card
    assert "REVIEW10" in card["description"] and "120/200/290" in card["description"]
    endpoint = card["supportedInterfaces"][0]["url"]
    assert endpoint.startswith(base + "/")

    def rpc(method, params):
        request = {"jsonrpc": "2.0", "id": uuid.uuid4().hex, "method": method, "params": params}
        wire = urllib.request.Request(endpoint, data=json.dumps(request).encode(),
            headers={"Content-Type": "application/json", "A2A-Version": "1.0"})
        with urllib.request.urlopen(wire, timeout=90) as response:
            body = json.load(response)
        with (OUT / "referral-public-wire.jsonl").open("a") as log:
            log.write(json.dumps({"request": request, "response": body}) + "\n")
        assert body.get("id") == request["id"] and "error" not in body, body
        return body["result"].get("task", body["result"])

    def send(text, metadata=None):
        message = {"messageId": uuid.uuid4().hex, "role": "ROLE_USER", "parts": [{"text": text}]}
        if metadata:
            message["metadata"] = metadata
        return rpc("SendMessage", {"message": message})

    denied = send("lamp", {"buyerName": "cheapest-in-town", "probe": "TEST_ONLY_SELF_DECLARED_IDENTITY"})
    assert denied["status"]["state"] == "TASK_STATE_REJECTED" and not denied.get("artifacts")
    record["declared_identity_probe"] = {"state": denied["status"]["state"], "no_artifacts": True,
                                        "real_buyer_identity_verified": False}
    record["quotes"] = []
    normal = {"acquisition": {"lamp": 100, "chair": 180, "bookshelf": 260},
              "recovery": {"lamp": 120, "chair": 200, "bookshelf": 290}}
    discount = {"lamp": 108, "chair": 180, "bookshelf": 261}
    for item, code in [("lamp", False), ("lamp", True), ("chair", True), ("bookshelf", True)]:
        task = send(item + (" REVIEW10" if code else ""))
        state = json.loads((ROOT / ".market/rank-pricing.json").read_text())
        metadata = task["status"]["message"].get("metadata", {})
        expected = discount[item] if code and state["phase"] == "recovery" else normal[state["phase"]][item]
        probe = {"item": item, "code_claimed": code, "ask": metadata.get("ask"), "expected": expected,
                 "phase": state["phase"], "pricing_state": state, "state": task["status"]["state"],
                 "referral": metadata.get("reviewReferral")}
        try:
            assert state["policy_version"] == "rank-cycle-referral-v2"
            assert task["status"]["state"] == "TASK_STATE_INPUT_REQUIRED"
            assert metadata.get("ask") == expected and metadata.get("itemId") == item
        finally:
            canceled = rpc("CancelTask", {"id": task["id"]})
            probe["cancel_state"] = canceled["status"]["state"]
            record["quotes"].append(probe)
        assert probe["cancel_state"] == "TASK_STATE_CANCELED"
        print(json.dumps({"item": item, "code_claimed": code, "ask": metadata.get("ask"),
                          "phase": state["phase"], "passed": True}), flush=True)

    board = get("https://market.ourolabs.ai/api/board")
    (OUT / "referral-board-after.json").write_text(json.dumps(board, ensure_ascii=False) + "\n")
    before = json.loads((OUT / "baseline.json").read_text())
    for role in ("shops", "buyers"):
        expected = {r["name"]: r["joined"] for r in before["profiles_before"][role]}
        actual = {r["name"]: r["joined"] for r in board[role]
                  if (r.get("owner") or {}).get("login") == "SUNGMYEONGGI"}
        assert expected == actual
        row = next(r for r in board[role] if r["name"] == "myeonggi")
        record[role + "_online"] = row["alive"]
        assert row["alive"]
        if role == "shops":
            assert row["card"]["url"] == base
    record["profiles_unchanged"] = True
    record["keys_unchanged"] = all(hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == digest
                                   for name, digest in before["key_hashes"].items())
    assert record["keys_unchanged"]
    record["source_matches_deployment"] = all(hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == digest
                                             for name, digest in restart["source_hashes"].items())
    assert record["source_matches_deployment"]
    kits = []
    for process in Path("/proc").iterdir():
        if process.name.isdigit():
            try:
                args = (process / "cmdline").read_bytes().split(b"\0")
                if b"myeonggi" in args and b"join" in args and any(a.endswith(b"/market_kit.py") for a in args):
                    kits.append(int(process.name))
            except OSError:
                pass
    record["kit_pids"] = kits
    assert kits == [restart["kit_pid"]]


try:
    run()
    record["passed"] = True
except Exception as exc:
    record.update(passed=False, error=type(exc).__name__ + ": " + str(exc))
finally:
    record["finished_at"] = datetime.now(timezone.utc).isoformat()
    name = "referral-deployment-verified.json" if record["passed"] else (
        "referral-verification-failed-" + datetime.now(timezone.utc).strftime("%H%M%S") + ".json")
    with (OUT / name).open("x") as log:
        log.write(json.dumps(record, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({k: v for k, v in record.items() if k not in ("card", "quotes")}), flush=True)
raise SystemExit(0 if record["passed"] else 1)
