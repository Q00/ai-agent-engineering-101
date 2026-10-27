"""Record public market observations without changing the seller or its prices."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import ssl
import time
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parent
LOGS = ROOT / "logs" / "shop-monitor"
BASE = "https://market.ourolabs.ai"
NAME = "kcwarni"


def main():
    now = datetime.now(timezone.utc)
    LOGS.mkdir(parents=True, exist_ok=True)
    path = LOGS / (now.strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid4().hex[:8] + ".json")
    record = {"observed_at": now.isoformat(), "shop": NAME, "status": "error", "alerts": []}
    try:
        with httpx.Client(verify=ssl.create_default_context(), timeout=30) as client:
            registry_response = client.get(BASE + "/api/shops")
            registry_response.raise_for_status()
            registry = registry_response.json()
            round_number = registry["round"]
            board_response = client.get(BASE + "/api/board", params={"round": round_number})
            board_response.raise_for_status()
            board = board_response.json()
        shop = next(s for s in board["shops"] if s["name"] == NAME)
        listing = next(s for s in registry["shops"] if s["name"] == NAME)
        record.update(status="ok", round=round_number, alive=shop.get("alive"),
                      deals=shop.get("deals"), visits=shop.get("visits"),
                      stars=shop.get("stars"), reviews=shop.get("reviews"),
                      recent_reviews=listing.get("reviews", []))
        if shop.get("alive") is False:
            record["alerts"].append("Market reports shop is not alive; inspect connection and kit logs.")
        previous = None
        for candidate in sorted(LOGS.glob("*.json"), reverse=True):
            old = json.loads(candidate.read_text(encoding="utf-8"))
            if old.get("status") != "ok" or old.get("round") != round_number:
                continue
            elapsed = (now - datetime.fromisoformat(old["observed_at"])).total_seconds()
            if elapsed >= 1800:
                previous = old
                record["comparison_file"] = candidate.name
                record["comparison_seconds"] = elapsed
                break
        if previous is None:
            record["alerts"].append("Baseline saved; need an observation at least 30 minutes earlier in the same round.")
        else:
            for field in ("deals", "visits", "reviews"):
                if isinstance(record.get(field), (int, float)) and isinstance(previous.get(field), (int, float)):
                    record[field + "_change"] = record[field] - previous[field]
            if record.get("deals_change") == 0:
                record["alerts"].append("No new counted deals since baseline; check visits and connection before changing prices.")
            if record.get("deals_change", 0) < 0:
                record["alerts"].append("Deal count decreased; possible reset or changed dashboard; comparison is not reliable.")
            if (record.get("reviews_change", 0) >= 5 and isinstance(record.get("stars"), (int, float))
                    and isinstance(previous.get("stars"), (int, float))
                    and record["stars"] <= previous["stars"] - 0.05):
                record["alerts"].append("Displayed average rating fell by at least 0.05 with five or more new reviews; inspect reviews.")
            low = [r for r in record["recent_reviews"] if isinstance(r.get("stars"), (int, float)) and r["stars"] < 5]
            if low:
                record["alerts"].append(f"Public recent-review list contains {len(low)} reviews below 5 stars; inspect their text and dates separately.")
    except (httpx.HTTPError, ValueError, KeyError, TypeError, StopIteration, OSError) as error:
        record["alerts"].append(f"Observation failed: {type(error).__name__}: {error}")
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"log": str(path), **record}, ensure_ascii=False, indent=2))
    return 0 if record["status"] == "ok" else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--interval", type=int, default=0, help="Repeat in seconds (minimum 1800); 0 runs once")
    args = parser.parse_args()
    if args.interval and args.interval < 1800:
        parser.error("interval must be 0 or at least 1800 seconds")
    if not args.interval:
        raise SystemExit(main())
    while True:
        main()
        time.sleep(args.interval)
