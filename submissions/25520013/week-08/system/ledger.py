"""R3 layer 1: append-only event ledger shared by the shop and the buyer. Schema: SCHEMA.md.

Every hook calls `Ledger().ingest_all(market_dir, ...)` first; each source is read from its cursor, so the cost
of one call is O(new input), not O(history). Settlement lines are copied to settle_archive.jsonl so they survive
the kit's 20 MB log rotation.
"""
import contextlib
import fcntl
import hashlib
import json
import math
import os
import time
from collections import OrderedDict
from pathlib import Path

DEDUPE_KEEP = 20_000            # recent keys kept in cursor.json (cursors do the rest)
UNIT_COST_KEEP = 20000        # unit -> cost map in the cursor (a round is a few thousand units)
BREAK_WINDOW_H = 6              # goods break up to 6 h after the sale (agent.md)
LEDGER_PAGES = 20               # at most 20 x 500 market entries per call: bounded latency


def own_owner_names(policy_path=None, state=None):
    """Lower-cased names (and runtime-resolved shop URLs, from owner.py's cache; no network here) of our GitHub
    owner's shops and buyers: a settle involving one of them is flagged own_owner so shop-side stats can leave our
    own buyer out (same owner cannot settle)."""
    p = Path(policy_path or os.environ.get("R3_POLICY", Path(__file__).resolve().parent / "policy.json"))
    try:
        o = json.loads(p.read_text(encoding="utf-8")).get("owner") or {}
    except (OSError, ValueError):
        return set()
    import owner as OW
    names, urls = OW.cached({"owner": o}, state)
    return ({OW.norm_name(n) for n in [o.get("github") or ""] + list(o.get("own_buyer_names") or [])} | names |
            {OW.norm_url(u) for u in urls}) - {""}


def own_shop_keys_safe(state=None, me_shop=None, policy_path=None):
    """v120c-B name: owner.own_shop_keys for our running shop, no network; {} keys on any error (exact-name match only)."""
    p = Path(policy_path or os.environ.get("R3_POLICY", Path(__file__).resolve().parent / "policy.json"))
    try:
        pol = json.loads(p.read_text(encoding="utf-8"))
        import owner as OW
        extra = [n for n in (me_shop, os.environ.get("R3_SHOP_NAME_PREV")) if n]   # r3c: name file rename keeps old name ours
        return OW.own_shop_keys(pol, state, extra_names=extra)
    except Exception:            # noqa: BLE001
        return {}


MARKET_KINDS = ("settle", "broken", "refund")
LOG_MARKS = ("[acp] settled", "[acp] paid", "[market] ledger #", "[acp] not paying", "[acp] checkout refused",
             "[acp] settle refused", "[acp] payment", "[acp] the checkout", "[sign]", "[supply]", "[broken]")


def state_dir():
    return Path(os.environ.get("R3_STATE", ".r3"))


def as_int(x):
    """Prices are whole credits; the kit's checkout refuses non-int prices (market_kit.py:407)."""
    if isinstance(x, bool):
        return None
    if isinstance(x, int):
        return x
    if isinstance(x, float) and math.isfinite(x) and x.is_integer():
        return int(x)
    if isinstance(x, str) and x.strip().lstrip("-").isdigit():
        return int(x.strip())
    return None


REASON_CODES = (("checkout refused", "checkout_refused"), ("does not match", "mismatch"),
                ("above the mandate", "over_cap"), ("acp.json", "no_acp"), ("not on the shop's host", "acp_host"),
                ("certificate", "cert"), ("shelf life", "shelf_life"), ("grade", "grade"), ("warranty", "warranty"),
                ("payment", "payment_failed"))   # first match wins: kit-level refusals before accept-policy reasons


def reason_code(reason):
    r = (reason or "").lower()
    return next((c for k, c in REASON_CODES if k in r), "other")


class Ledger:
    def __init__(self, root=None, me_shop=None, me_buyer=None):
        self.root = Path(root) if root else state_dir()
        self.root.mkdir(parents=True, exist_ok=True)
        self.events = self.root / "events.jsonl"
        self.cursor_file = self.root / "cursor.json"
        self.archive = self.root / "settle_archive.jsonl"
        self.me_shop = me_shop or os.environ.get("R3_SHOP_NAME")
        self.me_buyer = me_buyer or os.environ.get("R3_BUYER_NAME")
        self.own = own_owner_names(state=self.root)
        self.shop_keys = own_shop_keys_safe(self.root, self.me_shop)   # v120c-B name: stable own-shop ids, not only R3_SHOP_NAME
        self._lock_fd = None
        self.cur = {}
        self.added = 0
        self.new_events = []            # events appended by this instance (one hook run): bounded by its input

    # --- locking and cursor
    def __enter__(self):
        self._lock_fd = open(self.root / "lock", "a+")
        fcntl.flock(self._lock_fd, fcntl.LOCK_EX)
        self.cur = self._load_cursor()
        return self

    def __exit__(self, *exc):
        self._save_cursor()
        fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
        self._lock_fd.close()
        self._lock_fd = None

    def _load_cursor(self):
        try:
            c = json.loads(self.cursor_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            c = {}
        c.setdefault("seq", 0)
        c.setdefault("keys", [])
        c.setdefault("files", {})
        self._keys = OrderedDict.fromkeys(c["keys"])
        if c["seq"] == 0 and self.events.exists() and self.events.stat().st_size:
            c["seq"] = self._recover_seq()          # cursor lost: the ledger itself is the truth
        return c

    def _recover_seq(self):
        last = 0
        with open(self.events, "rb") as f:
            for line in f:
                with contextlib.suppress(ValueError):
                    e = json.loads(line)
                    last = e["seq"]
                    self._keys[e["key"]] = None
        while len(self._keys) > DEDUPE_KEEP:
            self._keys.popitem(last=False)
        return last

    def _save_cursor(self):
        self.cur["keys"] = list(self._keys)[-DEDUPE_KEEP:]
        tmp = self.cursor_file.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(self.cur, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.cursor_file)

    def last_seq(self):
        if self.cur:
            return self.cur["seq"]
        try:
            return json.loads(self.cursor_file.read_text(encoding="utf-8")).get("seq", 0)
        except (OSError, ValueError):
            return 0

    # --- write
    def append(self, type_, src, key, data, ts=None, role="market", mine=False):
        """Append one event unless its key was seen. Must be called inside `with Ledger() as L`."""
        assert self._lock_fd is not None, "use `with Ledger() as L:`"
        if key in self._keys:
            return None
        self.cur["seq"] += 1
        ev = {"seq": self.cur["seq"], "ts": ts if ts is not None else round(time.time(), 3), "type": type_,
              "src": src, "key": key, "role": role, "mine": bool(mine), "data": data}
        with open(self.events, "a", encoding="utf-8") as f:
            f.write(json.dumps(ev, ensure_ascii=False, separators=(",", ":")) + "\n")
        self._keys[key] = None
        if len(self._keys) > DEDUPE_KEEP * 2:
            while len(self._keys) > DEDUPE_KEEP:
                self._keys.popitem(last=False)
        if type_ == "restock" and isinstance(data, dict) and data.get("unit") and isinstance(data.get("unit_cost"), int):
            uc = self.cur["files"].setdefault("unit_costs", {})            # unit -> cost, for settle unit_cost
            uc[data["unit"]] = data["unit_cost"]
            if len(uc) > UNIT_COST_KEEP:
                for k in list(uc)[: len(uc) - UNIT_COST_KEEP]:
                    del uc[k]
        if type_ == "settle":
            self._archive({"from": "event", "seq": ev["seq"], "key": key, "data": data, "ts": ev["ts"]})
        self.added += 1
        self.new_events.append(ev)
        return ev

    def _archive(self, row):
        with open(self.archive, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")

    # --- read
    def iter_from(self, offset=0):
        """Yield (next_offset, event) for every event after byte `offset`. O(new events)."""
        if not self.events.exists():
            return
        with open(self.events, "rb") as f:
            f.seek(offset)
            while True:
                line = f.readline()
                if not line or not line.endswith(b"\n"):     # a half-written last line is read next time
                    return
                yield f.tell(), json.loads(line)

    # --- adapters
    def ingest_market(self, fetch, first_after=-500):
        """fetch(after) -> list of ledger entries (the market's GET /api/ledger?after=N, oldest first)."""
        after = self.cur.get("market_seq", first_after)
        n = 0
        for _ in range(LEDGER_PAGES):
            rows = fetch(after) or []
            for e in rows:
                n += self._market_entry(e)
            if not rows:
                break
            after = rows[-1]["seq"]
            self.cur["market_seq"] = after
            if len(rows) < 500:
                break
        return n

    def unit_cost(self, unit):
        """Cost of a unit our ledger has seen (restock event, opening stock or seller first-seen), else None."""
        if not unit:
            return None
        return self.cur["files"].get("unit_costs", {}).get(unit)

    def first_seen_unit(self, u, src="seller", now=None):
        """v1.3 (ML Engineer, COGS unknown): a unit that is supplied and sold between two inventory ingests never
        appears in inventory.json at ingest time, so it has no restock event and its sale has no cost. The seller
        calls this when it sells a unit it read from inventory.json: same event and key as ingest_inventory
        (`restock`, `unit:<id>`), so whichever comes first wins and the other is a no-op. Written before the
        market settle is ingested, so the cost layer precedes the sale in the ledger."""
        uid = u.get("unit")
        if not uid:
            return None
        cost = as_int(u.get("cost"))
        data = {k: u.get(k) for k in ("unit", "sku", "category", "kind", "grade", "supplied_at", "expires_at", "name")}
        data.update(cost=cost, unit_cost=cost, list_price=as_int(u.get("list_price")),
                    lot=f"{u.get('sku')}@{u.get('supplied_at')}", fifo=[u.get("sku"), u.get("supplied_at"), uid],
                    seen_by=src)
        return self.append("restock", src, f"unit:{uid}", data, u.get("supplied_at") or now, "shop", True)

    def _mine(self, d):
        names = {self.me_shop, self.me_buyer} - {None}
        if names & {d.get("buyer"), d.get("shop"), d.get("shop_name"), d.get("seller")}:
            return True
        # v120c-B name: a rename of R3_SHOP_NAME (display name) must not drop our sales; match the shop field
        # against owner.own_shop_keys (policy own_shop_names + cache names/urls/pubkeys + R3_SHOP_NAME)
        import owner as OW
        return any(OW.is_own_shop_ref(d.get(k), self.shop_keys) for k in ("shop", "shop_name", "seller"))

    def _market_entry(self, e):
        kind, d, seq = e.get("kind"), e.get("data") or {}, e.get("seq")
        key = f"market:{seq}"
        if kind in ("order", "result") and isinstance(e.get("ts"), (int, float)):
            # v1.9 (c): market-wide demand signal for the restock idle gate, independent of our stock (a sold-out
            # shop gets no settles but still sees orders). Kept in the cursor only: no event per market order.
            md = self.cur.setdefault("market_demand", {})
            md[kind + "s"] = md.get(kind + "s", 0) + 1
            md["last_" + kind + "_ts"] = max(e["ts"], md.get("last_" + kind + "_ts") or 0)
        if kind == "settle":
            data = norm_settle(d, ledger=seq)
            import owner as OW
            data["own_owner"] = bool(self.own & {OW.norm_name(data.get("buyer")), OW.norm_name(data.get("shop"))})
            uc = self.unit_cost(data.get("unit"))
            if uc is not None:
                data["unit_cost"] = uc                         # v1.3: cost of the sold unit when our ledger knows it
            return bool(self.append("settle", "market", key, data, e.get("ts"), "market", self._mine(d)))
        if kind == "broken":
            data = {"name": d.get("name"), "sku": d.get("item") or d.get("sku"), "buyer": d.get("buyer"),
                    "shop": d.get("shop"), "broke_at": e.get("ts"), "ledger": seq}
            return bool(self.append("broken", "market", key, data, e.get("ts"), "market", self._mine(d)))
        if kind == "storage" and self._mine(d):
            # v1.13 (M13): a storage fee the market charged our shop (format assumed: {shop, amount}; the mock's
            # --storage-scale rows). Not confirmed for the real market; nothing here depends on it being present.
            amt = as_int(d.get("amount"))
            if amt is None:
                return 0
            return bool(self.append("storage", "market", key, {"shop": d.get("shop"), "amount": amt, "ledger": seq},
                                    e.get("ts"), "shop", True))
        if kind == "refund":
            return bool(self.append("fail", "market", key, {"stage": "refund", "detail": d, "ledger": seq},
                                    e.get("ts"), "market", self._mine(d)))
        if kind == "result" and self._mine(d):
            n = 0
            role = "buyer" if d.get("buyer") == self.me_buyer else "shop"
            if not d.get("verified", True):
                failed = [k for k, v in (d.get("checks") or {}).items() if not v]
                n += bool(self.append("fail", "market", key, {"order": d.get("order"), "stage": "result",
                                                             "detail": failed, "ledger": seq}, e.get("ts"), role, True))
            if d.get("stars") is not None:
                n += bool(self.append("review", "market", key + ":review",
                                      {"order": d.get("order"), "shop": d.get("shop"), "stars": d.get("stars"),
                                       "review": (d.get("review") or "")[:300], "ledger": seq}, e.get("ts"), role, True))
            return n
        return 0

    def _tail_lines(self, path, name):
        """New complete lines of an append-only file since the cursor; restarts at 0 if the file shrank."""
        path = Path(path)
        if not path.exists():
            return []
        fc = self.cur["files"].setdefault(name, {"off": 0})
        size = path.stat().st_size
        if size < fc["off"]:
            fc["off"] = 0
        out = []
        with open(path, "rb") as f:
            f.seek(fc["off"])
            while True:
                line = f.readline()
                if not line or not line.endswith(b"\n"):
                    break
                fc["off"] = f.tell()
                out.append(line.decode("utf-8", "replace").strip())
        return [x for x in out if x]

    def ingest_payments(self, path):
        n = 0
        for line in self._tail_lines(path, "payments"):
            try:
                p = json.loads(line)
            except ValueError:
                continue
            order = p.get("order")
            base = {"order": order, "shop": p.get("shop"), "item": p.get("item"), "price": as_int(p.get("price"))}
            n += bool(self.append("trade", "payments", f"pay:{order}", base, None, "buyer", True))
            if p.get("paid"):
                data = dict(base, amount=base["price"], ledger=p.get("ledger"))
                n += bool(self.append("settle", "payments", f"pay:{order}:settle", data, None, "buyer", True))
            else:
                data = dict(base, reason=p.get("reason"), reason_code=reason_code(p.get("reason")))
                n += bool(self.append("reject", "payments", f"pay:{order}:reject", data, None, "buyer", True))
        return n

    def ingest_broken(self, path):
        """broken.jsonl has only name and broke_at. Attribute each line to our purchase of that name: the latest
        settled deal (from deal.json) of the same name at most BREAK_WINDOW_H before broke_at, not yet matched."""
        n = 0
        buys = self.cur["files"].setdefault("buys", [])
        for line in self._tail_lines(path, "broken"):
            try:
                b = json.loads(line)
            except ValueError:
                continue
            at = b.get("broke_at") or time.time()
            match = None
            for p in reversed(buys):
                if p["name"] == b.get("name") and not p.get("broken") and 0 <= at - p["ts"] <= BREAK_WINDOW_H * 3600 + 60:
                    match = p
                    break
            data = {"name": b.get("name"), "broke_at": b.get("broke_at"), "attributed": match is not None}
            if match:
                match["broken"] = True
                w = match.get("warranty_hours") or 0
                data.update(shop=match["shop"], sku=match.get("sku"), order=match.get("order"),
                            ledger=match.get("ledger"), price=match.get("price"),
                            under_warranty=bool(w) and at - match["ts"] <= w * 3600)
            n += bool(self.append("broken", "broken", f"broken:{b.get('name')}:{b.get('broke_at')}",
                                  data, b.get("broke_at"), "buyer", True))
        return n

    def ingest_deal(self, path):
        path = Path(path)
        if not path.exists():
            return 0
        raw = path.read_bytes()
        h = hashlib.sha256(raw).hexdigest()
        if self.cur["files"].get("deal_sha") == h:
            return 0
        self.cur["files"]["deal_sha"] = h
        try:
            d = json.loads(raw)
        except ValueError:
            return 0
        s = d.get("settled") or {}
        order = (d.get("order") or {}).get("order") if isinstance(d.get("order"), dict) else d.get("order")
        data = norm_settle(s, ledger=s.get("ledger"), order=order)
        o = d.get("order") if isinstance(d.get("order"), dict) else {}
        data.update({"use_within_hours": o.get("use_within_hours"), "budget": as_int(o.get("budget")),
                     "category": o.get("category") or data.get("category")})
        ev = self.append("settle", "deal", f"deal:{order}", data, None, "buyer", True)
        if ev and data.get("name"):
            buys = self.cur["files"].setdefault("buys", [])
            buys.append({"name": data["name"], "shop": data.get("shop"), "sku": data.get("sku"), "order": order,
                         "ledger": data.get("ledger"), "price": data.get("amount"),
                         "warranty_hours": data.get("warranty_hours"), "ts": d.get("settled_at") or ev["ts"]})
            del buys[:-2000]
        return bool(ev)

    def ingest_inventory(self, path, now=None):
        path = Path(path)
        try:
            inv = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return 0
        now = now or time.time()
        opening = "inv_units" not in self.cur["files"]         # first ingest: everything present is opening stock
        prev = self.cur["files"].get("inv_units", {})          # unit -> [sku, expires_at]
        units = {u["unit"]: u for u in inv.get("units") or [] if u.get("unit")}
        n = 0
        for uid, u in units.items():
            if uid in prev:
                continue
            data = {k: u.get(k) for k in ("unit", "sku", "category", "kind", "grade", "supplied_at", "expires_at", "name")}
            cost = as_int(u.get("cost"))
            data.update(cost=cost, unit_cost=cost, list_price=as_int(u.get("list_price")),
                        lot=f"{u.get('sku')}@{u.get('supplied_at')}",          # FIFO order: (sku, supplied_at, unit)
                        fifo=[u.get("sku"), u.get("supplied_at"), uid])
            if opening:
                data["opening"] = True
            n += bool(self.append("restock", "inventory", f"unit:{uid}", data, u.get("supplied_at"), "shop", True))
        for uid, row in prev.items():
            sku, exp, cost, cat = row[:4]
            sup = row[4] if len(row) > 4 else None             # v1.2 cursor rows carry supplied_at
            if uid not in units:
                why = "expired" if exp and exp <= now else "sold_or_seized"
                n += bool(self.append("unit_gone", "inventory", f"gone:{uid}",
                                      {"unit": uid, "sku": sku, "category": cat, "cost": cost, "unit_cost": cost,
                                       "supplied_at": sup, "expires_at": exp, "why": why}, None, "shop", True))
        self.cur["files"]["inv_units"] = {uid: [u.get("sku"), u.get("expires_at"), as_int(u.get("cost")), u.get("category"),
                                                u.get("supplied_at")] for uid, u in units.items()}
        self.cur["files"]["inv_balance"] = inv.get("balance")
        return n

    def ingest_forum(self, path):
        try:
            posts = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return 0
        if isinstance(posts, dict):
            posts = posts.get("posts") or []
        n = 0
        for p in sorted(posts, key=lambda p: str(p.get("id"))):
            data = {k: p.get(k) for k in ("id", "author", "role", "thread", "refs", "likes", "dislikes", "score")}
            data["text"] = (p.get("text") or "")[:500]
            n += bool(self.append("forum", "forum", f"forum:{p.get('id')}", data, p.get("ts"), "market", False))
        return n

    def archive_kit_log(self, log_path):
        """Copy settlement/refusal lines of logs/market-kit.log to settle_archive.jsonl, surviving rotation:
        the kit renames market-kit.log to .log.1 at 20 MB (market_kit.py:104)."""
        log_path = Path(log_path)
        rotated = log_path.with_suffix(".log.1")
        st = self.cur["files"].setdefault("kitlog", {"head": None, "off": 0})
        n = 0

        def head(p, k=256):
            """sha256 of the first k bytes (k <= 256): identifies a log file even while it is still growing."""
            with contextlib.suppress(OSError):
                with open(p, "rb") as f:
                    b = f.read(k)
                    return hashlib.sha256(b).hexdigest(), len(b)
            return None, 0

        def read(p, off):
            nonlocal n
            with open(p, "rb") as f:
                f.seek(off)
                while True:
                    line = f.readline()
                    if not line or not line.endswith(b"\n"):
                        return f.tell() - len(line)
                    s = line.decode("utf-8", "replace").rstrip("\n")
                    if any(m in s for m in LOG_MARKS):
                        self._archive({"from": "kitlog", "file": p.name, "line": s, "seen": round(time.time(), 3)})
                        n += 1

        if not log_path.exists():
            return 0
        k = st.get("head_len", 256)
        same = st["head"] is not None and head(log_path, k)[0] == st["head"] and log_path.stat().st_size >= st["off"]
        if not same:
            if st["head"] and rotated.exists() and head(rotated, k)[0] == st["head"]:
                read(rotated, st["off"])                # finish the file that was rotated away
            st["off"] = 0
        st["head"], st["head_len"] = head(log_path)
        st["off"] = read(log_path, st["off"])
        return n

    def ingest_all(self, market_dir=None, fetch=None, kit_log=None):
        md = Path(market_dir or os.environ.get("MARKET_DIR", ".market"))
        counts = {}
        counts["inventory"] = self.ingest_inventory(md / "inventory.json")   # v1.3: cost layers before sales
        if fetch is not None:
            counts["market"] = self.ingest_market(fetch)
        counts["payments"] = self.ingest_payments(md / "payments.jsonl")
        counts["deal"] = self.ingest_deal(md / "deal.json")
        counts["broken"] = self.ingest_broken(md / "broken.jsonl")
        counts["forum"] = self.ingest_forum(md / "forum.json")
        if kit_log:
            counts["kitlog"] = self.archive_kit_log(kit_log)
        return counts


def norm_settle(d, ledger=None, order=None):
    out = {k: d.get(k) for k in ("order", "buyer", "shop", "unit", "name", "grade", "claim", "claim_honest",
                                 "warranty_hours", "expires_at", "category", "kind")}
    out["order"] = order or d.get("order")
    out["sku"] = d.get("sku") or d.get("item")
    out["amount"] = as_int(d.get("amount") if d.get("amount") is not None else d.get("price"))
    out["value"] = as_int(d.get("value"))
    out["list_price"] = as_int(d.get("list_price"))
    out["ledger"] = d.get("ledger", ledger)
    return out


def market_fetcher(base, timeout=15):
    """GET /api/ledger?after=N on the market (read only)."""
    import httpx

    def fetch(after):
        r = httpx.get(f"{base.rstrip('/')}/api/ledger", params={"after": after}, timeout=timeout)
        return r.json() if r.status_code == 200 else []
    return fetch


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="ingest every source once and print the counts")
    ap.add_argument("--market-dir", default=os.environ.get("MARKET_DIR", ".market"))
    ap.add_argument("--market", default=None, help="market base URL for GET /api/ledger (omit: no network)")
    ap.add_argument("--kit-log", default="logs/market-kit.log")
    a = ap.parse_args()
    with Ledger() as L:
        c = L.ingest_all(a.market_dir, market_fetcher(a.market) if a.market else None, a.kit_log)
        print(json.dumps({"counts": c, "seq": L.last_seq()}))
