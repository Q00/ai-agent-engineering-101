"""Read-only access to the market's public API and the files the kit writes in .market/."""
import json
import time
from pathlib import Path

import httpx

from . import config

_HTTP = None


def http():
    global _HTTP
    if _HTTP is None:
        _HTTP = httpx.Client(base_url=config.MARKET_URL, timeout=15, trust_env=False)
    return _HTTP


def get_json(path, **params):
    """GET a public endpoint. Returns None on any network or JSON problem: callers degrade, never crash."""
    try:
        r = http().get(path, params=params or None)
        if r.status_code != 200:
            return None
        return r.json()
    except (httpx.HTTPError, ValueError):
        return None


def read_json(name, default=None, md=None):
    """A file the kit keeps in .market/ (replaced whole, so a read never sees half a file)."""
    try:
        return json.loads(Path(md or config.MARKET_DIR, name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


_CATALOG = None


def catalog(md=None):
    """sku -> {name, category, list_price, kind, shelf_hours}."""
    global _CATALOG
    if _CATALOG is None:
        _CATALOG = read_json("catalog.json", {}, md) or {}
        if not _CATALOG:
            rows = get_json("/api/catalog") or []
            _CATALOG = {x["sku"]: x for x in rows if isinstance(x, dict) and "sku" in x}
    return _CATALOG


def leaf(category):
    return (category or "").split(">")[-1]


def read_jsonl(path, offset=0):
    """Lines after byte offset; returns (rows, new_offset). A torn last line is left for next time."""
    rows = []
    try:
        with open(path, "rb") as f:
            f.seek(offset)
            data = f.read()
    except OSError:
        return rows, offset
    end = data.rfind(b"\n") + 1
    for line in data[:end].splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows, offset + end


def now():
    return time.time()
