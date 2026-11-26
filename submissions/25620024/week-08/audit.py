"""Week 08 — audit.py: the audit team (HQ 2026-11-04: "감사팀은 바이어유닛의 오류만 집중적으로 발견해").

The audit team does one job: find the buyer unit's errors before headquarters does. It covers the buyer team, Enuri and
the review team. It is code only, with no tokens. Every check reads records the agents already write, and each check
comes from a failure HQ had to find by hand on 11-04:
  - Walking away from final asks under list price. Surplus is list - paid; 475 such walk-aways in 24 h.
  - Shops paused because filtered quotes were logged as "no answer".
  - Bans for things that are not offenses.
  - An experiment that never measured anything.
  - A ranking built on the wrong objective (budget - price).

Each finding names the team that owns it, a severity, what is wrong, the money at stake and the evidence:
  - red: losing surplus, deals or forum score now
  - yellow: drifting

Findings go to:
  - private/audit_buyer.json
  - the buyer team's brief
  - the top of the HR report
  - Drive week08-reports/AUDIT_BUYER.md

    python audit.py        # audit now and print it
"""
import json
import re
import time
from pathlib import Path

HERE = Path(__file__).parent
MD = HERE / ".market"
OUT = HERE / "private" / "audit_buyer.json"
LOG = HERE / "logs" / "market-kit.log"
REPORTS = Path("/mnt/gdrive-imajason-kimdaehakwon/week08-reports")
TEAM = {"buyer": "바이어팀", "enuri": "에누리팀", "review": "리뷰팀"}


def _jsonl(path, n=5000):
    out = []
    try:
        for line in Path(path).read_text(encoding="utf-8").splitlines()[-n:]:
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
    except OSError:
        pass
    return out


def _load(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _paid(m):
    return m.get("receipt_price") or m.get("agreed")


def audit(board=None, now=None, minutes=60):
    now = now or time.time()
    f = []

    def add(team, sev, what, money=0, ev=None):
        f.append({"team": team, "severity": sev, "what": what, "money": int(money or 0), "evidence": ev})

    import buyer_memory
    T = buyer_memory.theta()
    mem = [m for m in _jsonl(HERE / "memory.jsonl") if m.get("ts", 0) > now - minutes * 60 and m.get("list_price")]
    orders = [m for m in mem if m.get("outcome") in ("deal", "no deal", "no answer", "no candidates")]
    deals = [m for m in orders if m["outcome"] == "deal" and _paid(m)]

    # --- buyer team ----------------------------------------------------------------------------------------------
    def close_at(m):
        # HQ 11-15 16:40: the buyer's real close line is per order (price_plan: patch 3.6 walks away under r_min, the
        # share of the shop's margin we keep) and memory.jsonl records it. The list-ratio line below flagged 86 "missed"
        # orders in 6 h that the buyer walked away from on purpose; against the recorded close_at there were 0.
        if m.get("close_at"):
            return m["close_at"]
        r = T.get("close_ratio_big", 1.0) if m["list_price"] >= T.get("big_list", 100000) else T.get("close_ratio", 0.9)
        return min(m["budget"], int(m["list_price"] * r))
    walked = [m for m in orders if m["outcome"] == "no deal" and m.get("last_ask") is not None
              and m["last_ask"] <= close_at(m)]
    if walked:
        add("buyer", "red" if len(walked) >= 3 else "yellow",
            f"{len(walked)} orders ended with no deal although the final ask was under the close line "
            f"(min(budget, close_ratio x list))", sum(m["list_price"] - m["last_ask"] for m in walked),
            [{k: m.get(k) for k in ("shop", "list_price", "budget", "last_ask")} for m in walked[:3]])
    neg = [m for m in deals if _paid(m) > m["list_price"]]
    if neg:
        add("buyer", "red", f"{len(neg)} deals paid above list price: negative surplus",
            sum(_paid(m) - m["list_price"] for m in neg),
            [{k: m.get(k) for k in ("shop", "list_price", "budget", "agreed")} for m in neg[:3]])
    # patch 3.4 (11-06): the buyer score is the saved RATE (share of list kept per deal, summed / (deals + 100)); a deal
    # keeping less than the current rate pulls it down, a deal that served nothing counts -1
    if deals:
        kept = [1 - _paid(m) / m["list_price"] for m in deals]
        rate = sum(kept) / len(kept)
        thin = [m for m, k in zip(deals, kept) if k < 0.10]
        if len(thin) >= 3:
            add("buyer", "yellow", f"{len(thin)} deals kept under 10% of list (hour average {rate:.1%}): each pulls the "
                f"saved rate down", 0, [{k: m.get(k) for k in ("shop", "list_price", "agreed")} for m in thin[:3]])
        if rate < 0.30:
            add("buyer", "red" if rate < 0.25 else "yellow", f"saved rate this hour {rate:.1%}, under the 30% line "
                f"(board saved ~35.8% on 11-07)", 0, {"deals": len(deals)})
    over = [m for m in deals if _paid(m) > m["budget"]]
    if over:
        add("buyer", "red", f"{len(over)} deals paid above the order budget", 0,
            [{k: m.get(k) for k in ("shop", "budget", "agreed")} for m in over[:3]])
    if len(orders) >= 20:
        rate = len(deals) / len(orders)
        if rate < 0.70:
            add("buyer", "red" if rate < 0.5 else "yellow", f"deal rate {rate:.0%} in the last {minutes} min",
                0, {o: sum(m["outcome"] == o for m in orders) for o in ("deal", "no deal", "no answer", "no candidates")})
    big_lost = [m for m in orders if m["list_price"] >= T.get("big_list", 100000) and m["outcome"] != "deal"]
    if big_lost:
        add("buyer", "red", f"{len(big_lost)} big-ticket orders (list >= {T.get('big_list', 100000):,}) lost",
            sum(m["list_price"] * 0.3 for m in big_lost),
            [{k: m.get(k) for k in ("outcome", "shop", "list_price", "budget", "last_ask", "problem")} for m in big_lost[:3]])
    noans = [m for m in orders if m["outcome"] == "no answer"]
    if len(noans) >= 3:
        add("buyer", "yellow", f"{len(noans)} orders ended 'no answer' (connection errors only; check the shops)", 0,
            sorted({m.get("shop") for m in noans})[:6])
    try:
        import buyer_team
        bad = {k: T.get(k) for k, (lo, hi, _) in buyer_team.THETA_KNOBS.items()
               if T.get(k) is not None and not lo <= T[k] <= hi}
        if bad:
            add("buyer", "yellow", "buyer theta outside the team's allowed ranges", 0, bad)
    except Exception:
        pass
    try:
        lines = LOG.read_text(encoding="utf-8", errors="replace").splitlines()[-20000:]
    except OSError:
        lines = []
    errs = [l for l in lines[-3000:] if "[buyer]" in l and re.search(r"Traceback|buyer error|Error:", l)]
    if errs:
        add("buyer", "red" if len(errs) >= 5 else "yellow", f"{len(errs)} buyer errors in the recent log", 0,
            [e[:160] for e in errs[-3:]])
    pays = _jsonl(MD / "payments.jsonl", 100)
    refused = [p for p in pays if not p.get("paid")]
    if len(pays) >= 20 and len(refused) / len(pays) > 0.10:
        why = {}
        for p in refused:
            k = re.sub(r"[\d.]+", "N", p.get("reason") or "?")[:60]
            why[k] = why.get(k, 0) + 1
        add("buyer", "red", f"{len(refused)} of the last {len(pays)} agreed deals were not paid by the kit "
                            f"(agreed, then refused at the check)", 0, why)

    # --- Enuri -------------------------------------------------------------------------------------------------------
    adv = [m for m in orders if m.get("enuri_first")]
    if len(adv) >= 10:
        miss = [m for m in adv if m.get("shop") == m["enuri_first"] and m["outcome"] != "deal"]
        if len(miss) / len(adv) > 0.35:
            add("enuri", "yellow", f"Enuri's first pick failed in {len(miss)} of {len(adv)} orders", 0,
                sorted({m["enuri_first"] for m in miss})[:6])
    st = _load(HERE / "private" / "enuri" / "state.json", {})
    cases = [c for c in (st.get("cases") or {}).values() if c.get("ts", 0) > now - minutes * 60]
    by = {}
    for c in cases:
        by.setdefault(c.get("type"), [0, 0])
        by[c.get("type")][0] += 1
        by[c.get("type")][1] += c.get("lost") or 0
    for typ, (n, lost) in sorted(by.items(), key=lambda kv: -kv[1][1]):
        if typ in ("no candidates, but stocked", "overpaid") and n >= 5:
            add("enuri", "yellow", f"{n} '{typ}' cases in the last {minutes} min", lost, None)
    walks = [m for m in deals if m.get("enuri_walk") and _paid(m) > 1.3 * m["enuri_walk"]]
    if len(walks) >= 5:
        add("enuri", "yellow", f"{len(walks)} deals closed 30%+ above Enuri's walk-away: its price book is stale", 0,
            [{k: m.get(k) for k in ("shop", "list_price", "enuri_walk", "agreed")} for m in walks[:3]])

    # --- review team (forum) -------------------------------------------------------------------------------------
    sent = _jsonl(MD / "outbox.sent.jsonl", 400)
    posts = [s for s in sent if (s.get("sent") or {}).get("as") == "buyer" and "post" in (s.get("sent") or {})]
    failed = [s for s in posts[-30:] if s.get("status") != 200]
    if failed:
        add("review", "yellow", f"{len(failed)} of the last {len(posts[-30:])} buyer posts were not accepted", 0,
            [str(s.get("answer"))[:100] for s in failed[-2:]])
    fs = _load(HERE / "private" / "forum_state.json", {})
    last = (fs.get("last") or {}).get("buyer", 0)
    try:
        import forum
        gap = (forum.DEFAULTS.get("gap_s") or {}).get("buyer", 1800)
    except Exception:
        gap = 1800
    if last and now - last > gap * 1.5 + 600:
        add("review", "red", f"no buyer post for {int((now - last) / 60)} min (cadence {gap // 60} min)", 0, None)
    rt = _load(HERE / "private" / "review_team.json", {})
    arms = rt.get("arms") or {}
    if "reply:text" in arms and (arms["reply:text"] or {}).get("n", 0) == 0 and \
            now - (HERE / "forum.py").stat().st_mtime > 2 * 3600:
        add("review", "yellow", "the reply experiment has no measured reply after 2 h", 0, None)
    if board:
        bs = [x for x in board.get("buyers", []) if x.get("deals")]
        me = next((x for x in bs if x.get("name") == "dragonash"), None)
        if me and bs:
            p = sum(1 for x in bs if (x.get("forum") or 0) < (me.get("forum") or 0)) / max(1, len(bs) - 1)
            if p < 0.5:
                add("review", "red", f"forum rating {me.get('forum')} is percentile {p:.2f} (weight 0.3 of buyer ALL)",
                    0, {"top": max(x.get("forum") or 0 for x in bs)})

    f.sort(key=lambda x: (x["severity"] != "red", -x["money"]))
    out = {"ts": now, "at": time.strftime("%m-%d %H:%M"), "window_min": minutes, "findings": f,
           "red": {t: sum(x["team"] == t and x["severity"] == "red" for x in f) for t in TEAM},
           "orders": len(orders), "deals": len(deals)}
    try:
        OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError:
        pass
    return out


def summary():
    return _load(OUT, None)


def markdown(a):
    icon = {"red": "🔴", "yellow": "🟡"}
    head = f"# 감사팀 · 바이어 유닛 점검 {a['at']} (최근 {a['window_min']}분, 주문 {a['orders']} · 거래 {a['deals']})"
    if not a["findings"]:
        return head + "\n- 이상 없음\n"
    lines = [head, "빨강: " + ", ".join(f"{TEAM[t]} {n}" for t, n in a["red"].items())]
    for x in a["findings"]:
        lines.append(f"- {icon[x['severity']]} [{TEAM[x['team']]}] {x['what']}"
                     + (f" — 걸린 돈 약 {x['money']:,}원" if x["money"] else "")
                     + (f"\n  - 근거: {json.dumps(x['evidence'], ensure_ascii=False)[:300]}" if x["evidence"] else ""))
    return "\n".join(lines) + "\n"


def run(board=None):
    a = audit(board)
    try:
        (REPORTS / "AUDIT_BUYER.md").write_text(markdown(a), encoding="utf-8")
    except OSError:
        pass
    return a


if __name__ == "__main__":
    import sys
    b = None
    if "--board" in sys.argv:
        import httpx
        import kpi
        b = httpx.get(kpi.BOARD, timeout=30).json()
    print(markdown(run(b)))
