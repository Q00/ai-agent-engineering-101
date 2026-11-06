"""Round 3 basic pipeline -- what the shop, the buyer and the hooks share.

The kit keeps the market's side in MARKET_DIR (inventory.json, catalog.json,
demand.json, order.json, shops.json, deal.json, payments.jsonl) and reads one
JSON line back from each program. Everything here only reads those files;
records/ next to them is ours: one line per negotiation, order and restock, so
the enhanced version has past deals to learn from.
"""
import base64
import gzip
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography.hazmat.primitives import serialization

HERE = Path(__file__).resolve().parent
MARKET_DIR = Path(os.environ.get("MARKET_DIR", HERE / ".market"))
RECEIPT_KEYS = ("shop", "item", "price", "task")      # what sig_seller covers
KST = timezone(timedelta(hours=9))
# Round 3 ends 2026-11-17 23:59 KST (week-08 notice); ROUND_END_TS moves it for tests.
ROUND_END = float(os.environ.get("ROUND_END_TS") or datetime(2026, 11, 17, 23, 59, 59, tzinfo=KST).timestamp())
ROUND = 3                # the ledger runs on across rounds, each entry marked with its round: only this one counts
STRATEGY = {"phase": "explore"}
# The market registers our shop and buyer as Kiro729 (one name for both, the kit's --name); the shop's
# agent card and receipts say SHOP_NAME. Change the card when the focus phase picks a specialty: it must
# stay true to what the shop sells.
SHOP_BRAND = "Kiro Everyday"
SHOP_NAME = f"{SHOP_BRAND}: Groceries & Goods"
OWN_NAMES = {"Kiro729", "Kiro729 Shop", SHOP_BRAND}  # ours, matched as parts of a name: left out of what we publish about shops


GRADE_ORDER = "ABC"      # best first


def worse_than_claimed(got: dict) -> bool:
    """A settled deal whose grade claim the market found false with the goods worse than claimed (or either grade
    unknown). A claim the goods beat (a shop that said B for an A) is no deceit (11/6 16:40)."""
    if got.get("claim_honest") is not False:
        return False
    c, g = got.get("claim"), got.get("grade")
    if c in GRADE_ORDER and g in GRADE_ORDER:
        return GRADE_ORDER.index(g) > GRADE_ORDER.index(c)
    return True


def is_ours(name) -> bool:
    """A shop, buyer or player name of ours (OWN_NAMES as parts of it): forum.own and ledger.is_ours."""
    return bool(name) and any(o.lower() in str(name).lower() for o in OWN_NAMES)
# The rules the trading modules follow. The integrated analysis rewrites analysis/active_rules.json
# (only in the focus phase, within its limits); a missing file or key keeps these defaults.
RULES = {
    "seller": {"focus_categories": [], "drop_categories": [], "price_adjust": 0.0,
               "warranty_extra": {"A": 0.025, "B": 0.08, "C": 0.20}, "walk_threshold": 0.7},
    "buyer": {"avoid_shops": [], "prefer_shops": [], "open_ratio": 0.50, "max_ratio": 0.90,
              "warranty_all_goods": False, "over_budget_try": True},
}


# Owners whose buyers rate shops 1 star on purpose (the user's call, 11/5 15:45: "go to their shops as little as
# possible"; inextro stays as it is). From the public ledger on 11/5: totato2203's buyer gave every shop 1 star on
# every purchase from 03:50 (us 40 of 40, other shops 219 of 219); from 12:43 shaun0927's two buyers gave us 1 star on
# 30 of 30 and the other top shops most of theirs (inextro 17/19, psyoung82 30/53, yms979 10/16; kimauve 1/16).
# The analysis maps them to their shops on the board (analysis/bad_faith.json); the buyer tries those shops last.
BAD_FAITH_OWNERS = ("shaun0927", "totato2203")


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


_TS = re.compile(r'"ts":\s*(-?[0-9.]+)')


def read_jsonl(path, since: float | None = None) -> list:
    """Every whole line of a .jsonl file; a missing file or a half-written last line is skipped. With since,
    only rows whose ts is since or later: an older line is skipped by its "ts" near the start, before it is
    parsed. Read a line at a time (11/5: the analysis read the whole ledger file, 69k rows and 26 MB, in four
    places, and peaked at 398 MB on a server with 837 MB)."""
    rows = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                if since is not None:
                    m = _TS.search(line, 0, 120)
                    if m and "{" not in line[1:m.start()] and float(m.group(1)) < since:   # the top level's ts
                        continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if since is not None and (not isinstance(row, dict) or (row.get("ts") or 0) < since):
                    continue
                rows.append(row)
    except OSError:
        pass
    return rows


def records(stream: str) -> list:
    return read_jsonl(MARKET_DIR / "records" / f"{stream}.jsonl")


def archive_old(path, before: float, slack: float = 0) -> int:
    """Move the lines of a .jsonl file whose ts is before `before` into archive/<name>-<YYYYMMDD>.jsonl.gz
    next to it, and keep the rest: nothing is dropped, the file read every hour stays small. Skipped
    while its oldest line is younger than before - slack (so it runs every few hours, not every pass).
    Other processes append to these files; lines they add while this runs are read again just before
    the swap and kept. Returns how many lines moved."""
    path = Path(path)
    try:
        with open(path, encoding="utf-8") as f:
            oldest = json.loads(f.readline()).get("ts") or 0
    except (OSError, ValueError):
        return 0
    if oldest >= before - slack:
        return 0
    raw = path.read_bytes()
    old, keep = [], []
    for line in raw.decode("utf-8").splitlines(keepends=True):
        try:
            ts = json.loads(line).get("ts") or 0
        except ValueError:
            keep.append(line)                                    # a line still being written stays where it is
            continue
        (old if ts < before else keep).append(line)
    if not old:
        return 0
    (path.parent / "archive").mkdir(exist_ok=True)
    day = datetime.now(KST).strftime("%Y%m%d")
    with gzip.open(path.parent / "archive" / f"{path.stem}-{day}.jsonl.gz", "at", encoding="utf-8", newline="") as g:
        g.writelines(old)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        f.writelines(keep)
        f.write(path.read_bytes()[len(raw):].decode("utf-8", errors="replace"))   # appended meanwhile
    os.replace(tmp, path)
    return len(old)


def write_json(path, data) -> None:
    """Replace a file whole, so a reader never sees half of it."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def strategy() -> dict:
    """strategy.json next to the code: the phase, switched by a person at the second enhancement
    (explore -> focus). The integrated analysis adopts rule changes only in the focus phase.
    STRATEGY_FILE points elsewhere (tests)."""
    return dict(STRATEGY, **(read_json(os.environ.get("STRATEGY_FILE") or HERE / "strategy.json", {}) or {}))


def rules() -> dict:
    """The rules in force: the defaults above, overridden key by key by analysis/active_rules.json."""
    got = read_json(MARKET_DIR / "analysis" / "active_rules.json", {}) or {}
    out = json.loads(json.dumps(RULES))
    for role, fields in out.items():
        fields.update({k: v for k, v in (got.get(role) or {}).items() if k in fields})
    return out


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
