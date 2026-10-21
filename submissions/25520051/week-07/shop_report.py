"""How is Moss & Oak doing? Sales, reviews and our rank in the market, from files we already have.

    python shop_report.py            # full report
    python shop_report.py --brief    # one line, for a periodic monitor

Sources: logs/market-kit.log ([seller] lines) and .market/shops.json (the shop list the kit hands
our buyer for each order, with up to ten newest reviews per shop). Sales are split at the time
prices.json was last tuned, so challenge 1 can compare before and after.
"""
import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).parent
RESERVE = {"lamp": 100, "chair": 180, "bookshelf": 260}
OURS = {"moss-and-oak", "move_76"}
THEMES = {   # what reviewers keep coming back to; one review can count for several
    "slow haggling": r"rounds|drawn out|gradual|slow|took (a while|long|several)",
    "fast deal": r"quick|fast|in 1 round|in one round|immediately",
    "fair price": r"fair|good price|reasonable|cheap",
    "receipt ok": r"receipt matched|signed receipt|receipt (was )?signed|honest receipt",
    "receipt bad": r"overcharg|receipt (said|did ?n.t|was higher)|mismatch",
    "no deal": r"no deal|no agreement|did not|walked",
}


def item_of(line, price):
    m = re.search(r"deal at \d+ \((lamp|chair|bookshelf)", line)
    return m.group(1) if m else ("lamp" if price < 150 else ("chair" if price < 255 else "bookshelf"))


def sales():
    since = ""
    if (HERE / "prices.json").exists():
        since = json.loads((HERE / "prices.json").read_text(encoding="utf-8")).get("since", "")
    out = {"before": defaultdict(list), "after": defaultdict(list)}
    rejected = Counter()
    for ln in (HERE / "logs" / "market-kit.log").read_text(encoding="utf-8", errors="ignore").splitlines():
        period = "after" if since and ln[:8] >= since else "before"
        if m := re.search(r"\[seller\] -> deal at (\d+)", ln):
            p = int(m.group(1))
            out[period][item_of(ln, p)].append(p)
        elif "[seller] -> rejected after" in ln:
            rejected[period] += 1
    return out, rejected, since


def reviews():
    f = HERE / ".market" / "shops.json"
    shops = json.loads(f.read_text(encoding="utf-8")) if f.exists() else []
    mine = [r for s in shops if s["name"] in OURS for r in s["reviews"]]
    board = []
    for s in shops:
        st = [r["stars"] for r in s["reviews"] if r.get("stars")]
        if len(st) >= 3:
            board.append((sum(st) / len(st), len(st), s["name"]))
    board.sort(reverse=True)
    return mine, board


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--brief", action="store_true")
    a = ap.parse_args()
    s, rej, since = sales()
    mine, board = reviews()
    stars = [r["stars"] for r in mine if r.get("stars")]
    rank = next((i + 1 for i, b in enumerate(board) if b[2] in OURS), None)
    themes = Counter(t for r in mine for t, rx in THEMES.items() if re.search(rx, str(r.get("review", "")), re.I))

    def line(period):
        parts = []
        for item in RESERVE:
            ps = s[period][item]
            if ps:
                parts.append(f"{item} {len(ps)}x avg {sum(ps) / len(ps):.0f}")
        return ", ".join(parts) or "none"

    if a.brief:
        avg = f"{sum(stars) / len(stars):.2f}" if stars else "-"
        print(f"[report] after tuning ({since}): {line('after')}; rejected {rej['after']} | "
              f"reviews {len(stars)} avg {avg}, rank {rank}/{len(board)} | themes {dict(themes)}", flush=True)
        return

    print(f"== sales (prices tuned at {since or 'never'})")
    for period in ("before", "after"):
        n = sum(len(v) for v in s[period].values())
        print(f"{period:>6}: {n} deals, {rej[period]} rejected after six offers | {line(period)}")
        for item, ps in [(k, v) for k, v in s[period].items() if v]:
            print(f"        {item}: margin over reserve {sum(ps) / len(ps) - RESERVE[item]:+.1f}, prices {sorted(ps)}")
    print(f"\n== our reviews (newest ten the market lists): {len(stars)}, "
          f"avg {sum(stars) / len(stars):.2f}" if stars else "\n== our reviews: none listed yet")
    print("   stars:", dict(sorted(Counter(stars).items())))
    print("   themes:", dict(themes.most_common()))
    for r in mine:
        print(f"   {r['stars']}* {str(r.get('review', ''))[:150]}")
    print(f"\n== market, shops with 3+ listed reviews ({len(board)}); we are #{rank}")
    for i, (avg, n, name) in enumerate(board[:10]):
        print(f"   {i + 1:>2}. {avg:.2f} ({n})  {name}{'  <- us' if name in OURS else ''}")


if __name__ == "__main__":
    main()
