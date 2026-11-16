"""Tests for meta_r3's number rules (11/9): one shop number per area a cycle, and the 2 h verdict judged on our share
of the market's paid sales. No model is called; the ledger cache and the logs live in a temp folder."""
import json
import tempfile
import time
from pathlib import Path

import meta_r3 as mr
import metrics_r3

RESULTS = []


def case(name, ok, detail=""):
    RESULTS.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  ({detail})" if detail and not ok else ""))


def ledger(base, rows, early=True):
    (base / ".market").mkdir(parents=True, exist_ok=True)
    (base / "logs").mkdir(exist_ok=True)
    url = "https://ours.example"
    entries = [{"kind": "join", "ts": 0, "data": {"name": metrics_r3.ME, "role": "shop", "url": url}}]
    # the cache is a continuous span: an early settle elsewhere so it reaches back past every window in a test
    entries += [] if not early else [{"kind": "settle", "ts": min(r[0] for r in rows) - 30000, "data": {"shop": "https://far.example",
                                                                                    "amount": 0, "unit": "u-far"}}]
    for i, (ts, shop, amount) in enumerate(rows):
        entries.append({"kind": "settle", "ts": ts, "data": {"shop": url if shop == "us" else "https://other.example",
                                                            "amount": amount, "unit": f"u-{i}"}})
    (base / ".market" / "ledger-recent.json").write_text(json.dumps(entries))


def main():
    cur = {"ask_frac": 0.9, "floor_frac": 0.8, "min_margin": 0.1, "max_list": 600_000, "sku_depth": 3,
           "dear_first": 0.5, "stock_value": 50_000_000}

    # 1. one number per area: the manager's first in each area stays, the rest go back
    s = dict(cur, max_list=900_000, sku_depth=5, dear_first=1.0, min_margin=0.06, ask_frac=0.95)
    out, held = mr.one_per_area(dict(s), cur, ["sku_depth", "max_list", "min_margin", "dear_first", "ask_frac"])
    case("the first merchandising number in the manager's order stays", out["sku_depth"] == 5)
    case("the other merchandising numbers go back", out["max_list"] == 600_000 and out["dear_first"] == 0.5,
         str(out))
    case("one sales number stays beside it", out["min_margin"] == 0.06 and out["ask_frac"] == 0.9, str(out))
    case("the held-back numbers are reported", set(held) == {"max_list", "dear_first", "ask_frac"}, str(held))
    out, held = mr.one_per_area(dict(cur, sku_depth=4), cur, [])
    case("a single change is untouched", out["sku_depth"] == 4 and not held)
    # a held-back ask must not leave the floor above it (guard's floor <= ask rule)
    s = dict(cur, ask_frac=0.85, floor_frac=0.85)
    out, held = mr.one_per_area(dict(s, ask_frac=0.95, floor_frac=0.93), dict(cur, ask_frac=0.9),
                                ["floor_frac", "ask_frac"])
    case("the floor never ends above a held-back ask", out["floor_frac"] <= out["ask_frac"], str(out))

    # 2. market share from the ledger cache: count and value, ours over the market's
    tmp = Path(tempfile.mkdtemp(prefix="meta-r3-"))
    t = time.time() - 3 * 3600
    ledger(tmp, [(t - 3000, "us", 100), (t - 2000, "x", 900), (t - 1000, "x", 1000),
                 (t + 1000, "us", 300), (t + 2000, "x", 300), (t + 9000, "us", 50)])
    ms = metrics_r3.market_share(t - 7200, t, tmp)
    case("share by count", ms["ours"] == 1 and ms["market"] == 3 and abs(ms["share_n"] - 1 / 3) < 1e-3, str(ms))
    case("share by value", abs(ms["share_value"] - 100 / 2000) < 1e-3, str(ms))
    case("no market sales gives no share", metrics_r3.market_share(0, 1, tmp)["share_value"] is None)

    # 2b. the 10-minute buckets: filed from the cache, then used once the cache no longer reaches back
    tb = Path(tempfile.mkdtemp(prefix="meta-r3-b-"))
    b0 = (int(time.time()) // 600 - 30) * 600           # 30 buckets ago, on a bucket edge
    ledger(tb, [(b0 + 60, "us", 100), (b0 + 120, "x", 300), (b0 + 700, "x", 600), (b0 + 1300, "us", 50),
                (b0 + 3000, "x", 0)])                    # the cache has caught up 15 min past the span
    added = metrics_r3.record_buckets(tb)
    again = metrics_r3.record_buckets(tb)
    case("closed buckets are filed once", added >= 3 and again == 0, (added, again))
    ledger(tb, [(b0 + 9000, "x", 1)], early=False)                    # the cache has moved on: the early sales are gone from it
    old = metrics_r3.market_share(b0, b0 + 1800, tb)
    case("an old span is read from the buckets", old["ours"] == 2 and old["market"] == 4
         and abs(old["share_value"] - 150 / 1050) < 1e-3, str(old))
    case("a span neither covers gives no share", metrics_r3.market_share(b0 - 7200, b0, tb)["share_value"] is None)

    # 3. the 2 h verdict leads with the share, so the hour of day cancels out: we sold 3x more after (the morning
    # rush) but so did the market, so the share is flat even though raw counts tripled
    rows = [(t - 6000, "us", 100), (t - 5000, "x", 900)] + \
           [(t + 100 * i, "us", 100) for i in range(1, 4)] + [(t + 100 * i + 50, "x", 900) for i in range(1, 4)]
    ledger(tmp, rows)
    real_window, real_share = metrics_r3.window, metrics_r3.market_share
    metrics_r3.window = lambda a, b, *x, **k: {"shop_margin_h": 1 if b <= t else 3, "shop_deals_h": 1 if b <= t else 3,
                                                "board_pts_h": None, "outage_min": 0}
    metrics_r3.market_share = lambda a, b, base=tmp: real_share(a, b, tmp)
    mr.NUMBER_TRIALS, mr.CODE_CHANGES = tmp / "logs" / "trials.json", tmp / "logs" / "changes.jsonl"
    mr.NUMBER_TRIALS.write_text(json.dumps([{"t": t, "changes": {"sku_depth": [3, 5]}, "reason": "test"}]))
    try:
        out = mr.number_verdicts(now=t + mr.NUMBER_CHECK + 1)
    finally:
        metrics_r3.window, metrics_r3.market_share = real_window, real_share
    row = json.loads(mr.CODE_CHANGES.read_text().splitlines()[-1])
    case("the verdict leads with the share of the market", str(out[0]).startswith("sku_depth 3 -> 5: share of the "
                                                                             "market's paid value"), str(out))
    case("a rush hour that lifted the whole market reads as a flat share", "(+0%)" in row["verdict"].split(";")[0],
         row["verdict"])
    case("raw margin/h stays as context", "margin/h 1 -> 3 (+200%)" in row["verdict"], row["verdict"])
    case("the ledger row names the share as its metric", row["metric"] == "share_value" and row["share"] == 0.0)
    case("the judged change leaves the queue", json.loads(mr.NUMBER_TRIALS.read_text()) == [])

    # shop changes one at a time: none while another is judged, else only the manager's first
    s = dict(cur, max_list=900_000, min_margin=0.06)
    out, held = mr.one_shop_change(dict(s), cur, ["min_margin", "max_list"])
    case("with the slot free only the manager's first shop number moves", out["min_margin"] == 0.06
         and out["max_list"] == 600_000 and set(held) == {"max_list"}, (out, held))
    out, held = mr.one_shop_change(dict(s), cur, ["min_margin", "max_list"], busy="ask_frac is being judged")
    case("with the slot busy no shop number moves", out == cur and set(held) == {"min_margin", "max_list"}, held)
    d = Path(tempfile.mkdtemp(prefix="meta-r3-busy-"))
    mr.NUMBER_TRIALS, mr.CODE_CHANGES = d / "trials.json", d / "changes.jsonl"
    mr.PLAY_MODE = d / "play_mode.json"     # the live play mode (11/11 aggressive) must not decide the slot test
    mr.NUMBER_TRIALS.write_text("[]")
    mr.CODE_CHANGES.write_text(json.dumps({"t": 1, "source": "meta (auto)", "commit": "abc", "files": ["buyer_r3.py"]}) + "\n")
    free = mr.shop_busy()
    with mr.CODE_CHANGES.open("a") as f:
        f.write(json.dumps({"t": 2, "source": "meta (auto)", "commit": "def", "files": ["restock_r3.py"]}) + "\n")
    code_busy = mr.shop_busy()
    mr.NUMBER_TRIALS.write_text(json.dumps([{"t": 1000, "changes": {"ask_frac": [0.7, 0.75]}}]))
    case("the slot is busy while a shop number or a shop-file code change is judged, free for buyer code",
         free is None and "def" in (code_busy or "") and "ask_frac" in (mr.shop_busy() or ""), (free, code_busy))
    # the shop's points count stars: reviews above our mean add, below it take away (11/9 review)
    pr = {"book": 1e-9, "revenue": 1e-10, "stars": 0.1, "stars_now": 4.5, "reviews_total": 1000}
    base_pts = metrics_r3.shop_points_h(1000, 10000, pr)
    up = metrics_r3.shop_points_h(1000, 10000, pr, reviews_h=10, stars_mean=5.0)
    down = metrics_r3.shop_points_h(1000, 10000, pr, reviews_h=10, stars_mean=3.0)
    case("five-star reviews raise the shop's points, low ones lower them", down < base_pts < up, (down, base_pts, up))
    # whole writes: a strategy file is replaced in one step, no .tmp left behind
    w = Path(tempfile.mkdtemp(prefix="meta-r3-w-")) / "s.json"
    mr.write_json(w, {"ask_frac": 0.7})
    case("a strategy file is written whole", json.loads(w.read_text()) == {"ask_frac": 0.7}
         and not w.with_suffix(".tmp").exists())
    print(f"{sum(RESULTS)}/{len(RESULTS)} passed")
    # order 9 in code (11/14): the team's own same-direction step within 2 h is held; the opposite direction, a
    # supervisor row, or a step older than 2 h is not
    real = mr.NUMBER_TRIALS
    tmp = Path(tempfile.mkdtemp(prefix="o9-")) / "trials.json"
    t0 = time.time()
    tmp.write_text(json.dumps([
        {"t": t0 - 3 * 3600, "changes": {"clear_hours": [14, 12]}, "reason": "team"},
        {"t": t0 - 3000, "changes": {"food_first": [0.25, 0.5]}, "reason": "team"},
        {"t": t0 - 2000, "changes": {"max_list": [800000, 900000]}, "reason": "team"},
        {"t": t0 - 1000, "changes": {"max_list": [1200000, 900000]}, "reason": "supervisor: order 9"}]))
    mr.NUMBER_TRIALS = tmp
    try:
        c9 = {"food_first": 0.5, "max_list": 900000, "clear_hours": 12, "ask_frac": 0.8, "floor_frac": 0.66}
        out, held = mr.order9_hold(dict(c9, food_first=0.75, max_list=1000000, clear_hours=10), c9, now_t=t0)
        case("order 9: the team's second same-direction step within 2 h is held, past a supervisor revert",
             out["food_first"] == 0.5 and out["max_list"] == 900000 and set(held) == {"food_first", "max_list"}, held)
        case("order 9: a step whose last team move is over 2 h old goes through", out["clear_hours"] == 10, out)
        out, held = mr.order9_hold(dict(c9, food_first=0.25), c9, now_t=t0)
        case("order 9: the opposite direction goes through", out["food_first"] == 0.25 and not held, held)
        # 11/16 15:32: a supervisor's value still in force holds both directions for 2 h (the fallback undid c_first)
        tmp.write_text(json.dumps([
            {"t": t0 - 3 * 3600, "changes": {"lines": [20, 18]}, "reason": "supervisor: old"},
            {"t": t0 - 600, "changes": {"c_first": [1, 0.0]}, "reason": "supervisor: closed -42% verdict"}]))
        c9s = dict(c9, c_first=0.0, lines=18)
        out, held = mr.order9_hold(dict(c9s, c_first=1, lines=20), c9s, now_t=t0)
        case("order 9: a supervisor value set within 2 h is held against a move back",
             out["c_first"] == 0.0 and "c_first" in held, held)
        case("order 9: a supervisor value older than 2 h may move", out["lines"] == 20 and "lines" not in held, held)
        out, held = mr.order9_hold(dict(c9s, c_first=0.0, lines=18), dict(c9s, c_first=1), now_t=t0)
        case("order 9: a supervisor value no longer in force does not hold", "c_first" not in held, held)
    finally:
        mr.NUMBER_TRIALS = real
    return all(RESULTS)


if __name__ == "__main__":
    raise SystemExit(0 if main() else 1)
