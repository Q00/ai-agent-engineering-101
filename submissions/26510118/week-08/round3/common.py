"""Round 3 basic pipeline -- what the shop, the buyer and the hooks share.

The kit keeps the market's side in MARKET_DIR (inventory.json, catalog.json,
demand.json, order.json, shops.json, deal.json, payments.jsonl) and reads one
JSON line back from each program. Everything here only reads those files;
records/ next to them is ours: one line per negotiation, order and restock, so
the enhanced version has past deals to learn from.
"""
import base64
import json
import os
import time
from pathlib import Path

from cryptography.hazmat.primitives import serialization

HERE = Path(__file__).resolve().parent
MARKET_DIR = Path(os.environ.get("MARKET_DIR", HERE / ".market"))
RECEIPT_KEYS = ("shop", "item", "price", "task")      # what sig_seller covers


def read_json(path, default=None):
    """A file the kit replaces whole; missing or unreadable -> default."""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def market_file(name: str, default=None):
    return read_json(MARKET_DIR / name, default)


def catalog(market_dir: Path = MARKET_DIR) -> dict:
    """sku -> {sku, name, category, list_price, kind, shelf_hours}."""
    return read_json(market_dir / "catalog.json", {}) or {}


def record(stream: str, **fields) -> None:
    """Append one line to records/<stream>.jsonl. (Not `kind`: an order's fields include kind.)"""
    folder = MARKET_DIR / "records"
    folder.mkdir(parents=True, exist_ok=True)
    with open(folder / f"{stream}.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(dict(ts=int(time.time()), **fields), ensure_ascii=False) + "\n")


def canon(d: dict) -> bytes:
    """The market's canonical JSON: sorted keys, no spaces, UTF-8."""
    return json.dumps(d, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def load_key(env: str, default: str):
    """The kit's key (the market checks signatures with the one registered at join)."""
    path = Path(os.environ.get(env) or HERE / default)
    return serialization.load_pem_private_key(path.read_bytes(), password=None)


def sign_receipt(key, receipt: dict) -> dict:
    """sig_seller over {shop, item, price, task}; other fields (unit, claim,
    warranty_hours, cert) ride along unsigned and the kit takes them from us."""
    body = {k: receipt[k] for k in RECEIPT_KEYS}
    return dict(receipt, sig_seller=base64.b64encode(key.sign(canon(body))).decode())


def last_line(obj: dict) -> None:
    """The kit reads the last JSON line a hook prints; everything before it is log."""
    print(json.dumps(obj, ensure_ascii=False), flush=True)
