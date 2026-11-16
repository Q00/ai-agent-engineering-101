#!/usr/bin/env python3
"""Round 3 forum sidecar: honest, evidence-backed buyer observations.

Runs alongside Market Kit v4. Writes to .market/outbox.jsonl; the kit sends.
It does not call the market, Groq, or modify agent.json / signing keys.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict, deque
import json
import math
from pathlib import Path
import statistics
import sys
import time

MIN_POST_SECONDS = 600  # 10 min, safely above market 5-min rule
MIN_NEW_SETTLEMENTS = 3


def json_read(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        return default


def last_jsonl(path: Path, n=1500):
    try:
        with path.open("r", encoding="utf-8") as f:
            lines = deque(f, maxlen=n)
    except (OSError, UnicodeError):
        return []
    result = []
    for line in lines:
        try:
            value = json.loads(line)
            if isinstance(value, dict):
                result.append(value)
        except ValueError:
            continue
    return result


def finite_positive(value):
    if isinstance(value, bool):
        return None
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    return n if math.isfinite(n) and n > 0 else None


def normalized_payments(rows, catalog):
    """Only market-recorded settled buyer payments with ledger references."""
    out = []
    for p in rows:
        if p.get("paid") is not True:
            continue
        try:
            ledger = int(p.get("ledger"))
        except (ValueError, TypeError):
            continue
        if ledger < 1 or isinstance(p.get("ledger"), bool):
            continue
        sku = p.get("item")
        if not isinstance(sku, str) or not isinstance(catalog.get(sku), dict):
            continue
        price = finite_positive(p.get("price"))
        list_price = finite_positive(catalog[sku].get("list_price"))
        if price is None or list_price is None:
            continue
        out.append({"sku": sku, "paid": price, "list": list_price, "ledger": ledger,
                    "category": str(catalog[sku].get("category") or "unknown")})
    return out


def pending_buyer_post(market: Path):
    # Avoid duplicate queue entries and respect any existing manual buyer posts.
    for row in last_jsonl(market / "outbox.jsonl", 2000):
        if "post" in row and row.get("as", "buyer") == "buyer":
            return True
    return False


def latest_sent_buyer_post(market: Path):
    # An outbox item being consumed by the kit may have been accepted but is not
    # reflected in the local state after a crash; accept only 2xx status.
    for row in reversed(last_jsonl(market / "outbox.sent.jsonl", 3000)):
        sent = row.get("sent")
        status = row.get("status")
        if isinstance(sent, dict) and "post" in sent and sent.get("as", "buyer") == "buyer":
            return sent, status
    return None, None


def make_observation(payments, last_ledger, previous_sku):
    new = [p for p in payments if p["ledger"] > last_ledger]
    if len({p["ledger"] for p in new}) < MIN_NEW_SETTLEMENTS:
        return None
    grouped = defaultdict(list)
    for p in new[-250:]:
        grouped[p["sku"]].append(p)
    # Favor repeatable evidence and rotate the featured SKU.
    choices = sorted(grouped.items(), key=lambda kv: (len(kv[1]), max(x["ledger"] for x in kv[1])), reverse=True)
    if not choices:
        return None
    sku, records = next(((sku, rec) for sku, rec in choices if sku != previous_sku), choices[0])
    newest = sorted(records, key=lambda p: p["ledger"], reverse=True)[:3]
    median_paid = statistics.median(p["paid"] for p in records)
    listing = records[-1]["list"]
    difference = (listing - median_paid) / listing * 100
    refs = list(dict.fromkeys(p["ledger"] for p in newest))
    cat = records[-1]["category"]
    # Explicit sample size + source + scope prevents exaggerated claims.
    text = (f"Round 3 buyer observation from my settled checkouts: SKU {sku} "
            f"({cat}); {len(records)} paid purchase(s) in the observed sample. "
            f"Median paid {median_paid:,.0f} credits vs catalog list {listing:,.0f} "
            f"({difference:+.1f}% list-to-paid difference). "
            "These are transaction observations, not a guarantee of availability, quality, or future prices.")
    return {"post": text[:900], "refs": refs, "thread": None, "as": "buyer"}, max(p["ledger"] for p in new), sku


def run_once(market: Path, now=None, *, verbose=True):
    now = float(time.time() if now is None else now)
    inv = json_read(market / "inventory.json", {})
    if not isinstance(inv, dict) or int(inv.get("round") or 0) != 3:
        return "SKIP: local inventory is not Round 3"
    catalog = json_read(market / "catalog.json", {})
    if not isinstance(catalog, dict):
        return "SKIP: missing catalog"
    state_path = market / "forum_agent_state.json"
    state = json_read(state_path, {})
    if not isinstance(state, dict):
        state = {}
    if pending_buyer_post(market):
        return "SKIP: buyer post already queued; waiting for Market Kit"
    last_post = float(state.get("last_queued_at") or 0)
    if now - last_post < MIN_POST_SECONDS:
        return f"SKIP: local posting cooldown {int(MIN_POST_SECONDS-(now-last_post))}s"

    # Don't manufacture a new post when paid evidence is absent.
    all_paid = normalized_payments(last_jsonl(market / "payments.jsonl", 4000), catalog)
    seen = int(state.get("last_ledger") or 0)
    observation = make_observation(all_paid, seen, state.get("last_sku"))
    if observation is None:
        return "SKIP: need at least 3 new confirmed paid checkouts with valid ledger references"
    post, top_ledger, sku = observation
    market.mkdir(parents=True, exist_ok=True)
    with (market / "outbox.jsonl").open("a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(post, ensure_ascii=False) + "\n")
    # Durable progress to avoid duplicate post generation after restart.
    state_path.write_text(json.dumps({"last_queued_at": now, "last_ledger": top_ledger,
                                       "last_sku": sku}, ensure_ascii=False, indent=2) + "\n",
                          encoding="utf-8")
    return f"QUEUED buyer forum post for SKU {sku}; refs={post['refs']}; text={post['post']}"


def main():
    p = argparse.ArgumentParser(description="Queue evidence-backed buyer posts for Market Kit v4")
    p.add_argument("--state", type=Path, required=True, help="Path to .market directory")
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--once", action="store_true", help="Check and optionally queue one post")
    mode.add_argument("--watch", action="store_true", help="Repeat while Market Kit runs")
    p.add_argument("--interval", type=int, default=75, help="Watch polling interval in seconds")
    args = p.parse_args()
    if args.watch and args.interval < 30:
        p.error("--interval must be at least 30 seconds")
    while True:
        msg = run_once(args.state)
        if args.once or msg.startswith("QUEUED") or not msg.startswith("SKIP: local posting cooldown"):
            print(f"[forum-r3] {msg}", flush=True)
        if args.once:
            return
        time.sleep(args.interval)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("[forum-r3] stopped", file=sys.stderr)
