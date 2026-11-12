"""Bounded visibility observations and fail-closed stock-depth policy. No credentials."""
from __future__ import annotations

import json
import math
import os
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx


def read_json(path):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(exist_ok=True)
    temporary = path.with_name(path.name + f".{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        temporary.write_text(json.dumps(value, allow_nan=False), encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def paused(state, now=None):
    now = time.time() if now is None else now
    if not isinstance(state, dict):
        return True
    ts = state.get("checked_at")
    return not (state.get("version") == 1 and state.get("state") == "healthy"
                and isinstance(ts, (int, float)) and not isinstance(ts, bool)
                and math.isfinite(ts) and 0 <= now - ts <= 180)


def own_sale(hub, sku, now):
    # store.apply_ledger_rows admits only our real settled shop sales here.
    last_sales = hub.get("own_last_sale_ts", {})
    last = last_sales.get(sku) if isinstance(last_sales, dict) else None
    if isinstance(last, (int, float)) and not isinstance(last, bool) and 0 <= now - last <= 3600:
        return True
    sales = hub.get("own_sale_events", {})
    if not isinstance(sales, dict):
        return False
    return any(isinstance(s, dict) and s.get("sku") == sku
               and isinstance(s.get("ts"), (int, float))
               and 0 <= now - s["ts"] <= 3600 for s in sales.values())


def safe_url(url):
    if not isinstance(url, str) or len(url) > 2048:
        return False
    try:
        p = urlsplit(url)
        return p.scheme == "https" and bool(p.hostname) and not p.username and not p.password and not p.query and not p.fragment
    except ValueError:
        return False


class VisibilityGuard:
    def __init__(self, market, directory, name, url_supplier, *, client=None, clock=time.time):
        self.market, self.directory, self.name = market, Path(directory), name
        self.url_supplier, self.clock = url_supplier, clock
        self.client = client or httpx.Client(timeout=8, follow_redirects=False)
        self.good = self.bad = self.attempts = 0
        self.next_retry = 0
        self.last_url = None
        self.auth_failed = False
        self.stop = threading.Event()
        self.publish("unknown", "starting", None)

    def publish(self, state, reason, url, *, status=None):
        value = {"version": 1, "pid": os.getpid(), "checked_at": self.clock(),
                 "state": state, "reason": reason, "current_url": url,
                 "needs_restore": state != "healthy", "streaks": {"good": self.good, "bad": self.bad},
                 "next_retry": self.next_retry, "attempts": self.attempts}
        if status is not None:
            value["status"] = status
        atomic_json(self.directory / "visibility_guard.json", value)

    def rows(self, path):
        response = self.client.get(self.market.base + path, timeout=8, follow_redirects=False)
        if response.status_code != 200:
            raise ValueError("http_status")
        data = response.json()
        rows = data.get("shops") if isinstance(data, dict) else None
        if not isinstance(rows, list) or any(not isinstance(r, dict) or not isinstance(r.get("name"), str) for r in rows):
            raise ValueError("schema")
        names = [r["name"] for r in rows]
        if len(names) != len(set(names)):
            raise ValueError("duplicate")
        for row in rows:
            if path == "/api/board":
                if (type(row.get("alive")) is not bool or not isinstance(row.get("card"), dict)
                        or not safe_url(row["card"].get("url"))):
                    raise ValueError("board_schema")
            elif not safe_url(row.get("url")) or not isinstance(row.get("stock"), dict):
                raise ValueError("shops_schema")
        return next((r for r in rows if r["name"] == self.name), None)

    def tick(self):
        url = self.url_supplier()
        if url != self.last_url:
            self.good = self.bad = 0
            self.last_url = url
        try:
            if not safe_url(url):
                raise ValueError("url")
            board, shop = self.rows("/api/board"), self.rows("/api/shops")
            if board is not None and (type(board.get("alive")) is not bool
                                      or not isinstance(board.get("card"), dict)
                                      or board["card"].get("url") != url):
                raise ValueError("board_schema_or_url")
            if shop is not None and (shop.get("url") != url or not isinstance(shop.get("stock"), dict)):
                raise ValueError("shop_schema_or_url")
            card = self.client.get(url.rstrip("/") + "/.well-known/agent-card.json", timeout=8, follow_redirects=False)
            if card.status_code != 200:
                self.good = self.bad = 0
                self.publish("unknown", "card_unavailable", url, status=card.status_code)
                return
            with self.market.guard_lock:
                if self.url_supplier() != url:
                    self.good = self.bad = 0
                    self.publish("unknown", "url_changed", self.url_supplier())
                    return
                healthy = board is not None and board["alive"] and shop is not None
                both_bad = (board is None or not board["alive"]) and shop is None
                self.good = min(2, self.good + 1) if healthy else 0
                self.bad = min(2, self.bad + 1) if both_bad else 0
                reauth = False
                if self.auth_failed:
                    response = self.market.call("GET", "/api/inventory", "shop")
                    if response.status_code == 401:
                        reauth = True
                    elif response.status_code == 200:
                        self.auth_failed = False
                    else:
                        raise ValueError("inventory_status")
                if (self.bad >= 2 or reauth) and self.clock() >= self.next_retry:
                    self.attempts = min(3, self.attempts + 1)
                    self.next_retry = self.clock() + min(1200, 300 * 2 ** (self.attempts - 1))
                    self.good = 0
                    self.publish("rejoining", "inventory_401" if reauth else "both_absent", url)
                    try:
                        name, key, _ = self.market.joined["shop"]
                        self.market.join("shop", name, key, url, tries=1)
                        self.auth_failed = False
                    except (Exception, SystemExit):
                        self.publish("degraded", "join_failed", url)
                    return
                if self.good >= 2 and not self.auth_failed:
                    self.attempts = 0
                    self.next_retry = 0
                    self.publish("healthy", "confirmed", url)
                else:
                    self.publish("degraded" if both_bad else "unknown",
                                 "both_absent" if both_bad else "confirmation_pending" if healthy else "inconsistent", url)
        except Exception:
            self.good = self.bad = 0
            self.publish("unknown", "observation_failed", url if safe_url(url) else None)

    def run(self):
        while not self.stop.is_set():
            self.tick()
            self.stop.wait(60)


class RuntimeLock:
    """OS lock, not a stale pid-file heuristic; held for the wrapper lifetime."""
    def __init__(self, directory):
        self.path = Path(directory) / "visibility_guard.lock"
        self.file = None

    def __enter__(self):
        self.path.parent.mkdir(exist_ok=True)
        self.file = self.path.open("a+b")
        if self.path.stat().st_size == 0:
            self.file.write(b"0")
            self.file.flush()
        self.file.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            raise RuntimeError("guard_runtime_already_locked") from None
        return self

    def __exit__(self, *args):
        if os.name == "nt":
            import msvcrt
            self.file.seek(0)
            msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
        self.file.close()
