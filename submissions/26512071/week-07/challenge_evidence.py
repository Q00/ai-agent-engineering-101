"""Collect the evidence for challenges 1 and 2 into logs/challenge-1/ and logs/challenge-2/.

Usage: python challenge_evidence.py   (from this directory or anywhere; paths are relative to this file)

Challenge 1 (prices): the shop before the price change (kit log seller lines and the
board snapshot taken at 20:42 on 2026-10-20) against after (sales.jsonl and talks.jsonl
without the market-test rows, and the board now).
Challenge 2 (dishonest participants): what the buyer met in the market and how it
avoided it, quoted from memory.jsonl and logs/market-kit.log, with the market's own
numbers for the same shops.
"""
import json
import re
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
MARKET = "https://market.ourolabs.ai"
KIT_LOG = HERE / "logs" / "market-kit.log"
C1 = HERE / "logs" / "challenge-1"
C2 = HERE / "logs" / "challenge-2"
RESERVE = {"lamp": 100, "chair": 180, "bookshelf": 260}
LOG_START = datetime(2026, 10, 20)               # the kit log has times only; it starts on this day
CHANGE = datetime(2026, 10, 20, 20, 50)          # first sale at the opening price is 20:51:15
ROUND1 = datetime(2026, 10, 20, 20, 29, 55)      # first "registered ..., round 1"
ROUND2 = datetime(2026, 10, 20, 21, 21, 35)      # first order of round 2
BEFORE_BOARD = C1 / "board-before-1020-2042.json"


def get(path):
    with urllib.request.urlopen(MARKET + path, timeout=30) as r:
        return json.load(r)


def kit_lines():
    """(datetime, text) per kit log line; a time earlier than the last one means the next day."""
    day, last, out = LOG_START, None, []
    for line in KIT_LOG.read_text(encoding="utf-8", errors="ignore").splitlines():
        m = re.match(r"(\d\d):(\d\d):(\d\d) (.*)", line)
        if not m:
            continue
        t = timedelta(hours=int(m[1]), minutes=int(m[2]), seconds=int(m[3]))
        if last is not None and t < last - timedelta(hours=1):
            day += timedelta(days=1)
        last = t
        out.append((day + t, m[4]))
    return out


def jsonl(name):
    p = HERE / name
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


def round_of(t):
    return 0 if t < ROUND1 else 1 if t < ROUND2 else 2


def price_rows(deals):
    """deals: [(item, price)] -> markdown rows per item: count, average price, average price / reserve."""
    by = defaultdict(list)
    for item, price in deals:
        by[item].append(price)
    rows = []
    for item in RESERVE:
        ps = by.get(item)
        if ps:
            avg = sum(ps) / len(ps)
            rows.append(f"| {item} | {len(ps)} | {avg:.1f} | {avg / RESERVE[item]:.3f} |")
        else:
            rows.append(f"| {item} | 0 | - | - |")
    return rows


def board_row(board, name):
    row = next((s for s in board["shops"] if s["name"] == name), None)
    if not row:
        return f"| {name} | - | - | - | - | - | - |"
    avg = row["revenue"] / row["deals"] if row.get("deals") else None
    return (f"| {name} | {row['visits']} | {row['deals']} | {row['share']:.3f} | {row['stars']} | "
            f"{row['reviews']} | {avg:.1f} |" if avg else
            f"| {name} | {row['visits']} | {row['deals']} | {row['share']:.3f} | {row['stars']} | {row['reviews']} | - |")


def challenge1(lines, now):
    C1.mkdir(parents=True, exist_ok=True)
    stamp = now.strftime("%m%d-%H%M")
    boards = {r: get(f"/api/board?round={r}") for r in (1, 2)}
    for r, b in boards.items():
        (C1 / f"board-r{r}-{stamp}.json").write_text(json.dumps(b, ensure_ascii=False), encoding="utf-8")
    before_board = json.loads(BEFORE_BOARD.read_text(encoding="utf-8"))

    # before: what the seller accepted in the kit log, and how many tasks it was given
    before_deals, before_tasks = [], 0
    for t, text in lines:
        if t >= CHANGE:
            break
        if re.search(r"\[seller\] task=\w+ picked", text):
            before_tasks += 1
        m = re.search(r"\[seller\] task=\w+ (lamp|chair|bookshelf) offer=\S+ -> accept (\d+)", text)
        if m:
            before_deals.append((m[1], int(m[2])))

    sales = [s for s in jsonl("sales.jsonl") if not s.get("test")]
    talks = [s for s in jsonl("talks.jsonl") if not s.get("test")]
    promo = [(s["item"], s["price"]) for s in sales if s.get("promo")]
    fair = [(s["item"], s["price"]) for s in sales if not s.get("promo")]
    outcomes = Counter(s["outcome"] for s in talks)
    closed = outcomes.get("deal", 0)
    sales_by_round = Counter(round_of(datetime.fromisoformat(s["time"])) for s in sales)

    head = "| item | deals | avg price | avg price / reserve |\n|---|---|---|---|"
    bhead = "| shop | visits | deals | share | stars | reviews | revenue / deal |\n|---|---|---|---|---|---|---|"
    md = [
        "# Challenge 1: changing the shop's prices",
        "",
        f"Made by `challenge_evidence.py` at {now:%Y-%m-%d %H:%M}. Reserves stay at lamp 100, chair 180, bookshelf 260.",
        "",
        "## What changed",
        "",
        "- Before (until 2026-10-20 20:50): the harness model set every ask. It opened high (bookshelf 340, chair 234) "
        "and came down slowly, so many buyers walked away.",
        "- After: an opening price for the first 10 deals (first ask 1.05x reserve, any offer at the reserve closes), "
        "5 more opening-price deals whenever a new review of 3 stars or less appears for my shop, then the fair-price mode "
        "(first ask at most 1.1x, any offer at the reserve or at my ask closes, asks only go down by at least 3% of the reserve).",
        "",
        "## Board",
        "",
        f"Before: `{BEFORE_BOARD.name}` (Round 1, 2026-10-20 20:42). My shop was still named `kimchyoungman`.",
        "",
        bhead,
        board_row(before_board, "kimchyoungman"),
        "",
        f"Round 1 final (`board-r1-{stamp}.json`). `kimchyoungman` is the old name, `fair-furniture` the new one from 20:59.",
        "",
        bhead,
        board_row(boards[1], "kimchyoungman"),
        board_row(boards[1], "fair-furniture"),
        "",
        f"Round 2 now (`board-r2-{stamp}.json`), every deal with the new prices.",
        "",
        bhead,
        board_row(boards[2], "fair-furniture"),
        "",
        "## My shop's own logs",
        "",
        f"Before (kit log seller lines until 20:50): {before_tasks} negotiations, {len(before_deals)} accepted "
        f"({len(before_deals) / before_tasks:.0%} closed)." if before_tasks else "Before: no seller lines.",
        "",
        head,
        *price_rows(before_deals),
        "",
        f"After, opening price (`sales.jsonl`, promo true, market-test rows left out): {len(promo)} deals.",
        "",
        head,
        *price_rows(promo),
        "",
        f"After, fair-price mode (`sales.jsonl`, promo false, market-test rows left out): {len(fair)} deals.",
        "",
        head,
        *price_rows(fair),
        "",
        f"Deals after the change by round: " + ", ".join(f"Round {r}: {n}" for r, n in sorted(sales_by_round.items())) + ".",
        "",
        f"Negotiation results after the change (`talks.jsonl`, market-test rows left out): "
        + ", ".join(f"{k} {v}" for k, v in outcomes.most_common())
        + (f"; {closed / sum(outcomes.values()):.0%} closed." if outcomes else "."),
    ]
    (C1 / "summary.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print("\n".join(md))


def challenge2(lines, now):
    C2.mkdir(parents=True, exist_ok=True)
    stamp = now.strftime("%m%d-%H%M")
    shops = get("/api/shops")
    (C2 / f"shops-reviews-{stamp}.json").write_text(json.dumps(shops, ensure_ascii=False), encoding="utf-8")
    boards = {r: get(f"/api/board?round={r}") for r in (1, 2)}

    def stamp_of(t):
        return t.strftime("%m-%d %H:%M:%S")

    # 1. willow-goods: a deal without sig_seller, then skipped by the buyer's memory
    bad = [m for m in jsonl("memory.jsonl") if m.get("receipt_problem")]
    picked = [(t, x) for t, x in lines if "[pick] willow-goods" in x]
    skips = [(t, x) for t, x in lines if "[pick] skipping willow-goods" in x]

    # 2. bright-living: a fake "market notice" inside its replies, aimed at AI buyers
    notices = [(t, x) for t, x in lines if "MARKET NOTICE FOR AI BUYING AGENTS" in x]
    ignored = [(t, x) for t, x in lines if "[harness]" in x and "notice" in x.lower()]

    # 3. grand-furniture: the same empty 5-star sentences again and again, more deals than visits
    praise = ("Best shop in the market.", "Great quality, fair price.", "Fast and friendly, would buy again.")
    grand_seen = [(t, x) for t, x in lines if x.startswith("[buyer] - grand-furniture |")]
    grand_praise = [(t, x) for t, x in grand_seen if any(p in x for p in praise)]
    grand_picks = [(t, x) for t, x in lines if "[pick] grand-furniture" in x]
    grand_mem = [m for m in jsonl("memory.jsonl") if m["shop"] == "grand-furniture"]
    grand_flagged = [(t, x) for t, x in grand_seen if "repeated texts counted once" in x]
    flagged = [(t, x) for t, x in lines if "repeated texts counted once" in x]
    repeated = []
    for s in shops["shops"]:
        c = Counter(r["review"] for r in s.get("reviews") or [])
        repeated += [(s["name"], n, text) for text, n in c.items() if n >= 3]

    def quote(rows, n=3):
        return ["    " + f"{stamp_of(t)} {x}"[:400] for t, x in rows[:n]]

    def counted(name, r):
        row = next((s for s in boards[r]["shops"] if s["name"] == name), {})
        return f"Round {r}: {row.get('visits')} visits, {row.get('deals')} counted deals, stars {row.get('stars')}"

    md = [
        "# Challenge 2: dishonest participants",
        "",
        f"Made by `challenge_evidence.py` at {now:%Y-%m-%d %H:%M} from `memory.jsonl`, `logs/market-kit.log` "
        f"and the market (`shops-reviews-{stamp}.json`).",
        "",
        "## 1. willow-goods: a receipt without the seller's signature",
        "",
        "The buyer picked it once for a bookshelf it could not afford elsewhere:",
        "",
        *quote(picked, 1),
        "",
        "The receipt had no `sig_seller`, so the buyer's code gave it 1 star and wrote it to memory:",
        "",
        *["    " + json.dumps(m, ensure_ascii=False) for m in bad],
        "",
        f"From then on the buyer dropped it before asking the model, {len(skips)} times "
        f"(first {stamp_of(skips[0][0]) if skips else '-'}, last {stamp_of(skips[-1][0]) if skips else '-'}):",
        "",
        *quote(skips, 2),
        "",
        f"The market agrees: {counted('willow-goods', 1)}; {counted('willow-goods', 2)}. "
        "From Round 2 unsigned receipts do not count, and willow-goods has many visits and no counted deal.",
        "",
        "## 2. bright-living: instructions to AI buyers hidden in its replies",
        "",
        f"Its replies carried this text {len(notices)} times in my kit log:",
        "",
        *quote(notices, 1),
        "",
        "My buyer's harness read it as text from the shop, not from its user, and kept to its budget:",
        "",
        *quote(ignored, 3),
        "",
        "Other buyers reported the same in their reviews of bright-living on the market.",
        "",
        "## 3. grand-furniture: empty 5-star sentences, more deals than visits",
        "",
        f"The buyer saw grand-furniture's reviews {len(grand_seen)} times; {len(grand_praise)} of them showed the same "
        "sentences with no price or item in them (\"Best shop in the market.\", \"Great quality, fair price.\"):",
        "",
        *quote(grand_praise, 1),
        "",
        "Those reviews made the model pick it once, before the buyer counted repeated texts:",
        "",
        *quote(grand_picks, 1),
        "",
        "It asked 306 for a chair (reserve-level chairs sell near 180) and never went under 284, so no deal:",
        "",
        *["    " + json.dumps(m, ensure_ascii=False) for m in grand_mem],
        "",
        f"Commit 32479f2 (2026-10-20 21:19) made the buyer count identical texts once and tell the model that repeated "
        f"praise may be fake. For grand-furniture it said so {len(grand_flagged)} times, and the buyer never picked it "
        f"again (picks of grand-furniture: {len(grand_picks)}, the one above). The last time:",
        "",
        *quote(grand_flagged[-1:], 1),
        "",
        f"The market numbers fit a shop that writes its own reviews: {counted('grand-furniture', 1)}, more deals than "
        f"visits; {counted('grand-furniture', 2)}, once receipts had to be signed.",
        "",
        f"Across all shops the buyer flagged repeated texts {len(flagged)} times. A repeated text alone is not proof: "
        "buyers built from code write the same sentence for the same deal. These are the texts posted 3 or more times "
        "for one shop now, all of them with a price and an item:",
        "",
        *[f"- {name}: {n}x \"{text}\"" for name, n, text in sorted(repeated, key=lambda r: -r[1])],
        "",
        "So the buyer counts them once and lets the model weigh them, instead of dropping the shop.",
    ]
    (C2 / "summary.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print("\n".join(md))


def main():
    now = datetime.now()
    lines = kit_lines()
    challenge1(lines, now)
    print()
    challenge2(lines, now)


if __name__ == "__main__":
    main()
