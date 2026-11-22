"""Rebuy goods whose B and C units made money today (owner 11/17 17:06).

Today (07:10 KST on) goods margin was A 28.6M, B 15.3M, C 23.0M, while buying of
1M+ list goods fell from 57M an hour to 16M: market-first judged holds and the
"no sale in an hour" stop by total units held, so unsold A units blocked the
rebuy of SKUs whose B/C sold out. SKUs whose B+C (margin - refunds) today is
BC_MIN_NET or more, holding BC_MAX_HELD or fewer of today's B+C units, get
BC_QTY units a window regardless of A units held: BC_PER_WINDOW SKUs a window,
each at most once an hour, BC_SKU_CAP of today's units per SKU, BC_SPEND_CAP in
all, none after BC_STOP_AT, skipped while today's supply cost is over
BC_COST_JUMP x today's own average buy cost. Zinus goods are left out.
"""

from __future__ import annotations

import contextlib
import json
import sqlite3
import statistics
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from r3.market_files import CatalogItem, Inventory

BC_STATE_FILE: Final = "r3-bc-rebuy.json"
BC_DAY_START: Final = 1794867000.0  # 2026-11-17 07:10 KST
BC_STOP_AT: Final = 1794925800.0  # 23:30 KST (owner 21:20; 21:00 until then)
# 18:2x (owner): the 250M cap counts the supply rows' real totals (the estimate was
# 20 % low), and the path stops while cash is under BC_CASH_FLOOR.
BC_CASH_FLOOR: Final = 150_000_000  # 200M 18:2x-19:2x
# 19:15 judged: no B/C unit of these sold in the 2 h after their first rebuy.
BC_EXCLUDED: Final = frozenset({"G2698c6f8"})
BC_MATCH_SECONDS: Final = 1800.0
BC_MIN_NET: Final = 500_000
BC_MAX_HELD: Final = 2
BC_QTY: Final = 10
BC_PER_WINDOW: Final = 4
BC_WINDOW_SECONDS: Final = 15 * 60.0
BC_SKU_GAP_SECONDS: Final = 3600.0
BC_SKU_CAP: Final = 80
BC_SPEND_CAP: Final = 420_000_000  # 320M until 21:2x (owner), real spend
BC_COST_JUMP: Final = 1.10
BC_ON: Final = True
ZINUS_MARK: Final = "지누스"
OUR_SUPPLY_NAME: Final = "25620027"  # our shop in the ledger's supply rows


def _load_state(path: Path) -> dict[str, object]:
    try:
        raw = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return {"spent": 0, "bought": {}}
    return raw if isinstance(raw, dict) else {"spent": 0, "bought": {}}


def bc_rebuy_lines(  # noqa: C901, PLR0912, PLR0913, PLR0915, PLR0917 - one pass
    data_dir: Path,
    shop: str,
    now: float,
    catalog: Mapping[str, CatalogItem],
    inventory: Inventory,
    goods_cost_room: float,
    cash_room: float,
) -> list[dict[str, object]]:
    """Return this window's B/C rebuy lines and record them in BC_STATE_FILE."""
    if not BC_ON or not shop or now >= BC_STOP_AT:
        return []
    state_path = data_dir / BC_STATE_FILE
    state = _load_state(state_path)
    spent = float(state.get("spent") or 0)
    bought = state.get("bought")
    bought_at: dict[str, list[float]] = (
        {
            str(k): [float(t) for t in v]
            for k, v in bought.items()
            if isinstance(v, list)
        }
        if isinstance(bought, dict)
        else {}
    )
    recent_skus = {
        sku
        for sku, stamps in bought_at.items()
        if any(now - t < BC_WINDOW_SECONDS for t in stamps)
    }
    slots = BC_PER_WINDOW - len(recent_skus)
    if slots <= 0 or spent >= BC_SPEND_CAP:
        return []
    cost: dict[str, float] = {}
    net: dict[str, float] = {}
    sold_units: dict[str, str] = {}
    own_lots: dict[str, list[tuple[float, float]]] = {}
    own_rows: list[tuple[float, str, float]] = []
    market_lots: dict[str, list[float]] = {}
    try:
        ev = sqlite3.connect(f"file:{data_dir / 'r3-events.sqlite'}?mode=ro", uri=True)
        try:
            for unit, unit_cost in ev.execute(
                "select unit, cost from seller_receipts where ts>=? and cost>0",
                (BC_DAY_START - 6 * 3600,),
            ):
                cost[str(unit)] = float(unit_cost)
        finally:
            ev.close()
        con = sqlite3.connect(f"file:{data_dir / 'r3-ledger.sqlite'}?mode=ro", uri=True)
        try:
            for sku, data in con.execute(
                "select sku, data from rows where kind='settle' and ts>=?"
                " and rtrim(shop,'/')=? and grade in ('B','C')",
                (BC_DAY_START, shop.rstrip("/")),
            ):
                item = catalog.get(str(sku))
                if item is None or item.kind != "goods":
                    continue
                row = json.loads(data)
                unit = str(row.get("unit") or "")
                if unit not in cost:
                    continue
                amount = float(row.get("amount") or 0)
                net[str(sku)] = net.get(str(sku), 0.0) + amount - cost[unit]
                sold_units[unit] = str(sku)
            for (data,) in con.execute(
                "select data from rows where kind='broken' and ts>=?"
                " and rtrim(shop,'/')=?",
                (BC_DAY_START, shop.rstrip("/")),
            ):
                row = json.loads(data)
                sku = sold_units.get(str(row.get("unit") or ""))
                if sku is not None:
                    net[sku] = net.get(sku, 0.0) - float(row.get("refunded") or 0)
            for ts, shop_name, data in con.execute(
                "select ts, shop, data from rows where kind='supply' and ts>=?",
                (min(BC_DAY_START, now - 6 * 3600),),
            ):
                row = json.loads(data)
                sku, qty, total = str(row.get("sku")), row.get("qty"), row.get("total")
                if not qty or not total:
                    continue
                if str(shop_name) == OUR_SUPPLY_NAME and float(ts) >= BC_DAY_START:
                    own_lots.setdefault(sku, []).append((float(qty), float(total)))
                    own_rows.append((float(ts), sku, float(total)))
                if float(ts) >= now - 6 * 3600:
                    market_lots.setdefault(sku, []).append(float(total) / float(qty))
        finally:
            con.close()
    except (sqlite3.Error, OSError, ValueError):
        return []
    # Real spend: the first own supply row of the SKU within BC_MATCH_SECONDS of
    # each rebuy run.
    real = 0.0
    used: set[int] = set()
    for sku, stamps in bought_at.items():
        for t in stamps:
            for i, (ts, row_sku, total) in enumerate(own_rows):
                if i not in used and row_sku == sku and t <= ts <= t + BC_MATCH_SECONDS:
                    used.add(i)
                    real += total
                    break
    spent = max(spent, real)
    if spent >= BC_SPEND_CAP:
        return []
    cash_room = min(cash_room, inventory.balance - BC_CASH_FLOOR)
    if cash_room <= 0:
        return []
    surge = {entry.sku: entry.factor for entry in inventory.surge}
    today_bc: dict[str, int] = {}
    for u in inventory.units:
        if u.supplied_at >= BC_DAY_START and u.grade in {"B", "C"}:
            today_bc[u.sku] = today_bc.get(u.sku, 0) + 1
    rows: list[tuple[float, str, float, float]] = []
    for sku, value in net.items():
        item = catalog.get(sku)
        if (
            item is None
            or value < BC_MIN_NET
            or ZINUS_MARK in (item.name or "")
            or sku in BC_EXCLUDED
            or today_bc.get(sku, 0) > BC_MAX_HELD
            or sku in recent_skus
            or any(now - t < BC_SKU_GAP_SECONDS for t in bought_at.get(sku, []))
        ):
            continue
        lots = own_lots.get(sku, [])
        qty_sum = sum(q for q, _ in lots)
        spend_today = sum(t for _, t in lots)
        if qty_sum <= 0 or sku not in market_lots:
            continue
        own_avg = spend_today / qty_sum
        now_cost = statistics.median(market_lots[sku]) * surge.get(sku, 1.0)
        if now_cost > BC_COST_JUMP * own_avg:
            continue
        rows.append((value / spend_today, sku, now_cost, qty_sum))
    rows.sort(reverse=True)
    out: list[dict[str, object]] = []
    for _, sku, unit_cost, bought_today in rows:
        if len(out) >= slots:
            break
        room = min(goods_cost_room, cash_room, BC_SPEND_CAP - spent)
        qty = min(BC_QTY, BC_SKU_CAP - int(bought_today), int(room // unit_cost))
        if qty <= 0:
            continue
        out.append({"sku": sku, "qty": qty})
        spent += qty * unit_cost
        goods_cost_room -= qty * unit_cost
        cash_room -= qty * unit_cost
        bought_at.setdefault(sku, []).append(now)
    if out:
        with contextlib.suppress(OSError):
            _ = state_path.write_text(
                json.dumps({"spent": spent, "bought": bought_at}), "utf-8"
            )
    return out


def apply_bc_rebuy(
    lines: list[dict[str, object]],
    rebuy: list[dict[str, object]],
    catalog: Mapping[str, CatalogItem],
    max_lines: int,
    food_kept: int,
) -> list[dict[str, object]]:
    """Put rebuy lines first among goods; keep up to ``food_kept`` food lines."""
    if not rebuy:
        return lines
    taken = {str(r["sku"]) for r in rebuy}

    def is_food(row: dict[str, object]) -> bool:
        item = catalog.get(str(row["sku"]))
        return item is not None and item.kind == "food"

    food_rows = [r for r in lines if is_food(r)][:food_kept]
    others = [r for r in lines if not is_food(r) and str(r["sku"]) not in taken]
    return [*rebuy, *others][: max_lines - len(food_rows)] + food_rows
