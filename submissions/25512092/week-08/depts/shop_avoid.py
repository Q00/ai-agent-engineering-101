"""Shop avoid rules for the buyer (person's decision 11/11 ~21:00), on top of the permanent "cheated me before" block
(kept: one item_mismatch / injection / receipt_mismatch in memory.jsonl still blocks a shop for good). Rules in
shop_avoid.json (read every order; off without it); state in logs/shop_avoid_state.json:
  item_mismatch   my orders flagged item_mismatch (11/11 ~21:20: or receipt_mismatch, the amount) at the shop in the
                  last 24 h: 1 -> the shop goes to the back of the
                  candidates for 24 h, 2+ -> avoided for 24 h (from the event that tripped it)
  grade claims    the ledger (market.db settles, all buyers): the shop's grade claims in the last 48 h, claim_honest
                  false share > 20% with at least 5 claims -> avoided for 24 h
  injection       the shop's messages flagged as an injection (logs/shop_events.jsonl, written by the buyer, plus
                  memory.jsonl injection flags) 2+ times in 24 h -> avoided for 24 h
  missed          an order where an avoided shop had the SKU in stock and the order ended without a deal (or with no
                  shop left) -> logs/shop_avoid_missed.jsonl (the 08:00 / 20:00 summary counts them)
  guard           like the shop_order guard: >= 2 h after the start, the last 2 h (>= 100 orders, logs/soft_neg.jsonl)
                  close rate < the 6 h before the start - 5 %p, or paid deals per hour < 80% of it -> a warning
                  (Telegram, at most every 6 h); the rules stay on, the person decides
"""
import json
import os
import sqlite3
import time
from datetime import datetime

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RULES = os.path.join(HERE, "shop_avoid.json")
STATE = os.path.join(HERE, "logs", "shop_avoid_state.json")
EVENTS = os.path.join(HERE, "logs", "shop_events.jsonl")
MISSED = os.path.join(HERE, "logs", "shop_avoid_missed.jsonl")
MEMORY = os.path.join(HERE, "memory.jsonl")
SN_LOG = os.path.join(HERE, "logs", "soft_neg.jsonl")
DB = os.path.join(HERE, "market.db")
EVERY_S = 120
DEFAULTS = {"avoid_h": 24, "item_window_h": 24, "item_avoid_n": 2, "grade_window_h": 48, "grade_min_claims": 5,
            "grade_max_false": 0.20, "inj_window_h": 24, "inj_avoid_n": 2,
            "guard_after_h": 2, "guard_h": 2, "guard_min_orders": 100, "rate_drop": 0.05, "dph_drop": 0.20,
            "missed_h": 2, "missed_min": 5}


def rules(path=None):
    try:
        with open(path or RULES, encoding="utf-8") as f:
            r = json.load(f)
    except (OSError, ValueError):
        return {}
    return dict(DEFAULTS, **r) if r.get("enabled") else {}


def _load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _save(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)
    os.replace(tmp, path)


def _jsonl(path, t0):
    out = []
    try:
        with open(path, encoding="utf-8") as f:
            for ln in f:
                try:
                    e = json.loads(ln)
                except ValueError:
                    continue
                if (e.get("ts") or 0) >= t0:
                    out.append(e)
    except OSError:
        pass
    return out


def released(path=None):
    """Shops out of the permanent "cheated me before" block (11/11 ~21:20, person): shop_avoid.json released {shop: ts}."""
    try:
        with open(path or RULES, encoding="utf-8") as f:
            return set((json.load(f).get("released") or {}).keys())
    except (OSError, ValueError):
        return set()


def note_event(shop, kind, text="", path=None, now=None):
    """The buyer flagged a shop's message (kind 'injection')."""
    try:
        with open(path or EVENTS, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": round(now or time.time(), 1), "shop": shop, "kind": kind, "text": text[:200]},
                               ensure_ascii=False) + "\n")
    except OSError:
        pass


def memory_flags(t0, flag, path=None):
    """[(ts, shop)] of memory.jsonl rows with this flag since t0 (the file is read from the end)."""
    out = []
    try:
        with open(path or MEMORY, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 8_000_000))
            lines = f.read().decode("utf-8", errors="replace").splitlines()
    except OSError:
        return out
    for ln in lines:
        if f'"{flag}"' not in ln:
            continue
        try:
            m = json.loads(ln)
            t = datetime.strptime(m["ts"], "%Y-%m-%d %H:%M:%S").timestamp()
        except (ValueError, KeyError):
            continue
        if t >= t0 and m.get("flag") == flag:
            out.append((t, m.get("shop")))
    return out


def grade_claims(t0, db_path=None):
    """{shop: (claims, false)} in the ledger since t0 (all buyers)."""
    d = sqlite3.connect(f"file:{db_path or DB}?mode=ro", uri=True, timeout=60)
    try:
        rows = d.execute("SELECT shop, COUNT(*), SUM(claim_honest=0) FROM settles WHERE ts>=? AND claim_honest IS NOT "
                         "NULL AND shop IS NOT NULL GROUP BY shop", (t0,)).fetchall()
    finally:
        d.close()
    return {s: (n, f or 0) for s, n, f in rows}


def _trip(state, shop, kind, at, until, reason):
    cur = state["shops"].get(shop)
    rank = {"avoid": 2, "back": 1}
    if cur and cur["until"] > at and (rank[cur["kind"]] > rank[kind] or (cur["kind"] == kind and cur["until"] >= until)):
        return
    state["shops"][shop] = {"kind": kind, "at": round(at), "until": round(until), "reason": reason}


def _drop_cleared(state, rules_path=None):
    """11/12 ~09:30 (person): shop_avoid.json cleared {shop: ts} - an entry tripped by an event at or before ts is
    dropped (real_yellow: the item mismatch was our buyer accepting without a check). Applied on every read, so a
    process that saves an older state cannot bring it back."""
    cl = _load(rules_path or RULES, {}).get("cleared") or {}
    for s, ts in cl.items():
        v = state.get("shops", {}).get(s)
        if v and v.get("at", 0) <= ts:
            state["shops"].pop(s, None)
    return state


def update(now=None, r=None, db_path=None, mem_path=None, ev_path=None, state_path=None, force=False):
    """The avoid state, recomputed at most every EVERY_S."""
    now = now or time.time()
    r = rules() if r is None else r
    sp = state_path or STATE
    state = _drop_cleared(_load(sp, {"shops": {}, "checked": 0}))
    state.setdefault("shops", {})
    if not r or (not force and now - state.get("checked", 0) < EVERY_S):
        return state
    H = 3600
    av = r["avoid_h"] * H
    # item mismatch: 1 -> back, 2+ -> avoid (24 h from the event that tripped it)
    ev = {}                                       # 11/11 ~21:20: an amount mismatch (receipt above the agreed price)
    t0 = now - r["item_window_h"] * H             # counts like an item mismatch (the released shops' 24 h rule)
    for t, s in sorted(memory_flags(t0, "item_mismatch", mem_path) + memory_flags(t0, "receipt_mismatch", mem_path)):
        ev.setdefault(s, []).append(t)
    for s, ts in ev.items():
        if len(ts) >= r["item_avoid_n"]:
            _trip(state, s, "avoid", ts[r["item_avoid_n"] - 1], ts[r["item_avoid_n"] - 1] + av,
                  f"품목·금액 불일치 {len(ts)}건 (최근 {r['item_window_h']}시간)")
        _trip(state, s, "back", ts[0], ts[0] + av, f"품목·금액 불일치 {len(ts)}건 (최근 {r['item_window_h']}시간): 후보 맨 뒤")
    # grade claims in the ledger
    try:
        for s, (n, f) in grade_claims(now - r["grade_window_h"] * H, db_path).items():
            if n >= r["grade_min_claims"] and f / n > r["grade_max_false"]:
                _trip(state, s, "avoid", now, now + av,
                      f"등급 주장 거짓 {f}/{n} ({f / n:.0%} > {r['grade_max_false']:.0%}, 최근 {r['grade_window_h']}시간 원장)")
    except sqlite3.Error:
        pass
    # injections
    inj = {}
    for e in _jsonl(ev_path or EVENTS, now - r["inj_window_h"] * H):
        if e.get("kind") == "injection":
            inj.setdefault(e["shop"], set()).add(round(e["ts"]))
    for t, s in memory_flags(now - r["inj_window_h"] * H, "injection", mem_path):
        if not any(abs(t - x) < 300 for x in inj.get(s, ())):      # the same negotiation counted once
            inj.setdefault(s, set()).add(round(t))
    for s, ts in inj.items():
        ts = sorted(ts)
        if len(ts) >= r["inj_avoid_n"]:
            _trip(state, s, "avoid", ts[r["inj_avoid_n"] - 1], ts[r["inj_avoid_n"] - 1] + av,
                  f"인젝션 {len(ts)}번 (최근 {r['inj_window_h']}시간)")
    state["shops"] = {s: v for s, v in state["shops"].items() if v["until"] > now - 7 * 86400}
    _drop_cleared(state)
    state["checked"] = round(now)
    _save(sp, state)
    return state


def status(keys, state, now=None):
    """('avoid' | 'back' | None, reason) for a shop by any of its names."""
    now = now or time.time()
    for k in keys:
        v = state.get("shops", {}).get(k)
        if v and v["until"] > now:
            return v["kind"], f"{v['reason']}, {datetime.fromtimestamp(v['until']):%m-%d %H:%M}까지"
    return None, None


def current(state=None, now=None):
    """{shop: entry} of the shops avoided or sent to the back now."""
    now = now or time.time()
    state = _drop_cleared(_load(STATE, {"shops": {}})) if state is None else state
    return {s: v for s, v in state.get("shops", {}).items() if v["until"] > now}


def note_missed(order, item, shops, outcome, path=None, back=()):
    """An order that ended without a deal while avoided shops were left out, or back-of-the-list shops were never
    tried (11/12 ~09:10: both count)."""
    try:
        with open(path or MISSED, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": round(time.time(), 1), "order": order, "item": item, "avoided": list(shops),
                                "back": list(back), "outcome": outcome}, ensure_ascii=False) + "\n")
    except OSError:
        pass


def missed_since(t0, path=None):
    return _jsonl(path or MISSED, t0)


def summary_line(now=None, t0=None):
    """The 08:00 / 20:00 summary: shops avoided / at the back now with the reason, orders missed since t0."""
    now = now or time.time()
    t0 = t0 or now - 12 * 3600
    cur = current(now=now)
    miss = [m for m in missed_since(t0) if m.get("ts", 0) <= now]   # 11/12: a summary for 08:00 stops at 08:00
    per = {}
    for m in miss:
        for s in (m.get("avoided") or []) + (m.get("back") or []):
            per[s] = per.get(s, 0) + 1
    if not cur and not miss:
        return "회피 가게 없음 (영구 차단 '이전에 속임' 가게는 별도)"
    parts = [f"{s} [{'회피' if v['kind'] == 'avoid' else '맨 뒤'}] {v['reason']}, "
             f"{datetime.fromtimestamp(v['until']):%m-%d %H:%M}까지, 놓친 주문 {per.get(s, 0)}" for s, v in
             sorted(cur.items(), key=lambda x: -x[1]["until"])]
    return (f"회피·맨 뒤 가게 {len(cur)}곳, 회피·맨 뒤 때문에 놓친 주문 {len(miss)}건 (최근 {(now - t0) / 3600:.0f}시간): "
            + ("; ".join(parts) if parts else "지금 회피 중인 가게 없음"))


def _sn_stats(t0, t1, path=None):
    R = _jsonl(path or SN_LOG, t0)
    R = [e for e in R if e.get("ts", 0) < t1]
    h = max(1e-6, (t1 - t0) / 3600)
    return {"n": len(R), "rate": sum(1 for e in R if e.get("deal")) / len(R) if R else None,
            "dph": sum(1 for e in R if e.get("paid_ok")) / h}


def guard(now=None, r=None, sn_path=None, send=None, rules_path=None, missed_path=None):
    """The warning (person 11/12 ~09:10; replaces the close-rate / deals-per-hour drop warning of 11/11): only when
    orders missed because of the avoid / back rules (logs/shop_avoid_missed.jsonl) reach missed_min (5) in the last
    missed_h (2) hours; at most one warning per missed_h. The rules themselves are unchanged. Returns the reason or
    None. (sn_path is kept for old callers and unused.)"""
    now = now or time.time()
    rp = rules_path or RULES
    raw = _load(rp, {})
    r = rules(rp) if r is None else r
    if not r or not raw.get("since"):
        return None
    if now - raw.get("guard_checked", 0) < 600:
        return None
    h, need = r["missed_h"], r["missed_min"]
    t0 = max(now - h * 3600, raw.get("missed_from") or 0)  # 11/12 ~09:20: rows before the narrowed back rule ignored
    miss = [m for m in missed_since(t0, missed_path) if m.get("ts", 0) <= now]
    raw["guard_checked"] = round(now)
    why = None
    if len(miss) >= need:
        per = {}
        for m in miss:
            for s in (m.get("avoided") or []) + (m.get("back") or []):
                per[s] = per.get(s, 0) + 1
        top = ", ".join(f"{s} {n}" for s, n in sorted(per.items(), key=lambda x: -x[1])[:5])
        why = f"회피·맨 뒤 때문에 놓친 주문 최근 {h}시간 {len(miss)}건 (기준 {need}건 이상; 가게별 {top})"
    if why and now - raw.get("missed_warned", 0) >= h * 3600:
        raw["missed_warned"] = round(now)
        try:
            if send is None:
                import alerts
                send = alerts.send
            send("urgent", "shop_avoid_drop", f"가게 회피 규칙: {why}. 규칙은 켜진 채, 끌지 판단 필요. 지금 "
                 + summary_line(now, raw["since"]))
        except Exception:
            pass
    _save(rp, raw)
    return why
