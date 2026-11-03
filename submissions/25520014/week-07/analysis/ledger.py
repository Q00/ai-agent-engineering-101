"""The market's public ledger, cached in .market/ledger.jsonl (gitignored) and topped up on every load."""
import datetime as dt
import json
import pathlib

import httpx

MARKET = "https://market.ourolabs.ai"
CACHE = pathlib.Path(__file__).resolve().parent.parent / ".market" / "ledger.jsonl"
ME = "inextro"
RESERVE = {"lamp": 100, "chair": 180, "bookshelf": 260}


def load():
    entries = [json.loads(line) for line in open(CACHE, encoding="utf-8")] if CACHE.exists() else []
    after = entries[-1]["seq"] if entries else 0
    with open(CACHE, "a", encoding="utf-8") as out:
        while True:
            batch = httpx.get(f"{MARKET}/api/ledger", params={"after": after}, timeout=30).json()
            if not batch:
                break
            for e in batch:
                out.write(json.dumps(e, ensure_ascii=False) + "\n")
            entries += batch
            after = batch[-1]["seq"]
            if len(batch) < 500:
                break
    return entries


def shop_names(entries):
    """Shop URL -> market name, from the join entries (a shop's URL changes when its kit restarts)."""
    return {e["data"]["url"].rstrip("/"): e["data"]["name"] for e in entries
            if e["kind"] == "join" and e["data"].get("url")}


def shop_of(e, names):
    return names.get((e["data"].get("shop") or "").rstrip("/"), "?")


def utc(ts):
    return dt.datetime.fromtimestamp(ts, dt.UTC).strftime("%m-%d %H:%M")
