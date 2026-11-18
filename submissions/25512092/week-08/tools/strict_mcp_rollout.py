"""--strict-mcp-config rollout (person's decision 11/07 ~15:25). One detached process:

  buyer + analyst  on since 15:23 (buyer.py / depts/analyst_brain.py; the buyer runs per order, the analyst was
                   restarted). At APPLY + 1 h: buyer JSON failure rate, deal rate, paid / list price, first offer /
                   ask, the hour after vs the hour before -> Telegram (report only). The buyer's asking-price fix
                   went on at the same time, so the price measures carry both changes.
  seller           at SELLER_AT (20:05, after the 15:30-20:00 no-restart window): strict_mcp.json seller true +
                   one seller restart (harness.py reads the flag at import). At + 1 h: seller JSON failure rate, deal
                   rate, receipt price / first ask, the hour after vs the hour before. Worse -> flag off, seller
                   restarted again, urgent Telegram; else info Telegram.
    worse = JSON failures + 2 %p or more (>= 50 calls), deal rate - 5 %p or more, or price / first ask - 2 %p or
            more (>= 20 receipts each side)

    python tools/strict_mcp_rollout.py            (waits; log in logs/strict_mcp_rollout.log)
"""
import glob
import json
import os
import re
import sqlite3
import sys
import time
from datetime import datetime

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "tools"))
os.chdir(HERE)
import alerts  # noqa: E402
import decisions  # noqa: E402
from seller_conc_rollout import restart_seller, seller_back  # noqa: E402

APPLY = datetime(2026, 11, 7, 15, 23).timestamp()
SELLER_AT = datetime(2026, 11, 7, 20, 5).timestamp()
FLAG = os.path.join(HERE, "strict_mcp.json")
LOG = os.path.join(HERE, "logs", "strict_mcp_rollout.log")


def log(m):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"{datetime.now():%m-%d %H:%M:%S} {m}\n")


def wait(t):
    while time.time() < t:
        time.sleep(min(60, max(1, t - time.time())))


def timed_lines(path, tag, t0, t1, tail_bytes=40_000_000):
    """(epoch, line) for lines of `path` with `tag`, HH:MM:SS dated by walking back from the end (now)."""
    with open(path, "rb") as f:
        f.seek(max(0, os.path.getsize(path) - tail_bytes))
        lines = f.read().decode("utf-8", errors="replace").splitlines()
    out, day, prev = [], datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp(), None
    for ln in reversed(lines):
        m = re.match(r"(\d\d):(\d\d):(\d\d) ", ln)
        if not m:
            continue
        t = day + int(m[1]) * 3600 + int(m[2]) * 60 + int(m[3])
        if prev is not None and t > prev + 3600:
            day -= 86400
            t -= 86400
        prev = t
        if t < t0:
            break
        if t < t1 and tag in ln:
            out.append((t, ln))
    return out


def is_json_move(raw):
    m = re.search(r"\{.*\}", raw, re.S)
    try:
        return bool(m) and "action" in json.loads(m.group())
    except ValueError:
        return False


def buyer_metrics(t0, t1):
    rows = timed_lines(os.path.join(HERE, "logs", "market-kit.log"), "[buyer]", t0, t1)
    raws = [re.search(r"harness: '(.*)'$", ln) for _, ln in rows if " harness: " in ln]
    raws = [r[1] for r in raws if r]
    firsts = [re.search(r"first offer \d+% of (\d+) -> (\d+)", ln) for _, ln in rows if "[variant] first offer" in ln]
    firsts = [int(m[2]) / int(m[1]) for m in firsts if m and int(m[1])]
    db = sqlite3.connect(os.path.join(HERE, "market.db"), timeout=30)
    try:
        d = db.execute("SELECT outcome, price, list_price, offers FROM buyer_deals WHERE ts>=? AND ts<?", (t0, t1)).fetchall()
    finally:
        db.close()
    deals = [r for r in d if r[0] == "deal"]
    tried = [r for r in d if r[0] in ("deal", "fail")]
    paid = [r[1] / r[2] for r in deals if r[1] and r[2]]
    return {"calls": len(raws), "json_fail": round(sum(not is_json_move(r) for r in raws) / len(raws), 4) if raws else None,
            "negotiations": len(tried), "deal_rate": round(len(deals) / len(tried), 3) if tried else None,
            "paid_vs_list": round(sum(paid) / len(paid), 3) if paid else None,
            "first_offer_vs_ask": round(sum(firsts) / len(firsts), 3) if firsts else None}


def seller_metrics(t0, t1):
    f = sorted(glob.glob(os.path.join(HERE, "logs", "kit-console-*.txt")), key=os.path.getmtime)[-1]
    raws = [ln.split("[seller] [harness] ", 1)[1] for _, ln in timed_lines(f, "[seller] [harness] ", t0, t1)]
    db = sqlite3.connect(os.path.join(HERE, "market.db"), timeout=30)
    try:
        d = db.execute("SELECT outcome, price, first_ask FROM shop_deals WHERE ts>=? AND ts<?", (t0, t1)).fetchall()
    finally:
        db.close()
    done = [r for r in d if r[0] in ("receipt", "cancelled", "rejected")]
    rc = [r for r in done if r[0] == "receipt"]
    ratio = [r[1] / r[2] for r in rc if r[1] and r[2]]
    return {"calls": len(raws), "json_fail": round(sum(not is_json_move(r) for r in raws) / len(raws), 4) if raws else None,
            "negotiations": len(done), "deal_rate": round(len(rc) / len(done), 3) if done else None,
            "receipts": len(rc), "price_vs_first_ask": round(sum(ratio) / len(ratio), 3) if ratio else None}


def worse(a, b):
    bad = []
    if a["calls"] >= 50 and a["json_fail"] is not None and b["json_fail"] is not None and a["json_fail"] >= b["json_fail"] + 0.02:
        bad.append(f"JSON failures {a['json_fail']:.1%} vs {b['json_fail']:.1%}")
    if a["deal_rate"] is not None and b["deal_rate"] is not None and a["deal_rate"] <= b["deal_rate"] - 0.05:
        bad.append(f"deal rate {a['deal_rate']:.1%} vs {b['deal_rate']:.1%}")
    if a["receipts"] >= 20 and b["receipts"] >= 20 and a["price_vs_first_ask"] is not None and \
            b["price_vs_first_ask"] is not None and a["price_vs_first_ask"] <= b["price_vs_first_ask"] - 0.02:
        bad.append(f"price / first ask {a['price_vs_first_ask']:.3f} vs {b['price_vs_first_ask']:.3f}")
    return bad


def fmt(m):
    return ", ".join(f"{k} {v}" for k, v in m.items())


def rule_counts(t0, t1):
    """11/07 ~18:30: stop-loss and buy-guard activity in [t0, t1): negotiations started at the stop-loss floor and
    their receipts (seller first_ask decisions with 'rule stoploss', shop_haggle / shop_deals), and high-price buys
    the guard skipped (buy_guard_skip decisions)."""
    neg, skips = 0, 0
    with open(os.path.join(HERE, "logs", "decisions.jsonl"), encoding="utf-8") as f:
        for ln in f:
            if '"buy_guard_skip"' in ln or "rule stoploss" in ln:
                d = json.loads(ln)
                if not (t0 <= d["ts"] < t1):
                    continue
                if d.get("action") == "buy_guard_skip":
                    skips += 1
                elif d.get("action") == "first_ask":
                    neg += 1
    db = sqlite3.connect(os.path.join(HERE, "market.db"), timeout=30)
    try:
        rc = db.execute("SELECT COUNT(*), COALESCE(SUM(d.price - d.cost), 0) FROM shop_haggle h JOIN shop_deals d ON "
                        "d.task=h.task WHERE h.rule='stoploss' AND d.outcome='receipt' AND d.ts>=? AND d.ts<?",
                        (t0, t1)).fetchone()
    finally:
        db.close()
    return {"stoploss_negotiations": neg, "stoploss_receipts": rc[0], "stoploss_margin": rc[1], "guard_skips": skips}


def main():
    if "--seller-only" in sys.argv:                 # restarted after the 16:23 buyer check (11/07 ~18:30)
        return seller_part()
    log("waiting: buyer check at 16:23, seller switch at 20:05")
    wait(APPLY + 3600)
    a, b = buyer_metrics(APPLY, APPLY + 3600), buyer_metrics(APPLY - 3600, APPLY)
    decisions.record("buyer", "strict_mcp_check", "buyer.HARNESS_CMD", {"after": a, "before": b},
                     "1 h after --strict-mcp-config (and the asking-price fix) vs the hour before")
    log(f"buyer after: {fmt(a)} | before: {fmt(b)}")
    alerts.send("info", "strict_mcp_buyer", "buyer --strict-mcp-config 1시간 비교 (호가 파싱 수정 같이 적용)\n"
                f"후: {fmt(a)}\n전: {fmt(b)}", log=log)
    seller_part()


def seller_part():
    log("waiting: seller switch at 20:05")
    wait(SELLER_AT)
    with open(FLAG, "w", encoding="utf-8") as f:
        json.dump({"seller": True, "since": round(time.time())}, f)
    t = time.time()
    killed = restart_seller()
    decisions.record("sales", "strict_mcp_on", "harness.CMD", True, "person's decision 11/07 ~15:25: seller "
                     "--strict-mcp-config with one restart after 20:00; 1 h check, worse -> off + restart")
    log(f"seller flag on, restarted (killed {killed})")
    time.sleep(120)
    back = seller_back(t)
    log(f"seller back: {back}")
    if not back:
        alerts.send("urgent", "strict_mcp_seller_down", "seller --strict-mcp-config 재시작 후 2분 안에 seller 응답 "
                    "없음. 확인 필요", log=log)
    wait(t + 3600)
    a, b = seller_metrics(t, t + 3600), seller_metrics(t - 3600, t)
    bad = worse(a, b)
    rc = rule_counts(t, t + 3600)                    # information for the person, not a revert condition
    rc_line = (f"\n손절 규칙: 협상 {rc['stoploss_negotiations']}건, 영수증 {rc['stoploss_receipts']}건 (원가 대비 "
               f"{rc['stoploss_margin']:+,}); 고가 매입 가드 차단 {rc['guard_skips']}건")
    decisions.record("sales", "strict_mcp_check", "harness.CMD", {"after": a, "before": b, "worse": bad, "rules": rc},
                     "1 h after vs the hour before")
    log(f"seller after: {fmt(a)} | before: {fmt(b)} | worse: {bad}")
    if bad:
        with open(FLAG, "w", encoding="utf-8") as f:
            json.dump({"seller": False, "since": round(time.time()), "reverted": bad}, f)
        killed = restart_seller()
        decisions.record("sales", "strict_mcp_off", "harness.CMD", False, "; ".join(bad))
        log(f"reverted, seller restarted (killed {killed})")
        alerts.send("urgent", "strict_mcp_revert", "seller --strict-mcp-config 되돌림 (재시작): " + "; ".join(bad)
                    + f"\n후: {fmt(a)}\n전: {fmt(b)}" + rc_line, log=log)
    else:
        alerts.send("info", "strict_mcp_seller", "seller --strict-mcp-config 1시간 비교: 나빠진 지표 없음, 유지\n"
                    f"후: {fmt(a)}\n전: {fmt(b)}" + rc_line, log=log)
    log("done")


if __name__ == "__main__":
    if "--dry" in sys.argv:
        now = time.time()
        print("buyer last hour:", buyer_metrics(now - 3600, now))
        print("seller last hour:", seller_metrics(now - 3600, now))
        print("worse(self, self):", worse(seller_metrics(now - 3600, now), seller_metrics(now - 3600, now)))
    else:
        main()
