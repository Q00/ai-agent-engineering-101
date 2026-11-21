"""My shop's broken units over the last HOURS of the ledger: hours after sale, warranty, refund (for the warranty length)."""
import httpx, json, time, collections
HOURS = 24
mine = set(json.load(open("restock_memory.json", encoding="utf-8"))["customers"]["my_urls"])
c = httpx.Client(); M = "https://market.ourolabs.ai"
head = c.get(f"{M}/api/ledger", params={"after": -1}, timeout=20).json()[-1]["seq"]
after = head - int(HOURS * 3600 / 112 * 500)
t0 = time.time() - HOURS * 3600
rows, all_b = [], []
while True:
    r = c.get(f"{M}/api/ledger", params={"after": after}, timeout=30).json()
    if not r: break
    after = r[-1]["seq"]
    for e in r:
        d = e.get("data") or {}
        if e["kind"] == "broken" and e["ts"] >= t0:
            all_b.append(d.get("hours_after_sale") or 0)
            if d.get("shop") in mine:
                rows.append((d.get("hours_after_sale") or 0, d.get("warranty_hours"), d.get("refunded") or 0, d.get("item")))
    if len(r) < 500: break
print(f"my breaks {len(rows)}, refunded {sum(x[2] for x in rows):,} won; market breaks {len(all_b)}")
edges = [0, 6, 12, 24, 36, 48, 72, 1e9]
def hist(hs, w=None):
    out = []
    for a, b in zip(edges, edges[1:]):
        sel = [x for x in hs if a <= (x[0] if w else x) < b]
        out.append((f"{a}-{b if b < 1e9 else 'inf'}h", len(sel), sum(x[2] for x in sel) if w else None))
    return out
print("mine by hours after sale (count, refunded won):", hist(rows, True))
print("market by hours after sale:", hist(all_b))
for cut in (24, 36, 48):
    kept = sum(x[2] for x in rows if x[0] >= cut)
    print(f"warranty {cut} h would have saved {kept:,} won of {sum(x[2] for x in rows):,}")
