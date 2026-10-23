"""Preserve actual Round 0 mismatch and later market selection evidence; no trades."""

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main():
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    out = ROOT / "logs/challenge-2" / stamp
    out.mkdir(parents=True)
    manifest = {}

    def preserve(source, name):
        content = source.read_bytes()
        target = out / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        manifest[name] = {"source": source.relative_to(ROOT).as_posix(),
                          "sha256": hashlib.sha256(content).hexdigest()}
        return content

    raw = ROOT / "logs/buyer/20261020T105617121814Z-e001b0b7"
    for source in sorted(raw.iterdir()):
        if source.is_file():
            preserve(source, "original-buyer/" + source.name)
    session = json.loads((raw / "session.json").read_text(encoding="utf-8"))
    request = json.loads((raw / "03.request.json").read_text(encoding="utf-8"))
    response = json.loads((raw / "03.response.txt").read_text(encoding="utf-8"))
    task = response["result"]["task"]
    offer = int(request["body"]["params"]["message"]["parts"][0]["text"])
    receipt = task["artifacts"][0]["parts"][0]["data"]
    assert task["id"] == session["task_id"] == receipt["task"]
    assert request["body"]["params"]["message"]["taskId"] == task["id"]
    assert task["status"]["message"]["parts"][0]["text"] == f"Deal: chair at {offer}."
    assert offer == 234 and receipt["price"] == 269 and session["budget"] == 250

    # The kit used a local proxy in Round 0; link it to the actual remote shop/order.
    kit = (ROOT / "logs/market-kit.log").read_bytes()
    lines = kit.decode("utf-8").splitlines(keepends=True)
    start = next(i for i, line in enumerate(lines) if "[order o-3506d008]" in line)
    end = next(i for i in range(start, len(lines)) if "[market] ledger #1307:" in lines[i])
    excerpt = "".join(lines[start:end + 1])
    assert "shop for this order: maple-home" in excerpt
    assert "not signing" in excerpt and "verified False" in excerpt
    (out / "kit-order-excerpt.txt").write_text(excerpt, encoding="utf-8")
    manifest["kit-order-excerpt.txt"] = {
        "source": "logs/market-kit.log", "source_sha256_at_read": hashlib.sha256(kit).hexdigest(),
        "line_start": start + 1, "line_end": end + 1, "kind": "derived excerpt; original unchanged"}

    memory_bytes = (ROOT / ".market/buyer-memory.jsonl").read_bytes()
    memory = [json.loads(line) for line in memory_bytes.decode("utf-8").splitlines() if line.strip()]
    by_task = {row.get("task"): row for row in memory if row.get("task")}
    warnings = Counter()
    selected_warnings = Counter()
    samples = []
    scanned = excluded = 0
    for source in sorted((ROOT / "logs/market-buyer").glob("*.json")):
        data = json.loads(source.read_text(encoding="utf-8"))
        record = data.get("record", {})
        actual = by_task.get(record.get("task"))
        # Test runs (including Kit Shop North/South) have no matching live-memory record.
        if not actual or any(actual.get(k) != record.get(k)
                             for k in ("timestamp", "shop_name", "url_at_time", "item", "budget")):
            excluded += 1
            continue
        scanned += 1
        for candidate in data.get("selection_evidence", []):
            if candidate.get("repeated_public_price_complaints"):
                warnings[candidate["name"]] += 1
                if candidate["name"] == record["shop_name"]:
                    selected_warnings[candidate["name"]] += 1
                if candidate["name"] == "maple-home" and len(samples) < 3:
                    preserve(source, "later-selections/" + source.name)
                    samples.append({"source": source.relative_to(ROOT).as_posix(),
                                    "timestamp": record["timestamp"], "task": record["task"],
                                    "selected": record["shop_name"], "candidate": candidate})
    assert samples and all(s["selected"] != "maple-home" for s in samples)
    # Capture current review input separately: it is not an archived input for old decisions.
    shops_bytes = preserve(ROOT / ".market/shops.json", "current-shops-input.json")
    maple = next(s for s in json.loads(shops_bytes) if s["name"] == "maple-home")
    direct = [row for row in memory if row["outcome"] == "receipt_mismatch"]
    mismatches = [row for row in memory if isinstance(row.get("receipt"), dict) and any(
        row["receipt"].get(k) != row.get(v)
        for k, v in (("price", "agreed_price"), ("item", "item"), ("task", "task")))]
    result = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "actual_round0_case": {"shop": "maple-home", "order": "o-3506d008", "ledger": 1307,
                               "task": task["id"], "offer_and_deal_price": offer,
                               "receipt_price": receipt["price"], "budget": session["budget"],
                               "buyer_signed": False, "counted_deal": False},
        "live_memory": {"records": len(memory), "sha256_at_read": hashlib.sha256(memory_bytes).hexdigest(),
                        "outcomes": dict(Counter(row["outcome"] for row in memory)),
                        "recorded_mismatches": len(direct), "independent_field_mismatches": len(mismatches),
                        "round0_task_present": task["id"] in by_task},
        "selection_audit": {"matched_live_logs": scanned, "unmatched_logs_excluded": excluded,
                            "warning_candidate_counts": dict(warnings),
                            "warning_candidates_selected": dict(selected_warnings), "samples": samples},
        "current_maple_reviews": maple["reviews"],
        "finding": "Actual price-mismatch encounter and later public-review-based avoidance evidenced.",
        "limitations": ["No automatic migration of Round 0 incident into current buyer memory.",
                        "Later avoidance uses repeated public complaints, not a stored direct-mismatch block.",
                        "Current review snapshot does not establish exact historical review input.",
                        "Nonselection alone cannot prove which ranking factor was decisive.",
                        "Receipt signature is retained, not independently reverified against a historical key."],
        "files": manifest,
    }
    (out / "audit.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"evidence": str(out), **{k: result[k] for k in
                     ("actual_round0_case", "live_memory", "selection_audit", "finding")}},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
