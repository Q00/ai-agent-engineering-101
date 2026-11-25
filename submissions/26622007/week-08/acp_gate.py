"""Buyers this shop does not sell to (pricing.json seller.refuse_buyers), checked when a buyer completes an ACP checkout.

The A2A talk carries no buyer name; the first place the shop learns who pays is payment_data.order at
/checkout_sessions/{id}/complete. The market's public ledger has one "order" row per order naming its buyer, written
when the order is issued (before the buyer talks to any shop), so the order is looked up there. A refused buyer gets
the answer a sold-out unit gets (409, the session canceled, the unit back on sale) and nothing is sent to /api/settle.
Every other buyer, and any order the ledger lookup cannot place, goes on to settle as before.

The refused buyer, the time and the order go to .market/measure/refused.jsonl (record only, never read by a decision).
An empty list (or no "seller" key) turns this off without a restart: pricing.json is read on every checkout.

seller.refuse_one_star_at (대표님 11/05 11:34): a buyer with that many verified 1-star results on this shop, over the
whole of Round 3 and every URL the shop has had, is refused the same way (OneStars, .market/one_star.json). 0 is off.
seller.refuse_exempt (대표님 11/05 12:27): buyers the 1-star rule never refuses (refuse_buyers still does).

Used by the kit's AcpShop (scripts/market_kit.py loads this file from the shop folder) and by acp_shop.AcpCheckout.
"""
import importlib.util
import json
import os
import threading
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
MARKET = os.environ.get("MARKET_URL", "https://market.ourolabs.ai")
OUT_OF_STOCK = "Sorry, this item is currently out of stock. Thank you for your interest."
PAGE = 500              # rows /api/ledger returns per call
BACK = 4000             # rows to read back on a cold start or after a long gap: ~15 min at ~4 rows/s; orders last 10 min
MAX_ORDERS = 20000
EVERY = 10              # seconds between background ledger reads
WAIT = 2                # seconds a payment may spend on the ledger: waiting for the lock, then one page
ROUND = 3
SCAN_PAGES = 100        # pages per one-star pass (~4 min at ~2.2 s a page); the round's first pass (~1,300 pages) ~50 min
SCAN_EVERY = 60         # seconds between one-star passes once caught up


def _seller(path=None):
    try:
        with open(path or os.path.join(HERE, os.environ.get("SHOP_PRICING", "pricing.json")), encoding="utf-8") as f:
            s = json.load(f).get("seller") or {}
    except (OSError, ValueError, AttributeError):
        return {}
    return s if isinstance(s, dict) else {}


def one_star_at(path=None):
    """pricing.json seller.refuse_one_star_at: verified 1-star results that put a buyer on the list. 0 (or absent) is off."""
    at = _seller(path).get("refuse_one_star_at")
    return at if isinstance(at, int) and not isinstance(at, bool) and at > 0 else 0


def _names(seller, key):
    names = seller.get(key) or []
    return [n for n in names if isinstance(n, str) and n] if isinstance(names, list) else []


def refused_buyers(path=None, md=None):
    """pricing.json seller.refuse_buyers, plus (refuse_one_star_at) every buyer with that many verified 1-star results
    on this shop in .market/one_star.json, read now, except those in seller.refuse_exempt (대표님 11/05 12:27: q00).
    refuse_exempt only lifts the 1-star rule; a buyer named in refuse_buyers is refused anyway. [] when absent or
    unreadable."""
    seller = _seller(path)
    names = _names(seller, "refuse_buyers")
    at = one_star_at(path)
    if at:
        exempt = _names(seller, "refuse_exempt")
        names += [b for b, n in OneStars(md).counts().items() if n >= at and b not in names and b not in exempt]
    return names


def _get(path, timeout=10):
    with urllib.request.urlopen(MARKET.rstrip("/") + path, timeout=timeout) as r:
        return json.loads(r.read())


class OrderBuyers:
    """order id -> buyer from the ledger's "order" rows, read forward from the last row seen."""

    def __init__(self, get=_get):
        self.get = get
        self.seq = None
        self.buyers = {}
        self.lock = threading.Lock()

    def _read(self):
        """Background: from the last row seen up to the ledger's end (or BACK rows back on a cold start).
        The lock is taken per page, so a payment never waits for more than one page."""
        hi = self.get("/api/ledger?after=-1")[-1]["seq"]
        with self.lock:
            if self.seq is None or hi - self.seq > BACK:
                self.seq = max(0, hi - BACK)      # after=-1 would mean "the last row only"
        while self.seq < hi:
            with self.lock:
                more = self._page(10)
            if not more:
                break

    def _page(self, timeout):
        """One page after the last row seen; True when there may be more."""
        rows = self.get(f"/api/ledger?after={self.seq}", timeout=timeout)
        if rows:
            for e in rows:
                d = e.get("data") or {}
                if e.get("kind") == "order" and d.get("order") and d.get("buyer"):
                    self.buyers[d["order"]] = d["buyer"]
            self.seq = rows[-1]["seq"]
        while len(self.buyers) > MAX_ORDERS:
            self.buyers.pop(next(iter(self.buyers)))
        return len(rows or ()) == PAGE

    def buyer(self, order):
        """The buyer of this order, or None when the ledger does not have it (or cannot be read in time).
        An order is issued minutes before its payment, so with warm() running it is almost always cached already."""
        found = self.buyers.get(order)
        if found is not None or self.seq is None:     # cold: the background read has not finished its first pass
            return found
        if not self.lock.acquire(timeout=WAIT):  # a slow read elsewhere: do not hold the payment up
            return self.buyers.get(order)
        try:                                     # at most one short page here; the rest is the background's job
            if order not in self.buyers:
                self._page(WAIT)
            return self.buyers.get(order)
        finally:
            self.lock.release()

    def warm(self, on, log=print):
        """A daemon thread that keeps reading the ledger while on() is true (the refuse list is not empty)."""
        def loop():
            while True:
                try:
                    if on():
                        self._read()
                except Exception as e:
                    log(f"[acp] refuse_buyers: ledger read failed ({type(e).__name__}: {e})")
                time.sleep(EVERY)
        threading.Thread(target=loop, daemon=True, name="acp-gate-ledger").start()


def _md(md=None):
    return md or os.environ.get("MARKET_DIR") or os.path.join(HERE, ".market")


class OneStars:
    """Verified 1-star results on this shop over the whole of Round 3, read forward from the ledger into
    .market/one_star.json: {"after": last seq read, "urls": this shop's URLs, "rows": the 1-star results}.

    The shop's URL changes with every tunnel, so its URLs are the ones on results whose receipt names this shop
    (SHOP_CARD_NAME / SHOP_MARKET_NAME); a verified result always has the receipt its seller signed. Every verified
    1-star result is kept and counted against the URLs when read, so one seen before its URL was known still counts."""
    FILE = "one_star.json"

    def __init__(self, md=None, get=_get, names=None, log=print):
        self.path = os.path.join(_md(md), self.FILE)
        self.get = get
        self.log = log
        names = names or {os.environ.get("SHOP_CARD_NAME", "Nocha Furniture"), os.environ.get("SHOP_MARKET_NAME", "nocha12")}
        self.names = {n for n in names if isinstance(n, str) and n}

    def load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                st = json.load(f)
        except (OSError, ValueError):
            return None
        ok = isinstance(st, dict) and isinstance(st.get("after"), int) and isinstance(st.get("urls"), list) \
            and all(isinstance(u, str) for u in st["urls"]) and isinstance(st.get("rows"), list)
        return st if ok else None

    def counts(self):
        """buyer -> verified 1-star results on this shop's URLs."""
        st = self.load() or {}
        urls = set(st.get("urls") or ())
        n = {}
        for r in st.get("rows") or ():
            if isinstance(r, dict) and r.get("shop") in urls and isinstance(r.get("buyer"), str):
                n[r["buyer"]] = n.get(r["buyer"], 0) + 1
        return n

    def _start(self):
        """The seq just before Round 3's first entry (binary search; seq only grows)."""
        lo, hi = 0, self.get("/api/ledger?after=-1")[-1]["seq"] - 1
        while lo < hi:
            mid = (lo + hi) // 2
            page = self.get(f"/api/ledger?after={mid}", timeout=20)
            if page and page[0].get("round", 0) >= ROUND:
                hi = mid
            else:
                lo = mid + 1
        return lo

    def _take(self, e, urls, rows):
        """One ledger row into urls / rows. Only a verified result counts: its receipt is the one its seller signed,
        so an unverified row naming this shop cannot make another shop's URL ours."""
        d = e.get("data")
        if e.get("kind") != "result" or not isinstance(e.get("round"), int) or e["round"] < ROUND \
                or not isinstance(d, dict) or d.get("verified") is not True:
            return
        shop = d.get("shop").rstrip("/") if isinstance(d.get("shop"), str) else ""
        receipt = d.get("receipt") if isinstance(d.get("receipt"), dict) else {}
        if shop and isinstance(receipt.get("shop"), str) and receipt["shop"] in self.names:
            urls.add(shop)
        stars = d.get("stars")
        if stars == 1 and not isinstance(stars, bool) and isinstance(d.get("order"), str) and d["order"] \
                and isinstance(d.get("buyer"), str):
            rows[d["order"]] = {"buyer": d["buyer"], "order": d["order"], "shop": shop, "seq": e.get("seq"),
                                "ts": e.get("ts")}

    def scan(self, max_pages=SCAN_PAGES):
        """Reads up to max_pages new pages and saves what it read, even when a page fails. True when there may be more.
        A row it cannot read is skipped, so one odd row never stops the count."""
        st = self.load()
        if st is None:
            if os.path.exists(self.path):
                self.log(f"[acp] refuse_one_star: {self.path} unreadable; counting the round again")
            st = {"after": self._start(), "urls": [], "rows": []}
        urls, rows = set(st["urls"]), {r.get("order"): r for r in st["rows"] if isinstance(r, dict)}
        more = False
        try:
            for _ in range(max_pages):
                page = self.get(f"/api/ledger?after={st['after']}", timeout=20)
                if not isinstance(page, list):
                    raise ValueError(f"ledger answered {type(page).__name__}, not a list")
                for e in page:
                    if isinstance(e, dict):
                        try:
                            self._take(e, urls, rows)
                        except (TypeError, AttributeError, ValueError):
                            continue
                seqs = [e["seq"] for e in page if isinstance(e, dict) and isinstance(e.get("seq"), int)]
                if seqs:
                    st["after"] = max(st["after"], seqs[-1])
                more = len(page) == PAGE
                if not more:
                    break
        finally:
            st["urls"], st["rows"] = sorted(urls), sorted(rows.values(), key=lambda r: r.get("seq") or 0)
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = f"{self.path}.{os.getpid()}.tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(st, f, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
        return more

    def warm(self, at, refused, log=print):
        """A daemon thread that keeps the file current while at() (refuse_one_star_at) is set; refused() is the list."""
        def loop():
            while True:
                more = False
                try:
                    if at():
                        before = set(refused())
                        more = self.scan()
                        new = sorted(set(refused()) - before)
                        if new:
                            log(f"[acp] refuse_buyers: {new} reached {at()} verified 1-star results; refused from now")
                except Exception as e:
                    log(f"[acp] refuse_one_star: ledger read failed ({type(e).__name__}: {e})")
                time.sleep(1 if more else SCAN_EVERY)
        threading.Thread(target=loop, daemon=True, name="acp-gate-one-star").start()


def _measure():
    spec = importlib.util.spec_from_file_location("week08_measure", os.path.join(HERE, "measure.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class Gate:
    def __init__(self, orders=None, pricing=None, md=None, log=print, warm=False):
        self.orders = orders or OrderBuyers()
        self.pricing = pricing                  # tests pass a pricing.json path
        self.md = md
        self.log = log
        if warm:
            self.orders.warm(lambda: bool(refused_buyers(self.pricing, self.md)), log)
            OneStars(md, log=log).warm(lambda: one_star_at(self.pricing), lambda: refused_buyers(self.pricing, self.md), log)

    def refusal(self, order, session=None):
        """The message to answer this checkout with instead of settling it, or None to settle as usual."""
        names = refused_buyers(self.pricing, self.md)
        if not names or not isinstance(order, str) or not order:
            return None
        try:
            buyer = self.orders.buyer(order)
        except Exception as e:                  # the ledger is down: never block a sale over it
            self.log(f"[acp] refuse_buyers: ledger lookup for {order} failed ({type(e).__name__}: {e}); settling")
            return None
        if buyer not in names:
            return None
        s = session or {}
        rec = {"buyer": buyer, "order": order, "session": s.get("id"), "task": s.get("task"), "unit": s.get("unit"),
               "item": (s.get("line_items") or [{}])[0].get("id"), "price": (s.get("totals") or [{}])[0].get("amount"),
               "at": time.strftime("%Y-%m-%d %H:%M:%S")}
        try:
            _measure().write(_md(self.md), "refused", rec)
        except Exception as e:
            self.log(f"[acp] refuse_buyers: could not record ({type(e).__name__}: {e})")
        self.log(f"[acp] REFUSED buyer {buyer} order {order} ({rec['item']} at {rec['price']}): answered out of stock")
        return OUT_OF_STOCK


_gate = None


def start(log=print):
    """The kit's entry, called once when it loads this file: one Gate per process, its ledger read already running,
    so a payment never waits for the first read."""
    global _gate
    if _gate is None:
        _gate = Gate(log=log, warm=True)
    return _gate.refusal
