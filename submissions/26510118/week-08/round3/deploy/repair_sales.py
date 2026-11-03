"""One-time repair of the shop's sale records (11/3, Round 3 night).

Until restock.check_deals counted only the latest deal on a unit, every agreed deal whose unit
later left the inventory was recorded as a sale. Most buyers who agree do not pay (their own checks
refuse the receipt), the unit is offered again, and one unit could be agreed several times: 519
sales were recorded against 280 settles on the market. This rebuilds records/sales.jsonl and
unpaid.jsonl from records/deals.jsonl and the public ledger's settles of our shop (matched by unit,
the price preferred, the latest deal before the settle), keeps the old files as *.bak-1103, and
resets restock's pending deals to those still inside UNPAID_AFTER.

    cd ~/market/round3 && MARKET_DIR=$PWD/.market ~/market/.venv/bin/python deploy/repair_sales.py [--write]

Without --write it prints what it would do and changes nothing.
"""
import argparse
import json
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import common   # noqa: E402
import ledger   # noqa: E402
import restock  # noqa: E402

FIELDS = ("task", "unit", "sku", "price", "cost", "list_price", "grade", "warranty")


def rebuild(deals: list, settles: list, now: float) -> tuple[list, list, dict]:
    """(sales, unpaid, pending) from the deals and our shop's settles."""
    taken, sales = set(), []
    for s in sorted(settles, key=lambda s: s.get("ts") or 0):
        options = [d for d in deals if d["unit"] == s.get("unit") and d["task"] not in taken
                   and (d.get("ts") or 0) <= (s.get("ts") or 0) + 5]
        if not options:
            continue
        d = max(options, key=lambda d: (d.get("price") == s.get("amount"), d.get("ts") or 0))
        taken.add(d["task"])
        sales.append(dict(ts=int(s.get("ts") or now), deal_ts=d.get("ts"), ledger=s.get("seq"),
                          **{k: d.get(k) for k in FIELDS}))
    unpaid, pending = [], {}
    for d in deals:
        if d["task"] in taken:
            continue
        if now - (d.get("ts") or now) <= restock.UNPAID_AFTER:
            pending[d["task"]] = d
        else:
            unpaid.append(dict(ts=int((d.get("ts") or now) + restock.UNPAID_AFTER), deal_ts=d.get("ts"), rebuilt=True,
                               **{k: d.get(k) for k in FIELDS}))
    return sales, sorted(unpaid, key=lambda u: u["ts"]), pending


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    write = ap.parse_args().write
    now = time.time()
    print(f"ledger read on: {ledger.collect()} new settle/broken entries")
    ours = {url for url, name in ledger.names().items() if name in common.OWN_NAMES} | set(common.OWN_NAMES)
    settles = [s for s in common.read_jsonl(ledger.folder() / "settles.jsonl") if s.get("shop") in ours]
    deals = common.records("deals")
    sales, unpaid, pending = rebuild(deals, settles, now)
    old_sales, old_unpaid = common.records("sales"), common.records("unpaid")
    print(f"our settles on the ledger {len(settles)}, agreed deals {len(deals)}")
    print(f"sales {len(old_sales)} -> {len(sales)}, unpaid {len(old_unpaid)} -> {len(unpaid)}, pending {len(pending)}")
    print(f"revenue {sum(s['price'] for s in old_sales)} -> {sum(s['price'] for s in sales)}")
    if not write:
        print("(dry run: nothing written; add --write)")
        return 0
    folder = common.MARKET_DIR / "records"
    for name in ("sales.jsonl", "unpaid.jsonl", "restock_state.json"):
        if (folder / name).exists():
            shutil.copy2(folder / name, folder / f"{name}.bak-1103")
    for name, rows in (("sales.jsonl", sales), ("unpaid.jsonl", unpaid)):
        with open(folder / name, "w", encoding="utf-8") as f:
            f.writelines(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
    state = common.read_json(folder / "restock_state.json", {}) or {}
    state.pop("deals_offset", None)                 # restock reads deals.jsonl by offset since 11/4; it re-derives it
    state.update(pending=pending, deals_seen=len(deals))
    common.write_json(folder / "restock_state.json", state)
    print("written; the old files are kept as *.bak-1103")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
