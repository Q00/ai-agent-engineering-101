"""Product memory: what we bought and sold of each SKU, kept up to date for the strategist (strategist.py).

    python product_memory.py            (strategist.py calls update(); safe to run any time)

Reads, incrementally (positions kept in product_memory.json "_pos"):
  - .market/inventory.json: units in stock now; a unit seen for the first time is a purchase (cost, grade, time)
  - seller_r3_log.jsonl: deals (price, cost, task), buyers who left without a deal (canceled, lapsed, lost),
    buyers turned away for lack of stock (no_stock), blocked buyers
  - the public ledger (/api/ledger, up to 60 pages a run; more than 60k entries behind, it jumps to the last 30k): stars and breakages of our sales, matched by task id
Per SKU it keeps lifetime totals and hourly buckets for the last 48 h, so rates over any recent window can be
read back with window(). Nothing here decides anything; strategist.py does.
"""
import json
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
MEM = HERE / "product_memory.json"
LOG = HERE / "seller_r3_log.jsonl"
INV = HERE / ".market" / "inventory.json"
CATALOG = HERE / ".market" / "catalog.json"
MARKET = "https://market.ourolabs.ai"
KEEP_HOURS = 48
FIELDS = ("bought", "cost_in", "sold", "revenue", "margin", "hold_hours", "cancels", "lost", "no_stock",
          "stars_n", "stars_sum", "broken")


def read(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def load():
    m = read(MEM, {})
    m.setdefault("_pos", {"log": 0, "seq": None})
    m.setdefault("_units", {})        # unit -> [sku, supplied_at]   (units we have held)
    m.setdefault("_tasks", {})        # task -> [sku, unit]          (our recent sales)
    m.setdefault("skus", {})
    return m


def add(m, sku, t, **kw):
    s = m["skus"].setdefault(sku, {"total": {}, "hours": {}})
    h = s["hours"].setdefault(str(int(t // 3600)), {})
    for k, v in kw.items():
        s["total"][k] = s["total"].get(k, 0) + v
        h[k] = h.get(k, 0) + v


def update():
    m, now = load(), time.time()
    cat = read(CATALOG, {})
    inv = read(INV, {})
    for u in inv.get("units", []):                       # purchases: units we have not seen before
        if u.get("unit") and u["unit"] not in m["_units"]:
            t = u.get("supplied_at") or now
            m["_units"][u["unit"]] = [u["sku"], t]
            add(m, u["sku"], t, bought=1, cost_in=u.get("cost") or 0)
    try:                                                 # our seller's log since last time
        with open(LOG, encoding="utf-8") as f:
            f.seek(m["_pos"]["log"])
            for line in f:
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                ev, t = e.get("event"), e.get("t", now)
                if ev == "deal" and e.get("sku"):
                    held = m["_units"].get(e.get("unit") or "", [None, None])[1]
                    add(m, e["sku"], t, sold=1, revenue=e.get("price") or 0,
                        margin=(e.get("price") or 0) - (e.get("cost") or 0),
                        hold_hours=round((t - held) / 3600, 2) if held else 0)
                    if e.get("task"):
                        m["_tasks"][e["task"]] = [e["sku"], e.get("unit")]
                elif ev in ("canceled", "lapsed") and e.get("sku"):
                    add(m, e["sku"], t, cancels=1)
                elif ev == "lost" and e.get("sku"):
                    add(m, e["sku"], t, lost=1)
                elif ev == "no_stock" and e.get("skus"):
                    add(m, e["skus"][0], t, no_stock=1)
            m["_pos"]["log"] = f.tell()
    except OSError:
        pass
    try:                                                 # stars and breakages of our sales, from the ledger
        seq = m["_pos"]["seq"]
        units = {v[1]: v[0] for v in m["_tasks"].values() if v[1]}
        with urllib.request.urlopen(f"{MARKET}/api/ledger?after=-500", timeout=20) as r:   # far behind: jump
            head = (json.load(r) or [{}])[-1].get("seq")
        if seq is None or (head and head - seq > 60000):
            seq = (head or 0) - 30000
        for _ in range(60):                                  # ~15-20k entries an hour; 60 pages keeps up hourly
            with urllib.request.urlopen(f"{MARKET}/api/ledger?after={seq if seq is not None else -5000}",
                                        timeout=20) as r:
                page = json.load(r)
            if not page:
                break
            for e in page:
                seq, d = e["seq"], e.get("data") or {}
                task = (d.get("receipt") or {}).get("task")
                if e.get("kind") == "result" and task in m["_tasks"] and d.get("stars") is not None:
                    add(m, m["_tasks"][task][0], e.get("ts") or now, stars_n=1, stars_sum=d["stars"])
                elif e.get("kind") == "broken" and d.get("unit") in units:
                    add(m, units[d["unit"]], e.get("ts") or now, broken=1)
            if len(page) < 500:
                break
        m["_pos"]["seq"] = seq
    except Exception:
        pass
    cut = int(now // 3600) - KEEP_HOURS                  # forget old buckets, units and tasks
    for s in m["skus"].values():
        s["hours"] = {h: v for h, v in s["hours"].items() if int(h) >= cut}
    m["_units"] = {k: v for k, v in m["_units"].items() if v[1] >= now - 7 * 86400}
    m["_tasks"] = dict(list(m["_tasks"].items())[-20000:])
    for sku, s in m["skus"].items():
        v = cat.get(sku) or {}
        s.update(name=v.get("name", s.get("name")), kind=v.get("kind", s.get("kind")),
                 list_price=v.get("list_price", s.get("list_price")), category=v.get("category", s.get("category")))
    tmp = MEM.with_suffix(".tmp")
    tmp.write_text(json.dumps(m, ensure_ascii=False), encoding="utf-8")
    tmp.replace(MEM)
    return m


def window(m, sku, hours, now=None):
    """Totals of one SKU over the last `hours` hours (whole hourly buckets)."""
    now = now or time.time()
    first = int(now // 3600) - int(hours) + 1
    out = dict.fromkeys(FIELDS, 0)
    for h, v in (m["skus"].get(sku) or {}).get("hours", {}).items():
        if int(h) >= first:
            for k, x in v.items():
                out[k] = out.get(k, 0) + x
    return out


if __name__ == "__main__":
    m = update()
    top = sorted(m["skus"].items(), key=lambda kv: -kv[1]["total"].get("revenue", 0))[:10]
    for sku, s in top:
        t = s["total"]
        print(f"{sku} {str(s.get('name'))[:24]:24} bought {t.get('bought', 0):4} sold {t.get('sold', 0):4} "
              f"revenue {t.get('revenue', 0):>13,} margin {t.get('margin', 0):>12,} cancels {t.get('cancels', 0)}")
