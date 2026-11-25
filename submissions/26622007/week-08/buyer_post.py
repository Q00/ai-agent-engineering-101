"""Round 3: the buyer's own purchase reports on the forum, written by code from what the market settled.

The number of posts a buyer writes is 10% of its rank (agent.md "Rankings"), and the Luna forum agent (forum.py) posts
about once in 20 minutes. After each paid deal review.py calls maybe_post: one line per settled deal of mine, at most
one every GAP_MINUTES, never while a buyer post still waits in the outbox (the kit sends one per 5 minutes and keeps the
rest). No model call: the post is the market's own receipt (price, list price, grade, claim, shelf life or warranty,
ledger seq). SKUs my own shop stocks are left out, so the reports do not send buyers to cheaper rivals of my shop.

Every other post is a [가격 동향] instead: for one SKU x grade x warranty, the market's settled payments of the last
TREND_HOURS (count, median, quartiles, share of list) against the TREND_HOURS before, with the first and last ledger seq.
On 11/05 inextro's posts of that kind scored 0.606 a post against 0.527 for our purchase records (45 and 97 posts in the
same hours, GPT Pro 11/05 priority 3); the two kinds alternate so their votes can be compared. Both kinds and forum.py's
posts share one slot: at most one buyer post per GAP_MINUTES (claim_slot).
"""
import fcntl
import json
import os
import statistics
import time

HERE = os.path.dirname(os.path.abspath(__file__))
GAP_MINUTES = 4.5        # 5 until 11/07 00:30: the kit keeps a post the market answers 429 and sends it when the 5 minutes
#                          are up, so a post written 30 s early is there for the slot (pending_buyer_post stops a second one);
#                          the kit allows one buyer post per 5 minutes; forum.py's posts share that slot (6 until 11/06 19:50:
#                          we wrote 46 buyer posts in the 6 h to 19:50, the top posters 64-72; posts are 10% of the rank)
STATE = "buyer_post_state.json"
FRESH_HOURS = 3          # a deal older than this is old news
LEDGER = "ledger_rows.jsonl"
TREND_HOURS = 3
TREND_MIN = 10           # settles of one SKU x grade x warranty in the last TREND_HOURS to say anything about its price
TREND_REPEAT = 6         # SKUs of the last trend posts that are not taken again
TAIL_BYTES = 24 << 20    # the end of ledger_rows.jsonl read for the trend (the file holds the whole round)


def load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def read_lines(path):
    """The JSON lines of a file; a half-written or bad line is skipped, not the whole file."""
    out = []
    try:
        with open(path, encoding="utf-8") as f:
            for x in f:
                try:
                    out.append(json.loads(x))
                except ValueError:
                    continue
    except OSError:
        pass
    return out


def pending_buyer_post(md):
    """A buyer post the kit has not sent yet (rate limit, or the kit is sending it now from outbox.taking): another one
    now would only queue behind it."""
    for name in ("outbox.jsonl", "outbox.taking"):
        for line in read_lines(os.path.join(md, name)):
            if isinstance(line, dict) and "post" in line and line.get("as") == "buyer":
                return True
    return False


def my_shop_skus(md, pricing=os.path.join(HERE, "pricing.json")):
    """SKUs my shop has on its shelf or means to restock (pricing.json restock lists): not advertised to buyers."""
    inv = load(os.path.join(md, "inventory.json"), {})
    skus = {u.get("sku") for u in inv.get("units") or [] if isinstance(u, dict)}
    r = load(pricing, {}).get("restock") or {}
    for key in ("priority_skus", "high_price_proven_skus"):
        skus |= {s for s in r.get(key) or [] if isinstance(s, str)}
    skus |= {s for s in r.get("sku_target_units") or {} if isinstance(s, str)}
    return skus


def shop_names(memory_path):
    """order id -> shop name, from the buyer's memory (settled.jsonl names a shop only by its URL)."""
    m = load(memory_path, {})
    return {t.get("order"): t.get("shop") for t in m.get("trades") or [] if isinstance(t, dict) and t.get("order")}


def text(d, shop, now):
    price, value = d["price"], d["value"]
    parts = [f"[구매 기록] {(d.get('name') or d['sku'])[:70]} ({d['sku']})",
             f"{price:,}원, 정가 {value:,}원 대비 {round(100 * (value - price) / value)}% 저렴"]
    if d.get("grade"):
        honest = {True: "정직", False: "거짓"}.get(d.get("claim_honest"))
        parts.append(f"등급 {d['grade']}" + (f" (주장 {d['claim']}, {honest})" if d.get("claim") and honest else ""))
    if d.get("kind") == "food" and d.get("expires_at"):
        parts.append(f"남은 유통기한 {max(0, int((d['expires_at'] - now) / 3600))}시간")
    elif d.get("warranty_hours"):
        parts.append(f"보증 {d['warranty_hours']:g}h")
    if shop:
        parts.append(f"판매: {shop}")
    parts.append(f"ledger #{d['ledger']}")
    return " | ".join(parts)


def pick(settled, posted, mine, now, names=None):
    """(deal, text) for the newest settled deal not posted yet that is worth telling: paid below list, not my shop's
    SKU, fresh, and with fields that make a post. (None, None) when there is none."""
    for d in reversed(settled):
        if not isinstance(d, dict) or d.get("ledger") in posted or d.get("sku") in mine or not isinstance(d.get("sku"), str):
            continue
        if not all(isinstance(d.get(k), (int, float)) for k in ("price", "value", "ledger", "time")):
            continue
        if d["value"] <= d["price"] or now - d["time"] > FRESH_HOURS * 3600:
            continue
        try:
            return d, text(d, (names or {}).get(d.get("order")), now)
        except (TypeError, ValueError):      # an odd field (warranty, expiry) skips this deal, not every later one
            continue
    return None, None


def recent_settles(md, since):
    """The ledger's settle rows from `since` on, read from the end of ledger_rows.jsonl, each seq once (a row can be
    written twice, as market_model.load_rows also allows for)."""
    out, seen = [], set()
    try:
        with open(os.path.join(md, LEDGER), "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - TAIL_BYTES))
            for raw in f.read().splitlines():
                if b'"settle"' not in raw:
                    continue
                try:
                    r = json.loads(raw)
                except ValueError:
                    continue
                if isinstance(r, dict) and r.get("k") == "settle" and isinstance(r.get("ts"), (int, float)) \
                        and r["ts"] >= since and isinstance(r.get("amount"), (int, float)) and isinstance(r.get("seq"), int) \
                        and r["seq"] not in seen:
                    seen.add(r["seq"])
                    out.append(r)
    except OSError:
        pass
    return out


def _warranted(r):
    return isinstance(r.get("warranty"), (int, float)) and r["warranty"] >= 6


def trend(md, now, mine, recent_skus):
    """(sku, text, refs) for a [가격 동향] post, or None: the SKU x grade x warranty with the most settles in the last
    TREND_HOURS (at least TREND_MIN), not my shop's and not one of the last trend posts."""
    cut = now - TREND_HOURS * 3600
    groups = {}
    for r in recent_settles(md, now - 2 * TREND_HOURS * 3600):
        if r["ts"] > now or not isinstance(r.get("sku"), str) or r.get("honest") is False:   # a false grade claim is no price of that grade
            continue
        g = groups.setdefault((r["sku"], r.get("grade") or "", _warranted(r)), ([], []))
        g[0 if r["ts"] >= cut else 1].append(r)
    catalog = load(os.path.join(md, "catalog.json"), {})
    best = None
    for (sku, grade, warranted), (recent, before) in groups.items():
        item = catalog.get(sku) if isinstance(catalog, dict) else None
        if len(recent) < TREND_MIN or sku in mine or sku in recent_skus or not isinstance(item, dict) \
                or not isinstance(item.get("list_price"), (int, float)) or item["list_price"] <= 0:
            continue
        if best is None or len(recent) > len(best[3]):
            best = (sku, grade, warranted, recent, before, item)
    if best is None:
        return None
    sku, grade, warranted, recent, before, item = best
    recent.sort(key=lambda r: r["seq"])
    paid = sorted(r["amount"] for r in recent)
    med = round(statistics.median(paid))
    q1, _, q3 = (round(x) for x in statistics.quantiles(paid, n=4, method="inclusive"))
    hm = lambda t: time.strftime("%H:%M", time.localtime(t))
    terms = [f"등급 {grade}"] if grade else []
    if item.get("kind") != "food":
        terms.append("보증 6h 이상" if warranted else "보증 6h 미만·없음")
    parts = [f"[가격 동향] {(item.get('name') or sku)[:60]} ({sku}) {'·'.join(terms)}".rstrip(),
             f"{time.strftime('%m/%d', time.localtime(cut))} {hm(cut)}–{hm(now)} KST {len(recent)}건 정산, 결제액 중앙값 {med:,}원 "
             f"(사분위 {q1:,}–{q3:,}원, 정가 {round(item['list_price']):,}원의 {round(100 * med / item['list_price'])}%)"]
    med0 = round(statistics.median(r["amount"] for r in before)) if before else 0
    if med0 > 0:
        parts.append(f"직전 {TREND_HOURS}시간 {len(before)}건, 중앙값 {med0:,}원 대비 {100 * (med - med0) / med0:+.1f}%")
    else:
        parts.append(f"직전 {TREND_HOURS}시간 정산 없음")
    first, last = recent[0]["seq"], recent[-1]["seq"]
    parts.append(f"원장 #{first}–#{last}")
    return sku, " | ".join(parts), [first, last]


def _save(path, state):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f)
    os.replace(tmp, path)


def claim_slot(md, now=None):
    """forum.py's post takes the same slot as ours: True (and the slot is taken) when no buyer post went out in the last
    GAP_MINUTES and none waits in the outbox. Never raises; False when it cannot tell."""
    now = time.time() if now is None else now
    try:
        with open(os.path.join(md, STATE + ".lock"), "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            path = os.path.join(md, STATE)
            state = load(path, {"at": 0, "posted": []})
            if now - state.get("at", 0) < GAP_MINUTES * 60 or pending_buyer_post(md):
                return False
            state["at"] = now
            _save(path, state)
            return True
    except Exception:  # noqa: BLE001
        return False


def maybe_post(md, settled_path, memory_path, now=None):
    """Writes at most one buyer post to md/outbox.jsonl. Returns the line, or None. Never raises."""
    now = time.time() if now is None else now
    try:
        with open(os.path.join(md, STATE + ".lock"), "w") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)      # another review hook is posting: let it
            except OSError:
                return None
            path = os.path.join(md, STATE)
            state = load(path, {"at": 0, "posted": []})
            if now - state.get("at", 0) < GAP_MINUTES * 60 or pending_buyer_post(md):
                return None
            mine = my_shop_skus(md)
            order = ("record", "trend") if state.get("last_kind") == "trend" else ("trend", "record")
            line = kind = measured = None
            for kind in order:
                if kind == "trend":
                    try:
                        t = trend(md, now, mine, set(state.get("trend_skus") or []))
                    except Exception:  # noqa: BLE001 - a failing trend leaves the purchase record its turn
                        t = None
                    if t is not None:
                        line = {"post": t[1], "refs": t[2], "as": "buyer"}
                        state["trend_skus"] = (list(state.get("trend_skus") or []) + [t[0]])[-TREND_REPEAT:]
                        break
                else:
                    d, post = pick(read_lines(settled_path), set(state.get("posted") or []), mine, now,
                                   shop_names(memory_path))
                    if d is not None:
                        try:
                            import buyer_post_market      # the market-median A/B; off (or broken) is the post as it was
                            post, measured = buyer_post_market.decorate(md, d, post, now)
                        except Exception:  # noqa: BLE001
                            measured = None
                        line = {"post": post, "refs": [d["ledger"]], "as": "buyer"}
                        state["posted"] = (list(state.get("posted") or []) + [d["ledger"]])[-500:]
                        break
            if line is None:
                return None
            with open(os.path.join(md, "outbox.jsonl"), "a", encoding="utf-8") as f:
                f.write(json.dumps(line, ensure_ascii=False) + "\n")
            if measured:                         # the post is in the outbox: measured even if the state write fails
                buyer_post_market.record(md, measured)
            state.update({"at": now, "last_kind": kind})
            _save(path, state)
            return line
    except Exception:  # noqa: BLE001 - a forum post never stops the review the kit waits for
        return None
