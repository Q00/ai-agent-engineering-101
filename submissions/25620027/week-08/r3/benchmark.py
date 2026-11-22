"""Leaders' table: how the top shops and buyers win, next to our own numbers.

Every thirty minutes the manager takes the Class's leading shops by Book and
buyers by Saved, looks them up in the public-ledger mirror it already
keeps, and computes what can be measured without talking to them: settlements,
price as a share of list, how many categories, how much food, dishonest grade
claims, and how much of their volume is trade between agents of the same owner.
Rows that rely on same-owner trade or dishonest claims are marked ``do_not_copy``
so the policy loops do not chase numbers that were not earned. Each table is
appended to ``r3-benchmark.jsonl`` (history) and written to ``r3-benchmark.json``
(latest); the strategy and advisor read the latest and the one a day earlier.
"""

from __future__ import annotations

import json
import os
import statistics
import time
from pathlib import Path
from typing import TYPE_CHECKING, Final

from r3.guard import sanitize_name

# Reuse the graded Class identity contract instead of maintaining a second copy.
from r3.standing import board_owner, board_rows, class_rows, finite_number

if TYPE_CHECKING:
    from collections.abc import Mapping

    from r3.ledger_db import LedgerDB
    from r3.market_files import CatalogItem

BENCH_ENV: Final = "AX_R3_BENCHMARK"
LATEST: Final = "r3-benchmark.json"
HISTORY: Final = "r3-benchmark.jsonl"
TOP_N: Final = 10
SELF_TRADE_LIMIT: Final = 0.3
DISHONEST_LIMIT: Final = 0.05
SELECTION_VERSION: Final = "class_book_saved_v1"


def benchmark_path(market_dir: Path | None = None) -> Path:
    """Resolve the latest-table file beside ``.market`` (or AX_R3_BENCHMARK)."""
    configured = os.environ.get(BENCH_ENV)
    if configured:
        return Path(configured)
    base = market_dir if market_dir is not None else Path(".market")
    return base.resolve().parent / LATEST


def _num(value: object) -> float:
    try:
        return float(str(value))
    except ValueError:
        return 0.0


def leaders(
    board: Mapping[str, object], key: str, metric: str, ours: str
) -> list[dict[str, object]]:
    """Rank active Class representatives, retaining ties and our owner's row."""
    students = [
        row
        for row in board_rows(board, "players")
        if row.get("student") is True and not row.get("house")
    ]
    owners = {board_owner(row) for row in students}
    if not owners or "" in owners or len(owners) != len(students):
        return []
    selected_shops = class_rows(board, students, "shops", "shop")
    selected_buyers = class_rows(board, students, "buyers", "buyer")
    own_owner = next(
        (
            board_owner(row)
            for row in (*selected_shops, *selected_buyers)
            if row.get("name") == ours
        ),
        "",
    )
    players = selected_shops if key == "shops" else selected_buyers
    identities = {(row.get("name"), board_owner(row)) for row in players}
    if len(identities) != len(players) or any(row.get("house") for row in players):
        return []
    if any(not finite_number(row.get("deals")) for row in players):
        return []
    if key == "shops" and any(
        _num(row.get("deals")) == 0 and not finite_number(row.get("stock_units"))
        for row in players
    ):
        return []
    players = [
        row
        for row in players
        if _num(row.get("deals")) > 0
        or (key == "shops" and _num(row.get("stock_units")) > 0)
    ]
    if any(not finite_number(row.get(metric)) for row in players) or (
        metric == "saved" and any(_num(row.get(metric)) > 1 for row in players)
    ):
        return []
    players.sort(key=lambda r: _num(r.get(metric)), reverse=True)
    picked = players[:0]
    rank = 0
    previous = None
    for index, row in enumerate(players, start=1):
        value = _num(row.get(metric))
        if value != previous:
            rank = index
        row["_rank"] = rank
        row["_ours"] = bool(own_owner and board_owner(row) == own_owner)
        if rank <= TOP_N or row["_ours"]:
            picked.append(row)
        previous = value
    return picked


# A buyer with this many settlements at one shop in the window counts as repeat.
REPEAT_DEALS: Final = 5


def build(  # noqa: PLR0913, C901, PLR0915 - one explicit pass
    board: Mapping[str, object],
    ledger: LedgerDB,
    catalog: Mapping[str, CatalogItem],
    shop_urls: Mapping[str, str],
    *,
    ours: str,
    hours: float = 24,
    now: float | None = None,
) -> dict[str, object]:
    """Compute the leaders' table from the board and the ledger mirror.

    ``shop_urls`` maps shop name to URL (ledger rows name shops by URL).
    """
    stamp = now if now is not None else time.time()
    settles = ledger.settles(stamp - hours * 3600)
    warranties = ledger.shop_warranties(stamp - hours * 3600)
    shops = leaders(board, "shops", "book", ours)
    buyers = leaders(board, "buyers", "saved", ours)
    owner_of_buyer = {
        str(r.get("name")): board_owner(r) for r in board_rows(board, "buyers")
    }
    url_to_owner: dict[str, str] = {}
    for row in board_rows(board, "shops"):
        url = shop_urls.get(str(row.get("name")), "").rstrip("/")
        if url:
            url_to_owner[url] = board_owner(row)
    by_shop: dict[str, list[tuple[str, str, int, str, str]]] = {}
    by_buyer: dict[str, list[tuple[str, str, int, str, str]]] = {}
    for _, buyer, shop, sku, amount, grade, claim in settles:
        entry = (str(shop).rstrip("/"), sku, amount, grade, claim)
        by_shop.setdefault(entry[0], []).append((buyer, sku, amount, grade, claim))
        by_buyer.setdefault(str(buyer), []).append(entry)

    def ratio(sku: str, amount: int) -> float | None:
        item = catalog.get(sku)
        if item is None or not item.list_price:
            return None
        return amount / item.list_price

    shop_rows: list[dict[str, object]] = []
    for row in shops:
        name = str(row.get("name"))
        url = shop_urls.get(name, "").rstrip("/")
        owner = board_owner(row)
        sold = by_shop.get(url, [])
        ratios = [r for b, s, a, _g, _c in sold if (r := ratio(s, a)) is not None]
        cats = {catalog[s].category for _b, s, _a, _g, _c in sold if s in catalog}
        food = sum(
            1
            for _b, s, _a, _g, _c in sold
            if s in catalog and catalog[s].kind == "food"
        )
        dishonest = sum(1 for *_x, g, c in sold if c and g and c != g)
        self_trade = sum(
            1 for b, *_x in sold if owner and owner_of_buyer.get(str(b)) == owner
        )
        n = len(sold)
        self_share = round(self_trade / n, 2) if n else 0.0
        dishonest_share = round(dishonest / n, 2) if n else 0.0
        lists = [catalog[s].list_price for _b, s, _a, _g, _c in sold if s in catalog]
        grades = {g: sum(1 for *_x, gg, _c in sold if gg == g) for g in "ABC"}
        buyers_n = {}
        for b, *_x in sold:
            buyers_n[b] = buyers_n.get(b, 0) + 1
        repeat = sum(c for c in buyers_n.values() if c >= REPEAT_DEALS)
        leaf = {}
        for _b, s, _a, _g, _c in sold:
            if s in catalog:
                key = catalog[s].category.rsplit(">", 1)[-1]
                leaf[key] = leaf.get(key, 0) + 1
        covered, goods_n = warranties.get(url, (0, 0))
        shop_rows.append(
            {
                "rank": row.get("_rank"),
                "name": sanitize_name(name),
                "ours": row["_ours"],
                "book": row.get("book"),
                "profit": row.get("profit"),
                "revenue": row.get("revenue"),
                "stars": row.get("stars"),
                "stars_rank": row.get("stars_rank"),
                "deals_total": row.get("deals"),
                f"settles_{int(hours)}h": n,
                "median_price_to_list": round(statistics.median(ratios), 3)
                if ratios
                else None,
                "categories": len(cats),
                "top_categories": sorted(leaf, key=lambda k: -leaf[k])[:4],
                "median_list_price": int(statistics.median(lists)) if lists else None,
                "grade_mix": {g: round(c / n, 2) for g, c in grades.items()}
                if n
                else None,
                "warranty_share": round(covered / goods_n, 2) if goods_n else None,
                "repeat_buyer_share": round(repeat / n, 2) if n else None,
                "food_share": round(food / n, 2) if n else None,
                "dishonest_share": dishonest_share,
                "self_trade_share": self_share,
                "do_not_copy": self_share >= SELF_TRADE_LIMIT
                or dishonest_share >= DISHONEST_LIMIT,
            }
        )
    buyer_rows: list[dict[str, object]] = []
    for row in buyers:
        name = str(row.get("name"))
        owner = board_owner(row)
        bought = by_buyer.get(name, [])
        ratios = [r for _u, s, a, _g, _c in bought if (r := ratio(s, a)) is not None]
        self_trade = sum(
            1 for u, *_x in bought if owner and url_to_owner.get(u) == owner
        )
        n = len(bought)
        self_share = round(self_trade / n, 2) if n else 0.0
        blists = [catalog[s].list_price for _u, s, _a, _g, _c in bought if s in catalog]
        buyer_rows.append(
            {
                "rank": row.get("_rank"),
                "name": sanitize_name(name),
                "ours": row["_ours"],
                "surplus": row.get("surplus"),
                "saved": row.get("saved"),
                "deals_total": row.get("deals"),
                f"settles_{int(hours)}h": n,
                "median_paid_to_list": round(statistics.median(ratios), 3)
                if ratios
                else None,
                "median_list_price": int(statistics.median(blists)) if blists else None,
                "shops_used": len({u for u, *_x in bought}),
                "self_trade_share": self_share,
                "do_not_copy": self_share >= SELF_TRADE_LIMIT,
            }
        )
    return {
        "ts": round(stamp),
        "round": board.get("round"),
        "window_hours": hours,
        "scope": "class",
        "selection_version": SELECTION_VERSION,
        "rank_metrics": {"shops": "book", "buyers": "saved"},
        "status": "available" if shops and buyers else "insufficient_class_data",
        "role_status": {
            "shops": "available" if shops else "insufficient_class_data",
            "buyers": "available" if buyers else "insufficient_class_data",
        },
        "shops": shop_rows,
        "buyers": buyer_rows,
    }


def save(table: Mapping[str, object], latest: Path) -> None:
    """Write the latest table and append it to the history beside it."""
    latest.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(table, ensure_ascii=False)
    tmp = latest.with_suffix(".tmp")
    _ = tmp.write_text(text, encoding="utf-8")
    _ = tmp.replace(latest)
    with (latest.parent / HISTORY).open("a", encoding="utf-8") as stream:
        _ = stream.write(text + "\n")


def load_latest(latest: Path) -> dict[str, object]:
    """Read the latest table, or an empty one."""
    try:
        data = json.loads(latest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def load_day_ago(latest: Path, now: float | None = None) -> dict[str, object]:
    """Return the history entry closest to 24 hours before ``now`` (or empty)."""
    stamp = now if now is not None else time.time()
    target = stamp - 86400
    best: dict[str, object] = {}
    gap = float("inf")
    try:
        lines = (latest.parent / HISTORY).read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    for line in lines[-200:]:
        try:
            rows = board_rows({"history": [json.loads(line)]}, "history")
        except ValueError:
            continue
        for row in rows:
            if (
                row.get("selection_version") == SELECTION_VERSION
                and row.get("scope") == "class"
                and row.get("rank_metrics") == {"shops": "book", "buyers": "saved"}
                and abs(_num(row.get("ts")) - target) < gap
            ):
                gap = abs(_num(row.get("ts")) - target)
                best = row
    return best if gap <= 6 * 3600 else {}


def compact(table: Mapping[str, object], top: int = 5) -> dict[str, object]:
    """Return a short view for prompts: top rows and ours."""
    out: dict[str, object] = {
        "ts": table.get("ts"),
        "round": table.get("round"),
        "scope": table.get("scope"),
        "selection_version": table.get("selection_version"),
        "rank_metrics": table.get("rank_metrics"),
        "status": table.get("status"),
        "role_status": table.get("role_status"),
    }
    if (
        table.get("selection_version") != SELECTION_VERSION
        or table.get("scope") != "class"
        or table.get("rank_metrics") != {"shops": "book", "buyers": "saved"}
    ):
        return {**out, "status": "incompatible_selection", "shops": [], "buyers": []}
    for key in ("shops", "buyers"):
        rows = table.get(key)
        rows = rows if isinstance(rows, list) else []
        out[key] = [
            r
            for r in rows
            if isinstance(r, dict)
            and (
                r.get("ours")
                or (isinstance(r.get("rank"), int) and int(str(r.get("rank"))) <= top)
            )
        ]
    return out
