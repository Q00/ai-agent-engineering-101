"""Read-only HW operating check: no model calls, restarts, or server access."""

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import ssl
import subprocess

import httpx

ROOT = Path(__file__).resolve().parent


def main():
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    out = ROOT / "logs/hw-always-on" / stamp
    out.mkdir(parents=True)
    process = json.loads((ROOT / ".market/process.json").read_text(encoding="utf-8-sig"))
    pid = int(process["pid"])
    local_alive = False
    if os.name == "nt":
        # Check PID identity as well as existence; a stale PID can belong to another process.
        query = (f"$p=Get-CimInstance Win32_Process -Filter 'ProcessId={pid}'; "
                 "[bool]($p -and $p.CommandLine -match 'run_market.py') | ConvertTo-Json")
        check = subprocess.run(["powershell", "-NoProfile", "-Command", query],
                               capture_output=True, text=True, check=True)
        local_alive = json.loads(check.stdout.strip())
    with httpx.Client(verify=ssl.create_default_context(), timeout=40) as client:
        def fetch(round_number):
            response = client.get(f"https://market.ourolabs.ai/api/board?round={round_number}")
            response.raise_for_status()
            value = response.json()
            (out / f"board-round{round_number}.json").write_text(
                json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
            return str(round_number), {
                kind: next((row for row in value[kind] if row["name"] == "kcwarni"), None)
                for kind in ("shops", "buyers")}
        with ThreadPoolExecutor(max_workers=3) as pool:
            rounds = dict(pool.map(fetch, (0, 1, 2)))
        shop = rounds["2"]["shops"]
        response = client.get(shop["card"]["url"].rstrip("/") + "/.well-known/agent-card.json")
        response.raise_for_status()
        card = response.json()
        (out / "public-card.json").write_text(json.dumps(card, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = (ROOT / ".market/buyer-memory.jsonl").read_text(encoding="utf-8").splitlines()
    memory = [json.loads(line) for line in lines if line.strip()]
    checks = {"local_kit_process": local_alive, "shop_alive": shop["alive"],
              "buyer_alive": rounds["2"]["buyers"]["alive"],
              "r2_buyer_counted_purchase": rounds["2"]["buyers"]["deals"] >= 1,
              "github_owner": all(rounds["2"][kind]["owner"]["login"] == "kcwarni"
                                  for kind in ("shops", "buyers")),
              "public_card": card["name"] == shop["card"]["name"]}
    result = {"checked_at": datetime.now(timezone.utc).isoformat(), "deployment": "current Windows PC + Quick Tunnel",
              "excluded_servers": ["OCI", "C-SERVER"], "process": process, "checks": checks,
              "rounds": rounds, "memory_records": len(memory),
              "memory_outcomes": Counter(row["outcome"] for row in memory),
              "direct_mismatch_records": [row for row in memory if row["outcome"] == "receipt_mismatch"],
              "last_memory_timestamp": memory[-1]["timestamp"] if memory else None}
    (out / "status.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"evidence": str(out), "checks": checks, "rounds": {
        number: {kind: {key: row.get(key) for key in ("alive", "deals", "reviews", "stars", "revenue", "share", "orders")}
                 for kind, row in entries.items() if row}
        for number, entries in rounds.items()}, "memory_outcomes": result["memory_outcomes"]},
                     ensure_ascii=False, indent=2))
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
