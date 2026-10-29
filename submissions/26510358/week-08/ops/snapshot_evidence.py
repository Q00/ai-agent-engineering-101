"""Read-only market snapshots and isolated log analysis; never places an order.

Run from the repository root. Each output directory must be new and below this
week's logs directory. Counts describe captured evidence, not complete history.
"""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
import tempfile
from urllib.request import urlopen

WEEK08 = Path(__file__).resolve().parents[1]
WEEK07 = WEEK08.parent / "week-07"
sys.path.insert(0, str(WEEK07))
from market_memory import MarketMemory


def timestamp(text):
    value = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if value.tzinfo is None:
        raise ValueError("Use an ISO 8601 timestamp with a timezone")
    return value.timestamp()


def summarize_log(path, memory_path, since=None):
    """Parse only official prefixes; missing evidence remains missing."""
    feedback = MarketMemory(memory_path).ingest_kit_feedback(path)
    started, quoted, settlements = set(), set(), {}
    asks, seller_prices = defaultdict(list), defaultdict(list)
    failures, rounds = Counter(), set()
    with Path(path).open(encoding="utf-8", errors="replace") as stream:
        for line in stream:
            if not line.endswith("\n"):
                continue
            entry = re.match(r"^\d{2}:\d{2}:\d{2} (.*)$", line.rstrip("\n"))
            if not entry:
                continue
            text = entry[1]
            order = re.fullmatch(r"\[order [^]]+\] [^,]+, budget [^,]+, round (\d+)", text)
            if order:
                rounds.add(int(order[1]))
            paid = re.fullmatch(r"\[acp\] settled (\S+) at (\d+), ledger #(\d+)", text)
            if paid:
                settlements.setdefault(paid[3], (paid[1], int(paid[2])))
            if text.startswith("[market] ledger #"):
                failed = re.search(r"\(failed: ([^)]+)\)", text)
                if failed:
                    failures.update(failed[1].split(", "))
            if not text.startswith("[seller] {"):
                continue
            try:
                event = json.loads(text[len("[seller] "):])
            except ValueError:
                continue
            if not isinstance(event, dict) or event.get("event") != "trade_stage" or event.get("side") != "seller":
                continue
            task = event.get("task_id")
            if not isinstance(task, str) or not task:
                continue
            if event.get("stage") == "negotiation_started":
                started.add(task)
            if event.get("stage") != "quote_issued" or task not in started or task in quoted:
                continue
            quoted.add(task)
            if since is not None:
                try:
                    if timestamp(event.get("time", "")) < since:
                        continue
                except (ValueError, TypeError):
                    continue
            price, item = event.get("price"), event.get("item")
            if type(price) in (int, float) and math.isfinite(price) and price > 0 and isinstance(item, str):
                asks[item].append(price)
    for item, price in settlements.values():
        seller_prices[item].append(price)

    def stats(groups):
        return {item: {"n": len(values), "mean": sum(values) / len(values),
                       "min": min(values), "max": max(values)} for item, values in sorted(groups.items())}

    return {"observed_buyer_rounds": sorted(rounds),
            "buyer_round3_settlements": sum(row.get("settled") is True for row in feedback),
            "buyer_verified_results_by_round": dict(Counter(str(row.get("round")) for row in feedback if row["verified"])),
            "seller_round3_settlement_lines": len(settlements), "seller_settled_prices": stats(seller_prices),
            "opening_quotes": stats(asks), "market_rejected_check_occurrences": dict(failures),
            "since_applies_to": "opening_quotes_only", "opening_quotes_since_utc_epoch": since,
            "limits": ["Partial capture; these are observed counts, not deadline totals.",
                       "An opening quote requires negotiation_started and the first quote_issued for that task in this file.",
                       "Settlement lines have no date; --since cannot filter them or rejection lines.",
                       "Kit feedback is ingested in isolated temporary memory; live memory is never changed.",
                       "Seller receipt issuance alone does not count as a settlement."]}


def capture(output, log, market, name, since=None):
    output, log = Path(output).resolve(), Path(log).resolve()
    if not output.is_relative_to((WEEK08 / "logs").resolve()):
        raise ValueError("Output must be below week-08/logs")
    output.mkdir(parents=True, exist_ok=False)
    manifest = {"started_at_utc": datetime.now(timezone.utc).isoformat(), "name": name,
                "network_methods": ["GET"], "market": market, "files": {}, "errors": {}}

    def fetch(filename, url):
        try:
            with urlopen(url, timeout=20) as response:
                data = response.read()
                status = response.status
            with (output / filename).open("xb") as stream:
                stream.write(data)
            manifest["files"][filename] = {"url": url, "http_status": status,
                                            "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
            return json.loads(data)
        except Exception as exc:
            manifest["errors"][filename] = type(exc).__name__ + ": " + str(exc)
            return None

    board = fetch("board.json", market + "/api/board")
    round3 = fetch("board-round3.json", market + "/api/board?round=3")
    trades = fetch("trades.json", market + "/api/trades")
    fetch("ledger.json", market + "/api/ledger?after=-20")
    report = {"current_round": board.get("current") if board else None, "round3_final": False,
              "leaderboard": {}, "prices": {}, "scope": "Captured windows only; not an exhaustive market export"}
    report["round3_board_available"] = isinstance(round3, dict) and round3.get("round") == 3
    price_groups = defaultdict(list)
    for trade in trades or []:
        if trade.get("shop") == name and trade.get("verified") is True and trade.get("counted"):
            price = trade.get("price")
            if type(price) in (int, float) and math.isfinite(price) and price > 0:
                price_groups[(trade.get("round"), trade.get("item"))].append(price)
    report["prices"] = [{"round": rnd, "item": item, "n": len(values),
                          "mean": sum(values) / len(values), "min": min(values), "max": max(values)}
                         for (rnd, item), values in price_groups.items()]
    for role in ("shops", "buyers"):
        rows = round3.get(role, []) if report["round3_board_available"] else []
        row = next((r for r in rows if r.get("name") == name), None)
        report["leaderboard"][role] = {"round3_row": row,
            "position_in_returned_list": next((i for i, r in enumerate(rows, 1) if r.get("name") == name), None),
            "note": "List position only; the instructor's final leaderboard defines rank."}
    own = next((r for r in (board or {}).get("shops", []) if r.get("name") == name), None)
    if own and isinstance(own.get("card", {}).get("url"), str):
        base = own["card"]["url"].rstrip("/")
        fetch("public-acp.json", base + "/.well-known/acp.json")
        fetch("public-agent-card.json", base + "/.well-known/agent-card.json")

    private = WEEK08 / ".market"
    private.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=private) as temporary:
        snapshot = Path(temporary) / "market-kit.log"
        raw_hash = hashlib.sha256()
        with log.open("rb") as source, snapshot.open("xb") as target:
            stat = os.fstat(source.fileno())
            remaining, size = stat.st_size, stat.st_size
            while remaining:
                chunk = source.read(min(1024 * 1024, remaining))
                if not chunk:
                    raise RuntimeError("Live log was truncated during capture; retained output is incomplete")
                target.write(chunk)
                raw_hash.update(chunk)
                remaining -= len(chunk)
        with snapshot.open("rb") as source, (output / "market-kit.log.gz").open("xb") as target:
            with gzip.GzipFile(fileobj=target, mode="wb", mtime=0) as compressed:
                while chunk := source.read(1024 * 1024):
                    compressed.write(chunk)
        manifest["raw_log"] = {"source": str(log), "bytes": size, "sha256": raw_hash.hexdigest(),
                               "device": stat.st_dev, "inode": stat.st_ino,
                               "source_modified_utc": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
                               "snapshot": "market-kit.log.gz", "partial": True}
        report["log"] = summarize_log(snapshot, Path(temporary) / "memory.json", since)
    report["round3_started_observed"] = isinstance(report["current_round"], int) and report["current_round"] >= 3
    if not report["round3_started_observed"]:
        report["round3_status"] = "not_started_observed; Round 2 totals do not satisfy Round 3 requirements"
    manifest["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
    for filename, value in (("manifest.json", manifest), ("summary.json", report)):
        with (output / filename).open("x") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--log", type=Path, default=WEEK07 / "logs/market-kit.log")
    parser.add_argument("--market", default="https://market.ourolabs.ai")
    parser.add_argument("--name", default="myeonggi")
    parser.add_argument("--since", type=timestamp, help="ISO timestamp; filters opening quotes only")
    args = parser.parse_args()
    print(json.dumps(capture(args.output_dir, args.log, args.market.rstrip("/"), args.name, args.since),
                     ensure_ascii=False, indent=2))
