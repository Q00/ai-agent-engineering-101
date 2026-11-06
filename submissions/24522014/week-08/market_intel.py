"""Bounded public evidence collector. No model calls, private endpoints or trading."""

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import ssl
import statistics
import threading
import time
from uuid import uuid4

import httpx
from market_state import read, write
from forum_monitor import observe as observe_forum
from purchase_monitor import observe as observe_purchases, settlement_price

ROOT = Path(__file__).resolve().parent
BASE = "https://market.ourolabs.ai"


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def summarize(rows, catalog, now):
    seen, groups = set(), defaultdict(list)
    for row in rows:
        data = row.get("data") or {}
        sku, price = data.get("item"), settlement_price(data)
        if (row.get("kind") != "settle" or row.get("round") != 3 or sku not in catalog
                or not finite(row.get("ts")) or not 0 <= now - row["ts"] <= 86400
                or not finite(price) or price <= 0 or row.get("seq") in seen):
            continue
        seen.add(row["seq"])
        groups[sku].append(row)
    result = {}
    for sku, entries in groups.items():
        entries.sort(key=lambda row: row["ts"])
        buyers = {row["data"].get("buyer") for row in entries if row["data"].get("buyer")}
        gaps = [max(60, b["ts"] - a["ts"]) for a, b in zip(entries, entries[1:])]
        result[sku] = {"settles_24h": len(entries), "settles_6h": sum(now-r["ts"] <= 21600 for r in entries),
                       "distinct_buyers": len(buyers), "last_sale_at": entries[-1]["ts"],
                       "distinct_buyers_6h": len({r["data"].get("buyer") for r in entries
                                                  if now-r["ts"] <= 21600 and r["data"].get("buyer")}),
                       "median_price": statistics.median(settlement_price(r["data"]) for r in entries),
                       "observed_gap_hours": statistics.median(gaps)/3600 if gaps else None,
                       "ledger_refs": [r["seq"] for r in entries[-3:]]}
    return result


def board_weights(board):
    """ALL's weights for this board. Since patch 3.4 `saved` is a share of list price, where it used to be an amount, and buyers score on it; a board with `book` scores shops on it."""
    shops = {"book": .5, "revenue": .3, "stars_rank": .2} if any(r.get("book") is not None for r in board.get("shops", [])) else {"profit": .5, "revenue": .3, "stars": .2}
    saved = [r["saved"] for r in board.get("buyers", []) if finite(r.get("saved"))]
    buyers = {"saved": .5, "forum": .3, "deals": .1, "posts": .1} if saved and all(abs(v) <= 2 for v in saved) else {"surplus": .5, "forum": .3, "deals": .1, "posts": .1}
    return {"shops": shops, "buyers": buyers}


def competition(board, login="kcwarni"):
    """Use explicit official player scores/ranks only; never guess R3 field meanings."""
    players = board.get("players") or []
    def owner(row):
        value = row.get("owner")
        return value.get("login", "") if isinstance(value, dict) else value or row.get("login", "")
    me = next((r for r in players if str(owner(r)).casefold() == login.casefold()), None)
    mine = {group: next((r for r in board.get(group, [])
                        if str(owner(r)).casefold() == login.casefold() or r.get("name") == login), None)
            for group in ("shops", "buyers")}
    score_key = next((key for key in ("all", "score", "total")
                      if me and finite(me.get(key)) and all(finite(r.get(key)) for r in players)), None)
    ranked = sorted(players, key=lambda r: -r[score_key]) if score_key else []
    rank = 1 + sum(r[score_key] > me[score_key] for r in players) if score_key else (me or {}).get("rank")
    components = {}
    weights = board_weights(board)
    if board.get("round") == 3:
        for group, fields in weights.items():
            mine_row = mine[group] or {}
            eligible = [r for r in board.get(group, []) if r.get("deals", 0) > 0]
            for field, weight in fields.items():
                values = [r[field] for r in eligible if finite(r.get(field))]
                value = mine_row.get(field)
                components[f"{group}.{field}"] = {"weight": weight, "value": value,
                    "observed_participants": len(values),
                    "ahead_of_percent": 100*sum(v < value for v in values)/len(values)
                        if finite(value) and values else None,
                    "tied": sum(v == value for v in values) if finite(value) else None}
    alerts = []
    if board.get("round") == 3:
        for group, row in mine.items():
            if (row or {}).get("deals", 0) < 20:
                alerts.append(f"{group}: fewer than 20 settled deals")
        if not score_key:
            alerts.append("Official player score unavailable; Top 3 gap is unknown.")
        if any(r["ahead_of_percent"] is None for r in components.values()):
            alerts.append("Some component fields unavailable; no synthetic ALL score calculated.")
    return {"round": board.get("round"), "official_player": me, "rank": rank,
            "player_count": len(players), "score_field": score_key,
            "gap_to_third": max(0, ranked[2][score_key] - me[score_key]) if len(ranked) >= 3 else None,
            "own": mine, "top3": ranked[:3], "complete_score_available": bool(score_key),
            "diagnostic_components": components, "alerts": alerts}


def collect(root=ROOT, client=None, now=None):
    now = time.time() if now is None else now
    md = root / ".market"
    previous = read(md / "market-intel.json", {})
    catalog = read(md / "catalog.json", {})
    own_client = client is None
    client = client or httpx.Client(verify=ssl.create_default_context(), timeout=15)
    try:
        def get(path, **params):
            r = client.get(BASE + path, params=params)
            r.raise_for_status()
            return r.json()
        registry = get("/api/shops")
        round_number = registry["round"]
        board = get("/api/board", round=round_number)
        rows = previous.get("rows", []) if previous.get("round") == round_number else []
        cursor = previous.get("cursor", -500) if previous.get("round") == round_number else -500
        pages, behind = 0, False
        gaps = previous.get("skipped_seq_ranges", []) if previous.get("round") == round_number else []
        if round_number == 3:
            for _ in range(3):
                batch = get("/api/ledger", after=cursor)
                if not isinstance(batch, list):
                    raise ValueError("ledger is not a list")
                pages += 1
                if batch:
                    seqs = [r["seq"] for r in batch]
                    if cursor >= 0 and max(seqs) <= cursor:
                        raise ValueError("ledger cursor did not advance")
                    cursor = max(seqs)
                    rows.extend(r for r in batch if r.get("kind") in ("settle", "broken", "refund"))
                behind = len(batch) >= 500
                if not behind:
                    break
            if behind:
                # Prefer a fresh bounded sample to an ever-growing stale backlog.
                tail = get("/api/ledger", after=-500)
                pages += 1
                if tail:
                    first, last = min(r["seq"] for r in tail), max(r["seq"] for r in tail)
                    if first > cursor + 1:
                        gaps = (gaps + [[cursor+1, first-1]])[-20:]
                    rows.extend(r for r in tail if r.get("kind") in ("settle", "broken", "refund"))
                    cursor = max(cursor, last)
        by_seq = {r["seq"]: r for r in rows if finite(r.get("ts")) and now-r["ts"] <= 86400}
        rows = sorted(by_seq.values(), key=lambda r: r["seq"])[-10000:]
        result = {"round": round_number, "observed_at": now, "cursor": cursor, "rows": rows,
                  "pages": pages, "catching_up": behind, "bounded_sample": True,
                  "skipped_seq_ranges": gaps,
                  "skus": summarize(rows, catalog, now), "competition": competition(board)}
        result["forum"] = observe_forum(md, now=now, ledger_rows=rows)
        result["purchases"] = observe_purchases(md, now=now, snapshot=result)
        inventory = read(md / "inventory.json", {})
        if inventory.get("round") == 3:
            result["cash_risk"] = {"balance": inventory.get("balance"),
                                   "bankrupt_line": inventory.get("bankrupt_line", 500000),
                                   "bankruptcies_left": inventory.get("bankruptcies_left"),
                                   "bankruptcy_requested": False}
            if inventory.get("balance", 0) < 1000000:
                result["competition"]["alerts"].append("Cash below emergency reserve; check storage/refunds. New buying is guarded separately.")
        write(md / "market-intel.json", result)
        write(root / "logs/market-intel" / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid4().hex[:8] + ".json"), result)
        return result
    finally:
        if own_client:
            client.close()


def fresh(md, now=None):
    now = time.time() if now is None else now
    try:
        snapshot = read(Path(md) / "market-intel.json", {})
        if snapshot.get("round") != 3 or not 0 <= now - snapshot.get("observed_at", 0) <= 3600:
            return {}
        # Recompute from immutable raw observations even if an older in-process collector wrote the cache.
        snapshot["skus"] = summarize(snapshot.get("rows", []), read(Path(md)/"catalog.json", {}), now)
        snapshot["forum"] = read(Path(md)/"forum-intel.json", {})
        snapshot["purchases"] = read(Path(md)/"purchase-intel.json", {})
        return snapshot
    except (OSError, ValueError, TypeError):
        return {}


def start_worker(root):
    stop = threading.Event()
    def loop():
        while not stop.is_set():
            try:
                result = collect(root)
                print(f"[intel] round {result['round']}, observed SKU groups {len(result['skus'])}; no model calls", flush=True)
            except Exception as error:
                print(f"[intel] observation failed: {type(error).__name__}: {error}", flush=True)
            stop.wait(1800)
    thread = threading.Thread(target=loop, name="market-evidence", daemon=True)
    thread.start()
    return stop


if __name__ == "__main__":
    result = collect()
    print(json.dumps({"round": result["round"], "pages": result["pages"],
                      "sku_groups": len(result["skus"]), "rank": result["competition"]["rank"],
                      "catching_up": result["catching_up"]}, ensure_ascii=False))
