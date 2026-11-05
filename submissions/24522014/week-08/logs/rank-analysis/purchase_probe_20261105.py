"""Read-only, no network, no model. First look at stopped orders: reply shapes with digits and names masked, retries per order, stops by category and shop."""
import ctypes
from collections import Counter, defaultdict
import glob
import json
import os
from pathlib import Path
import re
from statistics import median

ROOT = Path(__file__).resolve().parents[2]
ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x00100000)
SINCE = "20261104T100000"
rows = []
for name in sorted(n for n in os.listdir(ROOT / "logs/market-buyer") if n >= SINCE):
    try:
        rows.append(json.loads((ROOT / "logs/market-buyer" / name).read_text(encoding="utf-8")))
    except (OSError, ValueError):
        pass


def last_reply(rec):
    folder = Path(str(rec.get("buyer_log_dir") or "").replace("\\", "/")).name
    files = sorted(glob.glob(str(ROOT / "logs/buyer" / folder / "*.response.txt")))
    return Path(files[-1]).read_text(encoding="utf-8", errors="replace") if files else ""


def text_of(raw):
    """The agent's words from a saved response, when it is JSON."""
    try:
        data = json.loads(raw)
    except ValueError:
        return raw
    found = []

    def walk(value):
        if isinstance(value, dict):
            if isinstance(value.get("text"), str):
                found.append(value["text"])
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)
    walk(data)
    return " ".join(found) or raw


def mask(text):
    text = re.sub(r"\[?sku [^\]\s]+\]?", "<sku>", text, flags=re.I)
    text = re.sub(r"G[0-9a-f]{8}", "<sku>", text)
    text = re.sub(r"\d[\d,.]*", "#", text)
    text = re.sub(r"[가-힣]{2,}(?:\s+[가-힣]{2,})*", "<k>", text)
    return re.sub(r"\s+", " ", text)


counts = Counter()
orders = Counter(r["record"].get("order") for r in rows if r["record"].get("order"))
print("records", len(rows), "orders", len(orders), "orders seen more than once", sum(n > 1 for n in orders.values()))
templates = defaultdict(Counter)
shops = Counter()
for row in rows:
    rec = row["record"]
    trail = [s for s in rec.get("decision_trail") or [] if isinstance(s, dict)]
    if trail and trail[-1].get("parser_reason") == "no_labeled_price":
        shops[rec.get("shop_name")] += 1
        templates[rec.get("shop_name")][mask(text_of(last_reply(rec)))[:170]] += 1
print("no_labeled_price anywhere", sum(shops.values()), shops.most_common(12))
for shop, table in sorted(templates.items(), key=lambda kv: -sum(kv[1].values()))[:9]:
    print(" ", shop, sum(table.values()))
    for template, n in table.most_common(3):
        print("    ", n, template)
rejected = defaultdict(Counter)
rej_shops = Counter()
for row in rows:
    rec = row["record"]
    if rec.get("failure_reason") == "seller_rejected" or rec.get("refused_at_open"):
        rej_shops[rec.get("shop_name")] += 1
        rejected[mask(text_of(last_reply(rec)))[:90]][rec.get("shop_name")] += 1
print("seller_rejected", sum(rej_shops.values()), "shops", len(rej_shops), rej_shops.most_common(8))
for template, table in sorted(rejected.items(), key=lambda kv: -sum(kv[1].values()))[:14]:
    print("   ", sum(table.values()), len(table), template)
print("keys of a skipped record", sorted(rows[0]["record"].keys())[:40])
sample = next((r for r in rows if r["record"].get("failure_reason") == "selection_attempt_cooldown"), None)
print("cooldown example", {k: sample["record"].get(k) for k in ("outcome", "failure_reason", "category", "order", "selection_reason", "timestamp", "item")} if sample else None, str(sample.get("result"))[:150] if sample else None)
