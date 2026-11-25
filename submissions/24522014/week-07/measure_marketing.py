"""Read-only observation of marketing exposure, genuine pledges, and sales."""
from concurrent.futures import ThreadPoolExecutor
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import ssl

import httpx

ROOT = Path(__file__).resolve().parent
RESERVES = {"lamp": 100, "chair": 180, "bookshelf": 260}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', choices=['marketing', 'purchase', 'refund'], default='marketing')
    args = parser.parse_args()
    directory = {'marketing':'marketing', 'purchase':'purchase-experience', 'refund':'satisfaction-refund'}[args.profile]
    deployment = json.loads((ROOT / '.market' / f'{args.profile}-deployment.json').read_text(encoding="utf-8"))
    started = datetime.fromisoformat(deployment["started_at"]).astimezone(timezone.utc)
    cutoff = deployment["cutoff_seq"]
    diagnostics = set()
    for path in (ROOT / "logs/sales-experiment").glob("public-diagnostic-*.json"):
        record = json.loads(path.read_text(encoding="utf-8"))
        diagnostics.update(row["task"] for row in record.get("results", []))
    openings, pledges = {}, {}
    for path in (ROOT / "logs/seller").glob("*.json"):
        stamp = datetime.strptime(path.name.split("-")[0], "%Y%m%dT%H%M%S%fZ").replace(tzinfo=timezone.utc)
        if stamp < started:
            continue
        record = json.loads(path.read_text(encoding="utf-8"))
        if record.get("phase") == "opening" and record["task_id"] not in diagnostics:
            openings[record["task_id"]] = record
    for path in (ROOT / "logs/review-promises").glob("*.json"):
        record = json.loads(path.read_text(encoding="utf-8"))
        if datetime.fromisoformat(record["timestamp"]) >= started and record["task"] not in diagnostics:
            pledges[record["task"]] = record
    with httpx.Client(verify=ssl.create_default_context(), timeout=50) as client:
        def get(path):
            response = client.get("https://market.ourolabs.ai" + path)
            response.raise_for_status()
            return response.json()
        last = get("/api/ledger?after=-1")[0]["seq"]
        with ThreadPoolExecutor(max_workers=8) as pool:
            pages = list(pool.map(lambda seq: get(f"/api/ledger?after={seq}"), range(cutoff, last, 500)))
    entries = {row["seq"]: row for page in pages for row in page if cutoff < row["seq"] <= last}
    assert len(entries) == last - cutoff
    rows = [row for row in entries.values() if row["round"] == 2 and row["kind"] == "result"
            and row["data"].get("shop") in deployment.get('urls', [deployment['url']])]
    purchases = [row["data"] for row in rows if row["data"].get("verified")]
    promised_purchases = [row for row in purchases if row["receipt"]["task"] in pledges]
    ratings = [row["stars"] for row in purchases if type(row.get("stars")) in (int, float)]
    result = {"checked_at": datetime.now(timezone.utc).isoformat(), "deployment": deployment,
              "last_seq": last, "opening_tasks_excluding_diagnostics": len(openings),
              "promise_tasks_excluding_diagnostics": len(pledges),
              "recorded_promise_rate_per_opening_task": len(pledges) / len(openings) if openings else None,
              "finished_market_results": len(rows), "counted_purchases": len(purchases),
              "conversion_per_finished_result": len(purchases) / len(rows) if rows else None,
              "promised_counted_purchases": len(promised_purchases),
              "promised_purchases_with_verified_five_star_review": sum(row.get("stars") == 5 for row in promised_purchases),
              "mean_new_review_stars": sum(ratings) / len(ratings) if ratings else None,
              "price_minus_reserve_total": sum(row["receipt"]["price"] - RESERVES[row["receipt"]["item"]] for row in purchases),
              "limitations": "Opening tasks and finished market results are different denominators; in-flight tasks may remain. Pledges are not published reviews. Costs/cashback are excluded. Buyer mix changes prevent causal attribution."}
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    out = ROOT / 'logs' / directory / f"measurement-{stamp}"
    out.mkdir()
    (out / "summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "evidence.json").write_text(json.dumps({"results": rows, "pledges": list(pledges.values()),
                                                  "opening_tasks": list(openings)}, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Evidence:", out)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
