"""Read-only verification of the registered R2 shop and its public event card."""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import ssl

import httpx
from cryptography.hazmat.primitives import serialization


def main():
    root = Path(__file__).resolve().parent
    notice = json.loads((root / "review_event.json").read_text(encoding="utf-8"))["notice"]
    with httpx.Client(verify=ssl.create_default_context(), timeout=40) as client:
        board = client.get("https://market.ourolabs.ai/api/board?round=2")
        board.raise_for_status()
        board = board.json()
        participants = {kind: next(row for row in board[kind] if row["name"] == "kcwarni")
                        for kind in ("shops", "buyers")}
        url = participants["shops"]["card"]["url"]
        response = client.get(url.rstrip("/") + "/.well-known/agent-card.json")
        response.raise_for_status()
        card = response.json()
        stats = client.get("https://market.ourolabs.ai/api/stats")
        stats.raise_for_status()
        stats = stats.json()
    checks = {
        "shop_alive": participants["shops"]["alive"],
        "buyer_alive": participants["buyers"]["alive"],
        "owner_preserved": all(row["owner"]["login"] == "kcwarni" for row in participants.values()),
        "public_version": card["version"] == "0.4.0",
        "public_notice": notice in card["description"],
        "registered_notice": notice in participants["shops"]["card"]["description"],
        "homepage_has_r2": "2" in stats["rounds"],
    }
    before = root / "logs/review-event/buyer-memory-before-restart.jsonl"
    if before.exists():
        checks["memory_preserved"] = (root / ".market/buyer-memory.jsonl").read_bytes().startswith(before.read_bytes())
    identity = json.loads((root / "logs/lab1-steps345/identity-before-restart.json").read_text(encoding="utf-8-sig"))
    fingerprints = {}
    for kind in ("shop", "buyer"):
        key = serialization.load_pem_private_key((root / f"{kind}_key.pem").read_bytes(), password=None)
        public = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        fingerprints[kind] = hashlib.sha256(public).hexdigest()
        checks[f"{kind}_key_preserved"] = fingerprints[kind] == identity[f"{kind}_public_key_sha256"]
    result = {"checked_at": datetime.now(timezone.utc).isoformat(), "current": board.get("current"),
              "url": url, "checks": checks, "participants": participants, "public_card": card,
              "homepage_r2": stats["rounds"].get("2"),
              "public_key_fingerprints": fingerprints}
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    output = root / "logs/review-event" / f"live-verification-{stamp}.json"
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"url": url, "checks": checks,
                      "shop_deals": participants["shops"]["deals"],
                      "buyer_deals": participants["buyers"]["deals"]}, indent=2))
    if not all(checks.values()):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
