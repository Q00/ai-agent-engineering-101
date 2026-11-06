"""Read-only: a small public ledger sample of settled sales joined to their verified reviews. Public endpoint, no key. At most --blocks x 3 pages.

Blocks of three contiguous pages are spread evenly over the sequence range, because a review lands within about 200 rows of its sale. Review text is not kept: other agents wrote it and it is untrusted.
Usage: review_sample_20261106.py --first SEQ --last SEQ --blocks 20 --out PATH_OUTSIDE_THE_REPO
"""
import argparse
import json
import ssl
import time

import httpx

BASE = "https://market.ourolabs.ai"
parser = argparse.ArgumentParser()
parser.add_argument("--first", type=int, required=True)
parser.add_argument("--last", type=int, required=True)
parser.add_argument("--blocks", type=int, default=20)
parser.add_argument("--out", required=True)
args = parser.parse_args()
catalog = json.load(open(".market/catalog.json", encoding="utf-8"))
step = (args.last-args.first)//args.blocks
calls = rows_seen = 0
started = time.time()
with httpx.Client(verify=ssl.create_default_context(), timeout=20, headers={"User-Agent": "market-review-sample"}) as client, open(args.out, "w", encoding="utf-8") as out:
    for block in range(args.blocks):
        after = args.first+block*step
        sales = {}
        for _ in range(3):
            response = client.get(BASE+"/api/ledger", params={"after": after})
            calls += 1
            if response.status_code != 200:
                print("page", after, "status", response.status_code, flush=True)
                break
            body = response.json()
            rows = body if isinstance(body, list) else body.get("rows") or body.get("ledger") or []
            rows_seen += len(rows)
            for row in rows:
                data = row.get("data") or {}
                if row.get("kind") == "settle" and data.get("order"):
                    sales[data["order"]] = {"seq": row["seq"], "ts": row.get("ts"), "buyer": data.get("buyer"), "shop": data.get("shop"), "amount": data.get("amount"), "item": data.get("item"), "grade": data.get("grade"), "warranty_hours": data.get("warranty_hours") or 0, "expires_at": data.get("expires_at")}
                elif row.get("kind") == "result" and data.get("verified") and data.get("order") in sales and type(data.get("stars")) in (int, float):
                    sale = sales[data["order"]]
                    listed = (catalog.get(sale["item"]) or {}).get("list_price")
                    out.write(json.dumps({**sale, "stars": data["stars"], "rater": data.get("buyer"), "list_price": listed, "ratio": round(sale["amount"]/listed, 5) if listed else None, "kind": (catalog.get(sale["item"]) or {}).get("kind")})+"\n")
            after = max((row.get("seq", after) for row in rows), default=after)
            time.sleep(.4)
print("calls", calls, "rows", rows_seen, "seconds", round(time.time()-started), "at", time.strftime("%H:%M:%S"))
