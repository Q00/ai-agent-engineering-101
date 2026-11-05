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
"""
import difflib
import hashlib
import json
import re
import statistics as st
import time
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
DIFF_LINES = 12
KIT_LOG = Path(__file__).resolve().parent / "logs" / "market-kit.log"
OUTAGE_SIGNS = ("no shops in the market yet", "buyer token refused", "[market] order failed")


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
             "player": sorted(set().union(*[set(p) for p in players])) if players else [], "inventory": sorted(inv)}
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
        settles = common.read_jsonl(ledger.folder() / "settles.jsonl", since=now - (BEFORE + 2) * BIN)
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
    orders = [o for o in common.records("orders") if (o.get("ts") or 0) >= now - (BEFORE + 1) * BIN]
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
