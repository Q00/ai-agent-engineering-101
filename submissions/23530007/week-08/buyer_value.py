"""What a deal at each shop is worth to the buyer's score, from the whole market's settled deals (user, 11-06).

    from buyer_value import table, expected
    t = table()                      # refreshed from the public ledger at most every REFRESH s (file lock)
    e = expected(t, shop_name, kind, category)   # {"saved": .., "n": .., "p50": .., "p25": .., "warranty": ..}

Since patch 3.4 the buyer's main score (0.5) is the saved rate: per deal (list - price + refund) / list, a deal
that served nothing -1 (a warranty refund makes a broken deal count +1), averaged over deals + 100. What one deal
adds therefore depends mostly on WHERE we buy: on 11-06 (2 h of ledger, 5456 deals) food sold at 0.62-0.65 of list
at Kiro729, olstjr, 11im, jiseokkk and at 0.70-0.77 at psyoung82, where we bought most; goods at 0.70 at
sergiobuilds/Kiro729/seochanit and 0.85-0.88 at nocha12/myeonggi. Our own haggling matched other buyers' at the
same shop, so shop choice, not haggling, decides the saved rate. The top buyers (lee-sungsoo 0.39, 11im 0.39,
dragonash 0.38 over those 2 h) bought 75-92 % food at the cheapest food shops.

The table keeps every settle of the last WINDOW s (all buyers) with its price / list, warranty and whether the
unit broke (and was refunded), and gives per shop and kind (goods/food), and per shop and category, the realized
saved per deal, the median and lower-quartile price / list, the warranty share and the break rate. Small samples
are shrunk toward the kind's market mean (PRIOR_N pseudo-deals).
State: .market/shop_value.json (gitignored with .market/).
"""
import fcntl
import json
import statistics
import time
import urllib.request
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
STATE = HERE / ".market" / "shop_value.json"
LOCK = HERE / ".market" / "shop_value.lock"
MARKET = "https://market.ourolabs.ai"
WINDOW = 6 * 3600          # deals kept
HALF_LIFE_H = 2.0          # 11-09: default weight half-life of a market deal in the shop values (hours)
RECENT_PRICE_S = 2 * 3600  # price quantiles from this recent window when it holds MIN_RECENT deals
MIN_RECENT = 10
REFRESH = 600              # ledger read at most this often
PAGES = 20                 # ledger pages per refresh (500 entries each); farther behind: skip to the newest
FIRST_PAGES = 60           # first build: ~2 h of ledger
PRIOR_N = 10               # shrinkage toward the kind's market mean
MIN_CAT = 8                # deals at a shop in a category before the category's own numbers are used


def _get(after):
    with urllib.request.urlopen(f"{MARKET}/api/ledger?after={after}", timeout=30) as r:
        return json.load(r)


def _shop_names():
    with urllib.request.urlopen(f"{MARKET}/api/shops", timeout=30) as r:
        sh = json.load(r)
    sh = sh if isinstance(sh, list) else sh.get("shops", [])
    out = {}
    for s in sh:
        url = str(s.get("url") or (s.get("card") or {}).get("url") or "").rstrip("/")
        if url:
            out[url] = s.get("name")
    return out


def _our_urls():
    """Every tunnel URL our own shop has had (the kit logs '[tunnel] https://...' at each start), as yms979."""
    import re
    out = {}
    for f in [HERE / "logs" / "market-kit.log"]:
        try:
            for m in re.finditer(r"\[tunnel\] (https://[a-z0-9-]+\.trycloudflare\.com)", f.read_text(encoding="utf-8", errors="ignore")):
                out[m.group(1)] = "yms979"
        except OSError:
            pass
    try:
        for u, n in json.loads((HERE / ".market" / "shop_names.json").read_text()).get("names", {}).items():
            if n == "yms979":
                out[u.rstrip("/")] = "yms979"
    except (OSError, ValueError):
        pass
    return out


def _catalog():
    try:
        return json.loads((HERE / ".market" / "catalog.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def refresh(st):
    """Read new ledger entries into st["deals"] (rows) and st["broken"] (unit -> refunded), drop old rows."""
    cat = _catalog()
    names = _shop_names()
    st.setdefault("names", {}).update(names)
    st["names"].update(_our_urls())
    # 11-08: rows settled before a shop's (new) URL was known were kept under the URL; every kit restart gives our
    # shop a new tunnel URL, so our own sales read as an unknown shop (the shop strategist saw warranty_share 0 and
    # goods sales near 0 for us). Re-map those rows on every refresh.
    for r in st.get("deals", []):
        if str(r.get("shop", "")).startswith("http"):
            r["shop"] = st["names"].get(r["shop"].rstrip("/")) or r["shop"]
    after = st.get("seq")
    if after is None:
        after = -500 * FIRST_PAGES
    else:
        head = _get(-1)
        if head and head[-1]["seq"] - after > 500 * PAGES:
            after = head[-1]["seq"] - 500 * PAGES
    rows, broken = st.setdefault("deals", []), st.setdefault("broken", {})
    for _ in range(PAGES if st.get("seq") is not None else FIRST_PAGES):
        page = _get(after)
        if not page:
            break
        for e in page:
            d = e.get("data") or {}
            if e.get("kind") == "settle":
                item = cat.get(d.get("sku")) or {}
                lp = item.get("list_price")
                if not lp:
                    continue
                url = str(d.get("shop") or "").rstrip("/")
                rows.append({"ts": e["ts"], "shop": st["names"].get(url) or url, "buyer": d.get("buyer"),
                             "goods": d.get("expires_at") is None, "cat": item.get("category", ""),
                             "x": round(d["amount"] / lp, 4), "w": (d.get("warranty_hours") or 0) > 0,
                             "unit": d.get("unit"), "grade": d.get("grade"),
                             "sku": d.get("sku"), "amt": d.get("amount")})      # 11-06: market_picture.py reads these
            elif e.get("kind") == "result" and d.get("receipt") and d.get("stars") is not None \
                    and st["names"].get(str(d.get("shop") or "").rstrip("/")) == "yms979":
                # 11-08: the ratings our shop gets (paid deals only count for stars), for the shop's star diagnosis
                st.setdefault("our_ratings", []).append([e["ts"], d.get("buyer"), d["stars"], str(d.get("review") or "")[:90]])
            elif e.get("kind") == "broken":
                broken[d.get("unit")] = (d.get("refunded") or 0) > 0
        after = page[-1]["seq"]
        if len(page) < 500:
            break
    st["seq"] = after
    cut = time.time() - WINDOW
    st["deals"] = [r for r in rows if r["ts"] >= cut]
    st["our_ratings"] = [x for x in st.get("our_ratings", []) if x[0] >= cut]
    units = {r["unit"] for r in st["deals"]}
    st["broken"] = {u: v for u, v in broken.items() if u in units}
    st["t"] = time.time()
    st["stats"] = summarize(st)
    return st


def _saved(r, broken):
    if r["unit"] in broken:
        return 1.0 if broken[r["unit"]] else -1.0
    return 1 - r["x"]


def half_life():
    """Hours over which a market deal's weight halves in the shop values (user 11-09: prices drift, follow the
    newest saved, not the 6 h average). The buyer strategist owns it as global 'value_half_life_h'; 0 = no decay."""
    try:
        g = json.loads((HERE / "params_auto_buyer.json").read_text()).get("globals") or {}
        return float(g.get("value_half_life_h", HALF_LIFE_H))
    except (OSError, ValueError, TypeError):
        return HALF_LIFE_H


def summarize(st):
    broken = st.get("broken", {})
    groups = defaultdict(list)
    for r in st["deals"]:
        kind = "goods" if r["goods"] else "food"
        groups[f"{r['shop']}|{kind}"].append(r)
        groups[f"{r['shop']}|{kind}|{r['cat']}"].append(r)
        groups[f"*|{kind}"].append(r)
        if r.get("sku"):                                   # 11-08: per shop and SKU, for pick_sku's saved choice
            groups[f"{r['shop']}|sku|{r['sku']}"].append(r)
    now = max((r["ts"] for r in st["deals"]), default=time.time())
    hl = half_life()
    wt = (lambda r: 0.5 ** ((now - r["ts"]) / 3600 / hl)) if hl > 0 else (lambda r: 1.0)
    out = {}
    for k, rs in groups.items():
        # 11-09: weighted toward the newest deals, and price quantiles from the last RECENT_PRICE_S when there
        # are enough of them, so caps and shop choice follow where the market is now
        w = [wt(r) for r in rs]
        sw = sum(w) or 1.0
        recent = [r["x"] for r in rs if now - r["ts"] <= RECENT_PRICE_S]
        xs = sorted(recent if len(recent) >= MIN_RECENT else [r["x"] for r in rs])
        out[k] = {"n": len(rs), "saved": round(sum(wi * _saved(r, broken) for wi, r in zip(w, rs)) / sw, 4),
                  "p50": round(statistics.median(xs), 4), "p25": round(xs[len(xs) // 4], 4),
                  "warranty": round(sum(wi * r["w"] for wi, r in zip(w, rs)) / sw, 3),
                  "broke": round(sum(r["unit"] in broken for r in rs) / len(rs), 4)}
    return out


def table(max_age=REFRESH):
    """The stats as they are. When they are older than max_age a detached process refreshes them (the buyer
    never waits on the ledger; one refresher at a time through the lock)."""
    try:
        st = json.loads(STATE.read_text())
    except (OSError, ValueError):
        st = {}
    if time.time() - st.get("t", 0) >= max_age:
        import subprocess
        import sys
        subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--refresh", "--quiet"],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
    return st.get("stats") or {}


def refresh_now():
    """Refresh under the lock (skips when another process holds it); returns the stats."""
    LOCK.parent.mkdir(exist_ok=True)
    with open(LOCK, "w") as lk:
        try:
            fcntl.flock(lk, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return None
        try:
            st = json.loads(STATE.read_text())
        except (OSError, ValueError):
            st = {}
        st = refresh(st)
        tmp = STATE.with_suffix(".tmp")
        tmp.write_text(json.dumps(st, ensure_ascii=False))
        tmp.replace(STATE)
        return st["stats"]


def expected(stats, shop, kind, category=None):
    """What a deal at `shop` is expected to add (saved per deal), shrunk toward the market's mean for `kind`."""
    prior = stats.get(f"*|{kind}") or {"saved": 0.25, "p50": 0.75, "p25": 0.7, "warranty": 0, "broke": 0, "n": 0}
    s = stats.get(f"{shop}|{kind}|{category}") if category else None
    if not s or s["n"] < MIN_CAT:
        s = stats.get(f"{shop}|{kind}")
    if not s:
        return dict(prior, n=0, saved=prior["saved"])
    n = s["n"]
    shrunk = (n * s["saved"] + PRIOR_N * prior["saved"]) / (n + PRIOR_N)
    return dict(s, saved=round(shrunk, 4))


if __name__ == "__main__":
    import sys
    st = (refresh_now() if "--refresh" in sys.argv else None) or table(max_age=10 ** 9)
    if "--quiet" in sys.argv:
        sys.exit(0)
    for kind in ("food", "goods"):
        rows = sorted(((k.split("|")[0], v) for k, v in st.items() if k.endswith(f"|{kind}") and k.count("|") == 1),
                      key=lambda kv: -kv[1]["saved"])
        print(f"== {kind}: market {st.get(f'*|{kind}')}")
        for name, v in rows:
            if v["n"] >= 10:
                print(f"  {str(name)[:22]:22} n={v['n']:4} saved={v['saved']:.3f} p50={v['p50']:.3f} "
                      f"p25={v['p25']:.3f} warranty={v['warranty']:.2f} broke={v['broke']:.3f}")
