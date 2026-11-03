"""Long-term buyer memory: one row per order, plus the facts that arrive after the order is over.

market_buyer writes a row when the negotiation ends, before the kit pays, so by itself the row only knows the
receipt. Three things arrive later and are merged into that row, so the shop scores read certified facts:
  - deal.json (the review hook, after settlement): certified grade, claim_honest, value, warranty, ledger seq
  - payments.jsonl: the kit refused to pay (reason) -> the row is no deal any more (outcome "unpaid")
  - broken.jsonl: goods broke -> matched to the latest settled purchase of that name, with whether the warranty
    covered it; an unrefunded break is the buyer's full loss and counts against the shop
Rows are matched by the order id. A cursor file remembers how far payments.jsonl and broken.jsonl were read.
"""
import json
import time
from pathlib import Path

MEMORY = Path(".market") / "buyer_memory.json"
CURSOR = Path(".market") / "memory_sync.json"
BREAK_WINDOW = 6 * 3600   # goods can break up to 6 h after the sale (agent.md)


def load(path=MEMORY):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []


def save(rows, path=MEMORY):
    path = Path(path)
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")


def find(rows, order_id):
    if not order_id:
        return None
    return next((r for r in reversed(rows) if r.get("order") == order_id), None)


def record_settlement(rows, deal, now=None):
    """Merge the market's settled receipt into the order's row. Returns the row, or None when no row matches."""
    order = deal.get("order")
    order_id = order if isinstance(order, str) else (order or {}).get("order")
    settled, receipt = deal.get("settled") or {}, deal.get("receipt") or {}
    row = find(rows, order_id)
    if row is None:   # no id (week-07 test shape): the latest unsettled deal of that SKU at that price
        sku, price = settled.get("sku") or receipt.get("item"), receipt.get("price")
        row = next((r for r in reversed(rows) if r.get("outcome") == "deal" and not r.get("settled")
                    and sku in (r.get("sku"), r.get("item")) and r.get("price") == price), None)
    if row is None:
        return None
    row.update({"settled": True, "settled_at": int(now or time.time()), "ledger": settled.get("ledger"),
                "name": settled.get("name") or row.get("name"), "grade": settled.get("grade"),
                "claim": settled.get("claim"), "claim_honest": settled.get("claim_honest"),
                "value": settled.get("value"), "warranty_hours": settled.get("warranty_hours") or 0,
                "shop_url": settled.get("shop")})
    if settled.get("claim_honest") is False:
        row["honest"] = False
    return row


def reconcile_payments(rows, lines):
    """A payment the kit refused turns the row from a deal into "unpaid"; the reason is kept."""
    n = 0
    for p in lines:
        if p.get("paid") is not False:
            continue
        row = find(rows, p.get("order"))
        if row is not None and row.get("outcome") == "deal":
            row["outcome"], row["unpaid_reason"] = "unpaid", p.get("reason")
            n += 1
    return n


def reconcile_broken(rows, lines):
    """broken.jsonl gives only a name and a time; the row is the latest settled purchase of that name within 6 h."""
    n = 0
    for b in lines:
        name, broke_at = b.get("name"), b.get("broke_at")
        if not name or not isinstance(broke_at, (int, float)):
            continue
        fits = [r for r in rows if r.get("settled") and r.get("name") == name and not r.get("broken")
                and 0 <= broke_at - (r.get("settled_at") or broke_at) <= BREAK_WINDOW]
        if not fits:
            continue
        row = max(fits, key=lambda r: r.get("settled_at") or 0)
        warranty = (row.get("warranty_hours") or 0) * 3600
        row.update({"broken": True, "broke_at": broke_at,
                    "refunded": bool(warranty) and broke_at - (row.get("settled_at") or broke_at) <= warranty})
        n += 1
    return n


def _new_lines(path, cursors, key):
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    start = min(int(cursors.get(key, 0)), len(lines))
    cursors[key] = len(lines)
    out = []
    for line in lines[start:]:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def reconcile(rows, state_dir=None):
    """Read what payments.jsonl and broken.jsonl added since last time and merge it. Returns the counts."""
    state_dir = Path(state_dir) if state_dir else MEMORY.parent
    cursor_path = state_dir / CURSOR.name
    try:
        cursors = json.loads(cursor_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cursors = {}
    result = {"unpaid": reconcile_payments(rows, _new_lines(state_dir / "payments.jsonl", cursors, "payments")),
              "broken": reconcile_broken(rows, _new_lines(state_dir / "broken.jsonl", cursors, "broken"))}
    try:
        cursor_path.write_text(json.dumps(cursors) + "\n", encoding="utf-8")
    except OSError:
        pass
    return result
