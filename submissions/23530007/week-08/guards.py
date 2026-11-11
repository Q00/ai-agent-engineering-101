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
DAILY_CAP = 18.5           # $/24 h: (credit - 60 for Round 4) / days left on 11-11
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
        with urllib.request.urlopen(f"{MARKET}/api/board?round=3", timeout=15) as r:
            bd = json.load(r)
        shops = [s for s in bd["shops"] if s.get("book") is not None]
        mine = next(s for s in shops if s["name"] == "yms979")
        rival = max((s for s in shops if s["name"] != "yms979"), key=lambda s: s["book"])
        hist = [h for h in (st.get("book_hist") or []) if now - h["t"] <= 6.5 * 3600]
        hist.append({"t": now, "us": mine["book"], "rival": rival["name"], "rb": rival["book"]})
        st["book_hist"] = hist
        base = next((h for h in hist if now - h["t"] >= 5 * 3600 and h["rival"] == rival["name"]), None)
        if base:
            dt = (now - base["t"]) / 3600
            ours, theirs = (mine["book"] - base["us"]) / dt, (rival["book"] - base["rb"]) / dt
            lead = mine["book"] - rival["book"]
            eta = lead / (theirs - ours) if theirs > ours else None
            st["book_eta_h"] = round(eta, 1) if eta else None
            if eta is not None and 0 < eta < 72 and cooled(st, "G9", 6 * 3600, now):
                wake("shop", f"G9 book: lead {lead / 1e6:.0f}M over {rival['name']} closes in {eta:.0f} h (us {ours / 1e6:+.1f} M/h, them {theirs / 1e6:+.1f} M/h)")
                alert([f"G9 장부 격차 {lead / 1e6:.0f}M이 {eta:.0f}h 안에 닫힐 속도입니다 ({rival['name']} {theirs / 1e6:+.1f} M/h, 우리 {ours / 1e6:+.1f} M/h)"])
                fired(st, "G9", now)
                fires.append("G9")
    except Exception as e:
        log(f"G9 skipped: {e!r}")

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
