"""Buyer negotiation tactic experiment (person's decision 11/07): one tactic per order, by fixed weight
(buyer_tactic.json, read per order; market_buyer.py runs once per order, so no restart is needed):

  plain   60%  the old way (70% from the appeal restart, 11/07 ~09:20)
  appeal  20%  (10% from the restart) once, with my first offer: a short hardship line asking for a bit more off. One fixed line per shop
               (crc32 of the shop name). Never in reviews or forum posts (only negotiate() sends it).
  facts2  20%  (from 11/07 ~02:20) once, with my first offer, only when another shop's lowest price in the last
               24 h (ledger) is at least FACTS_GAP under the market average (market_stats): those two numbers, nothing
               else; otherwise the order goes the old way (tactic_used 0)
  facts        the first version (11/07 02:09 - ~02:20) also told my price limit; kept apart in the comparison

The offer number always comes first (sellers read the first number). Recorded per negotiation in buyer_deals
(tactic, tactic_used, secs, harness_calls), next to variant / mo_variant, so every combination can be compared.
Stop: alerts.watch -> guard(): the buyer's forum score on the board 0.01 or more under its value at the start, or a
forum post criticising my buyer (depts/forum_watch.py) -> appeal off at once (its 20% goes to plain) + Telegram.
The first stop (11/07 03:28) was a detector false alarm; appeal restarted at 10% with the stricter detector
(forum_watch.criticises), counting only criticism after appeal_since and a forum score drop from the restart value.
24 h after the start: interim results by tactic and by tactic x min-offers variant -> Telegram.
"""
import json
import os
import time
import zlib

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(HERE, "buyer_tactic.json")
TACTICS = ["plain", "appeal", "facts2", "facts"]
FACTS_GAP = 0.05
STORIES = [
    "Honestly, my business is close to going bankrupt this week, so every bit off really helps.",
    "Money is tight this week because baby formula ate most of my budget, so a little lower would help a lot.",
    "Someone in my family needs surgery soon and I am saving every won I can, so could you go a little lower?",
]
FORUM_DROP = 0.01
MIN_GROUP = 20          # metrics(): groups under this are left out of the common size (shown, marked small)


def rules():
    try:
        with open(PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save(r):
    with open(PATH, "w", encoding="utf-8") as f:
        json.dump(r, f, ensure_ascii=False)


def pick():
    import random
    r = rules()
    if not r.get("enabled"):
        return "plain", {}
    w = dict(r.get("weights") or {})
    if r.get("appeal_stopped"):
        w["plain"] = w.get("plain", 0) + w.pop("appeal", 0)
    w = {k: v for k, v in w.items() if v > 0}
    return random.choices(list(w), weights=list(w.values()))[0], w


def story(shop):
    return STORIES[zlib.crc32((shop or "").encode("utf-8")) % len(STORIES)]


def other_shop_low(sku, shop):
    """The lowest price another shop (not this one, not mine) sold this SKU for in the last 24 h, or None."""
    if not sku:
        return None
    from market_db import ME, connect
    db = connect()
    try:
        r = db.execute("SELECT MIN(amount) FROM settles WHERE sku=? AND ts>=? AND shop IS NOT NULL AND shop!=? "
                       "AND shop!=?", (sku, time.time() - 86400, shop or "", ME)).fetchone()
    finally:
        db.close()
    return int(r[0]) if r and r[0] else None


def line(tactic, shop, cap=None, avg=None, low=None):
    """The sentence sent after the offer number, or None."""
    if tactic == "appeal":
        return story(shop) + " Could you accept this?"
    if tactic == "facts2":                            # 11/07: no limit; only a real gap of >= FACTS_GAP
        if not (avg and low and low <= avg * (1 - FACTS_GAP)):
            return None
        return f"For reference: the market average is {int(avg)}; another shop sold it for {int(low)} in the last day."
    return None


# ---------- checks (alerts.watch, every restock run) ----------
def guard(send, log=print):
    r = rules()
    if not r.get("enabled") or r.get("appeal_stopped"):
        return None
    from market_db import connect
    why = None
    db = connect()
    try:
        row = db.execute("SELECT buyer FROM board_hist ORDER BY ts DESC LIMIT 1").fetchone()
    finally:
        db.close()
    forum = (json.loads(row[0]) or {}).get("forum") if row else None
    if forum is not None and r.get("forum_start") is not None and forum <= r["forum_start"] - FORUM_DROP:
        why = f"buyer forum score {r['forum_start']} -> {forum} (-{r['forum_start'] - forum:.3f})"
    crit = os.path.join(HERE, "logs", "forum_criticism.json")
    if not why and os.path.exists(crit):
        c = json.load(open(crit, encoding="utf-8"))
        hits = [x for x in c.get("buyer", []) if x.get("ts", 0) >= r.get("appeal_since", r.get("since", 0))]
        if hits:
            why = f"forum post #{hits[-1]['id']} by {hits[-1]['author']} criticises my buyer"
    if why:
        r.update(appeal_stopped=round(time.time()), appeal_stop_reason=why)
        save(r)
        import decisions
        decisions.record("buyer", "appeal_stopped", "buyer_tactic", None, f"stop condition: {why}; appeal -> plain")
        send("urgent", "appeal_stop", f"buyer 사정 호소 실험 즉시 중단: {why}. 사실 압박·기존 방식은 계속", log)
    return why


def metrics(since, until=None):
    """Per tactic and per tactic x mo_variant, on the same number of negotiations (the latest M of each)."""
    from market_db import ME, connect
    until = until or time.time()
    db = connect()
    try:
        rows = db.execute(
            "SELECT d.tactic, d.mo_variant, d.list_price, d.outcome, d.reason, d.offers, d.secs, d.harness_calls, "
            "(SELECT s.amount FROM settles s WHERE s.buyer=? AND s.sku=d.sku AND s.ts BETWEEN d.ts - 60 AND d.ts + 900 "
            "ORDER BY s.ts LIMIT 1) FROM buyer_deals d WHERE d.ts>=? AND d.ts<? AND d.tactic IS NOT NULL "
            "AND d.outcome!='skip' ORDER BY d.ts DESC", (ME, since, until)).fetchall()
    finally:
        db.close()

    def summary(groups):
        # 11/08 fix: the common size M is the smallest group with at least MIN_GROUP negotiations; a smaller group
        # (facts v1 ran ~11 min: 1 negotiation) used to set M = 1 for every group. Small groups: all their rows, "small"
        m = min((len(v) for v in groups.values() if len(v) >= MIN_GROUP), default=0)
        out = {}
        for k, x in groups.items():
            small = len(x) < MIN_GROUP
            y = x if small else x[:m]
            if not y:
                out[k] = {"n_all": len(x)}
                continue
            deals = [r for r in y if r[3] == "deal"]
            shares = [(r[2] - r[8]) / r[2] for r in y if r[8] is not None and r[2]]
            out[k] = {"n_all": len(x), "n": len(y), "saved": round(sum(shares) / (len(shares) + 100), 4),
                      "deal_rate": round(len(deals) / len(y), 3),
                      "offers": round(sum(r[5] or 0 for r in y) / len(y), 2),
                      "secs": round(sum(r[6] or 0 for r in y) / len(y), 1),
                      "timeouts": sum(1 for r in y if (r[4] or "") in ("time_limit", "error")),
                      "harness_calls": round(sum(r[7] or 0 for r in y) / len(y), 2)}
            if small:
                out[k]["small"] = True
        return out
    by_t, by_c = {t: [] for t in TACTICS}, {}
    for r in rows:
        if r[0] in by_t:
            by_t[r[0]].append(r)
        by_c.setdefault(f"{r[0]}|mo{r[1]}", []).append(r)
    return {"tactic": summary(by_t), "combo": summary(by_c)}


def interim(send, log=print):
    r = rules()
    if not r.get("since") or r.get("interim_sent") or time.time() < r["since"] + 86400:
        return None
    m = metrics(r["since"])
    r["interim_sent"] = round(time.time())
    save(r)
    names = {"facts": "facts_v1(한도 포함)", "facts2": "facts2"}
    fmt = lambda k, v: (f"{names.get(k.split('|')[0], k.split('|')[0])}{'|' + k.split('|')[1] if '|' in k else ''}: n {v.get('n', 0)}/{v['n_all']}, saved {v.get('saved')}, 성사 {v.get('deal_rate')}, "
                        f"흥정 {v.get('offers')}, {v.get('secs')}s, timeout {v.get('timeouts')}, 하네스 {v.get('harness_calls')}"
                        + (" (표본 부족: 전체 사용, 비교 제외)" if v.get("small") else ""))
    text = ("buyer 협상 실험 24시간 중간 결과 (같은 협상 수 기준)\n" + "\n".join(fmt(k, v) for k, v in m["tactic"].items())
            + "\n조합별:\n" + "\n".join(fmt(k, v) for k, v in sorted(m["combo"].items()))
            + (f"\n사정 호소 중단됨: {r.get('appeal_stop_reason')}" if r.get("appeal_stopped") else ""))
    import decisions
    decisions.record("buyer", "tactic_interim", "buyer_tactic", None, json.dumps(m["tactic"])[:300])
    send("daily", "tactic_interim", text, log)
    return m
