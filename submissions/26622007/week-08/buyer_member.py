"""Use only a member offer issued to our completed, subsequently paid trade.

No IO or extra requests. A code is bound to its issuing RPC endpoint and shop.
Its economic effect and any server-side expiry/reuse rule remain the seller's.
"""
import math
import re
import time
from urllib.parse import urlsplit

SHOP = "moss-and-oak"
MAX_AGE = 24 * 3600
CODE = re.compile(r"MOSS-[A-Z]{8}")
ID = re.compile(r"[A-Za-z0-9_-]{1,96}")


def _num(value):
    return type(value) in (int, float) and abs(value) < 1e12 and math.isfinite(value)


def _endpoint(value):
    if not isinstance(value, str) or len(value) > 1000:
        return False
    try:
        u = urlsplit(value)
        return bool(u.hostname and not u.username and not u.password and not u.query and not u.fragment and (
            u.scheme == "https" or u.scheme == "http" and u.hostname in ("127.0.0.1", "localhost", "::1")))
    except ValueError:
        return False


def capture(shop, out, order, now=None):
    """Keep an exact issuer sentence only after the caller's receipt/price checks."""
    now = time.time() if now is None else now
    if shop != SHOP or not isinstance(out, dict) or out.get("state") != "TASK_STATE_COMPLETED":
        return None
    r = out.get("receipt")
    if not isinstance(r, dict) or r.get("shop") != SHOP or not r.get("sig_seller"):
        return None
    task, sku, unit, price = r.get("task"), r.get("item"), r.get("unit"), r.get("price")
    if not (isinstance(order, str) and ID.fullmatch(order) and isinstance(task, str) and ID.fullmatch(task)
            and task == out.get("task") and _endpoint(out.get("rpc_url")) and _num(now) and now > 0
            and isinstance(sku, str) and re.fullmatch(r"G[0-9a-f]{8}", sku)
            and isinstance(unit, str) and re.fullmatch(r"u-[0-9a-f]{12,64}", unit)
            and _num(price) and 0 < price == int(price) and price == out.get("agreed")):
        return None
    text = out.get("final_message")
    if not isinstance(text, str) or len(text) > 2000:
        return None
    pattern = (r"Deal: [^\r\n]{1,600} \(" + re.escape(sku) + r"\) at " + str(int(price))
               + r"\. Signed receipt attached; the checkout ships unit " + re.escape(unit)
               + r" with its market certificate\. Your Moss & Oak member code is (MOSS-[A-Z]{8}); "
                 r"include it next time for member pricing\.")
    m = re.fullmatch(pattern, text)
    return {"code": m.group(1), "url": out["rpc_url"], "order": order, "task": task, "issued_at": now} if m else None


def available(trades, now=None):
    """Unpaid/refused/policy-refused/unsigned trades never authorize reuse."""
    now = time.time() if now is None else now
    if not isinstance(trades, list) or not _num(now):
        return {}
    for t in reversed(trades):
        if not isinstance(t, dict) or t.get("shop") != SHOP or t.get("paid") is not True or (
                t.get("result") != "deal" or t.get("signed") is not True):
            continue
        m = t.get("member_offer")
        if (isinstance(m, dict) and isinstance(m.get("code"), str) and CODE.fullmatch(m["code"])
                and _endpoint(m.get("url")) and isinstance(m.get("order"), str) and ID.fullmatch(m["order"])
                and m["order"] == t.get("order") and isinstance(m.get("task"), str) and ID.fullmatch(m["task"])
                and _num(m.get("issued_at")) and 0 <= now - m["issued_at"] < MAX_AGE):
            return {SHOP: dict(m)}
    return {}


def request(original, shop, rpc_url, offers):
    """Never send an issued code to another shop or another RPC URL."""
    m = offers.get(SHOP) if isinstance(offers, dict) else None
    if (shop == SHOP and isinstance(m, dict) and _endpoint(rpc_url) and rpc_url == m.get("url")
            and isinstance(m.get("code"), str) and CODE.fullmatch(m["code"])):
        return original + " Member code: " + m["code"] + "."
    return original
