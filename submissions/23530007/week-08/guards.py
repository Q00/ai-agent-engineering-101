"""Code guards for the buyer and the shop (11-11 restructure). Runs every 15 min from watch_round.sh, after
buyer_score.py. Each guard is justified by a past incident (volatility analysis, 11-11):

  G1 reconcile   buyer_score.reconcile.suspect: our per-deal v vs the board-implied v differ by > .03
                 -> alert; *_share moves frozen until a clean pass (the +1 refund scoring ran 96 h; this fires in 1 h)
  G2 breaks      goods broken over 3 h (>= 60 deals) > max(6%, 3 x market share)
                 -> override goods_delta 0.05 for 6 h (tighter grade ceilings), wake the buyer strategist, alert
  G3 collapse    deals in the last hour < 50% of the trailing-6 h mean (>= 30/h) while orders stay >= 80%
                 -> restore the 'before' value of every buyer knob move of the last 2 h, freeze moves 2 h, wake, alert
                 (the needwarr bug: deals 88/h -> 39/h in hour 2; this would have reverted at hour 1, not hour 15)
  G4 spike       an outcome's share of visits in the last hour is >= 20 points above its trailing-6 h share
                 -> wake the buyer strategist with the outcome, alert (4 fires in 146 h, 3 genuine)
  G6 churn       >= 4 moves on one buyer knob in 6 h -> that knob frozen 6 h, alert (cheapgoods_share moved 35 times)
  G7 rank step   class buyer <= -.009 or class shop <= -.007 in one 15-min step, confirmed by the next sample
                 -> wake the matching strategist (2 h cooldown)
  G8 stars       our board stars_rank worse by >= 2 ranks within 2.5 h (shop_parts.jsonl) -> wake the shop (<= 2/day)
  G17 food loss  food bought minus paid food sales over 6 M in 24 h or 25 M in all -> food restock off, phone alert
  G16 star food  (off since 21:40) no paid review from the two target accounts 24 h after the star foods started -> must_stock off
  G15 gains     > 6 skipped orders/h with expected v above m* -> wake the buyer strategist (<= 1 per 2 h)
  G14 final     projected final class rank (ops/final_rank.py, every 2 h) under 1st -> wake the shop, alert <= 1 per 12 h
  G13 revenue   the student shop below us in revenue passing us before the round ends (3 h slopes) -> wake the shop
                 (<= 1 per 2 h), phone alert under 72 h (user 11-11: never lose revenue 2nd)
  G9 book        book lead over the best other shop closing in < 72 h at the 6 h slopes -> wake the shop (<= 1 per 6 h)
  G10 budget     model spend in the last 24 h >= DAILY_CAP -> the strategists skip their non-wake slots; alert at 80%
  G11 skips      skipped share of orders over 6 h > 65% -> alert (the skip bar may be above the market)

State in .market/guard_state.json: frozen {knob: until}, freeze_shares_until, budget_block_until, last_fire {G: t},
book_hist. Overrides in .market/guard_overrides.json {knob: {"value": v, "until": t}} are applied by
policy.buyer_params() on top of the strategist's settings. Wakes: .market/buyer_strategist_wake (buyer),
.market/strategist_wake (shop). python guards.py --dry prints what would fire without writing anything.
"""
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
MD = HERE / ".market"
STATE = MD / "guard_state.json"
OVERRIDES = MD / "guard_overrides.json"
SCORE = MD / "buyer_score.json"
BUYER_WAKE = MD / "buyer_strategist_wake"
SHOP_WAKE = MD / "strategist_wake"
MEM = HERE / "buyer_memory.json"
BMEM = HERE / "buyer_strategist_memory.json"
AUTO_BUYER = HERE / "params_auto_buyer.json"
CLASS = MD / "class_history.jsonl"
PARTS = MD / "shop_parts.jsonl"
SHOP_VALUE = MD / "shop_value.json"
LOGF = HERE / "logs" / "guards.log"
MARKET = "https://market.ourolabs.ai"
STAR_ACCOUNTS = {"JangSeongWoong", "gom31-certified-goods"}   # G16: the accounts the must_stock foods are kept for
G16_ACTIVE = False         # 11-11 21:40: off, the wide food trial is judged by its loss (G17)
FOOD_LOSS_24H = 6e6        # G17: food bought minus paid food sales, credits, in any 24 h
FOOD_LOSS_TOTAL = 25e6     # G17: ... and since the wide food programme started
FOOD_LOSS_HARD = 50e6      # G17: the loss at which food restock stops even while the stars rise
STAR_HOURS = 24            # G16: hours to wait for a paid review from them before food restock is switched off
DAILY_CAP = 18.5           # $/24 h: (credit - 60 for Round 4) / days left on 11-11
try:                       # 11-11 (user: use nearly all of the credit before it expires): 1.4 x the paced daily budget
    import strategist_memory as _sm
    _b = _sm.pace()[2]
    if _b:
        DAILY_CAP = max(DAILY_CAP, round(1.4 * _b, 1))
except Exception:
    pass
TOTAL_GAP = 0.03           # G12: class-total lead under this wakes both strategists
DRY = "--dry" in sys.argv


def log(msg):
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime())} UTC {msg}"
    print(line)
    if not DRY:
        LOGF.parent.mkdir(exist_ok=True)
        with LOGF.open("a", encoding="utf-8") as f:
            f.write(line + "\n")


def read(p, default):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def write(p, obj):
    if not DRY:
        Path(p).write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")


def alert(items):
    log("ALERT " + " | ".join(items))
    if DRY:
        return
    try:
        import model_env
        model_env.alert_user("guard", items, min_gap=900)
    except Exception as e:
        log(f"alert failed: {e!r}")


G24_OVER_M, G24_NS_C = 30.0, 60      # 11-17: fresh overstock (M) / 'no stock' on C-only SKUs per hour


def wake(role, why):
    p = BUYER_WAKE if role == "buyer" else SHOP_WAKE
    log(f"wake {role}: {why}")
    if not DRY:
        p.write_text(json.dumps({"guard": True, "why": why, "t": time.time()}, ensure_ascii=False))


def cooled(st, g, secs, now):
    return now - (st.get("last_fire") or {}).get(g, 0) >= secs


def fired(st, g, now):
    st.setdefault("last_fire", {})[g] = now


def override(ov, knob, value, hours, now):
    ov[knob] = {"value": value, "until": now + hours * 3600}
    log(f"override {knob} = {value} for {hours} h")


def tail_jsonl(p, n):
    try:
        lines = Path(p).read_text(encoding="utf-8").splitlines()[-n:]
    except OSError:
        return []
    out = []
    for l in lines:
        try:
            out.append(json.loads(l))
        except ValueError:
            pass
    return out


def main():
    now = time.time()
    st = read(STATE, {})
    ov = {k: v for k, v in read(OVERRIDES, {}).items() if isinstance(v, dict) and v.get("until", 0) > now}
    sc = read(SCORE, {})
    trades = read(MEM, {}).get("trades", [])
    h1 = [t for t in trades if now - t["t"] < 3600]
    h6 = [t for t in trades if now - t["t"] < 6 * 3600]
    fires = []

    # G1 reconcile
    rec = sc.get("reconcile") or {}
    if rec.get("suspect"):
        if cooled(st, "G1", 3600, now):
            alert([f"G1 점수 대조 불일치: 우리 거래당 v {rec.get('our_v')} vs 보드 추정 {rec.get('implied_v')} (차이 {rec.get('gap')}). 비율 knob 이동을 동결합니다."])
            fired(st, "G1", now)
        st["freeze_shares_until"] = now + 2 * 3600
        fires.append("G1")
    elif st.get("freeze_shares_until", 0) > now and rec and not rec.get("suspect"):
        st["freeze_shares_until"] = 0
        log("G1 clear: shares unfrozen")

    # G2 breaks
    b3 = (sc.get("breaks") or {}).get("goods_3h") or {}
    market_share = None
    try:
        sv = read(SHOP_VALUE, {})
        market_share = ((sv.get("stats") or {}).get("*|goods") or {}).get("broke")
    except Exception:
        pass
    if b3.get("n", 0) >= 60 and b3.get("share") is not None and b3["share"] > max(0.06, 3 * (market_share or 0.02)):
        if cooled(st, "G2", 6 * 3600, now):
            override(ov, "goods_delta", 0.10, 6, now)   # 11-12: patch 3.6 scale
            wake("buyer", f"G2 breaks: goods broke {b3['share']:.0%} over 3 h (market {market_share}); goods_delta 0.10 for 6 h")
            alert([f"G2 고장 급증: 3h 공산품 고장 {b3['share']:.0%} (시장 {market_share}); 등급 상한을 6h 조입니다."])
            fired(st, "G2", now)
        fires.append("G2")

    # G3 deals collapse (orders steady)
    deals1 = sum(1 for t in h1 if t.get("outcome") == "deal" and t.get("signed") is not False)
    deals6 = sum(1 for t in h6 if t.get("outcome") == "deal" and t.get("signed") is not False) / 6
    orders1 = len({t.get("order") for t in h1 if t.get("order")})
    orders6 = len({t.get("order") for t in h6 if t.get("order")}) / 6
    if deals6 >= 30 and orders6 and orders1 >= 0.8 * orders6 and deals1 < 0.5 * deals6:
        if cooled(st, "G3", 2 * 3600, now):
            restored = []
            if not DRY:
                auto = read(AUTO_BUYER, {})
                for e in (read(BMEM, {}).get("episodes") or []):
                    if e.get("applied") and now - e.get("t", 0) <= 2 * 3600 and e.get("knob"):
                        d_ = (auto.setdefault("category", {}).setdefault(e["sku"], {}) if e.get("sku") and e["knob"] == "cap"
                              else auto.setdefault("shop", {}).setdefault(e["sku"], {}) if e.get("sku")
                              else auto.setdefault("globals", {}))
                        if e.get("before") is None:
                            d_.pop(e["knob"], None)
                        else:
                            d_[e["knob"]] = e["before"]
                        restored.append(f"{e['knob']} {e.get('sku') or ''} -> {e.get('before')}")
                write(AUTO_BUYER, auto)
            st["freeze_all_until"] = now + 2 * 3600
            wake("buyer", f"G3 deals collapse: {deals1}/h vs {deals6:.0f}/h trailing with orders steady; reverted {restored}")
            alert([f"G3 거래 붕괴: 최근 1h {deals1}건 vs 6h 평균 {deals6:.0f}건/h (주문은 유지). 최근 2h knob 이동을 되돌렸습니다: {', '.join(restored) or '없음'}"])
            fired(st, "G3", now)
        fires.append("G3")

    # G4 outcome spike (goods visits)
    def shares(rows):
        rows = [t for t in rows if t.get("kind") == "goods" and t.get("shop")]
        n = len(rows)
        out = {}
        for t in rows:
            k = str(t.get("outcome", "")).split(" (")[0].split(":")[0]
            out[k] = out.get(k, 0) + 1
        return n, {k: v / n for k, v in out.items()} if n else {}
    n1, s1 = shares(h1)
    n6, s6 = shares(h6)
    if n1 >= 40:
        for k, v in s1.items():
            if v - s6.get(k, 0) >= 0.20 and cooled(st, f"G4:{k}", 2 * 3600, now):
                wake("buyer", f"G4 outcome spike: '{k}' {v:.0%} of goods visits in 1 h vs {s6.get(k, 0):.0%} over 6 h")
                alert([f"G4 결과 급증: 공산품 방문 중 '{k}' {v:.0%} (6h 평균 {s6.get(k, 0):.0%})"])
                fired(st, f"G4:{k}", now)
                fires.append("G4")

    # G6 knob churn
    moves = {}
    for e in (read(BMEM, {}).get("episodes") or []):
        if e.get("applied") and now - e.get("t", 0) <= 6 * 3600:
            moves[e["knob"]] = moves.get(e["knob"], 0) + 1
    frozen = {k: u for k, u in (st.get("frozen") or {}).items() if u > now}
    for k, c in moves.items():
        if c >= 4 and k not in frozen:
            frozen[k] = now + 6 * 3600
            alert([f"G6 knob 요동: {k} 6h에 {c}회 이동 → 6h 동결"])
            fires.append("G6")
    st["frozen"] = frozen

    # G7 rank step (class history, two consecutive samples)
    ch = tail_jsonl(CLASS, 3)
    if len(ch) == 3:
        def me(r):
            return (r.get("p") or {}).get("yms979") or [None, None, None]
        a, b, c = me(ch[0]), me(ch[1]), me(ch[2])
        for idx, role, thr in ((2, "buyer", -0.009), (1, "shop", -0.007)):
            if None not in (a[idx], b[idx], c[idx]) and b[idx] - a[idx] <= thr and c[idx] <= b[idx] + 0.002:
                if cooled(st, f"G7:{role}", 2 * 3600, now):
                    wake(role, f"G7 rank step: class {role} {a[idx]} -> {b[idx]} -> {c[idx]}")
                    fired(st, f"G7:{role}", now)
                    fires.append("G7")

    # G8 stars slide (shop_parts.jsonl, board stars_rank of us)
    parts = tail_jsonl(PARTS, 12)
    if len(parts) >= 2:
        recent = [p for p in parts if now - p.get("t", 0) <= 2.5 * 3600 and (p.get("us") or {}).get("stars_rank")]
        if len(recent) >= 2:
            best = min(p["us"]["stars_rank"] for p in recent)
            cur = recent[-1]["us"]["stars_rank"]
            if cur - best >= 2 and cooled(st, "G8", 6 * 3600, now):
                wake("shop", f"G8 stars slide: board stars_rank {best} -> {cur} within 2.5 h")
                alert([f"G8 별점 순위 하락: {best}위 → {cur}위 (2.5h 안)"])
                fired(st, "G8", now)
                fires.append("G8")

    # G9 book lead closure (board, 6 h slopes kept in state)
    try:
        import urllib.request
        import board_api
        bd = board_api.fetch(timeout=15, tries=2, wait=12)      # 11-11: never the ~1 min zero-history board after a URL move
        shops = [s for s in bd["shops"] if s.get("book") is not None]
        mine = next(s for s in shops if s["name"] == "yms979")
        rival = max((s for s in shops if s["name"] != "yms979"), key=lambda s: s["book"])
        hist = [h for h in (st.get("book_hist") or []) if now - h["t"] <= 6.5 * 3600]
        hist.append({"t": now, "us": mine["book"], "rival": rival["name"], "rb": rival["book"]})
        st["book_hist"] = hist
        base = next((h for h in hist if now - h["t"] >= 5 * 3600 and h["rival"] == rival["name"]), None)
        if base:
            # 11-12: slopes fitted through every reading since `base` (trendfit.py): the lead moves +-3 M between
            # readings, and the two-reading slope used until now turned that into +-.5 M/h (+-70 M at the round's end)
            import trendfit
            same = [h for h in hist if h["t"] >= base["t"] and h["rival"] == rival["name"]]
            fu, fr = trendfit.linfit([(h["t"], h["us"]) for h in same]), trendfit.linfit([(h["t"], h["rb"]) for h in same])
            fl = trendfit.linfit([(h["t"], h["us"] - h["rb"]) for h in same])
            ours, theirs = fu["slope"], fr["slope"]
            st["book_fit"] = {"lead_M_h": round(fl["slope"] / 1e6, 2), "se_M_h": round((fl["se"] or 0) / 1e6, 2),
                              "flat": fl["flat"], "n": fl["n"], "span_h": round(fl["span_h"], 1),
                              "ours_M_h": round(ours / 1e6, 2), "rival_M_h": round(theirs / 1e6, 2), "rival": rival["name"]}
            lead = mine["book"] - rival["book"]
            eta = lead / (theirs - ours) if theirs > ours else None
            st["book_eta_h"] = round(eta, 1) if eta else None
            # 11-11 22:45: 72 h was a fixed bar; with 138 h left a lead closing in 150-200 h is thin too (the evening's
            # decisions, no grade C sales, a stock buffer, food under cost, take ~1-1.5 M/h off the book). The bar is
            # now 1.5 x the hours left (at least 72 h); the phone alert goes out under 1.15 x the hours left.
            left9 = max(0.0, (1794927540 - now) / 3600)
            st["book_lead_at_end_M"] = round((lead - (theirs - ours) * left9) / 1e6) if theirs > ours else round(lead / 1e6)
            # G20 (11-13): grade B goods back on sale when the book lead projected to the end falls under 120 M, off
            # again over 200 M (sell_b 0 since 11-12 15:46 for stars; book 1st only needs a positive lead, but the
            # lead fell 1.2 M/h on the evening of 11-13 as 25620027 gained 1.26 M/h)
            try:
                pa_path = HERE / "params_auto.json"
                pa = json.loads(pa_path.read_text())
                g20 = pa.setdefault("globals", {})
                end_m = st["book_lead_at_end_M"]
                # 11-13 09:55: grade B is on again for everyone but the accounts that rate it lower (seller
                # a_only_buyers.json), so G20 only ever turns it ON (it no longer switches B off over 200 M)
                want_b = 1 if end_m < 120 else g20.get("sell_b", 1)
                if want_b != g20.get("sell_b", 0) and not DRY:
                    g20["sell_b"] = want_b
                    pa_path.write_text(json.dumps(pa, ensure_ascii=False, indent=1) + "\n")
                    log(f"G20 sell_b -> {want_b} (book lead at the end {end_m} M)")
                    alert([f"상점 B등급 판매 {'재개' if want_b else '중단'}: 장부 1위 격차가 종료 시 약 {end_m}M 예상"])
                    fires.append("G20")
            except (OSError, ValueError) as e:
                log(f"G20 skipped: {e!r}")
            if eta is not None and 0 < eta < max(72, 1.5 * left9) and cooled(st, "G9", 3 * 3600, now):
                # 11-14 (user: 'do you find the cause and act?'): the wake carries the 6 h cause breakdown
                try:
                    import market_picture as _mp
                    cz_ = _mp.book_causes(6)
                    cz_txt = (f"; 6 h causes: margin {cz_['margin']:+.1f} M, refunds {cz_['refunds']:+.1f} M, storage "
                              f"{cz_['storage']:+.1f} M, expiry {cz_['expire']:+.1f} M, net {cz_['net']:+.1f} M; biggest refunds "
                              f"{cz_['top_refunds']}. Act on the largest negative item or say with numbers why it is gone"
                              ) if cz_ else ""
                except Exception:  # noqa: BLE001
                    cz_txt = ""
                wake("shop", f"G9 book: lead {lead / 1e6:.0f}M over {rival['name']} closes in {eta:.0f} h (us {ours / 1e6:+.1f} M/h, them {theirs / 1e6:+.1f} M/h){cz_txt}")
                if eta < max(72, 1.15 * left9) and cooled(st, "G9a", 6 * 3600, now):
                    alert([f"G9 장부 격차 {lead / 1e6:.0f}M이 {eta:.0f}h 안에 닫힐 속도입니다 (남은 시간 {left9:.0f}h; {rival['name']} {theirs / 1e6:+.1f} M/h, 우리 {ours / 1e6:+.1f} M/h)"])
                    fired(st, "G9a", now)
                fired(st, "G9", now)
                fires.append("G9")
    except Exception as e:
        log(f"G9 skipped: {e!r}")

    # G17 food loss limit (user 11-11 21:40: 'do food wide; stop if the loss gets heavy'). Since the wide food
    # programme started: what we paid for food units (every food unit seen in stock with supplied_at after the start,
    # plus food units sold before a tick saw them) minus what paid food sales brought in. Over FOOD_LOSS_24H in any
    # 24 h or FOOD_LOSS_TOTAL in all -> params food_wide.on = false and must_stock = {} (no more food is bought; the
    # food in stock is still sold), phone alert.
    try:
        fl = st.setdefault("food_loss", {"start": now, "units": {}, "sold": {}})
        cfg17 = json.loads((HERE / "params.json").read_text())
        on17 = bool((cfg17["shop"]["base"].get("food_wide") or {}).get("on")) or bool(cfg17["shop"]["base"].get("must_stock"))
        if on17 and not fl.get("stopped"):
            inv17 = json.loads((MD / "inventory.json").read_text()).get("units") or []
            for u in inv17:
                if u.get("kind") == "food" and (u.get("supplied_at") or 0) >= fl["start"]:
                    fl["units"][u["unit"]] = [u.get("supplied_at"), u.get("cost") or 0]
            paid17 = {d.get("unit") for d in (json.loads((MD / "shop_value.json").read_text()).get("deals") or []) if d.get("shop") == "yms979"}
            cat17 = json.loads((MD / "catalog.json").read_text())
            with open(HERE / "seller_r3_log.jsonl", encoding="utf-8") as f17:
                for line in f17.readlines()[-20000:]:
                    if '"deal"' not in line:
                        continue
                    try:
                        r17 = json.loads(line)
                    except ValueError:
                        continue
                    if (r17.get("event") == "deal" and r17.get("t", 0) >= fl["start"] and r17.get("unit") in paid17
                            and (cat17.get(r17.get("sku")) or {}).get("kind") == "food"):
                        fl["sold"][r17["unit"]] = [r17["t"], r17.get("price") or 0, r17.get("cost") or 0]
            def _loss(since):
                bought = sum(c for t_, c in fl["units"].values() if (t_ or 0) >= since)
                bought += sum(c for u_, (t_, p_, c) in fl["sold"].items() if u_ not in fl["units"] and t_ >= since)
                return bought - sum(p_ for t_, p_, c in fl["sold"].values() if t_ >= since)
            l24, ltot = _loss(now - 86400), _loss(fl["start"])
            fl["loss_24h_M"], fl["loss_total_M"] = round(l24 / 1e6, 2), round(ltot / 1e6, 2)
            fl["sales"] = len(fl["sold"])
            # user 21:45: 'if the stars rise more than expected and keep rising, it may go on'. The board's stars_rank
            # (2 decimals) is kept per tick; rising = at least +0.01 against 24 h ago (or against the start while the
            # programme is younger). While it rises the soft limits do not stop the programme; FOOD_LOSS_HARD does.
            try:
                sr17 = float(mine.get("stars_rank"))
                hist17 = [h for h in fl.setdefault("stars_hist", []) if now - h[0] <= 48 * 3600]
                hist17.append([now, sr17])
                fl["stars_hist"] = hist17
                fl.setdefault("stars_start", sr17)
                ref17 = next((h[1] for h in hist17 if now - h[0] <= 24 * 3600), fl["stars_start"]) if now - fl["start"] > 24 * 3600 else fl["stars_start"]
                rising17 = sr17 >= ref17 + 0.01
                fl["stars_now"], fl["stars_ref"], fl["stars_rising"] = sr17, ref17, rising17
            except Exception:
                rising17 = False
            over17 = l24 > FOOD_LOSS_24H or ltot > FOOD_LOSS_TOTAL
            if over17 and rising17 and ltot <= FOOD_LOSS_HARD:
                if cooled(st, "G17keep", 12 * 3600, now):
                    log(f"G17 food loss {l24 / 1e6:.1f} M in 24 h, {ltot / 1e6:.1f} M in all is over the soft limit, but stars_rank rose {fl.get('stars_ref')} -> {fl.get('stars_now')}: kept (hard limit {FOOD_LOSS_HARD / 1e6:.0f} M)")
                    alert([f"G17 식품 손해가 기준을 넘었지만(24시간 {l24 / 1e6:.1f}M, 누적 {ltot / 1e6:.1f}M) 별점이 {fl.get('stars_ref')} → {fl.get('stars_now')}로 오르는 중이라 계속합니다. 누적 {FOOD_LOSS_HARD / 1e6:.0f}M에서 멈춥니다."])
                    fired(st, "G17keep", now)
                over17 = False
            if over17:
                b17 = cfg17["shop"]["base"]
                if not DRY:
                    b17["food_wide"] = dict(b17.get("food_wide") or {}, on=False)
                    if b17.get("must_stock"):
                        b17["must_stock_off"] = b17["must_stock"]
                        b17["must_stock"] = {}
                    tmp17 = HERE / "params.json.tmp"
                    tmp17.write_text(json.dumps(cfg17, ensure_ascii=False, indent=1), encoding="utf-8")
                    tmp17.replace(HERE / "params.json")
                    fl["stopped"] = now
                log(f"G17 food loss {l24 / 1e6:.1f} M in 24 h, {ltot / 1e6:.1f} M in all (limits {FOOD_LOSS_24H / 1e6:.0f} / {FOOD_LOSS_TOTAL / 1e6:.0f}): food restock switched off")
                alert([f"G17 식품 손해가 한도를 넘어 식품 재입고를 껐습니다 (24시간 {l24 / 1e6:.1f}M, 누적 {ltot / 1e6:.1f}M; 판매 {len(fl['sold'])}건). 남은 식품은 계속 판매합니다."])
                fires.append("G17")
    except Exception as e:
        log(f"G17 skipped: {e!r}")

    # G16 star foods (user 11-11 21:25: 'if it does not seem to work, just stop stocking food'): the must_stock foods
    # are kept for two accounts (JangSeongWoong, gom31-certified-goods). If neither has left a PAID review of our shop
    # STAR_HOURS after the start, restock of them is switched off (params.json shop.base.must_stock -> {}, the list is
    # kept under must_stock_off); the food already in stock is still sold.
    try:
        sf = st.setdefault("star_food", {"start": now})
        if not sf.get("decided") and not G16_ACTIVE:      # 21:40: the user widened the food trial; G17's loss limit decides
            sf["decided"], sf["result"] = True, "superseded by the wide food trial (G17 loss limit)"
        if not sf.get("decided"):
            sv16 = json.loads((MD / "shop_value.json").read_text())
            hits = [r for r in (sv16.get("our_ratings") or []) if r[0] >= sf["start"] and r[1] in STAR_ACCOUNTS]
            if hits:
                sf["hits"] = len(hits)
                sf["last_hit"] = max(r[0] for r in hits)
            if now - sf["start"] >= STAR_HOURS * 3600:
                sf["decided"] = True
                if not sf.get("hits"):
                    cfg16 = json.loads((HERE / "params.json").read_text())
                    b16 = cfg16["shop"]["base"]
                    if b16.get("must_stock") and not DRY:
                        b16["must_stock_off"] = b16["must_stock"]
                        b16["must_stock"] = {}
                        tmp16 = HERE / "params.json.tmp"
                        tmp16.write_text(json.dumps(cfg16, ensure_ascii=False, indent=1), encoding="utf-8")
                        tmp16.replace(HERE / "params.json")
                    sf["result"] = "off"
                    log(f"G16 star foods: no paid review from {sorted(STAR_ACCOUNTS)} in {STAR_HOURS} h -> must_stock switched off")
                    alert([f"G16 별점용 식품: {STAR_HOURS}시간 동안 JangSeongWoong·gom31의 결제 리뷰가 없어 식품 재입고를 껐습니다 (남은 식품은 계속 판매)."])
                    fires.append("G16")
                else:
                    sf["result"] = "kept"
                    log(f"G16 star foods: {sf['hits']} paid review(s) from the target accounts in {STAR_HOURS} h -> kept")
    except Exception as e:
        log(f"G16 skipped: {e!r}")

    # G15 gains left on the table (user 11-11): more than 6 skipped orders an hour whose expected v beat m* -> wake the
    # buyer strategist (<= 1 per 2 h) to lower the bar on that kind
    try:
        sc15 = json.loads((MD / "buyer_score.json").read_text())
        ab = ((sc15.get("skips") or {}).get("above_mstar_per_h") or {})
        worst = max(ab.items(), key=lambda kv: kv[1], default=(None, 0))
        if worst[1] > 6 and cooled(st, "G15", 2 * 3600, now):
            wake("buyer", f"G15 {worst[1]} {worst[0]} orders/h skipped although their expected v beat m* {sc15.get('m_star')}: "
                          "each would raise the final saved; lower that kind's bar (skip_delta / goods_delta) or say why not")
            fired(st, "G15", now)
            fires.append("G15")
    except Exception as e:
        log(f"G15 skipped: {e!r}")

    # G14 projected final rank (user 11-11: 'finish 1st; the shop must hold 1st whatever it takes'): every 2 h,
    # ops/final_rank.py at our current buyer pace; projected under 1st (damped view) -> wake the shop strategist with
    # the threat and its shop slope, phone alert at most every 12 h
    try:
        if cooled(st, "G14chk", 2 * 3600, now):
            fired(st, "G14chk", now)
            sys.path.insert(0, str(HERE / "ops"))
            import final_rank as _fr
            sc_ = json.loads((MD / "buyer_score.json").read_text())
            v6_ = sc_.get("v6") or {}
            # 11-11 19:20: part-by-part projection (book, revenue, stars, saved, deals, posts each at its own slope)
            # instead of the shop score carried forward in a line, which named dragonash22 1st while its book fell
            comp = _fr.project_components()
            if comp:
                st["final_rank"] = {"rank": comp["us"]["rank_end"], "total": comp["us"]["total_end"], "t": now, "method": "components"}
                # the shops of everyone projected above us or within .05 of us: our buyer does not buy GOODS there
                # (a 1 M bed is 1 M of revenue and ~0.1-0.3 M of book for a rival whose ranks sit a few tens of M
                # from the next shop; food tickets are too small to matter and stay open)
                # 11-11 23:30: 'within .05 of us' shut out the six largest warranted-furniture shops and goods orders were
                # left with one or two candidate shops (26 of 28 goods orders skipped) just when the user asked for
                # volume. Now only the shops of students projected ABOVE us; the shops we race on revenue and book are
                # avoided through market_buyer.avoid_shops and price_rivals.json.
                cont = {r["shop_name"]: f"{r['player']} projected {r['total_end']} (us {comp['us']['total_end']})"
                        for r in comp["rows"] if r["player"] != "yms979" and r["total_end"] > comp["us"]["total_end"]}
                if not DRY:
                    (MD / "contenders.json").write_text(json.dumps({"t": now, "shops": cont}, ensure_ascii=False))
                # 11-12 (user: 'its shop is rising, can we hold it back?'): the shop of the nearest challenger on the
                # projected class total (within .03, a tie) is a STOCK rival only: restock.py holds its best sellers so
                # its buyers find them with us too. Not a price rival: the seller's prices do not follow it.
                ch_ = sorted((r for r in comp["rows"] if r["player"] != "yms979"
                              and abs(r["total_end"] - comp["us"]["total_end"]) < 0.03),
                             key=lambda r: abs(r["total_end"] - comp["us"]["total_end"]))
                sr_ = {r["shop_name"]: f"{r['player']} projected {r['total_end']} (us {comp['us']['total_end']})" for r in ch_[:1]}
                if not DRY:
                    (MD / "stock_rivals.json").write_text(json.dumps({"t": now, "shops": sr_}, ensure_ascii=False))
                if comp["us"]["rank_end"] > 1:
                    ahead = [r for r in comp["rows"] if r["rank_end"] < comp["us"]["rank_end"]]
                    msg = ", ".join(f"{r['player']} {r['total_end']} (shop {r['shop'][0]}>{r['shop'][1]}, buyer {r['buyer'][0]}>{r['buyer'][1]})" for r in ahead[:4])
                    me_ = next(r for r in comp["rows"] if r["player"] == "yms979")
                    mine_txt = f"us {me_['total_end']}: shop {me_['shop'][0]}>{me_['shop'][1]}, buyer {me_['buyer'][0]}>{me_['buyer'][1]} (saved rank {me_['ranks_now_end']['saved']}, deals rank {me_['ranks_now_end']['deals']})"
                    for role in ("shop", "buyer"):
                        wake(role, f"G14 projected final rank {comp['us']['rank_end']} by parts; ahead: {msg}. {mine_txt}. "
                                   "Tool final_rank shows every part; gaps under .03 are a tie.")
                    fires.append("G14")
                    # 11-12 23:20 (user: no future ranks to the phone): the wake stays, the phone alert is gone
            else:
                pr = _fr.project(float(v6_.get("deals_h") or 30), float(v6_.get("all") or 0.40))
                st["final_rank"] = {"damped": pr["us"]["rank_damped"], "trend": pr["us"]["rank_trend"], "t": now}
    except Exception as e:
        log(f"G14 skipped: {e!r}")

    # G18 flat raters (user 11-12 14:40, chose 'stop selling'): an account that gives EVERY shop the same low stars
    # whatever the price or grade (sergiobuilds: 4,340 reviews in 36 h, all 2.00, 104 of our sales a day; at that
    # pace our stars_rank -0.017 by the end, 3-5 star ranks) is declined by the seller (blocked_buyers.json, matched on
    # its first-message shape, unique to it in 116 of 116 joined uses). Released automatically when its last 24 h
    # stop being flat (mean > 2.5 or sd > 0.3 over >= 100 paid reviews of all shops).
    try:
        import sqlite3
        import statistics as _st
        FLAT = {"sergiobuilds": [r"^I want to buy .+\(G[0-9a-f]{8}\)\. What is your price\?$"]}
        db_ = sqlite3.connect(f"file:{HERE / 'data' / 'market_archive.db'}?mode=ro", uri=True, timeout=30)
        stars_ = {k: [] for k in FLAT}
        for (d_,) in db_.execute("SELECT data FROM ledger WHERE kind='result' AND ts > ?", (now - 24 * 3600,)):
            x_ = json.loads(d_).get("data") or {}
            if x_.get("buyer") in stars_ and x_.get("receipt") and x_.get("verified") and x_.get("stars") is not None:
                stars_[x_["buyer"]].append(x_["stars"])
        bl_path = HERE / "blocked_buyers.json"
        bl = json.loads(bl_path.read_text()) if bl_path.exists() else {}
        changed = False
        for name, pats in FLAT.items():
            L_ = stars_[name]
            flat = len(L_) >= 100 and _st.mean(L_) <= 2.5 and _st.pstdev(L_) <= 0.3
            if flat and name not in bl:
                bl[name] = pats
                changed = True
                alert([f"G18 {name}: 24시간 리뷰 {len(L_)}건 평균 {_st.mean(L_):.2f} (모든 상점에 같은 점수) -> 판매 중단"])
            elif not flat and name in bl and len(L_) >= 100:
                bl.pop(name)
                changed = True
                alert([f"G18 {name}: 24시간 평균 {_st.mean(L_):.2f}, 편차 {_st.pstdev(L_):.2f} -> 정상 평가로 판단, 판매 재개"])
        if changed and not DRY:
            bl_path.write_text(json.dumps(bl, ensure_ascii=False, indent=1))
            log(f"G18 blocked_buyers -> {sorted(bl)}")
    except Exception as e:
        log(f"G18 skipped: {e!r}")

    # G21 / G22 (user 11-13 14:00: 'you should handle these yourself, why do I have to point them out?'): side effects
    # of our own shop settings, checked every tick. G21: a class-total contender's buyer (total within .15 of ours)
    # buying >= 20% of its deals from us over 3 h at a per-deal v >= .03 above what it gets elsewhere (our clearance
    # at cost fed 25620027 65 of 108 goods deals at v .916 on 11-13). G22: our shop's paid deals over 2 h under half
    # of the trailing 12 h rate (grade-A-only cut them 120 -> 30/h on 11-12 and nobody noticed for 6 h).
    try:
        import sqlite3
        import cost_model as _cm
        db21 = sqlite3.connect(f"file:{HERE / 'data' / 'market_archive.db'}?mode=ro", uri=True, timeout=30)
        names21, ours21 = {}, set()
        for (d_,) in db21.execute("SELECT data FROM ledger WHERE kind='join'"):
            x_ = json.loads(d_).get("data") or {}
            if x_.get("url") and x_.get("name"):
                names21[x_["url"].rstrip("/")] = x_["name"]
                if x_["name"] == "yms979":
                    ours21.add(x_["url"].rstrip("/"))
        ch_ = [json.loads(l_) for l_ in open(MD / "class_history.jsonl")][-1]["p"]
        me_t = ch_["yms979"][0]
        contenders = {p_: v_ for p_, v_ in ch_.items() if p_ != "yms979" and me_t - v_[0] < 0.15}
        bname = {p_["player"]: (p_.get("buyer") or {}).get("name") for p_ in bd["players"]}
        # 11-16 (user: our dyishappy discount lifted its buyer 11th -> 8th past ours, and nothing flagged it): also the
        # students whose buyer board score is within .03 of ours
        ball_ = {x_["name"]: x_.get("all") for x_ in bd.get("buyers", [])}
        my_all = ball_.get(bname.get("yms979"))
        if my_all is not None:
            for p_ in bd["players"]:
                a_ = ball_.get(bname.get(p_["player"]))
                if p_.get("student") and p_["player"] != "yms979" and a_ is not None and abs(a_ - my_all) <= 0.03:
                    contenders.setdefault(p_["player"], ch_.get(p_["player"]) or [None])
        cat21 = json.loads((MD / "catalog.json").read_text(encoding="utf-8"))
        rows21 = [(ts_, json.loads(d_).get("data") or {}) for ts_, d_ in db21.execute(
            "SELECT ts, data FROM ledger WHERE kind='settle' AND ts > ?", (now - 12 * 3600,))]
        uc21 = _cm.unit_costs([r_["unit"] for _, r_ in rows21 if now - _ < 3 * 3600])
        why21 = []
        for p_ in contenders:
            bn_ = bname.get(p_)
            L_ = [r_ for ts_, r_ in rows21 if now - ts_ < 3 * 3600 and r_.get("buyer") == bn_ and r_.get("sku") in cat21
                  and r_["unit"] in uc21]
            if len(L_) < 30:
                continue
            def v_(r_):
                lp_ = cat21[r_["sku"]]["list_price"]; c_ = uc21[r_["unit"]]
                return 1.0 if lp_ <= c_ else min(1.0, (lp_ - r_["amount"]) / (lp_ - c_))
            us_ = [v_(r_) for r_ in L_ if str(r_.get("shop")).rstrip("/") in ours21]
            el_ = [v_(r_) for r_ in L_ if str(r_.get("shop")).rstrip("/") not in ours21]
            if len(us_) >= 0.2 * len(L_) and el_ and sum(us_) / len(us_) >= sum(el_) / len(el_) + 0.03:
                why21.append(f"{p_} (buyer {bn_}, total {contenders[p_][0]}, buyer board {ball_.get(bn_)} vs ours {my_all}) bought {len(us_)} of its {len(L_)} deals in 3 h from us "
                             f"at v {sum(us_) / len(us_):.3f} vs {sum(el_) / len(el_):.3f} elsewhere")
        if why21 and cooled(st, "G21", 3 * 3600, now):
            fired(st, "G21", now)
            wake("shop", "G21 our shop feeds a class-total contender's buyer: " + " | ".join(why21)
                 + ". Find which of our prices (clearance, star_buyers, slow units, rival_under) gives it this and fix it for everyone.")
            alert(["상점이 경쟁 학생 buyer에게 유리하게 팔고 있음: " + " / ".join(why21)[:300] + " -> 상점 전략가가 가격 점검 중"])
            fires.append("G21")
        mine22 = [ts_ for ts_, r_ in rows21 if str(r_.get("shop")).rstrip("/") in ours21]
        r2 = sum(1 for t_ in mine22 if now - t_ < 2 * 3600) / 2
        r12 = len(mine22) / 12
        if r12 >= 20 and r2 < 0.5 * r12 and cooled(st, "G22", 3 * 3600, now):
            fired(st, "G22", now)
            wake("shop", f"G22 our paid sales fell to {r2:.0f}/h over 2 h from {r12:.0f}/h over 12 h: find the cause "
                         "(a setting we changed, stock, prices, the market) and fix it")
            alert([f"상점 판매 급감: 최근 2시간 시간당 {r2:.0f}건 (12시간 평균 {r12:.0f}건) -> 상점 전략가가 원인 점검 중"])
            fires.append("G22")
        st["g21_g22"] = {"contenders": list(contenders), "shop_deals_h_2h": r2, "shop_deals_h_12h": round(r12, 1), "g21": why21}
    except Exception as e:
        log(f"G21/G22 skipped: {e!r}")

    # G23 unwarranted grade A (user 11-16: 'the warranty alerts keep coming, handle it yourself'). The buyer pays a
    # goods receipt without a 6 h warranty only for an honest grade A claim whose price clears the floor after the
    # break risk (market_buyer, 11-13). The agents kept reporting it as a defect. On the 72 h to 11-16 02:00 it was the
    # best goods bucket: 796 deals, 14 breaks (1.8%, all unrefunded, -1 each), v .869 against .827 for warranted A.
    # Checked every tick on deals 6-30 h old (every break happens within 6 h): if the unwarranted A mean, breaks at -1,
    # falls .02 under the warranted A mean over >= 100 deals, pay_unwarranted_A goes to 0 for 12 h and the user is told
    # why; otherwise nothing is sent.
    try:
        import sqlite3
        import cost_model as _cm
        db23 = sqlite3.connect(f"file:{HERE / 'data' / 'market_archive.db'}?mode=ro", uri=True, timeout=30)
        cat23 = json.loads((MD / "catalog.json").read_text(encoding="utf-8"))
        br23 = {}
        for (d_,) in db23.execute("SELECT data FROM ledger WHERE kind='broken' AND buyer='yms979' AND ts > ?",
                                  (now - 31 * 3600,)):
            x_ = json.loads(d_).get("data") or {}
            br23[x_.get("unit")] = x_
        rows23 = [json.loads(d_).get("data") or {} for (d_,) in db23.execute(
            "SELECT data FROM ledger WHERE kind='settle' AND buyer='yms979' AND ts > ? AND ts < ?",
            (now - 30 * 3600, now - 6 * 3600))]
        rows23 = [r_ for r_ in rows23 if (cat23.get(r_.get("sku")) or {}).get("kind") == "goods" and r_.get("claim") == "A"]
        uc23 = _cm.unit_costs([r_["unit"] for r_ in rows23])
        bk23 = {"none": [], "warr": []}
        for r_ in rows23:
            if r_["unit"] not in uc23:
                continue
            lp_, c_ = cat23[r_["sku"]]["list_price"], uc23[r_["unit"]]
            b_ = br23.get(r_["unit"])
            v_ = (-1.0 if b_ and not b_.get("refunded") else 0.0 if b_ else
                  1.0 if lp_ <= c_ else min(1.0, (lp_ - r_["amount"]) / (lp_ - c_)))
            bk23["warr" if (r_.get("warranty_hours") or 0) >= 6 else "none"].append((v_, bool(b_)))
        N_, W_ = bk23["none"], bk23["warr"]
        g23 = {"none_n": len(N_), "warr_n": len(W_)}
        if N_ and W_:
            mn_, mw_ = sum(v for v, _ in N_) / len(N_), sum(v for v, _ in W_) / len(W_)
            g23.update(none_v=round(mn_, 3), warr_v=round(mw_, 3), none_breaks=sum(b for _, b in N_))
            if len(N_) >= 100 and mn_ < mw_ - 0.02 and cooled(st, "G23", 12 * 3600, now):
                fired(st, "G23", now)
                override(ov, "pay_unwarranted_A", 0, 12, now)
                alert([f"보증 없는 A등급 구매가 손해로 바뀜: 최근 24시간 {len(N_)}건 v {mn_:.3f} "
                       f"(고장 {g23['none_breaks']}건, 건당 -1) < 보증 A {mw_:.3f} -> 12시간 동안 보증 없는 상품은 결제하지 않음"])
                fires.append("G23")
        st["g23"] = g23
    except Exception as e:
        log(f"G23 skipped: {e!r}")

    # G19 saved watch (user 11-12 23:50: 'saved changes in real time; when it moves past a threshold, wake the buyer
    # or adjust the negotiation'). The floors already follow m* every tick (vprice v_floor_rel); this wakes the buyer
    # strategist (no phone alert) when (a) our board-implied v over the last 2 h runs .03 under its 6 h value or
    # under m*, (b) a student passes us or is passed in saved, or (c) peer_bench finds a student beating our per-deal
    # v by >= .04 with a same-shop gap >= .08 (n >= 10 each), once per 6 h per student. Cool-down 90 min overall.
    try:
        import sqlite3
        db19 = sqlite3.connect(f"file:{HERE / 'data' / 'market_archive.db'}?mode=ro", uri=True, timeout=30)
        def bpace(hours):
            r_ = db19.execute("SELECT ts, data FROM board WHERE ts <= ? ORDER BY ts DESC LIMIT 1", (now - hours * 3600,)).fetchone()
            o_ = next(y for y in json.loads(r_[1])["buyers"] if y["name"] == "yms979")
            x_ = next(y for y in bd["buyers"] if y["name"] == "yms979")
            dd_ = x_["deals"] - o_["deals"]
            return (x_["saved"] * (x_["deals"] + 100) - o_["saved"] * (o_["deals"] + 100)) / max(1, dd_), dd_
        v2, n2 = bpace(2)
        v6, n6 = bpace(6)
        ms19 = (json.loads((MD / "buyer_score.json").read_text()) or {}).get("m_star")
        why19 = []
        if n2 >= 60 and (v2 < v6 - 0.03 or (ms19 and v2 < ms19)):
            why19.append(f"board v 2h {v2:.3f} vs 6h {v6:.3f} (m* {ms19})")
        names19 = {(p_.get("buyer") or {}).get("name") for p_ in bd["players"] if p_.get("student")}
        order19 = [x_["name"] for x_ in sorted((y for y in bd["buyers"] if y["name"] in names19 and y.get("saved") is not None),
                                               key=lambda y: -y["saved"])]
        i19 = order19.index("yms979")
        nb19 = order19[max(0, i19 - 2): i19 + 3]
        old19 = st.get("saved_order")
        if old19 and old19.get("rank") != i19 + 1:
            why19.append(f"saved rank {old19['rank']} -> {i19 + 1} (around us now: {', '.join(nb19)})")
        st["saved_order"] = {"rank": i19 + 1, "around": nb19, "t": now}
        try:
            pb = json.loads((MD / "peer_bench.json").read_text())
            for e_ in pb.get("better") or []:
                big = [z for z in e_.get("same_shop") or [] if z["their_n"] >= 10 and z["our_n"] >= 10
                       and z["their_v"] - z["our_v"] >= 0.08]
                if e_.get("v_gap", 0) >= 0.04 and big and cooled(st, f"G19:{e_['name']}", 6 * 3600, now):
                    fired(st, f"G19:{e_['name']}", now)
                    why19.append(f"{e_['name']} ({e_.get('player')}) beats our per-deal v by {e_['v_gap']:+.3f}; same shop: "
                                 + "; ".join(f"{z['shop']} {z['kind']} theirs {z['their_v']} at x {z['their_x']} vs ours "
                                             f"{z['our_v']} at x {z['our_x']}" for z in big[:3])
                                 + (f"; opens at our shop at {e_['opening_at_our_shop']}" if e_.get("opening_at_our_shop") else ""))
        except (OSError, ValueError):
            pass
        if why19 and cooled(st, "G19", 90 * 60, now):
            fired(st, "G19", now)
            wake("buyer", "G19 saved watch: " + " | ".join(why19) + ". Evidence 'peer_benchmark' has the details; "
                 "apply what fits (knobs) and say what you took from it.")
            fires.append("G19")
    except Exception as e:
        log(f"G19 skipped: {e!r}")

    # price rivals (user 11-11 23:00: 'no need to be the cheapest in the market; match the rival shop for each part,
    # revenue: inextro and so on, and sell a little under THAT shop'): the student shops next to us on each part of
    # the shop score, written for seller_r3 every tick.
    try:
        studs_ = {(p_.get("shop") or {}).get("name") for p_ in bd["players"] if p_.get("student")}
        ss_ = [s_ for s_ in bd["shops"] if s_["name"] in studs_ and s_.get("deals")]
        pr_ = {}
        # 11-12 (user: 'widen the book; revenue 1st is not worth it; sell what the shop catching up on the book
        # sells'): only the book neighbours are matched now. inextro (revenue ahead) settles grade A at .683 against
        # 25620027's .712, and the seller takes the LOWEST rival median, so racing inextro cost .03 of list a unit.
        for part_ in ("book",):
            L_ = sorted((s_ for s_ in ss_ if s_.get(part_) is not None), key=lambda s_: -s_[part_])
            i_ = next((k_ for k_, s_ in enumerate(L_) if s_["name"] == "yms979"), None)
            if i_ is None:
                continue
            if i_ > 0:
                pr_.setdefault(L_[i_ - 1]["name"], []).append(f"{part_} ahead of us")
            if i_ + 1 < len(L_):
                pr_.setdefault(L_[i_ + 1]["name"], []).append(f"{part_} right behind us")
            # 11-16 (user: 'think of other ways'): the second shop behind on the book too while it is within 200 M
            # (inextro 1,060 M at +3-6 M/h on 1-3 h against our 1,189 M flat; passing us costs .011 like the first)
            if i_ + 2 < len(L_) and L_[i_][part_] - L_[i_ + 2][part_] < 200e6:
                pr_.setdefault(L_[i_ + 2]["name"], []).append(f"{part_} second behind us")
        # 11-12 16:30 (user: 'can we hold inextro back?'): the nearest challenger on the projected class TOTAL
        # (stock_rivals.json, G14) is a price rival too: the seller sits just under its median settle of each SKU and
        # grade, so price-led buyers of its best sellers (which restock already holds) can buy them from us. Same price
        # for every buyer. Its book is projected 4th -> 2nd (+1.8 M/h, ~.022 to its total); ours only needs its 150 M
        # floor (projected ~1.5 B vs ~1.04 B), and price_ctl raises prices again if that floor is threatened.
        try:
            for n_, w_ in (json.loads((MD / "stock_rivals.json").read_text()).get("shops") or {}).items():
                if n_ != "yms979":
                    pr_.setdefault(n_, []).append(f"class total challenger ({w_})")
        except (OSError, ValueError):
            pass
        if pr_ and not DRY:
            (MD / "price_rivals.json").write_text(json.dumps({"t": now, "shops": pr_}, ensure_ascii=False))
        st["price_rivals"] = pr_
    except Exception as e:
        log(f"price rivals skipped: {e!r}")

    # G13 revenue 2nd (user 11-11: never lose it): the student shop right below us in revenue, 3 h slopes
    try:
        studs = {(p.get("shop") or {}).get("name") for p in bd["players"] if p.get("student")}
        rv = sorted([s for s in bd["shops"] if s["name"] in studs and s.get("revenue") is not None], key=lambda s: -s["revenue"])
        i = [s["name"] for s in rv].index("yms979")
        if i + 1 < len(rv):
            below = rv[i + 1]
            rh = [h for h in (st.get("rev_hist") or []) if now - h["t"] <= 3.5 * 3600]
            rh.append({"t": now, "us": mine["revenue"], "who": below["name"], "them": below["revenue"]})
            st["rev_hist"] = rh
            base = next((h for h in rh if now - h["t"] >= 2.5 * 3600 and h["who"] == below["name"]), None)
            if base:
                dt = (now - base["t"]) / 3600
                ours, theirs = (mine["revenue"] - base["us"]) / dt, (below["revenue"] - base["them"]) / dt
                gap = mine["revenue"] - below["revenue"]
                eta = gap / (theirs - ours) if theirs > ours else None
                st["rev_eta_h"] = round(eta, 1) if eta else None
                st["rev_rank"] = i + 1
                left_h = max(0.0, (1794927540 - now) / 3600)          # round end 11-17 23:59 KST
                # 11-17 08:00 (user: 'strategy, events and alarms must serve the book'): revenue 2nd cannot be lost
                # (3rd is 1.5 B behind with 7 h left) and a revenue wake pushes price cuts that cost the book: off
                if False and eta is not None and eta < left_h + 12 and cooled(st, "G13", 2 * 3600, now):
                    wake("shop", f"G13 revenue rank {i + 1}: {below['name']} closes the {gap / 1e9:.2f} B gap in {eta:.0f} h "
                                 f"(them {theirs / 1e6:.1f} M/h, us {ours / 1e6:.1f} M/h); the round has {left_h:.0f} h left")
                    fired(st, "G13", now)
                    fires.append("G13")
                    if eta < 72 and cooled(st, "G13a", 12 * 3600, now):
                        alert([f"G13 매출 {i + 1}위: {below['name']}가 {eta:.0f}h 안에 추월할 속도입니다 (그쪽 {theirs / 1e6:.1f} M/h, 우리 {ours / 1e6:.1f} M/h)"])
                        fired(st, "G13a", now)
    except Exception as e:
        log(f"G13 skipped: {e!r}")

    # G10 budget
    try:
        import model_env
        sp = model_env.spend_summary(now)
        st["spend_24h"] = sp["today"]
        if sp["today"] >= DAILY_CAP:
            st["budget_block_until"] = now + 3600
            if cooled(st, "G10", 3 * 3600, now):
                alert([f"G10 API 비용 24h ${sp['today']:.2f} ≥ 상한 ${DAILY_CAP}: 전략가 정기 실행을 1시간 건너뜁니다(가드 깨우기는 유지)."])
                fired(st, "G10", now)
            fires.append("G10")
        elif sp["today"] >= 0.8 * DAILY_CAP and cooled(st, "G10w", 6 * 3600, now):
            alert([f"G10 API 비용 24h ${sp['today']:.2f} (상한의 80% 이상)"])
            fired(st, "G10w", now)
    except Exception as e:
        log(f"G10 skipped: {e!r}")

    # G12 total race (11-11, user: who can take 1st): the class-total gap to the 2nd shrinks under TOTAL_GAP
    try:
        ch1 = tail_jsonl(CLASS, 1)
        if ch1:
            p = ch1[0].get("p") or {}
            tot = sorted(((v[0], n) for n, v in p.items() if v and v[0] is not None), reverse=True)
            if tot and tot[0][1] == "yms979" and len(tot) > 1:
                gap = tot[0][0] - tot[1][0]
                st["total_gap"] = round(gap, 3)
                if gap < TOTAL_GAP and cooled(st, "G12", 3 * 3600, now):
                    wake("buyer", f"G12 total race: gap to {tot[1][1]} is {gap:.3f}")
                    wake("shop", f"G12 total race: gap to {tot[1][1]} is {gap:.3f}")
                    alert([f"G12 종합 1위 격차 {gap:.3f} ({tot[1][1]}): 두 전략가를 깨웁니다."])
                    fired(st, "G12", now)
                    fires.append("G12")
            elif tot and tot[0][1] != "yms979" and cooled(st, "G12lost", 3 * 3600, now):
                alert([f"G12 종합 1위를 {tot[0][1]}에게 내줬습니다 ({tot[0][0]:.3f} vs 우리 {p.get('yms979', [0])[0]:.3f})"])
                fired(st, "G12lost", now)
                fires.append("G12")
    except Exception as e:
        log(f"G12 skipped: {e!r}")

    # G24 stock turnover (user 11-17: 'shouldn't the periodic agents catch this?'): restock overbought for 15 h
    # (412 M bought, 194 M sold) while 340 asks/h got 'no stock' because the most-asked SKUs were held only as
    # grade C; nothing measured it. Wake the shop with the numbers when fresh overstock (bought in 24 h, beyond 6 h
    # of our sales) passes G24_OVER_M or 'no stock' on SKUs held only as C passes G24_NS_C an hour
    try:
        import market_picture
        tv = market_picture.stock_turnover()
        if tv:
            st["turnover"] = {k: tv[k] for k in ("cash_M", "overstock_bought_24h_M", "no_stock_h")}
            ob, nsc = tv.get("overstock_bought_24h_M") or 0, (tv.get("no_stock_h") or {}).get("held_only_as_C") or 0
            # 11-17 08:00: off for the endgame: unsold stock counts in the book at cost, so clearing it gains nothing and
            # the cuts it prompts cost margin; restock has no cash to overbuy (balance ~10 M)
            if False and (ob > G24_OVER_M or nsc > G24_NS_C) and cooled(st, "G24", 2 * 3600, now):
                top = ", ".join(f"{r['sku']} {r['held']} held / {r['sales_h']}/h" for r in tv["top_overstock"][:3])
                why24 = (f"G24 stock turnover: {ob:.0f} M bought in 24 h beyond 6 h of our sales, 'no stock' on SKUs "
                         f"held only as grade C {nsc}/h, cash {tv['cash_M']} M (top overstock: {top}). Use the "
                         f"stock_turnover tool; the knobs are own_cover_h, big_per_window, big_room_share")
                wake("shop", why24)
                if cooled(st, "G24a", 6 * 3600, now):
                    alert([f"G24 재고 회전: 24시간 안에 산 재고 중 {ob:.0f}M이 6시간 판매분을 넘음, C등급만 있어 품절 {nsc}건/시, "
                           f"현금 {tv['cash_M']}M → 상점 에이전트를 깨웠습니다"])
                    fired(st, "G24a", now)
                fired(st, "G24", now)
                fires.append("G24")
    except Exception as e:
        log(f"G24 skipped: {e!r}")

    # G11 skip share
    ss = (sc.get("skips") or {}).get("share_of_orders_6h")
    if ss is not None and ss > 0.65 and cooled(st, "G11", 6 * 3600, now):
        alert([f"G11 건너뛴 주문 비중 {ss:.0%} (6h): 건너뛰기 기준이 시장보다 높을 수 있습니다 (m* {sc.get('m_star')})"])
        fired(st, "G11", now)
        fires.append("G11")

    st["t"] = now
    st["fires"] = fires
    write(STATE, st)
    write(OVERRIDES, ov)
    log(f"tick: deals1 {deals1} deals6/h {deals6:.0f} orders1 {orders1} fires {fires or '-'} overrides {list(ov) or '-'} "
        f"frozen {list(frozen) or '-'} eta {st.get('book_eta_h')} spend24 {st.get('spend_24h')}")


if __name__ == "__main__":
    main()
