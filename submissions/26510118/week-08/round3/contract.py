"""The files the parts hand each other: who writes each, who reads it, and the fields the readers
count on. Several of the kit's files (inventory, forum, payments) and the ledger's settle entries
have shapes we could only guess before Round 3; a field the market names differently would not
fail anything, it would just count as zero. check() looks at what is there now and names every
row that lacks a field its readers need; the analysis runner runs it with each collection and
logs the problems, so a wrong guess shows on the first day.

A missing file is not a problem (some appear only after the first sale, order or decision); a
file that does not parse, or rows without a needed field, are.
"""
import common

ROWS = 50                # the newest rows of a .jsonl file that are looked at

# (path under MARKET_DIR, writer, readers, the rows to check, the fields each row needs); brought up to date on
# 11/5 (rivals, scoreboard, line, line_state, post_votes, refusals, stock_flow and shops.json added; the index gone)
FILES = [
    # the kit's files
    ("inventory.json", "kit", "seller, restock, linewatch, briefings", lambda d: d.get("units") or [],
     ("unit", "sku", "cost", "list_price", "category", "kind")),
    ("catalog.json", "kit", "every part", lambda d: list(d.values())[:ROWS], ("sku", "name", "category", "list_price", "kind")),
    ("demand.json", "kit", "restock, forum (wanted posts), analysis (collect)", lambda d: d, ("category", "median_price")),
    ("shops.json", "kit", "market_buyer (candidates), analysis (collect: shops' stock)", lambda d: d, ("name", "url")),
    ("forum.json", "kit", "forum (replies, votes), notes, market_buyer (shop odds), buyer analysis", lambda d: d,
     ("id", "author", "text")),
    ("payments.jsonl", "kit", "buyer notebook (buyer_memory), notes (follow-ups)", None, ("order", "paid")),
    # the trading hooks' records
    ("records/negotiations.jsonl", "seller", "replay (data room), seller analysis", None,
     ("ts", "sku", "asks", "offers", "outcome", "floor", "list_price", "cost")),
    ("records/deals.jsonl", "seller", "restock (sales)", None, ("ts", "task", "unit", "sku", "price", "cost")),
    ("records/refusals.jsonl", "seller", "restock (missed categories), seller analysis", None, ("ts", "reason")),
    ("records/sales.jsonl", "restock", "restock, linewatch, analyses, briefings", None, ("ts", "sku", "price", "cost")),
    ("records/stock_flow.jsonl", "restock", "seller analysis", None, ("ts", "what")),
    ("records/orders.jsonl", "market_buyer", "replay, buyer analysis, integrated analysis (tries)", None,
     ("ts", "order", "category", "budget")),
    ("records/unmet.jsonl", "market_buyer", "analysis (wanted categories), linewatch (the twins), analyses", None, ("ts", "category")),
    # the analysis runner's files
    ("ledger/settles.jsonl", "analysis (collect)", "summary, tools, journal, notes, linewatch", None,
     ("seq", "ts", "shop", "item", "amount")),
    ("market_summary.json", "analysis (collect)", "restock, seller (asks), market_buyer, journal, notes, analyses", lambda d: [d],
     ("settles_24h", "by_sku", "by_category", "by_shop")),
    ("analysis/wanted.json", "analysis (collect)", "restock", lambda d: [d], ("at", "categories")),
    ("analysis/rivals.json", "analysis (collect)", "market_buyer (near ties)", lambda d: [d], ("at", "shops")),
    ("analysis/watch.json", "analysis (watch.py, every 20 min)", "a person (alerts on rule and market changes)", lambda d: [d],
     ("at", "alerts", "facts")),
    ("analysis/bad_faith.json", "analysis (collect)", "market_buyer (bad-faith owners' shops last)", lambda d: [d], ("at", "shops")),
    ("analysis/scoreboard.json", "analysis (collect: scoreboard.py)", "linewatch (stars), integrated analysis", lambda d: [d],
     ("at", "place", "total")),
    ("analysis/line.json", "linewatch, or a person (then left alone)", "restock (load_line), seller (asks)", lambda d: [d], ("skus",)),
    ("analysis/line_state.json", "linewatch", "linewatch, integrated analysis", lambda d: [d], ("caps", "defaults")),
    ("analysis/post_votes.json", "analysis (notes step)", "notes (pause), integrated analysis", lambda d: list(d.values())[-ROWS:],
     ("kind", "ts")),
    ("analysis/active_rules.json", "integrated analysis, or a person (analysts.person_set)", "seller, market_buyer, restock",
     lambda d: [d], ("seller", "buyer")),
    ("analysis/person_rules.json", "a person (analysts.person_set)", "integrated analysis (keeps its hands off)",
     lambda d: list(d.values()), ("value", "at")),
    ("analysis/insights.json", "integrated analysis", "briefings, data room", lambda d: d,
     ("id", "role", "text", "evidence", "use", "status")),
    ("analysis/reports.jsonl", "role analyses", "briefings, data room", None, ("role", "at", "report", "proposals")),
    ("analysis/memos.jsonl", "data room", "briefings", None, ("ts", "from", "to", "act", "text")),
]


def check() -> dict:
    """{"present": [...], "missing": [...], "problems": ["path: field missing in n of m rows (writer -> readers)"]}."""
    out = {"present": [], "missing": [], "problems": []}
    for path, writer, readers, rows_of, fields in FILES:
        full = common.MARKET_DIR / path
        if not full.exists():
            out["missing"].append(path)
            continue
        out["present"].append(path)
        if rows_of is None:
            text = full.read_text(encoding="utf-8").splitlines()
            rows = common.read_jsonl(full)[-ROWS:]
            if len(rows) < len([x for x in text if x.strip()][-ROWS:]):
                out["problems"].append(f"{path}: lines that do not parse ({writer} -> {readers})")
        else:
            data = common.read_json(full)
            try:
                rows = rows_of(data) if data is not None else None
            except (AttributeError, TypeError):
                rows = None
            if not isinstance(rows, list):
                out["problems"].append(f"{path}: not the shape its readers expect ({writer} -> {readers})")
                continue
        for f in fields:
            lacking = [r for r in rows if not isinstance(r, dict) or r.get(f) is None]
            if lacking:
                seen = sorted(lacking[0])[:8] if isinstance(lacking[0], dict) else type(lacking[0]).__name__
                out["problems"].append(f"{path}: '{f}' missing in {len(lacking)} of {len(rows)} rows, e.g. a row with {seen} "
                                       f"({writer} -> {readers})")
    return out
