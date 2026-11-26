"""Week 08 — mbt.py: the MBT (Make Buyer Top) task force (HQ 2026-11-05): the sales team and the shop agent.

The shop sees how other students' buyers buy from it. This TF turns that into know-how for our buyer team:
which buyers get the lowest price out of our shop, which moves gave it to them (price match, VIP, clerk needs,
group buy, haggling rounds), and what the same buyers pay elsewhere on the public ledger. The findings go to the
buyer team's brief (buyer_team.brief), into private/mbt.json and to Drive week08-reports/MBT.md.

No forum or buyer text goes into any model prompt here: only counts, prices and the shop's own log of what it did.

    python mbt.py        # analyse now and print the report
"""
import json
import re
import statistics
import time
from pathlib import Path

HERE = Path(__file__).parent
LOG = HERE / "logs" / "market-kit.log"
OUT = HERE / "private" / "mbt.json"
REPORTS = Path("/mnt/gdrive-imajason-kimdaehakwon/week08-reports")
ASK_RE = re.compile(r"\[seller\] \[shop\] ([0-9a-f]{8}) (G[0-9a-f]{8}) cost (\d+) -> ask (\d+)")
DEAL_RE = re.compile(r"(\d\d:\d\d:\d\d) \[seller\] \[shop\] ([0-9a-f]{8}) deal (G[0-9a-f]{8}) at (\d+)")
MOVES = {"price_match": re.compile(r"\] ([0-9a-f]{8}) price match"),
         "vip": re.compile(r"\] ([0-9a-f]{8}) .* VIP "),
         "clerk_need": re.compile(r"\] ([0-9a-f]{8}) clerk: \{'hours': [^N]|\] ([0-9a-f]{8}) clerk: \{.*'grades': \{"),
         "group_buy": re.compile(r"\] ([0-9a-f]{8}) G[0-9a-f]{8} cost .*group buy")}


def _tasks(lines):
    """{task: {"sku", "first_ask", "deal", "moves", "t"}} from the shop's own log lines."""
    t = {}
    for l in lines:
        m = ASK_RE.search(l)
        if m:
            x = t.setdefault(m.group(1), {"sku": m.group(2), "first_ask": int(m.group(4)), "asks": 0, "moves": set()})
            x["asks"] += 1
        m = DEAL_RE.search(l)
        if m and m.group(2) in t:
            t[m.group(2)].update(deal=int(m.group(4)), hms=m.group(1))
        for name, rx in MOVES.items():
            m = rx.search(l)
            if m:
                k = next(g for g in m.groups() if g)
                if k in t:
                    t[k]["moves"].add(name)
    return t


def analyse(c=None, now=None, n_lines=60000):
    import market_model
    c = c or market_model.db()
    now = now or time.time()
    try:
        lines = LOG.read_text(encoding="utf-8", errors="replace").splitlines()[-n_lines:]
    except OSError:
        lines = []
    tasks = _tasks(lines)
    deals = [x for x in tasks.values() if x.get("deal")]
    # who bought: our settles of the same SKU at the same price, in the window the log covers
    who = {}
    for buyer, sku, amount in c.execute("""select s.buyer, s.sku, s.amount from settles s join settle_units x on x.seq = s.seq
                                           join supply_units su on su.unit = x.unit where su.shop = 'dragonash'
                                           and s.ts > ?""", (now - 36 * 3600,)):
        who.setdefault((sku, int(amount)), []).append(buyer)
    for x in deals:
        b = who.get((x["sku"], x["deal"]))
        x["buyer"] = b.pop(0) if b else None
        x["off"] = 1 - x["deal"] / x["first_ask"] if x["first_ask"] else 0
    by_move = {}
    for x in deals:
        for mv in (x["moves"] or {"none"}):
            by_move.setdefault(mv, []).append(x["off"])
    moves = {mv: {"deals": len(v), "median_off_first_ask": round(statistics.median(v), 3)} for mv, v in by_move.items()}
    by_buyer = {}
    for x in deals:
        if x.get("buyer"):
            by_buyer.setdefault(x["buyer"], []).append(x)
    buyers = {}
    for b, xs in by_buyer.items():
        if len(xs) < 3:
            continue
        buyers[b] = {"deals_at_us": len(xs), "median_off_first_ask": round(statistics.median(x["off"] for x in xs), 3),
                     "moves": {mv: sum(mv in x["moves"] for x in xs) for mv in MOVES if any(mv in x["moves"] for x in xs)}}
    # the same buyers on the whole ledger (24 h): what share of list they pay, how many shops they use
    for b in list(buyers) + [r[0] for r in c.execute(
            "select buyer from settles where ts > ? group by buyer order by sum(list_price - amount) desc limit 8",
            (now - 24 * 3600,))]:
        r = c.execute("""select count(*), avg(amount * 1.0 / list_price), count(distinct shop), avg(list_price)
                         from settles where buyer = ? and ts > ? and list_price > 0""", (b, now - 24 * 3600)).fetchone()
        buyers.setdefault(b, {}).update(ledger_24h={"deals": r[0], "paid_share": round(r[1] or 0, 3),
                                                    "shops_used": r[2], "avg_list": int(r[3] or 0)})
    ours = c.execute("""select count(*), avg(amount * 1.0 / list_price), count(distinct shop), avg(list_price)
                        from settles where buyer = 'dragonash' and ts > ? and list_price > 0""", (now - 24 * 3600,)).fetchone()
    out = {"ts": now, "at": time.strftime("%m-%d %H:%M"), "deals_at_our_shop": len(deals), "moves": moves,
           "buyers": dict(sorted(buyers.items(), key=lambda kv: -(kv[1].get("deals_at_us") or 0))[:15]),
           "our_buyer_24h": {"deals": ours[0], "paid_share": round(ours[1] or 0, 3), "shops_used": ours[2],
                             "avg_list": int(ours[3] or 0)},
           "knowhow": knowhow(moves), "insights": insights(buyers, ours)}
    try:
        OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
        (REPORTS / "MBT.md").write_text(markdown(out), encoding="utf-8")
    except OSError:
        pass
    return out


def knowhow(moves):
    """What the shop learned, as moves our buyer can make at other shops, strongest discount first."""
    base = (moves.get("none") or {}).get("median_off_first_ask", 0)
    tips = {"price_match": "cite the lowest ledger price for the SKU (24 h) and ask the shop to match it",
            "vip": "say you are a returning buyer and quote your member code from the last receipt",
            "clerk_need": "state hours/grade needs up front; shops pick a unit that passes instead of losing the sale",
            "group_buy": "ask whether the shop runs a group price this hour"}
    out = []
    for mv, v in sorted(moves.items(), key=lambda kv: -kv[1]["median_off_first_ask"]):
        if mv in tips:
            out.append({"move": mv, "deals": v["deals"], "median_off_first_ask": v["median_off_first_ask"],
                        "vs_no_move": round(v["median_off_first_ask"] - base, 3), "how": tips[mv]})
    return out


def insights(buyers, ours):
    """Plain findings the buyer team can act on, from the numbers above."""
    top = [v for v in buyers.values() if v.get("deals_at_us", 0) >= 20 and v.get("ledger_24h")]
    out = []
    if top:
        off = statistics.median(v["median_off_first_ask"] for v in top)
        share = statistics.median(v["ledger_24h"]["paid_share"] for v in top)
        lst = statistics.median(v["ledger_24h"]["avg_list"] for v in top)
        out.append(f"the busiest buyers at our shop take {off:.0%} off our first ask (median) and pay {share:.0%} of list "
                   f"on the ledger; ours pays {ours[1] or 0:.0%}. They do not win on haggling: their average list is "
                   f"{lst:,.0f} against our {int(ours[3] or 0):,} -- surplus follows the price of what is bought")
        out.append("closing fast at a fair price beats haggling: every walk-away is 0 surplus and 0 deals")
    return out


def summary():
    try:
        return json.loads(OUT.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def markdown(a):
    lines = [f"# MBT TF {a['at']}: 우리 상점에서 본 다른 바이어의 구매 행동 ({a['deals_at_our_shop']}건)",
             "## 통한 수법 (첫 호가 대비 깎인 비율, 중간값)"]
    for k in a["knowhow"]:
        lines.append(f"- {k['move']}: {k['deals']}건, {k['median_off_first_ask']:.0%} 할인 (수법 없을 때보다 "
                     f"{k['vs_no_move']:+.0%}) — {k['how']}")
    lines.append("## 시사점")
    lines += [f"- {x}" for x in a.get("insights") or []]
    lines.append("## 바이어별")
    for b, v in list(a["buyers"].items())[:10]:
        lines.append(f"- {b}: {json.dumps(v, ensure_ascii=False)}")
    lines.append(f"## 우리 바이어 (24 h): {json.dumps(a['our_buyer_24h'], ensure_ascii=False)}")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    print(markdown(analyse()))
