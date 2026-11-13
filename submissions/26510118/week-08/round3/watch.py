"""The market watch (11/5 17:10, the person's call: "the professor may change a rule or the market at any time and we
run with big risks; analyse every 20 minutes, like the restock cycle, and say at once when something big changes").
On 11/5 16:12-16:19 KST the market answered with no shops at all (a restart with no patch note), and afterwards 34
of 81 shops were alive.

Every 20 minutes, after the ledger is collected, it compares what it sees with what it saw last time and with the
two hours before, and writes analysis/watch.json {"at", "alerts": [{"kind", "text"}], "facts"} (one line of it to
watch_log.jsonl). Code only, no model. An alert is:
    rules     /agent.md or the rules page (patch notes) changed: the newest patch and the changed lines
    shape     new or missing fields in the board, a shop or player row, or inventory.json
    params    storage_per_day, or the supply window's length, changed
    outage    the kit saw no shops in the market, or the market forgot our buyer, in the last 20 minutes
    volume    the market's settles in the last 20 minutes under half or over twice the median 20 minutes before
    alive     the shops alive on the board moved by a quarter or more since last time
    ours      our shop's or our buyer's deals under 40% of their own median while the market held
    rank      our class place moved
    stars     our stars fell 0.03 or more since last time
    price     other shops' paid share of list on our line's products moved 0.05 or more from the two hours before
    surge     a line product surged 1.25x or more
And three slower ones (11/5 18:00, the person's call: hidden changes to how the market works), each over the last
3 hours against the 21 before (breakage: purchases 6-18 h old against all earlier), raised once when it starts:
    taste     our buyer's orders: the category mix (overlap under 0.8), the budget over the median list (moved 0.15),
              the orders an hour (under 0.6x or over 1.6x)
    trend     the whole market: the paid share of list (moved 0.03 from the 24 h summary), the category mix (overlap
              under 0.85)
    breakage  our buyer's goods of one grade breaking at twice their rate before (5 breaks or more)
The integrated analysis reads brief(): these facts and the alerts of its last 3 hours.
"""
import difflib
import hashlib
import json
import re
import statistics as st
import time
from collections import Counter
from html import unescape
from pathlib import Path

import common
import ledger
import restock

BIN = 1200                     # seconds: the restock cycle
BEFORE = 6                     # bins before the last one: two hours
VOLUME_LOW, VOLUME_HIGH, VOLUME_MIN = 0.5, 2.0, 100
ALIVE_MOVE, ALIVE_MIN = 0.25, 5
OURS_LOW, MARKET_HELD = 0.4, 0.7
STARS_DROP = 0.03
PRICE_MOVE, PRICE_N = 0.05, 30
SURGE_HIGH = 1.25
DIFF_LINES = 40           # 12 until 11/6: Patch 3.4's "Surplus -> Saved" line fell outside them
KIT_LOG = Path(__file__).resolve().parent / "logs" / "market-kit.log"
OUTAGE_SIGNS = ("no shops in the market yet", "buyer token refused", "[market] order failed")
RECENT, BASE = 3 * 3600, 24 * 3600
TASTE_OVERLAP, BUDGET_MOVE, RATE_LOW, RATE_HIGH, TASTE_MIN = 0.8, 0.15, 0.6, 1.6, 100
TREND_PRICE_MOVE, TREND_OVERLAP, TREND_MIN = 0.03, 0.85, 500
BREAK_FROM, BREAK_TO, BREAK_MIN, BREAK_TIMES = 18 * 3600, 6 * 3600, 5, 2.0   # goods break within 6 h of the sale
BRIEF_HOURS = 3
# 11/12 (the person's call): the stop-loss lines of the "retake 1st" changes (0de7635 and after). Each raises an alert
# once when it is crossed (again after it has cleared); a person then reverts that one change.
SL_FOOD_EXPIRY, SL_FOOD_RECEIVED_MIN = 0.02, 100   # S1 (food for 3 h of its pace): food expired / received over 12 h
SL_FOOD_STALE_HOURS, SL_FOOD_STALE_N = 6, 60       # S2 (food priced higher): food units on hand 6 h or longer. Not 3 h:
                                                   # S1 holds 3 h of a product's pace on purpose
SL_DEAR, SL_DEAR_HOURS, SL_DEAR_COST = 500_000, 6, 150_000_000   # S3 (a wider line): 500k+ goods held 6 h+, at cost
SL_DEALS_H, SL_ORDERS_MIN = 25, 30                 # B1 (the margin floor since 11/13): our buyer's deals an hour, 3 h
                                                   # (40 until 11/13 11:40: the floor of 0.70 trades some deals away)
SL_SAVED_HOURS = 12                                # B1: saved not up over this long (scoreboard history)


def stoploss(now: float, flow: list, units: list, orders: list, catalog: dict, history: list) -> tuple[dict, dict]:
    """(the numbers, {flag: text}) for the stop-loss lines above."""
    kind = lambda s: (catalog.get(s) or {}).get("kind")
    lp = lambda s: (catalog.get(s) or {}).get("list_price") or 0
    f, flags = {}, {}
    food12 = [x for x in flow if (x.get("ts") or 0) >= now - 12 * 3600 and kind(x.get("sku")) == "food"]
    rec, exp = sum(1 for x in food12 if x.get("what") == "received"), sum(1 for x in food12 if x.get("what") == "expired")
    f["food_expired_12h"] = [exp, rec]
    if rec >= SL_FOOD_RECEIVED_MIN and exp / rec > SL_FOOD_EXPIRY:
        flags["stoploss:S1"] = (f"S1: {exp} of {rec} food units received in 12 h expired (over {SL_FOOD_EXPIRY:.0%}): "
                                "revert restock.FOOD_COVER_HOURS")
    age = lambda u: now - (u.get("supplied_at") or now)
    stale = sum(1 for u in units if kind(u.get("sku")) == "food" and age(u) >= SL_FOOD_STALE_HOURS * 3600)
    f["food_on_hand_6h"] = stale
    if stale >= SL_FOOD_STALE_N:
        flags["stoploss:S2"] = (f"S2: {stale} food units on hand {SL_FOOD_STALE_HOURS} h or longer: the bolder food asks "
                                "may be too high (experiments.FOOD_ARMS)")
    dear = sum(u.get("cost") or 0 for u in units if lp(u.get("sku")) >= SL_DEAR and age(u) >= SL_DEAR_HOURS * 3600)
    f["dear_held_6h_cost"] = int(dear)
    if dear >= SL_DEAR_COST:
        flags["stoploss:S3"] = (f"S3: {dear / 1e6:.0f}M at cost of 500k+ goods held {SL_DEAR_HOURS} h or longer: narrow the "
                                "line (linewatch.LINE_MAX_SKUS, EM_FIND)")
    o3 = [o for o in orders if (o.get("ts") or 0) >= now - 3 * 3600]
    deals_h = sum(1 for o in o3 if o.get("deal")) / 3
    f["buyer_deals_h"] = round(deals_h, 1)
    if len(o3) >= SL_ORDERS_MIN and deals_h < SL_DEALS_H:
        flags["stoploss:B1-deals"] = (f"B1: our buyer closed {deals_h:.0f} deals an hour over 3 h (under {SL_DEALS_H}): "
                                      "objective takes the margin floor down to objective.GUARD_K by itself")
    mine = sorted((r.get("at") or 0, v[0]) for r in history for k, v in (r.get("buyers") or {}).items()
                  if common.is_ours(k) and isinstance(v, list) and v and isinstance(v[0], (int, float)))
    then = [s for at, s in mine if at <= now - SL_SAVED_HOURS * 3600]
    if mine and then:
        f["saved_12h"] = [then[-1], mine[-1][1]]
        if mine[-1][1] <= then[-1]:
            flags["stoploss:B1-saved"] = (f"B1: saved {then[-1]} -> {mine[-1][1]} over {SL_SAVED_HOURS} h, not up: "
                                          "look at the margin floor (objective.margin_floor)")
    return f, flags


def shares(c) -> dict:
    n = sum(c.values())
    return {k: v / n for k, v in c.items()} if n else {}


def overlap(a: dict, b: dict) -> float:
    return sum(min(a.get(k, 0), b.get(k, 0)) for k in set(a) | set(b))


def taste(orders: list, now: float) -> tuple[dict, dict]:
    """({facts}, {flag: text}) for our buyer's orders: the last RECENT against the BASE - RECENT before."""
    leaf = lambda o: str(o.get("category") or "?").split(">")[-1]
    recent = [o for o in orders if (o.get("ts") or 0) >= now - RECENT and o.get("budget")]
    base = [o for o in orders if now - BASE <= (o.get("ts") or 0) < now - RECENT and o.get("budget")]
    if len(recent) < TASTE_MIN or len(base) < TASTE_MIN:
        return {"orders": [len(recent), len(base)]}, {}
    a, b = Counter(leaf(o) for o in recent), Counter(leaf(o) for o in base)
    ov = overlap(shares(a), shares(b))
    ratio = lambda xs: st.median([o["budget"] / o["median_list"] for o in xs if o.get("median_list")] or [0])
    r_now, r_before = ratio(recent), ratio(base)
    h_now, h_before = len(recent) / (RECENT / 3600), len(base) / ((BASE - RECENT) / 3600)
    facts = {"mix_overlap": round(ov, 3), "top_now": [k for k, _ in a.most_common(4)], "budget_over_list": [round(r_now, 2), round(r_before, 2)],
             "orders_per_h": [round(h_now), round(h_before)]}
    flags = {}
    if ov < TASTE_OVERLAP:
        new = [k for k, v in shares(a).items() if v - shares(b).get(k, 0) >= 0.05]
        flags["taste:mix"] = f"our buyer's order mix moved (overlap {ov:.2f} with the 21 h before); grown: {new[:4]}"
    if abs(r_now - r_before) >= BUDGET_MOVE:
        flags["taste:budget"] = f"our buyer's budgets over the median list {r_before:.2f} -> {r_now:.2f}"
    if not RATE_LOW * h_before <= h_now <= RATE_HIGH * h_before:
        flags["taste:rate"] = f"our buyer's orders an hour {h_before:.0f} -> {h_now:.0f}"
    return facts, flags


def trend(settles: list, summary: dict, catalog: dict, now: float) -> tuple[dict, dict]:
    """The whole market over the last RECENT against the 24 h summary: the paid share of list and the category mix."""
    recent = [s for s in settles if (s.get("ts") or 0) >= now - RECENT and (catalog.get(s.get("item")) or {}).get("list_price")]
    cats = summary.get("by_category") or {}
    sold = {c: v.get("sold_24h") or 0 for c, v in cats.items()}
    if len(recent) < TREND_MIN or sum(sold.values()) < TREND_MIN:
        return {"settles_3h": len(recent)}, {}
    p_now = st.median((s.get("amount") or 0) / catalog[s["item"]]["list_price"] for s in recent)
    p_day = sum((v.get("price_ratio") or 0) * sold[c] for c, v in cats.items()) / max(1, sum(sold[c] for c, v in cats.items() if v.get("price_ratio")))
    ov = overlap(shares(Counter(catalog[s["item"]]["category"] for s in recent)), shares(Counter(sold)))
    facts = {"paid_share": [round(p_now, 3), round(p_day, 3)], "mix_overlap": round(ov, 3), "settles_per_h": round(len(recent) / (RECENT / 3600))}
    flags = {}
    if abs(p_now - p_day) >= TREND_PRICE_MOVE:
        flags["trend:price"] = f"the market's paid share of list {p_day:.3f} (24 h) -> {p_now:.3f} (3 h)"
    if ov < TREND_OVERLAP:
        flags["trend:mix"] = f"what the market buys moved (category overlap {ov:.2f} with the 24 h)"
    return facts, flags


def breakage(purchases: list, breaks: list, now: float) -> tuple[dict, dict]:
    """Our buyer's goods by grade: bought 6-18 h ago against all earlier, the share that broke."""
    broke = {b.get("order") for b in breaks}
    goods = [p for p in purchases if p.get("expires_at") is None and p.get("grade") in ("A", "B", "C")]
    facts, flags = {}, {}
    for g in "ABC":
        mine = [p for p in goods if p["grade"] == g]
        recent = [p for p in mine if now - BREAK_FROM <= (p.get("settled_at") or 0) < now - BREAK_TO]
        before = [p for p in mine if (p.get("settled_at") or 0) < now - BREAK_FROM]
        n_now, n_before = sum(1 for p in recent if p.get("order") in broke), sum(1 for p in before if p.get("order") in broke)
        r_now, r_before = n_now / max(1, len(recent)), n_before / max(1, len(before))
        facts[g] = [round(r_now, 3), round(r_before, 3), len(recent)]
        if n_now >= BREAK_MIN and len(before) >= 50 and r_now >= BREAK_TIMES * max(r_before, 0.01):
            flags[f"breakage:{g}"] = f"grade {g} goods breaking {r_now:.1%} of {len(recent)} (bought 6-18 h ago) against {r_before:.1%} before"
    return facts, flags


def brief(now: float) -> dict | None:
    """For the integrated analysis: the last watch's slow facts and the alerts of the last BRIEF_HOURS."""
    w = common.read_json(folder() / "watch.json", None)
    if not isinstance(w, dict):
        return None
    f = w.get("facts") or {}
    rows = [r for r in common.read_jsonl(folder() / "watch_log.jsonl") if r.get("alerts") and (r.get("at") or 0) >= now - BRIEF_HOURS * 3600]
    return {"at": w.get("at"), "taste": f.get("taste"), "market_trend": f.get("trend"), "breakage_by_grade_now_before_n": f.get("breakage"),
            "alive": f.get("alive"), f"alerts_{BRIEF_HOURS}h": [f"{a['kind']}: {a['text'][:1500] if a['kind'] == 'rules' else a['text'][:200]}"
                                                            for r in rows for a in r["alerts"]][-8:]}


def folder():
    return common.MARKET_DIR / "analysis"


def fetch_text(path: str) -> str:
    import httpx
    r = httpx.get(f"{ledger.URL}{path}", timeout=30)
    r.raise_for_status()
    return r.text


def page_text(html: str) -> str:
    text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", html, flags=re.S)
    return "\n".join(x.strip() for x in unescape(re.sub(r"<[^>]+>", "\n", text)).splitlines() if x.strip())


def digest(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


def changed_lines(old: str, new: str) -> list:
    return [x for x in difflib.unified_diff(old.splitlines(), new.splitlines(), lineterm="", n=0)
            if x[:1] in "+-" and not x.startswith(("+++", "---"))][:DIFF_LINES]


def recent_log(now: float, path: Path = KIT_LOG, span: float = BIN, tail: int = 400_000) -> list:
    """The kit log's lines of the last `span` seconds (its lines carry only HH:MM:SS UTC)."""
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            start = max(0, f.tell() - tail)
            f.seek(start)
            lines = f.read().decode("utf-8", "replace").splitlines()[1 if start else 0:]   # a cut first line
    except OSError:
        return []
    day = now % 86400
    out = []
    for x in lines:
        m = re.match(r"(\d\d):(\d\d):(\d\d) ", x)
        if m:
            t = int(m[1]) * 3600 + int(m[2]) * 60 + int(m[3])
            if (day - t) % 86400 <= span:
                out.append(x)
    return out


def bins(rows: list, end: float, key=lambda r: True) -> list:
    """Counts of rows per BIN, oldest first, the last ending at `end`."""
    out = [0] * (BEFORE + 1)
    for r in rows:
        i = int((end - (r.get("ts") or 0)) // BIN)
        if 0 <= i <= BEFORE and key(r):
            out[BEFORE - i] += 1
    return out


def run(now: float | None = None, get_json=None, get_text=None, log_path: Path = KIT_LOG, settles: list | None = None) -> dict:
    now = now or time.time()
    get_text = get_text or fetch_text
    if get_json is None:
        import analysis
        get_json = analysis.market_get
    path = folder() / "watch_state.json"
    state = common.read_json(path, {}) or {}
    first = not state
    alerts, facts = [], {}
    say = lambda kind, text: alerts.append({"kind": kind, "text": text})

    for name, src in (("agent_md", "/agent.md"), ("rules", "/rules")):
        try:
            text = get_text(src)
        except Exception as e:                                   # noqa: BLE001
            facts[name] = f"unreadable ({type(e).__name__})"
            continue
        text = page_text(text) if name == "rules" else text
        if state.get(name) and digest(text) != digest(state[name]):
            newest = next((x for x in text.splitlines() if x.startswith("Patch ")), "")
            say("rules", f"{src} changed (newest: {newest}): " + " | ".join(changed_lines(state[name], text)))
        state[name] = text
        facts[name] = digest(text)

    board = get_json("/api/board") or {}
    shops = [s for s in board.get("shops") or [] if isinstance(s, dict)]
    players = [p for p in board.get("players") or [] if isinstance(p, dict)]
    inv = common.market_file("inventory.json", {}) or {}
    shape = {"board": sorted(board), "shop": sorted(set().union(*[set(s) for s in shops])) if shops else [],
             "player": sorted(set().union(*[set(p) for p in players])) if players else [], "inventory": sorted(inv),
             "buyer": sorted(set().union(*[set(x) for x in board.get("buyers") or [] if isinstance(x, dict)]))}
    for k, v in shape.items():
        old = (state.get("shape") or {}).get(k)
        if old is not None and set(v) != set(old):
            say("shape", f"{k} fields: new {sorted(set(v) - set(old))}, gone {sorted(set(old) - set(v))}")
    state["shape"] = shape
    window = inv.get("restock") or {}
    length = (window.get("closes_at") or 0) - (window.get("opens_at") or 0) if window.get("opens_at") else None
    params = {"storage_per_day": inv.get("storage_per_day"), "window": length}
    for k, v in params.items():
        old = (state.get("params") or {}).get(k)
        if v is not None and old is not None and (abs(v - old) > 60 if k == "window" else v != old):
            say("params", f"{k}: {old} -> {v}")
    state["params"] = {k: v if v is not None else (state.get("params") or {}).get(k) for k, v in params.items()}
    facts["params"] = params

    log = recent_log(now, log_path)
    signs = {s: sum(1 for x in log if s in x) for s in OUTAGE_SIGNS}
    if any(signs.values()):
        say("outage", "in the last 20 min the kit logged " + ", ".join(f"'{s}' x{n}" for s, n in signs.items() if n))

    alive = sum(1 for s in shops if s.get("alive"))
    facts["alive"] = f"{alive} of {len(shops)}"
    if state.get("alive") and abs(alive - state["alive"]) >= max(ALIVE_MIN, ALIVE_MOVE * state["alive"]):
        say("alive", f"shops alive {state['alive']} -> {alive} (of {len(shops)})")
    state["alive"] = alive

    if settles is None:
        settles = common.read_jsonl(ledger.folder() / "settles.jsonl", since=now - max((BEFORE + 2) * BIN, RECENT + BIN))
    end = max((s.get("ts") or 0 for s in settles), default=now)
    names = ledger.names()
    ours = lambda s: common.is_ours(names.get(s.get("shop"), s.get("shop")) or "")
    market, mine = bins(settles, end), bins(settles, end, ours)
    m_med, o_med = st.median(market[:-1]), st.median(mine[:-1])
    facts["market"] = {"last_20min": market[-1], "median_before": m_med, "ours": mine[-1], "ours_median": o_med,
                       "ledger_to": int(end)}
    held = m_med and market[-1] >= MARKET_HELD * m_med
    if m_med >= VOLUME_MIN and not VOLUME_LOW * m_med <= market[-1] <= VOLUME_HIGH * m_med:
        say("volume", f"market settles in the last 20 min {market[-1]} against a median {m_med:.0f} before (bins {market})")
    if held and o_med >= 10 and mine[-1] < OURS_LOW * o_med:
        say("ours", f"our shop sold {mine[-1]} in the last 20 min against its median {o_med:.0f}, while the market held")
    orders = [o for o in common.records("orders") if (o.get("ts") or 0) >= now - BASE]
    deals = bins(orders, now, lambda o: bool(o.get("deal")))
    d_med = st.median(deals[:-1])
    facts["buyer_deals"] = {"last_20min": deals[-1], "median_before": d_med}
    if held and d_med >= 10 and deals[-1] < OURS_LOW * d_med:
        say("ours", f"our buyer closed {deals[-1]} deals in the last 20 min against its median {d_med:.0f}, while the market held")

    me = next((p for p in players if common.is_ours(p.get("player") or "")), None)
    if me:
        key = "student_total" if me.get("student") else "total"
        pool = sorted((p for p in players if p.get("student") or key == "total"), key=lambda p: -(p.get(key) or 0))
        place = 1 + sum(1 for p in pool if (p.get(key) or 0) > (me.get(key) or 0))
        above = [p for p in pool if (p.get(key) or 0) > (me.get(key) or 0)]
        below = [p for p in pool if (p.get(key) or 0) < (me.get(key) or 0)]
        facts["rank"] = {"view": "class" if key == "student_total" else "everyone", "place": place, "total": me.get(key),
                         "gap_up": round(min(p[key] for p in above) - me[key], 4) if above else None,
                         "gap_down": round(me[key] - max(p[key] for p in below), 4) if below else None}
        if state.get("place") and place != state["place"]:
            say("rank", f"class place {state['place']} -> {place} ({me.get(key)}; next up {facts['rank']['gap_up']}, "
                        f"next down {facts['rank']['gap_down']})")
        state["place"] = place
    my_shop = next((s for s in shops if common.is_ours(s.get("name") or "")), None)
    if my_shop and isinstance(my_shop.get("stars_rank"), (int, float)):
        stars = my_shop["stars_rank"]
        facts["stars"] = stars
        if state.get("stars") and state["stars"] - stars >= STARS_DROP:
            say("stars", f"our stars {state['stars']} -> {stars} in 20 min")
        state["stars"] = stars

    line = restock.load_line(common.read_json(folder() / "line.json", None))
    catalog = common.catalog()
    share = lambda s: (s.get("amount") or 0) / ((catalog.get(s.get("item")) or {}).get("list_price") or 1)
    lined = [s for s in settles if s.get("item") in line and not ours(s) and (catalog.get(s.get("item")) or {}).get("list_price")]
    last = [share(s) for s in lined if s["ts"] >= end - BIN]
    prior = [share(s) for s in lined if s["ts"] < end - BIN]
    if len(last) >= PRICE_N and len(prior) >= PRICE_N:
        a, b = st.median(last), st.median(prior)
        facts["line_price"] = {"last_20min": round(a, 3), "before": round(b, 3)}
        if abs(a - b) >= PRICE_MOVE:
            say("price", f"other shops' paid share of list on our line {b:.3f} -> {a:.3f} in the last 20 min")
    hot = [(s.get("sku"), s.get("factor")) for s in inv.get("surge") or [] if s.get("sku") in line and (s.get("factor") or 0) >= SURGE_HIGH]
    if hot:
        say("surge", f"line products surged: {hot}")
    facts["surged"] = len(inv.get("surge") or [])

    slow = {}
    for name, (got, flags) in (("taste", taste(orders, now)),
                               ("trend", trend(settles, common.read_json(common.MARKET_DIR / "market_summary.json", {}) or {}, catalog, now)),
                               ("breakage", breakage(common.read_jsonl(common.MARKET_DIR / "buyer" / "purchases.jsonl"),
                                                     common.read_jsonl(common.MARKET_DIR / "buyer" / "breaks.jsonl"), now)),
                               ("stoploss", stoploss(now, common.read_jsonl(common.MARKET_DIR / "records" / "stock_flow.jsonl",
                                                                            since=now - 12 * 3600),
                                                     inv.get("units") or [], orders, catalog,
                                                     common.read_jsonl(folder() / "scoreboard_history.jsonl")))):
        facts[name] = got
        slow.update(flags)
    for key, text in slow.items():                               # raised once when it starts, again after it has cleared
        if key not in set(state.get("flags") or []):
            say(key.split(":")[0], text)
    state["flags"] = sorted(slow)

    if first:
        alerts = []                                              # the first run only sets what later runs compare with
    out = {"at": int(now), "alerts": alerts, "facts": facts}
    common.write_json(path, state)
    common.write_json(folder() / "watch.json", out)
    with open(folder() / "watch_log.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(out, ensure_ascii=False) + "\n")
    out["note"] = (f"{len(alerts)} alert(s): " + "; ".join(f"{a['kind']}: {a['text'][:160]}" for a in alerts)) if alerts else \
        (f"quiet (market {facts['market']['last_20min']}/20 min, ours {facts['market']['ours']}, alive {facts['alive']}"
         + (f", class {facts['rank']['place']}" if facts.get("rank") else "") + ")" + (" - baseline set" if first else ""))
    return out


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=1)[:4000])
