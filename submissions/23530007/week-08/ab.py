"""Live A/B experiments with a bandit (user 11-13: 'we used to A/B test and keep the better side; keep moving the
strategy that way').

    ab_config.json        experiments (and a queue to start when one ends), edited by people and strategists
    .market/ab_state.json per experiment: arm weights, per-arm stats, status (written by update(), every tick)
    logs/ab.log           assignments are not logged; updates and promotions are

An experiment:
    {"side": "buyer" | "seller", "knob": "<param name>", "arms": [v0, v1, ...], "kind": "food" | "goods" | null,
     "grade": "A" | "B" | null (seller), "min_n": 150, "promote_p": 0.95, "floor": 0.10, "start": <epoch>}

assign(side, kind, grade) -> {name: [arm index, value, knob]} for the running experiments that match; the caller applies the
values to its own parameters and records the indices (buyer: every trade record of the order as "ab"; seller: the ask's
open deal and its deal/canceled/lapsed events as "ab"). It never raises: an error means no experiment for that call.

The metric is each arm's contribution to the class score, per unit of traffic:
    buyer  per ORDER: (v of the order's deal - m*) if it closed, else 0  (m* = the projected final mean, buyer_score)
    seller per ASK:   price - cost - refund share x price + REVENUE_WEIGHT x price if it closed, else 0
Weights follow Thompson sampling on a normal model (P(arm is best)), floored at `floor` per arm so every arm keeps
learning. When every arm has min_n samples and one arm's P(best) >= promote_p, its value is written to the side's
params file (globals) and the experiment ends; the next one in the queue starts.

    python ab.py            update weights / promote (watch_round.sh, every tick)
    python ab.py --print    show the state
"""
import json
import math
import random
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONFIG = HERE / "ab_config.json"
STATE = HERE / ".market" / "ab_state.json"
LOG = HERE / "logs" / "ab.log"
TARGET = {"buyer": HERE / "params_auto_buyer.json", "seller": HERE / "params_auto.json"}
REFUND = {"A": 0.025, "B": 0.08, "C": 0.0}     # refunded share of the price by grade (warranty A/B 72 h; 11-13 measured)
REVENUE_WEIGHT = 0.03                          # a won of revenue counts as 0.03 won of book (revenue 2nd under threat)
_CACHE = {"t": 0, "cfg": None, "st": None}


def _read(p, default):
    try:
        return json.loads(Path(p).read_text())
    except (OSError, ValueError):
        return default


def log(msg):
    try:
        with open(LOG, "a") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S UTC ", time.gmtime()) + msg + "\n")
    except OSError:
        pass


def assign(side, kind=None, grade=None):
    """{name: [arm index, value]} for the running experiments of `side` matching kind/grade. Never raises."""
    try:
        now = time.time()
        if now - _CACHE["t"] > 60 or _CACHE["cfg"] is None:
            _CACHE.update(t=now, cfg=_read(CONFIG, {}), st=_read(STATE, {}))
        out = {}
        for name, e in (_CACHE["cfg"].get("experiments") or {}).items():
            if e.get("side") != side or not e.get("active", True) or not e.get("arms"):
                continue
            if e.get("kind") and kind and e["kind"] != kind:
                continue
            if e.get("kind") and not kind:
                continue
            if e.get("grade") and e["grade"] != grade:
                continue
            st = (_CACHE["st"] or {}).get(name) or {}
            if str(st.get("status", "running")).startswith("promoted"):
                continue
            w = st.get("weights") or [1.0 / len(e["arms"])] * len(e["arms"])
            r, i = random.random() * sum(w), 0
            while i < len(w) - 1 and r > w[i]:
                r -= w[i]
                i += 1
            out[name] = [i, e["arms"][i], e.get("knob")]
        return out
    except Exception:  # noqa: BLE001 - an experiment must never break an order or an ask
        return {}


def _stats(xs):
    n = len(xs)
    if not n:
        return [0, 0.0, 0.0]
    m = sum(xs) / n
    var = sum((x - m) ** 2 for x in xs) / (n - 1) if n > 1 else 0.0
    return [n, m, var]


def _p_best(stats, draws=4000):
    """P(each arm has the highest mean), normal approximation with a weak prior on the variance."""
    k = len(stats)
    pooled = [s[2] for s in stats if s[0] > 1]
    v0 = (sum(pooled) / len(pooled)) if pooled else 1.0
    wins = [0] * k
    for _ in range(draws):
        best, bi = -1e18, 0
        for i, (n, m, var) in enumerate(stats):
            se = math.sqrt(((var if n > 1 else v0) + v0 / 10) / max(1, n))
            x = random.gauss(m, se) if n else random.gauss(0, math.sqrt(v0) or 1)
            if x > best:
                best, bi = x, i
        wins[bi] += 1
    return [w / draws for w in wins]


def buyer_samples(e, name, since):
    """Per order: (v - m*) if the order closed, else 0, split by the arm recorded on its trades."""
    sys.path.insert(0, str(HERE))
    import buyer_score as bs
    trades = _read(HERE / "buyer_memory.json", {}).get("trades") or []
    m_star = (_read(HERE / ".market" / "buyer_score.json", {}) or {}).get("m_star") or 0.765
    rows = [t for t in trades if t.get("t", 0) >= since and t.get("order") and name in (t.get("ab") or {})]
    bs.costs(rows, time.time())
    orders = {}
    for t in rows:
        o = orders.setdefault(t["order"], [t["ab"][name][0] if isinstance(t["ab"][name], list) else t["ab"][name], None])
        if t.get("outcome") == "deal" and t.get("list") and t.get("price"):
            o[1] = bs.score(t)
    out = [[] for _ in e["arms"]]
    for arm, v in orders.values():
        if 0 <= arm < len(out):
            out[arm].append((v - m_star) if v is not None else 0.0)
    return out


LAST_STARS = {}


def _ledger_refunds_stars(since):
    """unit -> refunded credits, task -> stars, from the archive (read-only)."""
    import sqlite3
    ref, stars = {}, {}
    try:
        db = sqlite3.connect(f"file:{HERE / 'data' / 'market_archive.db'}?mode=ro", uri=True, timeout=30)
        for (d,) in db.execute("SELECT data FROM ledger WHERE kind='broken' AND ts > ?", (since,)):
            x = json.loads(d).get("data") or {}
            ref[x.get("unit")] = x.get("refunded") or 0
        for (d,) in db.execute("SELECT data FROM ledger WHERE kind='result' AND ts > ? AND data LIKE '%\"task\"%'", (since,)):
            x = json.loads(d).get("data") or {}
            t = (x.get("receipt") or {}).get("task")
            if t and x.get("stars") is not None and x.get("verified"):
                stars[t] = x["stars"]
    except Exception:  # noqa: BLE001
        pass
    return ref, stars


def seller_samples(e, name, since):
    """Per ask (a talk that got our price): price - cost - refunds + REVENUE_WEIGHT x price if it closed, else 0.
    11-15: with 'mature_h' (warranty experiments) only talks older than mature_h count and the ACTUAL refund of the
    unit is used (all breaks come within 6 h); the arms' mean stars go to LAST_STARS for the stars guard."""
    out = [[] for _ in e["arms"]]
    talks = {}
    mature = e.get("mature_h")
    cut = time.time() - (mature or 0) * 3600
    ref, stars = _ledger_refunds_stars(since) if mature or e.get("stars_guard") else ({}, {})
    st_arm = [[] for _ in e["arms"]]
    try:
        with open(HERE / "seller_r3_log.jsonl", "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 40_000_000))
            f.readline()
            for line in f:
                if b'"ab"' not in line:
                    continue
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                if ev.get("t", 0) < since or name not in (ev.get("ab") or {}):
                    continue
                key = ev.get("task") or f"{ev.get('sku')}|{ev.get('t')}"
                arm = ev["ab"][name][0] if isinstance(ev["ab"][name], list) else ev["ab"][name]
                if mature and ev.get("t", 0) > cut:
                    continue                                    # too young: its breaks have not all happened yet
                if ev.get("event") == "deal" and ev.get("cost") is not None:
                    p = ev["price"]
                    rf = ref.get(ev.get("unit"), 0) if mature else REFUND.get(ev.get("grade") or "A", 0.025) * p
                    talks[key] = (arm, p - ev["cost"] - rf + REVENUE_WEIGHT * p)
                    if ev.get("task") in stars and 0 <= arm < len(st_arm):
                        st_arm[arm].append(stars[ev["task"]])
                elif ev.get("event") in ("quote", "canceled", "lapsed") and key not in talks:
                    talks[key] = (arm, 0.0)
    except OSError:
        return out
    for arm, x in talks.values():
        if 0 <= arm < len(out):
            out[arm].append(x / 1e6)          # M credits per ask
    LAST_STARS[name] = [(round(sum(v) / len(v), 3), len(v)) if v else (None, 0) for v in st_arm]
    return out


def promote(name, e, value):
    p = TARGET[e["side"]]
    d = _read(p, {})
    d.setdefault("globals", {})[e["knob"]] = value
    p.write_text(json.dumps(d, ensure_ascii=False, indent=1))
    log(f"PROMOTED {name}: {e['knob']} = {value} ({p.name})")


END = 1794927540               # 2026-11-17 14:59 UTC
FINAL_H = 6                    # 11-15 (user: 'make 1st safe'): in the last FINAL_H hours every running experiment is
                               # decided now (leader if P(best) >= timed_p, else control) and no new one starts


def update():
    cfg = _read(CONFIG, {})
    st = _read(STATE, {})
    now = time.time()
    changed_cfg = False
    final = now >= END - FINAL_H * 3600
    if final:
        cfg["queue"] = []
    for name, e in list((cfg.get("experiments") or {}).items()):
        s = st.setdefault(name, {"status": "running"})
        if final and e.get("active", True) and not str(s.get("status")).startswith("promoted"):
            e["max_h"] = 0                 # forces the timed decision below on this pass
        if not e.get("active", True) or str(s.get("status")).startswith("promoted"):
            continue
        since = e.get("start") or now
        samples = (buyer_samples if e["side"] == "buyer" else seller_samples)(e, name, since)
        stats = [_stats(xs) for xs in samples]
        pb = _p_best(stats)
        floor = e.get("floor", 0.10)
        w = [max(floor, p) for p in pb]
        w = [x / sum(w) for x in w]
        s.update(t=now, stats=[[n, round(m, 5), round(v, 6)] for n, m, v in stats], p_best=[round(p, 3) for p in pb],
                 weights=[round(x, 3) for x in w])
        # 11-14 (user: 'it takes too long'): decide at P(best) >= promote_p (.90) with min_n per arm, or when the
        # experiment is max_h old (6 h): then the leader if its P(best) >= .75, else the control arm (index 0)
        done = all(n >= e.get("min_n", 100) for n, _, _ in stats) and max(pb) >= e.get("promote_p", 0.90)
        timed = final or ((now - since) >= e.get("max_h", 6) * 3600 and all(n >= 30 for n, _, _ in stats))
        if done or timed:
            i = pb.index(max(pb)) if (done or max(pb) >= e.get("timed_p", 0.75)) else 0
            sg = e.get("stars_guard")
            ls = LAST_STARS.get(name) or []
            if sg and i != 0 and len(ls) > i and ls[i][0] is not None and ls[0][0] is not None and ls[i][1] >= 20 \
                    and ls[i][0] < ls[0][0] - sg:
                log(f"{name}: arm {i} wins on money but its stars {ls[i][0]} < control {ls[0][0]} - {sg}: control kept")
                i = 0
            if timed and not done:
                log(f"{name}: {e.get('max_h', 6)} h limit, P(best) {max(pb):.2f} -> arm {i}")
            promote(name, e, e["arms"][i])
            s["status"] = f"promoted:{i}"
            e["active"] = False
            changed_cfg = True
            nxt = (cfg.get("queue") or [])
            if nxt:
                n_name, n_e = next(iter(nxt[0].items()))
                n_e["start"] = now
                cfg.setdefault("experiments", {})[n_name] = n_e
                cfg["queue"] = nxt[1:]
                log(f"started {n_name} from the queue")
        log(f"{name}: n {[x[0] for x in stats]} mean {[round(x[1], 4) for x in stats]} p_best {s['p_best']} weights {s['weights']}"
            + (f" stars {LAST_STARS[name]}" if LAST_STARS.get(name) else ""))
    STATE.write_text(json.dumps(st, ensure_ascii=False, indent=1))
    if changed_cfg:
        CONFIG.write_text(json.dumps(cfg, ensure_ascii=False, indent=1))
    return st


if __name__ == "__main__":
    out = update()
    if "--print" in sys.argv:
        print(json.dumps(out, ensure_ascii=False, indent=1))
