"""Backtest of three buyer changes (owner 11-06 22 h: "한 번 다 테스트해봐"), on our deals since patch 3.5 (18:00 KST).

1. No accept at the first ask: today the buyer takes a shop's first ask when it is under the shop's usual price
   (market_buyer.haggle, `ask <= target`), and that price ignores the grade. Here those deals haggle instead. A shop's
   answer is drawn from our own policy-v6 talks with one shop whose first ask was under the cap and that we countered:
   P(deal) and final / first ask, by kind and grade (A vs B/C).
2. A lower cap for food B and C: list x (1 - T_grade). A deal above the new cap is lost (low bound) or closes at the
   new cap (high bound); the truth lies between, since we never asked those shops for less.
3. Shop memory by category: market_buyer picks shops by model.Prices, one price ratio per shop over every category.
   Here a shop x category ratio (shrunk to the shop's) predicts each settle on the ledger; better predictions are what
   would pick cheaper shops. Also: among the three shops we quoted, how often each model ranks the cheapest first.

    python backtest_haggle.py --data data            # read-only on market.db and the buyer logs
"""
import argparse
import datetime as dt
import json
import sqlite3
from collections import defaultdict
from pathlib import Path

KST = dt.timezone(dt.timedelta(hours=9))
SINCE = dt.datetime(2026, 11, 6, 18, 0, tzinfo=KST).timestamp()   # patch 3.5
END = dt.datetime(2026, 11, 17, 23, 59, tzinfo=KST).timestamp()   # round 3 ends
HL = 24 * 3600            # model.Prices half-life (policy half_life_h 24)
PRIOR_W, R0 = 5, 0.85     # model.PRICE_PRIOR_W, model.PRICE_R0
US = "inextro"


def jsonl(p):
    out = []
    for line in Path(p).read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return out


def grade_said(rec, shop):
    s = (rec.get("shop_said") or {}).get(shop) or ""
    i = s.find("grade ")
    return s[i + 6] if i >= 0 and s[i + 6:i + 7] in "ABC" else None


def bucket(kind, grade):
    return (kind, "A" if grade == "A" else "BC" if grade in ("B", "C") else "?")


def immediate(r):
    """Deal taken at the shop's first ask with no counter."""
    return r.get("end") == "deal" and len(r.get("offers") or []) == 1 and r.get("asks") \
        and r["offers"][0] >= r["asks"][0] and r.get("tried") == 1


def summary(deals, hours, label):
    n = len(deals)
    mean = sum(deals) / n if n else 0
    return {"case": label, "deals": round(n, 1), "per_h": round(n / hours, 1), "saved_mean": round(mean, 4)}


def project(board_me, per_h, mean, now):
    """Saved on the board at the round's end if the rest of the round runs at this rate and mean (breaks aside)."""
    n0 = board_me["deals"]
    s0 = board_me["saved"] * (n0 + 100)
    h = (END - now) / 3600
    return round((s0 + per_h * h * mean) / (n0 + per_h * h + 100), 4)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data")
    a = ap.parse_args()
    d = Path(a.data)
    db = sqlite3.connect(f"file:{d / 'market.db'}?mode=ro", uri=True)
    cat = {s: (lp, k, c) for s, lp, k, c in db.execute("SELECT sku, list_price, kind, category FROM catalog")}
    grade = {p["order"]: p.get("grade") for p in jsonl(d / "purchases.jsonl")}
    paid = {p["order"] for p in jsonl(d / "purchases.jsonl")}
    recs = [r for r in jsonl(d / "buyer.jsonl") if r.get("kind")]
    v6 = [r for r in recs if r.get("policy") == 6]
    win = [r for r in recs if r["ts"] >= SINCE]
    now = max(r["ts"] for r in recs)
    hours = (now - SINCE) / 3600
    board = json.loads(db.execute("SELECT raw FROM board ORDER BY ts DESC LIMIT 1").fetchone()[0])
    me = next(b for b in board["buyers"] if b["name"] == US)

    # --- shop response to a counter, from v6 talks with one shop, first ask under the cap
    resp = defaultdict(lambda: {"deal": 0, "lost": 0, "r": []})
    for r in v6:
        if r.get("tried") != 1 or not r.get("asks") or not r.get("offers") or not r.get("cap"):
            continue
        if r["asks"][0] > r["cap"] or r["offers"][0] >= r["asks"][0]:
            continue                                   # not a countered ask under the cap
        g = grade.get(r["order"]) or grade_said(r, r.get("shop"))
        b = resp[bucket(r["kind"], g)]
        if r.get("end") == "deal" and r["order"] in paid:
            b["deal"] += 1
            b["r"].append(r["price"] / r["asks"][0])
        elif r.get("end") != "deal":
            b["lost"] += 1
    # lost talks rarely say the grade (it is in the receipt we never got), so P(deal) is per kind, the ratio per grade
    pk = {}
    for kind in ("food", "goods"):
        dl = sum(b["deal"] for k, b in resp.items() if k[0] == kind)
        ls = sum(b["lost"] for k, b in resp.items() if k[0] == kind)
        pk[kind] = dl / (dl + ls) if dl + ls else 0
    model = {}
    for k, b in resp.items():
        if b["r"]:
            model[k] = (pk[k[0]], sum(b["r"]) / len(b["r"]), len(b["r"]))
    print("## shop response to a counter (policy v6, one shop, first ask under cap)")
    for k, (p, r, n) in sorted(model.items()):
        print(f"  {k}: deals {n}, P(deal | kind) {p:.2f}, final/first ask {r:.3f}")

    deals = [r for r in win if r.get("end") == "deal" and r["order"] in paid and r.get("list")]
    base = [1 - r["price"] / r["list"] for r in deals]
    out = [summary(base, hours, "now")]

    # --- 1. no accept at the first ask
    for which in (("BC",), ("A", "BC")):
        vals, nd = [], 0.0
        for r in deals:
            g = bucket(r["kind"], grade.get(r["order"]))
            if immediate(r) and g[1] in which and g in model:
                p, ratio, _ = model[g]
                vals.append((1 - r["price"] * ratio / r["list"], p))
            else:
                vals.append((1 - r["price"] / r["list"], 1.0))
        n = sum(w for _, w in vals)
        mean = sum(v * w for v, w in vals) / n
        out.append({"case": f"1 haggle first asks {'+'.join(which)}", "deals": round(n, 1), "per_h": round(n / hours, 1),
                    "saved_mean": round(mean, 4), "changed": sum(1 for r in deals if immediate(r)
                                                              and bucket(r["kind"], grade.get(r["order"]))[1] in which)})

    # --- 2. lower cap for food B / C
    for tb, tc in ((0.25, 0.30), (0.25, 0.35), (0.30, 0.35), (0.25, 0.40), (0.30, 0.40)):
        lo, hi = [], []
        for r in deals:
            g = grade.get(r["order"])
            t = {"B": tb, "C": tc}.get(g) if r["kind"] == "food" else None
            s = 1 - r["price"] / r["list"]
            if t is None or s >= t:
                lo.append(s), hi.append(s)
            else:
                hi.append(t)                           # the shop would have come down to the new cap
        o = summary(lo, hours, f"2 food cap B {tb} C {tc}: lost")
        o["high"] = summary(hi, hours, "")["saved_mean"]
        out.append(o)

    print("\n## our deals since 18:00 KST,", round(hours, 1), "h; projected board Saved at 11-17 23:59 (breaks aside)")
    for o in out:
        o["final_saved"] = project(me, o["per_h"], o["saved_mean"], now)
        print(" ", json.dumps(o, ensure_ascii=False))
    print(f"  board now: saved {me['saved']}, deals {me['deals']}")

    # --- 3. shop memory by category
    names = {u: n for u, n in db.execute("SELECT url, name FROM shops")}
    S = {}                                             # key -> [sum r w, sum w, t]

    def get(k, t):
        v = S.get(k)
        if not v:
            return 0.0, 0.0
        f = 0.5 ** ((t - v[2]) / HL)
        return v[0] * f, v[1] * f

    def add(k, t, r):
        s, w = get(k, t)
        S[k] = [s + r, w + 1, t]

    err = defaultdict(lambda: [0.0, 0.0, 0])
    quotes = sorted((r["ts"], i) for i, r in enumerate(win) if len(r.get("quotes") or {}) >= 2)
    hit, qi = [0, 0, 0], 0

    def rank(r, t):
        q, qs = r["quotes"], r.get("quote_skus") or {}
        rows = [(s, q[s] / cat[qs[s]][0], cat[qs[s]][2]) for s in q if s in qs and qs[s] in cat and q[s]]
        if len(rows) < 2:
            return
        ms, mw = get("*", t)
        market = (ms + R0) / (mw + 1)

        def p_shop(s):
            ss, sw = get(("s", s), t)
            return (ss + PRIOR_W * market) / (sw + PRIOR_W)

        def p_cat(s, cg):
            cs, cw = get(("c", s, cg), t)
            return (cs + PRIOR_W * p_shop(s)) / (cw + PRIOR_W)
        best = min(rows, key=lambda t_: t_[1])[0]
        hit[0] += min(rows, key=lambda t_: p_shop(t_[0]))[0] == best
        hit[1] += min(rows, key=lambda t_: p_cat(t_[0], t_[2]))[0] == best
        hit[2] += 1

    for ts, x in db.execute("SELECT ts, data FROM ledger WHERE kind = 'settle' ORDER BY seq"):
        while qi < len(quotes) and quotes[qi][0] <= ts:          # each order ranked with the ledger before it
            rank(win[quotes[qi][1]], quotes[qi][0])
            qi += 1
        x = json.loads(x)
        c = cat.get(x.get("sku") or x.get("item"))
        if not c or not x.get("amount") or not x.get("shop"):
            continue
        lp, kind, cg = c
        shop, top = names.get(x["shop"], x["shop"]), cg.split(">")[0]
        r = x["amount"] / lp
        if ts >= SINCE and x.get("buyer") != US:
            ms, mw = get("*", ts)
            market = (ms + R0) / (mw + 1)
            ss, sw = get(("s", shop), ts)
            p_shop = (ss + PRIOR_W * market) / (sw + PRIOR_W)
            cs, cw = get(("c", shop, cg), ts)
            p_cat = (cs + PRIOR_W * p_shop) / (cw + PRIOR_W)
            for key in ("all", kind, top):
                e = err[key]
                e[0] += abs(r - p_shop)
                e[1] += abs(r - p_cat)
                e[2] += 1
        if x.get("buyer") != US:
            add("*", ts, r), add(("s", shop), ts, r), add(("c", shop, cg), ts, r)
    print("\n## 3. predicting each settle's price / list since 18:00 (other buyers): mean abs error")
    for k, (a1, a2, n) in sorted(err.items(), key=lambda kv: -kv[1][2])[:8]:
        print(f"  {k}: n {n}, shop model {a1 / n:.4f}, shop x category {a2 / n:.4f}")
    print(f"  our orders since 18:00, cheapest first ask among the quoted shops ranked first: shop model {hit[0]}/{hit[2]}, "
          f"shop x category {hit[1]}/{hit[2]}")


if __name__ == "__main__":
    main()
