"""Week 08 rule watch: report a market change within minutes, act on nothing (owner, 11-04 22:50).

The surge rule went live at 21:27 (inventory.json grew a `surge` field) and the shop manager first named it in its
22:01 report, 34 minutes on; the rules page is read only once an hour. Run every 2 minutes (crontab), this compares
  - the market's rules page (/agent.md, the Round 3 part, as rules.py reads it): changed lines;
  - inventory.json: new or removed top-level fields and unit fields (a new rule tends to show up as one);
  - catalog.json: SKUs added or removed;
with the last run's copy in data/watch_state.json, and appends what changed to data/alerts.md (newest last, KST).
It changes no policy and no file the shop reads; the owner decides what to do.
"""
import difflib
import hashlib
import json
import time
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent
MD, DATA = HERE / ".market", HERE / "data"
STATE, ALERTS = DATA / "watch_state.json", DATA / "alerts.md"
URL, START = "https://market.ourolabs.ai/agent.md", "## Round 3"
MAX_LINES = 30


def kst(t):
    return time.strftime("%m-%d %H:%M", time.gmtime(t + 9 * 3600))


def load(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def main():
    now = time.time()
    old = load(STATE, {})
    new, notes = dict(old), []

    try:
        r = httpx.get(URL, timeout=15)
        r.raise_for_status()
        text = r.text[r.text.index(START):] if START in r.text else r.text
        new["rules"] = text
        if old.get("rules") is not None and text != old["rules"]:
            diff = [l for l in difflib.unified_diff(old["rules"].splitlines(), text.splitlines(), lineterm="", n=0)
                    if l[:1] in "+-" and not l.startswith(("+++", "---"))]
            notes.append("rules page changed:\n" + "\n".join("    " + l[:220] for l in diff[:MAX_LINES])
                         + (f"\n    ... {len(diff) - MAX_LINES} more lines" if len(diff) > MAX_LINES else ""))
    except (httpx.HTTPError, ValueError):
        pass                                  # the page did not answer: try again next run, keep the last copy

    inv = load(MD / "inventory.json", None)
    if inv:
        top = sorted(inv)
        unit = sorted({k for u in inv.get("units", []) for k in u})
        for name, cur in (("inventory.json fields", top), ("unit fields", unit)):
            prev = old.get(name)
            if prev is not None and cur and cur != prev:
                notes.append(f"{name}: added {sorted(set(cur) - set(prev))}, removed {sorted(set(prev) - set(cur))}")
            if cur:
                new[name] = cur

    cat = load(MD / "catalog.json", None)
    if cat:
        skus = sorted(cat)
        prev = old.get("skus")
        if prev is not None:
            added, gone = sorted(set(skus) - set(prev)), sorted(set(prev) - set(skus))
            if added or gone:
                show = ", ".join(f"{s} {cat[s].get('name', '')[:24]} ({cat[s].get('list_price', 0):,.0f})"
                                 for s in added[:10])
                notes.append(f"catalog: {len(added)} SKUs added, {len(gone)} removed" + (f": {show}" if show else ""))
        new["skus"] = skus
        new["catalog_md5"] = hashlib.md5(json.dumps(cat, sort_keys=True).encode()).hexdigest()

    new["ts"] = now
    DATA.mkdir(exist_ok=True)
    STATE.write_text(json.dumps(new, ensure_ascii=False), encoding="utf-8")
    if notes:
        with ALERTS.open("a", encoding="utf-8") as f:
            for n in notes:
                f.write(f"- {kst(now)} {n}\n")


if __name__ == "__main__":
    main()
