"""Buyer speed, prices unchanged (person's decision 11/09 ~11:55; on after the 11/09 14:13 accept-rule report, tools/
buyer_speed_rollout.py). Rules in buyer_speed.json (read per order):
  skip_first_llm  the opening offer is the code's (the variant's share of the ask); the harness call made before it
                  was thrown away, so it is not made (about 4 s an order)
  card_ttl_s      a shop's agent card is kept per address for 600 s in logs/card_cache.json (no fetch per order)
  first_reply_s   no card or no first reply within 10 s -> SlowShop: the buyer goes to the next candidate shop and
                  records decision buyer slow_shop_skip
  regular_reply_s regulars (shops with >= regular_min_deals (20) deals with us in the last 24 h, logs/buyer_regulars.json,
                  refreshed from market.db buyer_deals when older than 1 h) get 20 s for the card and the first reply
                  instead (person's decision 11/09 ~17:20); other shops keep first_reply_s
Direct API calls instead of the claude -p harness are out (person 11/09, cost).
"""
import json
import os
import time

import httpx

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RULES = os.path.join(HERE, "buyer_speed.json")
CACHE = os.path.join(HERE, "logs", "card_cache.json")
REGULARS = os.path.join(HERE, "logs", "buyer_regulars.json")
REFRESH_S, WINDOW_S = 3600, 86400


class SlowShop(Exception):
    def __init__(self, stage, secs):
        super().__init__(f"no {stage} within {secs} s")
        self.stage, self.secs = stage, secs


def rules():
    try:
        with open(RULES, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def on(key, r=None):
    r = rules() if r is None else r
    return bool(r.get("enabled") and r.get(key))


def regulars(now=None, db_path=None):
    """{shop name: deals with us in the last 24 h} for shops at >= regular_min_deals; recomputed once an hour."""
    now = now or time.time()
    try:
        with open(REGULARS, encoding="utf-8") as f:
            d = json.load(f)
        if now - d.get("ts", 0) < REFRESH_S:
            return d.get("shops", {})
    except (OSError, ValueError):
        pass
    import sqlite3
    try:
        db = sqlite3.connect(db_path or os.path.join(HERE, "market.db"), timeout=30)
        try:
            rows = db.execute("SELECT shop, COUNT(*) FROM buyer_deals WHERE ts>? AND outcome='deal' GROUP BY shop",
                              (now - WINDOW_S,)).fetchall()
        finally:
            db.close()
    except Exception:
        return {}
    n = int(rules().get("regular_min_deals", 20))
    shops = {k: v for k, v in rows if k and v >= n}
    try:
        with open(REGULARS + ".tmp", "w", encoding="utf-8") as f:
            json.dump({"ts": round(now), "min_deals": n, "shops": shops}, f, ensure_ascii=False)
        os.replace(REGULARS + ".tmp", REGULARS)
    except OSError:
        pass
    return shops


def limit(shop, r=None):
    """(seconds for the card and the first reply, regular?) for this shop."""
    r = rules() if r is None else r
    base = float(r.get("first_reply_s", 10))
    if shop and r.get("regular_reply_s") and shop in regulars():
        return float(r["regular_reply_s"]), True
    return base, False


def _cache():
    try:
        with open(CACHE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save(c):
    try:
        with open(CACHE + ".tmp", "w", encoding="utf-8") as f:
            json.dump(c, f)
        os.replace(CACHE + ".tmp", CACHE)
    except OSError:
        pass


def client(url, A2AClient, headers, now=None, r=None, lim=None):
    """An A2AClient for url: the cached card when it is fresh, else a fetch (10 s limit when the rule is on)."""
    r = rules() if r is None else r
    if not r.get("enabled"):
        return A2AClient(url)
    now = now or time.time()
    ttl = float(r.get("card_ttl_s", 600))
    lim = float(r.get("first_reply_s", 10)) if lim is None else lim
    c = _cache()
    hit = c.get(url)
    obj = A2AClient.__new__(A2AClient)
    obj.n = 0
    if hit and now - hit["ts"] < ttl:
        obj.http = httpx.Client(timeout=120, headers=headers)
        obj.card, obj.url, obj.cached = hit["card"], hit["rpc"], True
        return obj
    obj.http = httpx.Client(timeout=120, headers=headers)
    try:
        card = obj.http.get(url.rstrip("/") + "/.well-known/agent-card.json", timeout=lim).json()
    except httpx.TimeoutException:
        raise SlowShop("card", lim)
    iface = next((i for i in card.get("supportedInterfaces", []) if i.get("protocolBinding") == "JSONRPC"), None)
    if iface is None:
        raise SystemExit("seller card has no JSONRPC interface")
    obj.card, obj.url, obj.cached = card, iface["url"], False
    c = {k: v for k, v in c.items() if now - v["ts"] < ttl}
    c[url] = {"ts": now, "card": card, "rpc": iface["url"]}
    _save(c)
    return obj


def first_send(client_, text, r=None, lim=None):
    """The first message with the first-reply limit (when the rule is on); SlowShop when the shop is too slow."""
    r = rules() if r is None else r
    if not r.get("enabled"):
        return client_.send(text)
    lim = float(r.get("first_reply_s", 10)) if lim is None else lim
    old = client_.http.timeout
    client_.http.timeout = httpx.Timeout(lim)
    try:
        return client_.send(text)
    except httpx.TimeoutException:
        raise SlowShop("first reply", lim)
    finally:
        client_.http.timeout = old
