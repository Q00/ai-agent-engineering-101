"""Shared Intelligence Hub (Bronze -> Silver/Gold Layer) for Round 3 Market Agent.

Coordinates state across the 4 independent processes invoked by market_kit.py v4:
- seller.py (A2A Shop Server)
- restock.py (60s Supply & Capital Allocation Engine)
- market_buyer.py (A2A Buyer & Surplus Optimizer)
- review.py (Post-settlement Audit, Broken Attribution & Forum Engine)

All persistent aggregated features live in `state_hub.json` (gitignored) with atomic
file replacement so concurrent processes never observe partial writes.
"""
from __future__ import annotations

import contextlib
import json
import math
import os
import statistics
import threading
import time
import urllib.request
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from adaptive_strategy import note_ledger_coverage, refresh_buyer_policy

MARKET_URL = os.environ.get("MARKET_URL", "https://market.ourolabs.ai").rstrip("/")
BASE_DIR = Path(__file__).resolve().parent
STATE_FILE = BASE_DIR / "state_hub.json"
_buyer_outcome_batches = ContextVar('buyer_outcome_batches', default=None)

# Core target non-food ("goods") categories for Stage 1 high-margin seed portfolio
SEED_GOODS_CATEGORIES = [
    "가구/침구>침구/홈데코>침구",
    "가구/침구>침구/홈데코>커튼/러그/쿠션/덮개",
    "가구/침구>가구>침대/매트리스",
    "가구/침구>가구>수납/장식 가구",
    "가구/침구>가구>테이블/책상/식탁",
    "통신기기>통신기기>통신기기액세서리",
    "음향/영상/카메라>음향/영상기기>음향/영상액세서리",
    "청소/생활잡화>청소/세탁/정리용품>정리용품",
    "조명/전기용품>조명>램프/전구/조명",
]

# Seed food categories are no longer a fixed list: restock.py picks the long-shelf ones from the catalog
# (long_shelf_food_categories).


# Goods break within GOODS_BREAK_WINDOW_H hours of the sale at these rates by grade: the instructor's figures in
# class (2026-11-03). The market's agent.md and the week-09 page do not publish them. Shared by seller.py
# (Safe-Care pricing) and market_buyer.py (whether to buy goods without a warranty).
BREAK_RATE = {"A": 0.03, "B": 0.15, "C": 0.35}
GOODS_BREAK_WINDOW_H = 6
BUYER_SHOP_METADATA_MAX_ROWS = 120  # global tag budget, not additional price observations
BUYER_SHOP_METADATA_SAMPLES = 3
BUYER_SHOP_METADATA_MAX_BYTES = 128

# Round 3 ends 2026-11-17 23:59 KST (week-08 page). Stock left then is worth 0 to net profit (agent.md: "stock
# that never sells is a loss"), so restock stops buying near the end and seller.py clears stock in the last day.
# MARKET_ROUND_END (epoch seconds) overrides it, for tests.
ROUND_END_TS = float(os.environ.get("MARKET_ROUND_END") or
                     datetime(2026, 11, 17, 23, 59, tzinfo=timezone(timedelta(hours=9))).timestamp())
# Our agents' market name (the kit's --name): settle entries name the buyer by it, e.g. "buyer": "11im"
OWN_NAME = os.environ.get("MARKET_AGENT_NAME", "mjforge")

CLEARANCE_WINDOW_S = 24 * 3600
STOP_BUYING_BEFORE_END_S = 36 * 3600


def clearance_factor(now: float | None = None) -> float:
    """1.0 until the last CLEARANCE_WINDOW_S of the round, then down linearly to 0.0 at the end."""
    left = ROUND_END_TS - (time.time() if now is None else now)
    return max(0.0, min(1.0, left / CLEARANCE_WINDOW_S))


def default_hub() -> dict[str, Any]:
    return {
        "updated_at": 0,
        "ledger_seq": 0,
        "broken_lines_read": 0,
        "payments_lines_read": 0,
        "demand_radar": {
            "inbound_wants": {},      # category_or_item -> count of buyer inquiries at our shop
            "missed_wants": {},       # category_or_item -> count of inquiries rejected due to out-of-stock
            "buyer_orders": {},       # category -> count of orders received by our buyer
            "market_settles": {},     # category -> count of settled deals on ledger
            "sku_settles": {},        # sku -> list of recent settled prices (max 30)
            "sku_comparables": {},    # sku -> structured settle observations (max 50)
            "recent_refs": [],        # recent ledger seqs for forum citations (max 25)
        },
        "capital_stage": {
            "stage": 1,               # Reporting only; no inventory cap.
            "balance": 5_000_000,
            "start": 5_000_000,
            "injected": 0,
            "stock_cost_value": 0,
            "estimated_profit": 0,
            "cash_floor": 0,
        },
        "shop_trust": {},             # shop_url -> {name, honest, dishonest, broken, settled, refuses, last_ts}
        "my_purchases": [],           # list of {sku, name, category, shop_url, shop_name, price, list_price, warranty_hours, ts, ledger, broke}
        "forum_state": {
            "last_buyer_post_ts": 0,
            "last_shop_post_ts": 0,
            "voted_posts": [],        # post IDs already voted on
        },
    }


@contextlib.contextmanager
def file_lock(path: Path = STATE_FILE, timeout: float = 8.0):
    """Cross-process advisory lock using msvcrt (Windows) or fcntl (POSIX)."""
    lock_file = path.with_suffix(".lock")
    start = time.time()
    fd = None
    while True:
        try:
            fd = os.open(str(lock_file), os.O_CREAT | os.O_RDWR)
            if os.name == "nt":
                import msvcrt
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except (BlockingIOError, OSError, PermissionError):
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
                fd = None
            if (time.time() - start) >= timeout:
                # Timeout safeguard: proceed anyway to avoid unrecoverable process freeze
                break
            time.sleep(0.04)

    try:
        yield
    finally:
        if fd is not None:
            try:
                if os.name == "nt":
                    import msvcrt
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
            try:
                os.close(fd)
            except OSError:
                pass


def migrate_no_rebuy(hub: dict[str, Any]) -> None:
    """Preserve round-long bans and observation warmup from older deployments."""
    legacy = hub.pop("dead_skus", {})
    if legacy:
        current = hub.setdefault("no_rebuy", {})
        for sku, ts in legacy.items():
            current.setdefault(sku, ts)
    if "dead_detection_since" in hub:
        hub.setdefault("no_rebuy_detection_since", hub.pop("dead_detection_since"))
    if "dead_skus_overflow" in hub:
        overflow = hub.pop("dead_skus_overflow")
        hub["no_rebuy_overflow"] = bool(hub.get("no_rebuy_overflow") or overflow)


def _compact_buyer_shop_metadata(comparables: dict, now: float) -> None:
    """Keep bounded prediction tags, never truncate/reprice core observations.

    Only shop is needed for ranking. Ledger cursor/seen_seqs already deduplicate
    ingestion, so per-row order/seq need not survive. Prefer complete recent
    condition groups, retaining three newest tags each; spare slots can seed
    sparse groups without claiming they meet the three-observation threshold.
    """
    groups = {}
    for sku, rows in comparables.items():
        for index, row in enumerate(rows):
            shop = row.pop('shop', None)
            row.pop('order', None)
            row.pop('seq', None)
            if (not isinstance(shop, str) or not shop or shop.strip() != shop
                    or not shop.isprintable()):
                continue
            try:
                if len(shop.encode('utf-8')) > BUYER_SHOP_METADATA_MAX_BYTES:
                    continue
            except UnicodeEncodeError:
                continue
            ts, amount = row.get('ts'), row.get('amount')
            grade, warranty = row.get('grade'), row.get('warranty_hours')
            if (not isinstance(grade, str) or grade not in BREAK_RATE
                    or not all(isinstance(x, (int, float)) and not isinstance(x, bool)
                               and math.isfinite(x) for x in (ts, amount, warranty))
                    or not 0 <= now-ts <= 10800 or not 0 < amount <= 10**12 or warranty < 0):
                continue
            condition = (sku, shop, grade, warranty >= GOODS_BREAK_WINDOW_H)
            groups.setdefault(condition, []).append((ts, index, row))
    selected = [(key, sorted(samples, key=lambda r: (r[0], r[1]), reverse=True)
                 [:BUYER_SHOP_METADATA_SAMPLES]) for key, samples in groups.items()]
    selected.sort(key=lambda group: (len(group[1]) < BUYER_SHOP_METADATA_SAMPLES,
                                     -group[1][0][0], *group[0]))
    remaining = BUYER_SHOP_METADATA_MAX_ROWS
    for (_, shop, _, _), samples in selected:
        if len(samples) <= remaining:
            for _, _, row in samples:
                row['shop'] = shop
            remaining -= len(samples)


def compact_hub(hub: dict[str, Any], now: float) -> None:
    """Lossless for current consumers: all 50 samples within their 3h window.

    A smaller sample cap changes medians. Do not truncate to 15 just to meet a
    byte target. Forum citations use their own bounded window, not comparables.
    Only a global bounded subset retains shop tags for buyer condition ranking.
    Core price/condition evidence and existing 50-observation/3h limits stay intact.
    """
    from seller_cash_policy import compact_cash_sales, compact_break_orders, number
    radar = hub.get('demand_radar', {})
    fields = ('ts', 'amount', 'grade', 'expires_at', 'warranty_hours', 'shop')
    radar['sku_comparables'] = {
        sku: kept for sku, rows in radar.get('sku_comparables', {}).items()
        if (kept := [{k:r[k] for k in fields if k in r and r[k] is not None}
                      for r in rows if number(r.get('ts')) and now-10800 <= r['ts'] <= now])}
    _compact_buyer_shop_metadata(radar['sku_comparables'], now)
    if 'seller_cash' in hub:
        compact_cash_sales(hub, now)
    if 'break_observer' in hub:
        compact_break_orders(hub, now)
    hub['hub_compaction'] = {'version':1, 'ts':now}


def load_hub(path: Path = STATE_FILE, *, _locked: bool = False) -> dict[str, Any]:
    """Load state_hub.json with exponential backoff retries against Windows sharing locks."""
    hub = default_hub()
    if not path.exists():
        return hub
    for attempt in range(5):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                for k, v in raw.items():
                    if isinstance(v, dict) and isinstance(hub.get(k), dict):
                        hub[k].update(v)
                    else:
                        hub[k] = v
            migrate_no_rebuy(hub)
            if hub.get('hub_compaction', {}).get('version') != 1:
                if not _locked:
                    # Re-read under lock: never overwrite a concurrent writer's
                    # newer state with the initial, unlocked migration read.
                    with file_lock(path):
                        return load_hub(path, _locked=True)
                compact_hub(hub, time.time())
                _save_hub_unlocked(hub, path)
            return hub
        except (OSError, ValueError):
            if attempt < 4:
                time.sleep(0.03)
    return hub


def _save_hub_unlocked(hub: dict[str, Any], path: Path) -> None:
    """Internal helper to write temporary JSON and replace file with retry backoff."""
    hub["updated_at"] = int(time.time())
    # Sweep at most once per minute, not for every inquiry/save/load.
    if time.time()-hub.get('hub_compaction', {}).get('ts', 0) >= 60:
        compact_hub(hub, time.time())
    tmp = path.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        tmp.write_text(json.dumps(hub, ensure_ascii=False, separators=(',',':')), encoding="utf-8")
        replaced = False
        for attempt in range(5):
            try:
                os.replace(tmp, path)
                replaced = True
                break
            except OSError:
                if attempt < 4:
                    time.sleep(0.04)
        if not replaced and tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
    except OSError:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def save_hub(hub: dict[str, Any], path: Path = STATE_FILE) -> None:
    """Atomically save state_hub.json under the cross-process lock."""
    with file_lock(path):
        _save_hub_unlocked(hub, path)


@contextlib.contextmanager
def mutate_hub(path: Path = STATE_FILE, timeout: float = 8.0):
    """Context manager to safely load, mutate, and save state_hub.json under an exclusive lock."""
    batches = _buyer_outcome_batches.get()
    outermost = batches is None
    if outermost:
        batches = []
        token = _buyer_outcome_batches.set(batches)
    try:
        with file_lock(path, timeout=timeout):
            hub = load_hub(path, _locked=True)
            yield hub
            _save_hub_unlocked(hub, path)
    finally:
        if outermost:
            _buyer_outcome_batches.reset(token)
            # Synchronous, short-lived producers: no daemon queue. Flush only
            # after ALL nested mutate_hub locks have been released, even on error.
            from buyer_outcomes import append_events, diagnostic
            for journal, events in batches:
                if events is None:
                    diagnostic('journal_prepare_failed_unknown')
                else:
                    append_events(events, journal)


def load_json_safe(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def load_catalog(market_dir: Path) -> dict[str, dict[str, Any]]:
    """Load SKU -> metadata dict from .market/catalog.json."""
    cat = load_json_safe(market_dir / "catalog.json", {})
    if isinstance(cat, list):
        return {x["sku"]: x for x in cat if isinstance(x, dict) and "sku" in x}
    return cat if isinstance(cat, dict) else {}


def load_inventory(market_dir: Path) -> dict[str, Any]:
    inv = load_json_safe(market_dir / "inventory.json", {})
    return inv if isinstance(inv, dict) else {}


def update_capital_stage(hub: dict[str, Any], inv: dict[str, Any]) -> dict[str, Any]:
    """Record capital metrics and the unified policy's cash-only spending limit."""
    if not inv:
        return hub["capital_stage"]

    balance = int(inv.get("balance", 5_000_000))
    start = int(inv.get("start", 5_000_000))
    injected = int(inv.get("injected", 0))
    units = inv.get("units") or []
    stock_cost = sum(int(u.get("cost", 0)) for u in units if u.get("grade") != "expired")

    # Net cash + inventory cost value compared to capital base
    total_equity = balance + stock_cost - injected
    est_profit = total_equity - start
    cash_profit = balance - start - injected

    # Legacy stages remain reporting metrics only, not purchase limits.
    if total_equity >= 6_000_000:
        stage = 3
    elif est_profit >= 250_000 or cash_profit >= 0 and stock_cost >= 1_800_000:
        stage = 2
    else:
        stage = 1

    # Keep the existing capped base floor, then reserve one day of storage.
    bankrupt_line = int(inv.get("bankrupt_line") or 500_000)
    cash_floor = min(1_500_000, max(bankrupt_line + 200_000, balance // 2))
    cash_floor += math.ceil(float(inv.get("storage_per_day", .01)) * stock_cost)
    available_to_spend = max(0, balance - cash_floor)

    cs = hub["capital_stage"]
    cs.update({
        "stage": stage,
        "balance": balance,
        "start": start,
        "injected": injected,
        "stock_cost_value": stock_cost,
        "estimated_profit": est_profit,
        "cash_floor": cash_floor,
        "available_budget": available_to_spend,
    })
    cs.pop("max_stock_cap", None)
    return cs


def fetch_ledger_rows(seq: int, timeout: float = 4.0) -> list[dict[str, Any]]:
    """Fetch ledger entries after seq from /api/ledger (the newest 400 when seq is 0). Network only: call it
    outside mutate_hub so the other processes are not kept waiting on the lock. Returns [] on any failure."""
    try:
        if seq == 0:
            req = urllib.request.Request(f"{MARKET_URL}/api/ledger?after=-1", headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                latest = json.loads(resp.read().decode("utf-8"))
            if isinstance(latest, list) and latest:
                top_seq = int(latest[-1].get("seq", 0))
                seq = max(0, top_seq - 400)
        req = urllib.request.Request(f"{MARKET_URL}/api/ledger?after={seq}", headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def fetch_public_buying_data(timeout: float = 2.0):
    """Public financials and FULL stock; call only outside mutate_hub."""
    try:
        results = []
        for endpoint in ("board", "shops"):
            req = urllib.request.Request(f"{MARKET_URL}/api/{endpoint}", headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=min(3.0, timeout)) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            rows = data.get("shops") if isinstance(data, dict) else data
            if not isinstance(rows, list):
                return None
            results.append(rows)
        return results[1], results[0]
    except Exception:
        return None


def fetch_own_shop_urls(timeout: float = 2.0) -> list[str]:
    """Public identity bootstrap only; callers must perform this outside the hub lock."""
    try:
        req = urllib.request.Request(f"{MARKET_URL}/api/shops", headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        shops = data.get("shops", []) if isinstance(data, dict) else data
        return [str(s["url"]).rstrip("/") for s in shops
                if s.get("name") == OWN_NAME and s.get("url")]
    except Exception:
        return []


def apply_ledger_rows(hub: dict[str, Any], rows: list[dict[str, Any]], catalog: dict[str, dict[str, Any]]) -> None:
    """Update demand_radar from rows fetched by fetch_ledger_rows. Call it inside mutate_hub.
    Counts only Round 3 'settle' entries: in Round 3 a deal counts only once it is settled, and the
    settle entry always names the shipped SKU and its 'amount'. 'result' entries are skipped because a
    verified receipt is not a settlement, its item may be the order's category, and counting it too
    would count each settled deal twice. Rounds 1/2 have no settle entries.
    Market aggregates skip rows at/below ledger_seq; own sales use separate seq keys
    so identity bootstrap can recover sales already consumed by another hook."""
    # Copy sanitized evidence only; NEVER perform journal I/O under the hub lock.
    # Standalone apply_ledger_rows is in-memory only. Production calls are under
    # mutate_hub, whose outermost exit flushes this context-local evidence.
    batches = _buyer_outcome_batches.get()
    if batches is not None:
        from buyer_outcomes import prepare
        try:
            evidence = prepare(rows, catalog, OWN_NAME, time.time())
        except Exception:
            evidence = None
        batches.append((BASE_DIR / 'runtime' / 'buyer-outcomes-v1.jsonl', evidence))
    note_ledger_coverage(hub, rows, time.time())
    radar = hub["demand_radar"]
    last_seq = int(hub.get("ledger_seq", 0))
    seq = last_seq
    seen_seqs = set()

    # Identity and supply accounting can be backfilled even when another hook advanced the cursor.
    own_urls = set(hub.get("own_shop_urls") or [])
    supplies = hub.setdefault("own_supplies", {})
    for e in rows:
        data = e.get("data") or {}
        if e.get("kind") == "join" and data.get("role") == "shop" and data.get("name") == OWN_NAME:
            url = data.get("url") or data.get("shop")
            if url:
                own_urls.add(str(url).rstrip("/"))
        if e.get("kind") == "supply" and data.get("shop") == OWN_NAME:
            total = data.get("total")
            if isinstance(total, (int, float)) and total > 0 and e.get("ts") is not None:
                supplies[str(e.get("seq"))] = {"ts": float(e["ts"]), "total": math.ceil(total),
                                               "qty": data.get("qty")}
    hub["own_shop_urls"] = sorted(own_urls)
    # Separate idempotent seller-only observations; never change the buyer's ranking state.
    from seller_cash_policy import observe
    observe(hub, rows, catalog, time.time(), own_urls, OWN_NAME)
    # A separate exact 3h forum window: comparables are capped at 50 per SKU
    # and exclude our buyer. Overflow makes the window unavailable, not biased.
    intel = hub.setdefault('forum_intel', {})
    now = time.time()
    events = {str(r['seq']): r for r in intel.get('settles', [])
              if now-10800 <= r['ts'] <= now}
    lookup = {s.lower(): s for s in catalog}
    for e in rows:
        d = e.get('data') or {}
        sku = lookup.get(str(d.get('sku') or d.get('item') or '').lower())
        if (e.get('kind') == 'settle' and isinstance(e.get('seq'), int)
                and isinstance(e.get('ts'), (int, float)) and now-10800 <= e['ts'] <= now
                and sku and isinstance(d.get('amount'), (int, float)) and d['amount'] > 0):
            events[str(e['seq'])] = {'seq': e['seq'], 'ts': e['ts'], 'sku': sku,
                                      'amount': d['amount']}
    ordered = sorted(events.values(), key=lambda r: (r['ts'], r['seq']))
    if len(ordered) > 20000:
        intel['overflow_until'] = ordered[-20001]['ts'] + 10800
    intel['settles'] = ordered[-20000:]
    # Small, topic-specific sale/break references; counters themselves stay in
    # the existing break observer. No order identities or seller policy changes.
    refs = intel.setdefault('quality_refs', {})
    for e in rows:
        d = e.get('data') or {}
        if e.get('kind') != 'settle' or not isinstance(e.get('seq'), int):
            continue
        sku = lookup.get(str(d.get('sku') or d.get('item') or '').lower())
        if sku and d.get('grade') in ('A', 'B', 'C'):
            key = sku + ':' + d['grade']
            refs[key] = list(dict.fromkeys(refs.get(key, []) + [e['seq']]))[-10:]
    intel['quality_refs'] = dict(list(refs.items())[-5000:])
    # Break ledger entries for the same SKU:grade, so a "k broke" claim cites the breaks
    # themselves (11/05: sale-only refs drew extra dislikes). Resolved via the break observer.
    brefs = intel.setdefault('quality_break_refs', {})
    observer = hub.get('break_observer', {})
    own_orders, market_orders = observer.get('orders', {}), observer.get('market_orders', {})
    for e in rows:
        d = e.get('data') or {}
        if e.get('kind') != 'broken' or not isinstance(e.get('seq'), int) or not d.get('order'):
            continue
        order = str(d['order'])
        if isinstance(own_orders.get(order), dict):
            key = f"{own_orders[order].get('sku')}:{own_orders[order].get('grade')}"
        elif isinstance(market_orders.get(order), list) and len(market_orders[order]) >= 2:
            key = f"{market_orders[order][0]}:{market_orders[order][1]}"
        else:
            continue
        brefs[key] = list(dict.fromkeys(brefs.get(key, []) + [e['seq']]))[-5:]
    intel['quality_break_refs'] = dict(list(brefs.items())[-5000:])

    # Fast case-insensitive catalog lookup for canonical SKU & category
    cat_lookup: dict[str, tuple[str, str]] = {}
    for c_sku, c_info in catalog.items():
        cat_lookup[c_sku.lower()] = (c_sku, c_info.get("category") or "general")

    # Own sales are independent of the shared market cursor. Keep 20 minutes plus
    # a 10-minute margin, bounded to 2,000 distinct seqs (not timestamps).
    now = time.time()
    own_sales = hub.setdefault("own_sale_events", {})
    for e in rows:
        data = e.get("data") or {}
        shop = str(data.get("shop") or data.get("seller") or "").rstrip("/")
        raw_sku = str(data.get("item") or data.get("sku") or "")
        amount = data.get("amount")
        if (e.get("kind") != "settle" or shop not in own_urls or data.get("buyer") == OWN_NAME
                or not raw_sku or not isinstance(amount, (int, float)) or amount <= 0
                or e.get("seq") is None or e.get("ts") is None):
            continue
        ts = float(e["ts"])
        if now - 1800 <= ts <= now:
            sku = cat_lookup.get(raw_sku.lower(), (raw_sku, "general"))[0]
            own_sales[str(int(e["seq"]))] = {"sku": sku, "ts": ts}
    own_sales = dict(sorted(((seq, sale) for seq, sale in own_sales.items()
                             if now - 1800 <= sale["ts"] <= now),
                            key=lambda pair: (pair[1]["ts"], int(pair[0])))[-2000:])
    hub["own_sale_events"] = own_sales
    # Last verified sale survives the short event tail, for supplied_at-based dead detection.
    last_sales = hub.setdefault("own_last_sale_ts", {})
    for e in rows:
        d = e.get("data") or {}
        raw = str(d.get("item") or d.get("sku") or "")
        if (e.get("kind") == "settle" and str(d.get("shop") or d.get("seller") or "").rstrip("/") in own_urls
                and d.get("buyer") != OWN_NAME and isinstance(d.get("amount"), (int, float))
                and d["amount"] > 0 and isinstance(e.get("ts"), (int, float)) and e["ts"] <= now):
            sku = cat_lookup.get(raw.lower(), (raw, "general"))[0]
            if sku in catalog:
                last_sales[sku] = max(last_sales.get(sku, 0), e["ts"])
    for sale in own_sales.values():
        last_sales[sale["sku"]] = max(last_sales.get(sale["sku"], 0), sale["ts"])
    # Actual unit costs, not settlement prices, are the preferred procurement estimate.
    costs = hub.setdefault("purchase_costs", {})
    for e in rows:
        d = e.get("data") or {}
        sku = cat_lookup.get(str(d.get("sku") or d.get("item") or "").lower(), ("", ""))[0]
        qty, total = d.get("qty"), d.get("total")
        if (e.get("kind") == "supply" and d.get("shop") == OWN_NAME and sku
                and isinstance(qty, (int, float)) and qty > 0
                and isinstance(total, (int, float)) and total > 0):
            if e.get("ts", 0) >= costs.get(sku, {}).get("ts", 0):
                costs[sku] = {"cost": math.ceil(total / qty), "ts": e.get("ts", 0)}
    own_sold_ts: dict[str, list[float]] = {}
    for sale in own_sales.values():
        own_sold_ts.setdefault(sale["sku"], []).append(sale["ts"])
    radar["own_sku_sold_ts"] = own_sold_ts

    for e in rows:
        s_id = int(e.get("seq", 0))
        if s_id <= last_seq or s_id in seen_seqs:
            continue
        seen_seqs.add(s_id)
        if s_id > seq:
            seq = s_id

        kind = e.get("kind")
        data = e.get("data") or {}

        # Round 3 settle schema: {"kind": "settle", "data": {"order": "...", "amount": 8551, "item": "G1f5d00c7", ...}}
        if kind != "settle":
            continue
        # Our own buyer's purchases are not demand our shop can serve (same owner cannot settle with itself)
        if data.get("buyer") == OWN_NAME:
            continue
        raw_sku = str(data.get("item") or data.get("sku") or "")
        amount = data.get("amount")
        if not raw_sku or not isinstance(amount, (int, float)) or amount <= 0:
            continue

        raw_lower = raw_sku.lower()
        if raw_lower in cat_lookup:
            canonical_sku, cat = cat_lookup[raw_lower]
        else:
            canonical_sku = raw_sku
            cat = catalog.get(raw_sku, {}).get("category") or "general"

        # Update category demand volume
        radar["market_settles"][cat] = radar["market_settles"].get(cat, 0) + 1

        # When it sold, per SKU and per category, so restock can weigh recent sales (other shops' included)
        ts = float(e.get("ts") or time.time())
        for key, bucket, keep in ((canonical_sku, "sku_sold_ts", 40), (cat, "cat_sold_ts", 200)):
            arr_ts = radar.setdefault(bucket, {}).setdefault(key, [])
            arr_ts.append(ts)
            if len(arr_ts) > keep:
                radar[bucket][key] = arr_ts[-keep:]

        # Update SKU price history
        arr = radar["sku_settles"].setdefault(canonical_sku, [])
        arr.append(int(amount))
        if len(arr) > 30:
            radar["sku_settles"][canonical_sku] = arr[-30:]

        # Preserve public settle metadata, not seller claims or inferred grades/expiry.
        # Missing fields stay unknown: old state needs no migration and cannot become
        # a misleading price comparable merely because it has a numeric price history.
        observations = radar.setdefault("sku_comparables", {}).setdefault(canonical_sku, [])
        observations.append({
            "ts": e.get("ts"), "amount": amount, "grade": data.get("grade"),
            "expires_at": data.get("expires_at"), "shelf_hours": data.get("shelf_hours"),
            "shop": data.get("shop") or data.get("seller"),
            "order": data.get("order"), "seq": s_id,
            "warranty_hours": data.get("warranty_hours"),
        })
        radar["sku_comparables"][canonical_sku] = observations[-50:]

        refs = radar.setdefault("recent_refs", [])
        if s_id not in refs:
            refs.append(s_id)
            if len(refs) > 25:
                radar["recent_refs"] = refs[-25:]

    hub["ledger_seq"] = seq
    # Bound prediction-only tags on every ingestion return, not just the minute
    # sweep. Do not alter core prices, demand counters, or the original journal.
    _compact_buyer_shop_metadata(radar.get('sku_comparables', {}), time.time())


def sync_broken_and_payments(hub: dict[str, Any], market_dir: Path) -> list[dict[str, Any]]:
    """Match .market/broken.jsonl against my_purchases to attribute broken items to the shop that sold them."""
    newly_attributed: list[dict[str, Any]] = []
    broken_file = market_dir / "broken.jsonl"
    if broken_file.exists():
        try:
            lines = broken_file.read_text(encoding="utf-8").splitlines()
            start_idx = int(hub.get("broken_lines_read", 0))
            for line in lines[start_idx:]:
                if not line.strip():
                    continue
                ev = json.loads(line)
                item_name = ev.get("name")
                broke_at = int(ev.get("broke_at") or time.time())
                # Match against most recent un-broken purchase of the same item name within 6.5 hours
                for p in reversed(hub.get("my_purchases", [])):
                    if not p.get("broke") and p.get("name") == item_name and (broke_at - int(p.get("ts", 0))) <= 25200:
                        p["broke"] = True
                        p["broke_at"] = broke_at
                        shop_url = p.get("shop_url", "")
                        if shop_url:
                            st = hub["shop_trust"].setdefault(shop_url, {
                                "name": p.get("shop_name", ""),
                                "honest": 0, "dishonest": 0, "broken": 0, "settled": 0, "refuses": 0, "last_ts": 0
                            })
                            st["broken"] = st.get("broken", 0) + 1
                            st["last_ts"] = broke_at
                        newly_attributed.append(p)
                        break
            hub["broken_lines_read"] = len(lines)
        except (OSError, ValueError):
            pass

    payments_file = market_dir / "payments.jsonl"
    if payments_file.exists():
        try:
            lines = payments_file.read_text(encoding="utf-8").splitlines()
            start_idx = int(hub.get("payments_lines_read", 0))
            for line in lines[start_idx:]:
                if not line.strip():
                    continue
                pm = json.loads(line)
                shop_url = str(pm.get("shop") or "").rstrip("/")
                if not shop_url:
                    continue
                st = hub["shop_trust"].setdefault(shop_url, {
                    "name": "", "honest": 0, "dishonest": 0, "broken": 0, "settled": 0, "refuses": 0, "last_ts": 0
                })
                if pm.get("paid"):
                    st["settled"] = st.get("settled", 0) + 1
                else:
                    st["refuses"] = st.get("refuses", 0) + 1
                st["last_ts"] = int(time.time())
            hub["payments_lines_read"] = len(lines)
        except (OSError, ValueError):
            pass

    return newly_attributed


def record_inbound_inquiry(category_or_item: str, matched: bool, path: Path = STATE_FILE, raw_text: str = "", *, sku: str = "", observed_at: float | None = None) -> None:
    """Record a buyer inquiry arriving at seller.py (including missed demand when out of stock)."""
    if not category_or_item and not sku:
        return
    category_or_item = category_or_item or sku
    now = time.time() if observed_at is None else observed_at
    with mutate_hub(path) as hub:
        radar = hub.setdefault("demand_radar", default_hub()["demand_radar"])
        import re
        keys = {sku} if sku else set(re.findall(r"\b([gfGF][0-9a-fA-F]{7,10})\b", raw_text))
        if not sku and re.fullmatch(r"[gfGF][0-9a-fA-F]{7,10}", category_or_item):
            keys.add(category_or_item)
        inquiries = radar.setdefault("sku_inquiries", {})
        for key in keys:
            key = key.lower()
            events = [e for e in inquiries.get(key, []) if 0 <= now - e["ts"] <= 7200]
            events.append({"ts": now, "miss": not matched})
            inquiries[key] = events[-100:]
        # At most 500 SKU buckets, 5,000 events globally; evict oldest observations.
        flattened = sorted((e["ts"], k, i, e) for k, es in inquiries.items()
                           for i, e in enumerate(es) if 0 <= now - e["ts"] <= 7200)[-5000:]
        recent_keys = set(k for _, k, _, _ in sorted(flattened, reverse=True)[:5000])
        recent_keys = set(sorted(recent_keys, key=lambda k: max(e["ts"] for e in inquiries[k]), reverse=True)[:500])
        radar["sku_inquiries"] = {}
        for _, k, _, e in flattened:
            if k in recent_keys:
                radar["sku_inquiries"].setdefault(k, []).append(e)
        radar["inbound_wants"][category_or_item] = radar["inbound_wants"].get(category_or_item, 0) + 1
        if not matched:
            radar["missed_wants"][category_or_item] = radar["missed_wants"].get(category_or_item, 0) + 1
            missed_ts = radar.setdefault("missed_ts", {})
            arr = [t for t in missed_ts.get(category_or_item, []) if 0 <= now - float(t) <= 7200]
            arr.append(now)
            missed_ts[category_or_item] = arr[-50:]
            if raw_text:
                import re
                for m in set(re.findall(r"\b([gfGF][0-9a-fA-F]{7,10})\b", raw_text.lower())):
                    k = m.lower()
                    if k == category_or_item.lower():
                        continue
                    s_arr = [t for t in missed_ts.get(k, []) if 0 <= now - float(t) <= 7200]
                    s_arr.append(now)
                    missed_ts[k] = s_arr[-50:]


def record_buyer_order(category: str, path: Path = STATE_FILE) -> dict[str, Any] | None:
    """Record an order category assigned to our market_buyer.py."""
    if not category:
        return
    with mutate_hub(path) as hub:
        refresh_buyer_policy(hub, time.time())
        radar = hub.setdefault("demand_radar", default_hub()["demand_radar"])
        radar["buyer_orders"][category] = radar["buyer_orders"].get(category, 0) + 1
        return hub.get("adaptive_strategy", {})


def get_sku_market_anchor(hub: dict[str, Any], sku: str, list_price: int) -> int:
    """Return 0.6*P65 + 0.4*Mean of settled prices for a SKU, or 95% of list_price if no data."""
    arr = (hub.get("demand_radar", {}).get("sku_settles", {}) or {}).get(sku) or []
    if len(arr) >= 2:
        s_arr = sorted(arr)
        p65 = s_arr[min(len(s_arr) - 1, int(len(s_arr) * 0.65))]
        mean_v = statistics.mean(s_arr)
        return int(round(0.6 * p65 + 0.4 * mean_v))
    return int(round(list_price * 0.95))


def queue_forum_action(market_dir: Path, payload: dict[str, Any]) -> None:
    """Append a post or vote JSON line to .market/outbox.jsonl."""
    market_dir.mkdir(exist_ok=True)
    outbox_path = market_dir / "outbox.jsonl"
    try:
        with open(outbox_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
        return True
    except OSError:
        return False

