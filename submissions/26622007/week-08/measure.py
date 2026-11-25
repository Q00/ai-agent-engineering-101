"""Round 3 measurements the public ledger and the kit's files do not keep (review/W-20261104-1507 GPT Pro §5).

One jsonl file per kind under .market/measure/, one line per event, never read back by a decision:

    orders.jsonl          my buyer, one line per order: candidates, quotes and why each lost, the choice and how it was made,
                          stage times, Luna calls and cap errors of the run, the outcome, and for food the hours the order needs
    units_in.jsonl        each unit the first time inventory.json shows it: cost, grade, supplied_at, expires_at
    units_out.jsonl       each unit when it leaves inventory.json: sold (matched to my negotiation) or expired, with hours left
    stock.jsonl           a SKU's unit count each time it changes, 0 included: the stock-out spans
    supply_sent.jsonl     the lines this restock run handed the kit, with the window it was open or closed for
    supply_windows.jsonl  per supply window, once the next one opens: per SKU the target, the lines sent, the units supplied
    sales_6h.jsonl        each sale of my shop once 6 h (the break window) have passed: cost, price, broke, refunded, net
    forum_followup.jsonl  my posts at 10 min, 1 h and 6 h: likes, dislikes, score, or that the market no longer lists them
    llm_cap.jsonl         each time harness refused a call at its per-process hourly cap

Everything here is local file work (no market or model call) and swallows its own errors: a measurement never stops a
restock or a trade. tick() runs from restock.py once a minute; the 6 h sales need the ledger rows and run every 10 minutes.
"""
import json
import os
import sys
import time

DIR = "measure"
STATE = "state.json"
FOLLOW = (("10m", 600), ("1h", 3600), ("6h", 6 * 3600))
SALE_AGE = 6 * 3600 + 600      # the 6 h a sold unit can break in, and 10 min for the ledger row of the break
SALES_EVERY = 600
WINDOW_SLACK = 120             # units supplied this long after a window closed still belong to it


def write(md, name, rec):
    """Appends one line; on any error says so on stderr and goes on."""
    try:
        d = os.path.join(md, DIR)
        os.makedirs(d, exist_ok=True)
        line = (json.dumps({"ts": round(time.time(), 1), **rec}, ensure_ascii=False) + "\n").encode("utf-8")
        fd = os.open(os.path.join(d, name + ".jsonl"), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            os.write(fd, line)      # one write on O_APPEND: lines of overlapping buyer runs do not interleave
        finally:
            os.close(fd)
    except Exception as e:   # noqa: BLE001 - a measurement never stops what it measures
        print(f"[measure] {name}: {type(e).__name__}: {e}", file=sys.stderr, flush=True)


def _load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            x = json.load(f)
        return x if isinstance(x, type(default)) else default
    except (OSError, ValueError):
        return default


def _save(path, state):
    tmp = f"{path}.{os.getpid()}.tmp"     # two overlapping restock runs do not share a temp file
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)
    os.replace(tmp, path)


UNIT_KEYS = ("sku", "kind", "grade", "cost", "list_price", "supplied_at", "expires_at")


def _units(md, state, inv, now, sold_by_unit):
    cur = {u["unit"]: u for u in inv.get("units") or [] if isinstance(u, dict) and u.get("unit")}
    seen = state.setdefault("units", {})
    first = not seen and not state.get("started")
    for uid, u in cur.items():
        if uid not in seen:
            seen[uid] = {k: u.get(k) for k in UNIT_KEYS}
            write(md, "units_in", {"unit": uid, **seen[uid], "name": u.get("name"), "first_tick": first})
    for uid in [x for x in seen if x not in cur]:
        was = seen.pop(uid)
        n = sold_by_unit.get(uid)
        exp = was.get("expires_at")
        left = round((exp - now) / 3600, 2) if isinstance(exp, (int, float)) else None
        how = "sold" if n else "expired" if left is not None and left <= 0.05 else "gone"
        write(md, "units_out", {"unit": uid, **was, "how": how, "hours_held": round((now - (was.get("supplied_at") or now)) / 3600, 2),
                                "hours_left": left, "price": (n or {}).get("price"), "warranty": (n or {}).get("warranty")})
    counts = {}
    for u in cur.values():
        counts[u.get("sku")] = counts.get(u.get("sku"), 0) + 1
    stock = state.setdefault("stock", {})
    for sku in set(stock) | set(counts):
        n = counts.get(sku, 0)
        if stock.get(sku) != n:
            write(md, "stock", {"sku": sku, "qty": n, "was": stock.get(sku)})
        if n:
            stock[sku] = n
        elif sku in stock:
            del stock[sku]          # written once at 0; a SKU I no longer stock is not followed for ever
    return cur


def _summarize(md, state, prev, pricing):
    lo, hi = prev["opens_at"] - 300, (prev.get("closes_at") or prev["opens_at"]) + WINDOW_SLACK
    sent, supplied = {}, {}
    for s in state.get("sent", []):
        if lo <= s["at"] <= hi:
            sent[s["sku"]] = sent.get(s["sku"], 0) + s["qty"]
    for s in state.get("arrived", []):
        if lo <= s["at"] <= hi:
            supplied[s["sku"]] = supplied.get(s["sku"], 0) + 1
    targets = ((pricing.get("restock") or {}).get("sku_target_units") or {})
    write(md, "supply_windows", {"opens_at": prev["opens_at"], "closes_at": prev.get("closes_at"),
                                 "stock_at_open": prev.get("stock_at_open"),
                                 "lines": [{"sku": k, "target": targets.get(k), "sent": sent.get(k, 0),
                                            "supplied": supplied.get(k, 0)} for k in sorted(set(sent) | set(supplied), key=str)],
                                 "sent_units": sum(sent.values()), "supplied_units": sum(supplied.values())})
    state["sent"] = [s for s in state.get("sent", []) if s["at"] > hi]
    state["arrived"] = [s for s in state.get("arrived", []) if s["at"] > hi]


def _windows(md, state, inv, now, pricing):
    """While supply is closed inventory.json already names the next window, so a window is set aside the moment its
    opens_at changes and summed up once WINDOW_SLACK has passed after it closed (late supplies still count for it)."""
    for k in ("sent", "arrived"):
        state[k] = [s for s in state.get(k, []) if now - s["at"] < 3 * 3600]
    w = inv.get("restock") or {}
    if isinstance(w.get("opens_at"), (int, float)):
        cur = state.get("window")
        if cur and cur.get("opens_at") != w["opens_at"]:
            state.setdefault("closing", []).append(cur)
            cur = None
        if not cur:
            cur = state["window"] = {"opens_at": w["opens_at"], "closes_at": w.get("closes_at"), "stock_at_open": None}
        cur["closes_at"] = w.get("closes_at") or cur.get("closes_at")
        if w.get("open") and cur.get("stock_at_open") is None:
            cur["stock_at_open"] = dict(state.get("stock", {}))
    for prev in sorted(state.get("closing", []), key=lambda x: x["opens_at"]):
        if now > (prev.get("closes_at") or prev["opens_at"]) + WINDOW_SLACK:
            _summarize(md, state, prev, pricing)
            state["closing"].remove(prev)


def _sales(md, state, now, rows, sold_records):
    """Each settle of my shop (loss_watch's seq -> unit from the kit log) once 6 h have passed."""
    import market_model
    mine = _load(os.path.join(md, "loss_state.json"), {}).get("mine") or {}
    done = set(state.setdefault("sales_done", []))
    settles = {str(r["seq"]): r for r in rows if r.get("k") == "settle"}
    broke = {str(s["seq"]): b for b, s in market_model.link_broken(rows)}
    by_unit = {}
    for n in sold_records:
        if n.get("unit") and n.get("result") == "deal":
            by_unit[n["unit"]] = n
    for seq, m in mine.items():
        if not str(seq).isdigit():
            continue
        s = settles.get(seq)
        if seq in done or not s or now - (s.get("ts") or now) < SALE_AGE:
            continue
        done.add(seq)
        n = by_unit.get(m.get("unit")) or {}
        b = broke.get(seq)
        after = round((b["ts"] - s["ts"]) / 3600, 2) if b else None
        w = s.get("warranty") if isinstance(s.get("warranty"), (int, float)) else n.get("warranty")
        refunded = bool(b) and isinstance(w, (int, float)) and w >= after
        price, cost = s.get("amount") or m.get("amount") or 0, n.get("cost")
        write(md, "sales_6h", {"seq": int(seq), "order": s.get("order"), "sku": s.get("sku"), "unit": m.get("unit"),
                               "grade": s.get("grade"), "buyer": s.get("buyer"), "sold_at": s.get("ts"), "price": price,
                               "cost": cost, "warranty": w, "broke": bool(b), "broke_after_h": after, "refunded": refunded,
                               "net": (0 if refunded else price) - cost if isinstance(cost, (int, float)) else None})
    state["sales_done"] = sorted((x for x in done if str(x).isdigit()), key=int)[-20000:]


def _forum(md, state, now, me):
    posts = _load(os.path.join(md, "forum.json"), [])
    shown = {str(p.get("id")): p for p in posts if isinstance(p, dict) and p.get("author") == me}
    track, finished = state.setdefault("posts", {}), state.setdefault("posts_done", [])
    for pid, p in shown.items():
        if pid in track or pid in finished:
            continue
        at = p.get("ts") if isinstance(p.get("ts"), (int, float)) else now
        # a point already passed (posts there on the first run) is skipped, not written late under its label
        track[pid] = {"at": at, "role": p.get("role"), "done": [lb for lb, age in FOLLOW if now - at > age + 120]}
    for pid, t in list(track.items()):
        for label, age in FOLLOW:
            if label in t["done"] or now - t["at"] < age:
                continue
            t["done"].append(label)
            p = shown.get(pid)
            write(md, "forum_followup", {"post": int(pid) if pid.isdigit() else pid, "at": label, "role": t.get("role"),
                                         "age_min": round((now - t["at"]) / 60, 1), "listed": p is not None,
                                         **({k: p.get(k) for k in ("likes", "dislikes", "score")} if p else {})})
        if len(t["done"]) == len(FOLLOW):
            del track[pid]
            finished.append(pid)
    state["posts_done"] = finished[-3000:]


def tick(md, inv=None, sent=(), pricing=None, now=None, sold_records=None, rows=None, skip_sales=False, window_arm=None):
    """One measurement pass; sent is what this restock run hands the kit. Each part catches its own errors and the state
    is saved whatever failed, so a broken part does not make the others write the same lines again next minute.
    skip_sales: the 6 h sales (the ledger rows file) are left to the next pass, their clock untouched
    (restock_window_fast's run inside a supply window). window_arm: that study's arm for the run's window ("A", "B",
    "B-fast" for the run that sent a carry), kept on the supply_sent line; None (the study off) leaves the line as it was
    (independent review 0c27ae5 M3: the window's arm was nowhere in the measurements)."""
    now = now or time.time()
    try:
        path = os.path.join(md, DIR, STATE)
        os.makedirs(os.path.join(md, DIR), exist_ok=True)
        state = _load(path, {})
        inv = inv if inv is not None else _load(os.path.join(md, "inventory.json"), {})
        if (inv.get("round") or 0) < 3:
            return
    except Exception as e:   # noqa: BLE001
        print(f"[measure] tick failed: {type(e).__name__}: {e}", file=sys.stderr, flush=True)
        return

    def part(name, fn):
        try:
            fn()
        except Exception as e:   # noqa: BLE001 - a measurement never stops what it measures
            print(f"[measure] {name} failed: {type(e).__name__}: {e}", file=sys.stderr, flush=True)

    sold = {"records": sold_records}

    def units():
        if sold["records"] is None:
            import loss_watch
            sold["records"] = loss_watch.sold_records()
        by_unit = {n["unit"]: n for n in sold["records"] if isinstance(n, dict) and n.get("unit") and n.get("result") == "deal"}
        before = set(state.get("units", {}))
        cur = _units(md, state, inv, now, by_unit)
        if state.get("started"):            # the first pass sees the stock already there, not a supply
            state.setdefault("arrived", []).extend({"at": cur[u].get("supplied_at") or now, "sku": cur[u].get("sku")}
                                                   for u in cur if u not in before)
        state["started"] = state.get("started") or now

    def supply():
        w = inv.get("restock") or {}
        if sent:
            lines = [{"sku": b.get("sku"), "qty": b.get("qty")} for b in sent if isinstance(b, dict)]
            write(md, "supply_sent", {"window_open": bool(w.get("open")), "opens_at": w.get("opens_at"), "lines": lines,
                                      **({"window_fast": window_arm} if isinstance(window_arm, str) and window_arm else {})})
            state.setdefault("sent", []).extend({"at": now, "sku": x["sku"], "qty": x["qty"] or 0} for x in lines)
        _windows(md, state, inv, now, pricing or {})

    def sales():
        if skip_sales or now - state.get("sales_at", 0) < SALES_EVERY:
            return
        state["sales_at"] = now
        r = rows
        if r is None:
            import market_model
            r = market_model.load_rows(md)
        if sold["records"] is None:
            import loss_watch
            sold["records"] = loss_watch.sold_records()
        _sales(md, state, now, r, sold["records"])

    part("units", units)
    part("supply", supply)
    part("forum", lambda: _forum(md, state, now, os.environ.get("SHOP_MARKET_NAME", "nocha12")))
    part("sales", sales)
    part("save", lambda: _save(path, state))
