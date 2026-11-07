"""Read-only replay: topics that candidates() would have offered at each saved market-intel snapshot since step 2, using actions known at that time. Nothing is queued."""
import datetime as d, glob, json, os, statistics as st, sys, time
sys.path.insert(0, ".")
import forum_buyer_posts as posts
KST = d.timezone(d.timedelta(hours=9))
START = d.datetime(2026, 11, 6, 12, 54, tzinfo=KST).timestamp()
catalog = json.load(open(".market/catalog.json", encoding="utf-8"))
actions = json.load(open(".market/forum-actions.json", encoding="utf-8"))
for p in sorted(glob.glob("logs/market-intel/*.json")):
    if os.path.getmtime(p) < START: continue
    try: snap = json.load(open(p, encoding="utf-8"))
    except (OSError, ValueError): continue
    now = snap.get("observed_at") or os.path.getmtime(p)
    rows = snap.get("rows")
    if rows is None:
        print(os.path.basename(p), "no rows", list(snap)[:12]); break
    known = {k: a for k, a in actions.items() if a.get("queued_at", 0) <= now}
    units, urls = posts.own_shop(".market", snap)
    left = [c for c in posts.candidates(rows, catalog, units, urls, known, now) if posts.safe(c[0]["post"])]
    pool = posts.sales(rows, catalog, units, urls, now)
    ts = [r.get("ts") for r in rows if r.get("kind") == "settle"]
    sk = [c[3]["samples"] for c in left if "sku" in c[3]]
    print(d.datetime.fromtimestamp(now, KST).strftime("%m-%d %H:%M"), "sales_6h", len(pool), "span_h", round((now-min(ts))/3600, 1) if ts else None, "sku", len(sk), "cat", sum("category" in c[3] for c in left), "median_n", st.median(sk) if sk else None)
