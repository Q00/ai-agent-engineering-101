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
            override(ov, "goods_delta", 0.05, 6, now)
            wake("buyer", f"G2 breaks: goods broke {b3['share']:.0%} over 3 h (market {market_share}); goods_delta 0.05 for 6 h")
            alert([f"G2 고장 급증: 3h 공산품 고장 {b3['share']:.0%} (시장 {market_share}); 등급 상한을 6h 조입니다."])
            fired(st, "G2", now)
        fires.append("G2")

    # G3 deals collapse (orders steady)
    deals1 = sum(1 for t in h1 if t.get("outcome") == "deal")
    deals6 = sum(1 for t in h6 if t.get("outcome") == "deal") / 6
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
            if eta is not None and 0 < eta < max(72, 1.5 * left9) and cooled(st, "G9", 3 * 3600, now):
                wake("shop", f"G9 book: lead {lead / 1e6:.0f}M over {rival['name']} closes in {eta:.0f} h (us {ours / 1e6:+.1f} M/h, them {theirs / 1e6:+.1f} M/h)")
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
                    if cooled(st, "G14a", 12 * 3600, now):
                        alert([f"G14 최종 예상 {comp['us']['rank_end']}위 ({comp['us']['total_end']}); 앞: {msg}"])
                        fired(st, "G14a", now)
            else:
                pr = _fr.project(float(v6_.get("deals_h") or 30), float(v6_.get("all") or 0.40))
                st["final_rank"] = {"damped": pr["us"]["rank_damped"], "trend": pr["us"]["rank_trend"], "t": now}
    except Exception as e:
        log(f"G14 skipped: {e!r}")

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
                if eta is not None and eta < left_h + 12 and cooled(st, "G13", 2 * 3600, now):
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
