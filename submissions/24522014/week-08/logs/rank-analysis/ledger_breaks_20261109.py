"""Read-only ledger read for our buyer's breaks (approved 11-09, at most 40 pages, 2 s apart). Run from week-08:
ledger_breaks.py <out.json>
For each line of .market/broken.jsonl (written by the kit from the full ledger), estimate the break's ledger seq from our own deals
(ledger seq and purchase time agree within about a second), read one page of 500 rows around it, find our broken event and any
refund in the page, and match it to .market/buyer-deals by settlement ledger or unit. Prints and writes aggregates per break;
no shop names are written."""
import bisect, json, glob, sys, time
import httpx

BASE = "https://market.ourolabs.ai"
ME = "kcwarni"
MAX_PAGES = 40
cat = json.load(open(".market/catalog.json", encoding="utf-8-sig"))
deals = [json.load(open(f, encoding="utf-8")) for f in glob.glob(".market/buyer-deals/*.json")]
by_ledger = {d["ledger"]: d for d in deals}
by_unit = {d.get("unit"): d for d in deals if d.get("unit")}
pts = sorted((d["purchased_at"], d["ledger"]) for d in deals)
T = [p[0] for p in pts]
breaks = [json.loads(l) for l in open(".market/broken.jsonl", encoding="utf-8") if l.strip()]
import ssl
# The system trust store, as market_intel.py uses; verification stays on.
client = httpx.Client(verify=ssl.create_default_context(), timeout=20)
pages = 0
seen_rows = {}


def estimate(t):
    i = bisect.bisect_left(T, t)
    if i <= 0: return pts[0][1]
    if i >= len(pts): return pts[-1][1]
    (t0, s0), (t1, s1) = pts[i - 1], pts[i]
    return int(s0 + (s1 - s0) * (t - t0) / (t1 - t0)) if t1 > t0 else s0


def page(after):
    global pages
    if pages >= MAX_PAGES:
        return None
    if pages:
        time.sleep(2)
    pages += 1
    r = client.get(BASE + "/api/ledger", params={"after": after})
    r.raise_for_status()
    rows = r.json()
    for x in rows:
        seen_rows[x["seq"]] = x
    return rows


results = []
for b in breaks:
    t = b["broke_at"]
    hit = next((x for x in seen_rows.values() if x.get("kind") == "broken" and (x.get("data") or {}).get("buyer") == ME and int(x["ts"]) == t), None)
    tries = 0
    est = estimate(t)
    while hit is None and tries < 2:
        rows = page(max(est - 250, 0))
        tries += 1
        if not rows:
            break
        hit = next((x for x in rows if x.get("kind") == "broken" and (x.get("data") or {}).get("buyer") == ME and int(x["ts"]) == t), None)
        if hit is None:
            lo, hi = rows[0], rows[-1]
            if hi["ts"] > lo["ts"]:
                rate = (hi["seq"] - lo["seq"]) / (hi["ts"] - lo["ts"])
                mid = lo if t < lo["ts"] else hi
                est = int(mid["seq"] + (t - mid["ts"]) * rate)
            else:
                break
    row = {"broke_at": t, "found": hit is not None, "pages_used_so_far": pages}
    if hit:
        data = hit.get("data") or {}
        row["event_keys"] = sorted(data.keys())
        row["event_fields"] = {k: v for k, v in data.items() if k not in ("shop", "buyer", "name", "item", "seller") and not isinstance(v, (dict, list))}
        d = by_ledger.get(data.get("settlement_ledger")) or by_unit.get(data.get("unit"))
        if d:
            c = cat.get(d.get("sku")) or {}
            lp = c.get("list_price")
            row.update(matched=True, kind=c.get("kind"), grade=d.get("grade"), warranty_hours=d.get("warranty_hours"), list_price=lp,
                       share=round(1 - d["price"] / lp, 4) if lp else None, purchased_at=d["purchased_at"], hours_to_break=round((t - d["purchased_at"]) / 3600, 2),
                       local_fault=bool(d.get("fault")), local_refund=bool(d.get("refund_confirmed")), category=(c.get("category") or "").split(">")[0])
        else:
            row["matched"] = False
        refunds = [x for x in seen_rows.values() if x.get("kind") == "refund" and (x.get("data") or {}).get("buyer") == ME
                   and ((x.get("data") or {}).get("settlement_ledger") == data.get("settlement_ledger") or ((x.get("data") or {}).get("unit") and (x.get("data") or {}).get("unit") == data.get("unit")))]
        row["refund_seen_in_pages"] = bool(refunds)
    results.append(row)
    print(json.dumps(row, ensure_ascii=False), flush=True)
    if pages >= MAX_PAGES and not hit:
        print("page cap reached", flush=True)
our_refunds = [x for x in seen_rows.values() if x.get("kind") == "refund" and (x.get("data") or {}).get("buyer") == ME]
summary = {"breaks": len(breaks), "found": sum(r["found"] for r in results), "matched": sum(bool(r.get("matched")) for r in results), "pages": pages,
           "our_refund_rows_seen": len(our_refunds), "refund_row_keys": sorted({k for x in our_refunds for k in (x.get("data") or {})})}
print("SUMMARY", json.dumps(summary), flush=True)
json.dump({"summary": summary, "rows": results}, open(sys.argv[1], "w", encoding="utf-8"), ensure_ascii=False, indent=1)
