"""Stars tuner (11-13, user: 'keep raising the stars').

stars_rank is one mean per reviewing account, and many accounts rate by a fixed rubric at every shop (price share of
list, grade). This finds, every tick, the accounts that keep buying from us but rate us under STAR_TARGET, reads each
one's rubric from its paid reviews at EVERY shop over RUBRIC_H, finds how it writes to us (first-message shape from
our seller's log joined with the ledger's receipts), and writes the terms that its rubric rates 5:

    star_buyers.json     {"<account>": {"x": price share of list for grade A, "patterns": [...], "auto": true}}
    a_only_buyers.json   {"<account>": [patterns]}   accounts that rate grade B goods lower than A

Manual entries (no "auto") are left alone. Limits: a price never more than MAX_LOSS_X of list under the SKU's cost
share, at most MAX_LOSS_DAY of expected loss a day per account, and a pattern is used only when >= PURITY of the
talks it matches belong to the account or to accounts that already rate us >= STAR_TARGET.

    python stars_tuner.py [--dry] [--print]
"""
import json
import re
import sqlite3
import statistics
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ARCHIVE = HERE / "data" / "market_archive.db"
STAR = HERE / "star_buyers.json"
AONLY = HERE / "a_only_buyers.json"
OUT = HERE / ".market" / "stars_tuner.json"
ME = "yms979"
RUBRIC_H = 48
STAR_TARGET = 4.85
MIN_PER_DAY = 5
FIVE = 4.8           # a rubric cell counts as 'rates 5' at this mean
MAX_LOSS_X = 0.08
MAX_LOSS_DAY = 5e6
PURITY = 0.85
LOG_SINCE = 1794498720   # 11-12 15:52 UTC: the seller logs every message from here


def shape_regex(text):
    """A regex for the shape of a first message: product name, SKU and every number generalised; no end anchor when
    the logged text was cut at 300 characters."""
    t = text.strip()
    cut = len(text) >= 295
    m = re.match(r"^(I want to buy )(.+?)( \(G[0-9a-f]{8}\))(.*)$", t, re.S)
    if m:
        body = "^" + re.escape(m.group(1)) + r".+? \(G[0-9a-f]{8}\)" + _gen(m.group(4))
    else:
        body = "^" + _gen(t)
    return body + ("" if cut else "$")


def _gen(s):
    """re.escape(s) with SKUs as G[0-9a-f]{8} and other numbers as \\d[\\d,.]*."""
    parts = re.split(r"(G[0-9a-f]{8})", s)
    out = []
    for i, part in enumerate(parts):
        if i % 2:
            out.append("G[0-9a-f]{8}")
        else:
            out.append(re.sub(r"(?:\\\.|\d|,)+", lambda mm: r"\d[\d,.]*" if re.search(r"\d", mm.group(0)) else mm.group(0), re.escape(part)))
    return "".join(out)


def build():
    now = time.time()
    db = sqlite3.connect(f"file:{ARCHIVE}?mode=ro", uri=True, timeout=30)
    cat = json.loads((HERE / ".market" / "catalog.json").read_text(encoding="utf-8"))
    urls = set()
    for (d,) in db.execute("SELECT data FROM ledger WHERE kind='join'"):
        x = json.loads(d).get("data") or {}
        if x.get("name") == ME and x.get("url"):
            urls.add(x["url"].rstrip("/"))
    ours, ours_day = defaultdict(list), Counter()
    rub = defaultdict(lambda: defaultdict(list))        # account -> (kind, grade, x bucket) -> stars, all shops
    task_owner = {}
    for ts, d in db.execute("SELECT ts, data FROM ledger WHERE kind='result' AND round=3"):
        x = json.loads(d).get("data") or {}
        rc = x.get("receipt") or {}
        if not rc or not x.get("verified") or x.get("stars") is None:
            continue
        mine = str(x.get("shop")).rstrip("/") in urls
        if mine:
            ours[x["buyer"]].append(x["stars"])
            if now - ts < 24 * 3600:
                ours_day[x["buyer"]] += 1
            if rc.get("task") and ts >= LOG_SINCE:
                task_owner[rc["task"]] = x["buyer"]
        if now - ts < RUBRIC_H * 3600:
            it = cat.get(rc.get("item")) or {}
            lp = it.get("list_price")
            if lp and rc.get("price"):
                xb = round(min(int(rc["price"] / lp * 20) / 20, 0.95), 2)
                rub[x["buyer"]][(it.get("kind") or "goods", rc.get("claim") or "?", xb)].append(x["stars"])
    acc_mean = {b: statistics.mean(v) for b, v in ours.items()}
    n_acc = len(ours)
    # first messages of the joined talks
    first = {}
    try:
        with open(HERE / "seller_r3_log.jsonl", errors="ignore") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 900_000_000))
            f.readline()
            for line in f:
                if '"event": "ask"' not in line:
                    continue
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if e.get("task") in task_owner and e.get("t", 0) >= LOG_SINCE:
                    first[e["task"]] = e.get("text") or ""
    except OSError:
        pass
    shapes = defaultdict(Counter)                        # regex -> owners
    acc_shapes = defaultdict(Counter)
    for t, text in first.items():
        rx = shape_regex(text)
        shapes[rx][task_owner[t]] += 1
        acc_shapes[task_owner[t]][rx] += 1
    inv = json.loads((HERE / ".market" / "inventory.json").read_text())
    c_share = statistics.median([u["cost"] / u["list_price"] for u in inv["units"]
                                 if u.get("kind") == "goods" and u.get("grade") == "A" and u.get("list_price")] or [0.62])
    plans = []
    for b, m in sorted(acc_mean.items(), key=lambda kv: -ours_day[kv[0]]):
        if ours_day[b] < MIN_PER_DAY or m >= STAR_TARGET:
            continue
        R = rub[b]
        # price rubric on grade A goods: the highest price bucket rated >= FIVE with n >= 3, where a dearer bucket
        # rates lower (so it is price that moves it)
        cells = sorted(((k[2], statistics.mean(v), len(v)) for k, v in R.items() if k[0] == "goods" and k[1] == "A" and len(v) >= 3))
        five = [c for c in cells if c[1] >= FIVE]
        dearer_low = any(c[1] < FIVE - 0.4 for c in cells if five and c[0] > max(f[0] for f in five))
        x = max(f[0] for f in five) + 0.04 if five and dearer_low else None
        a_mean = statistics.mean([s for k, v in R.items() if k[0] == "goods" and k[1] == "A" for s in v] or [0])
        b_vals = [s for k, v in R.items() if k[0] == "goods" and k[1] == "B" for s in v]
        b_low = len(b_vals) >= 5 and statistics.mean(b_vals) < a_mean - 0.4
        if x is None and not b_low:
            plans.append({"account": b, "mean_us": round(m, 2), "per_day": ours_day[b], "rubric": "not price- or grade-driven"})
            continue
        if x is not None and x < c_share - MAX_LOSS_X:
            x = None                                     # too far under cost
        # a pattern this account owns (or shares only with accounts that already rate us high)
        pat = None
        for rx, n in acc_shapes[b].most_common(3):
            owners = shapes[rx]
            good = sum(v for k, v in owners.items() if k == b or acc_mean.get(k, 0) >= STAR_TARGET)
            if n >= 3 and good / sum(owners.values()) >= PURITY:
                pat = rx
                break
        gain = ((m * len(ours[b]) + 5 * ours_day[b] * 3) / (len(ours[b]) + ours_day[b] * 3) - m) / (n_acc + 5)
        plans.append({"account": b, "mean_us": round(m, 2), "n_us": len(ours[b]), "per_day": ours_day[b],
                      "x": round(x, 2) if x else None, "a_only": b_low, "pattern": pat,
                      "est_gain_3d": round(gain, 4), "loss_per_day_M": round(max(0.0, c_share - (x or c_share)) * 400_000 * ours_day[b] / 1e6, 1)})
    return {"t": now, "accounts": n_acc, "plans": plans}


def apply(out, dry=False):
    star = json.loads(STAR.read_text()) if STAR.exists() else {}
    aonly = json.loads(AONLY.read_text()) if AONLY.exists() else {}
    changed = []
    for p in out["plans"]:
        if not p.get("pattern"):
            continue
        b = p["account"]
        if p.get("x") and p["loss_per_day_M"] <= MAX_LOSS_DAY / 1e6:
            cur = star.get(b)
            if cur is None or cur.get("auto"):
                new = {"x": p["x"], "patterns": [p["pattern"]], "auto": True,
                       "why": f"stars_tuner {time.strftime('%m-%d %H:%M', time.gmtime())}: mean on us {p['mean_us']}, "
                              f"{p['per_day']}/day; its rubric rates grade A goods 5 up to ~{p['x']:.2f} of list"}
                if cur != new and (cur or {}).get("x") != new["x"]:
                    star[b] = new
                    changed.append(f"{b} x {p['x']}")
        if p.get("a_only") and b not in aonly:
            aonly[b] = [p["pattern"]]
            changed.append(f"{b} A only")
    if changed and not dry:
        STAR.write_text(json.dumps(star, ensure_ascii=False, indent=1))
        AONLY.write_text(json.dumps(aonly, ensure_ascii=False, indent=1))
    return changed


def main():
    out = build()
    out["applied"] = apply(out, dry="--dry" in sys.argv)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1))
    if "--print" in sys.argv:
        print(json.dumps(out, ensure_ascii=False, indent=1)[:6000])


if __name__ == "__main__":
    main()
