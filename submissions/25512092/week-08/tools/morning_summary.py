"""11/08 08:00: one Telegram with the night's summary (person's decision 11/07 ~20:20), read-only:
  urgent alerts, buyer poster fallback switches, refused receipts, injection handling, automatic policy changes,
  analyst proposals (window NIGHT_FROM -> AT), and LLM calls per agent for the last 24 h:
    seller   one '[seller] [harness]' line per call (newest kit-console-*.txt)
    buyer    one '[buyer] ... harness:' line per call (logs/market-kit.log)
    analyst  analyst_meta calls:<day> (per calendar day, analyst_brain.call_llm)
    forum    forum_meta flow:<day> (one call per day, forum_watch.flow)
  12. (from 11/10 ~12:10, person) stock unsold over 12 h (cost and units, high-price / other) and storage paid
      (the 5-min cash table's storage_per_day over time), 12 h and since the round start; --daily sends at 08:00
      and 20:00 from then (the 12 h before each)
  9. (from 11/10) 우아미 소파 G5e3784e6 in the live buy_exclude.json, one line
    python tools/morning_summary.py [--dry | --daily]   (waits until AT; --dry: the last 12 h now, printed, not sent;
                                                         --daily: every day at 08:00 until the round end)
"""
import glob
import json
import os
import re
import sqlite3
import sys
import time
from collections import Counter
from datetime import datetime

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "tools"))
os.chdir(HERE)

AT = datetime(2026, 11, 8, 8, 0).timestamp()
if "--at" in sys.argv:                            # e.g. --at 2026-11-09T08:00 (the 11/09 summary, with 8. below)
    AT = datetime.fromisoformat(sys.argv[sys.argv.index("--at") + 1]).timestamp()
NIGHT_H = 12


def decisions_in(t0, t1, actions):
    out = []
    with open(os.path.join(HERE, "logs", "decisions.jsonl"), encoding="utf-8") as f:
        for ln in f:
            if any(f'"action": "{a}"' in ln for a in actions):
                d = json.loads(ln)
                if t0 <= d["ts"] < t1:
                    out.append(d)
    return out


def llm_calls(t0, t1):
    from strict_mcp_rollout import timed_lines
    con = sorted(glob.glob(os.path.join(HERE, "logs", "kit-console-*.txt")), key=os.path.getmtime)[-1]
    seller = len(timed_lines(con, "[seller] [harness] ", t0, t1, tail_bytes=400_000_000))
    buyer = 0                                       # the kit rotates market-kit.log at 20 MB into market-kit.log.1
    for name in ("market-kit.log.1", "market-kit.log"):
        p = os.path.join(HERE, "logs", name)
        if os.path.exists(p):
            buyer += sum(1 for _, ln in timed_lines(p, "[buyer]", t0, t1, tail_bytes=400_000_000) if " harness: " in ln)
    db = sqlite3.connect(os.path.join(HERE, "market.db"), timeout=30)
    try:
        days = sorted({datetime.fromtimestamp(t0).strftime("%Y%m%d"), datetime.fromtimestamp(t1 - 1).strftime("%Y%m%d")})
        analyst = {d: int((db.execute("SELECT v FROM analyst_meta WHERE k=?", (f"calls:{d}",)).fetchone() or [0])[0])
                   for d in days}
        flows = [r[0] for r in db.execute("SELECT k FROM forum_meta WHERE k LIKE 'flow:%'")]
    finally:
        db.close()
    forum = sum(1 for k in flows if k.split(":")[1] in days)
    return {"seller": seller, "buyer": buyer, "analyst_by_day": analyst, "forum_summary_days": forum}


def summary(t0, t1):
    lines = [f"밤사이 요약 {datetime.fromtimestamp(t0):%m-%d %H:%M} ~ {datetime.fromtimestamp(t1):%m-%d %H:%M}"]
    urgent = []
    with open(os.path.join(HERE, "logs", "alerts.jsonl"), encoding="utf-8") as f:
        for ln in f:
            a = json.loads(ln)
            if a.get("level") == "urgent" and t0 <= a["ts"] < t1:
                urgent.append(a)
    c = Counter(a["key"].split(":")[0] for a in urgent)
    lines.append(f"1. 긴급 알림 {len(urgent)}건" + (": " + ", ".join(f"{k} {n}" for k, n in c.most_common(8)) if urgent else ""))
    sw = decisions_in(t0, t1, ["buyer_poster_mode"])
    lines.append(f"2. 폴백 전환 {len(sw)}건" + (": " + ", ".join(f"{datetime.fromtimestamp(d['ts']):%H:%M} {d['value']}"
                                                          for d in sw[:6]) if sw else ""))
    ref = decisions_in(t0, t1, ["receipt_refused"])
    lines.append(f"3. 영수증 거절 {len(ref)}건, {sum(int(d.get('value') or 0) for d in ref):,}원"
                 + (": " + ", ".join(Counter((d.get("reason") or "").split(":")[0] for d in ref).keys()) if ref else ""))
    db = sqlite3.connect(os.path.join(HERE, "market.db"), timeout=30)
    try:
        inj = db.execute("SELECT outcome, COUNT(*) FROM buyer_deals WHERE buyer_case='injection_code' AND ts>=? AND ts<? "
                         "GROUP BY outcome", (t0, t1)).fetchall()
        changes = db.execute("SELECT id, ts, key, value, status, result FROM proposals WHERE kind='policy_change' AND "
                             "ts>=? AND ts<? ORDER BY ts", (t0, t1)).fetchall()
        props = db.execute("SELECT id, ts, key, value, status FROM proposals WHERE kind IN ('policy','limit') AND "
                           "ts>=? AND ts<? ORDER BY ts", (t0, t1)).fetchall()
    finally:
        db.close()
    no_avg = decisions_in(t0, t1, ["injection_no_avg"])
    lines.append(f"4. 인젝션 코드 협상 {sum(n for _, n in inj)}건 {dict(inj)}, 그중 시장 평균 없어 바로 walk {len(no_avg)}건")
    lines.append(f"5. 자동 정책 변경 {len(changes)}건")
    for i, ts, key, value, status, result in changes[:8]:
        v = json.loads(value)
        ch = "; ".join(f"{c['where']}: {c['old']} -> {c['new']}" for c in v.get("changes", []))
        lines.append(f"   #{i} {datetime.fromtimestamp(ts):%H:%M} {v.get('source')}: {ch}"
                     + (f" ({'버튼 ' + ', '.join(json.loads(result).keys())})" if (result or "").startswith("{") else ""))
    lines.append(f"6. 분석관 제안 {len(props)}건")
    for i, ts, key, value, status in props[:8]:
        lines.append(f"   #{i} {datetime.fromtimestamp(ts):%H:%M} {key} -> {json.loads(value)} [{status}]")
    llm = llm_calls(t1 - 86400, t1)
    lines.append(f"7. LLM 호출 (최근 24시간): seller {llm['seller']:,}, buyer {llm['buyer']:,}, 분석관 "
                 + ", ".join(f"{d[4:6]}/{d[6:]} {n}" for d, n in llm["analyst_by_day"].items())
                 + f", 포럼 요약 {llm['forum_summary_days']}회 (모두 haiku)")
    if t1 >= datetime(2026, 11, 9).timestamp():   # 11/08 ~11:20 (person): from the 11/09 summary
        from depts import review_link
        try:
            lines.append("8. " + review_link.line(review_link.high_sofa_reasons(t1 - 86400, t1)) + " (최근 24시간)")
        except Exception as e:
            lines.append(f"8. 고가·소파 취소 사유: 계산 실패 {type(e).__name__}")
    if t1 >= datetime(2026, 11, 10).timestamp():  # 11/09 ~19:45 (person): from the 11/10 summary
        lines.append("9. " + exclude_line())
    try:                                          # 11/09 ~23:40 (person): the high-price cost exclusion list
        from depts import high_cost_exclude
        lines.append("11. " + high_cost_exclude.line())
    except Exception as e:
        lines.append(f"11. 고가 매입 제외 목록 읽기 실패 {type(e).__name__}")
    try:                                          # 11/09 ~21:30 (person): the shop_order 6 h check, once it is due
        from depts import shop_order
        t_on = shop_order.rules().get("since")
        if t_on and t1 >= t_on + 6 * 3600 and t1 - t_on <= 36 * 3600:
            import shop_order_check
            lines.append("10. " + shop_order_check.report(t_on, t_on + 6 * 3600))
    except Exception as e:
        lines.append(f"10. 가게 순서 6시간 점검 실패 {type(e).__name__}")
    try:                                          # 11/10 ~12:10 (person): the brakes only warn, the person judges
        lines.append("12. " + stock_line(t1))
    except Exception as e:
        lines.append(f"12. 12시간+ 미판매 재고·보관비 계산 실패 {type(e).__name__}")
    try:                                          # 11/10 ~12:30 (person): saved target 0.39 at the round end
        import buyer_proj
        lines += ["13. " + x if i == 0 else "    " + x for i, x in enumerate(buyer_proj.summary_lines(t1))]
    except Exception as e:
        lines.append(f"13. buyer saved 예상 계산 실패 {type(e).__name__}")
    try:                                          # 11/10 ~12:20 (person): TV / 냉방 B grade warranty refunds
        lines.append("14. " + b_refund_line(t0, t1))
    except Exception as e:
        lines.append(f"14. TV·냉방 B등급 보증 환불 계산 실패 {type(e).__name__}")
    try:                                          # 11/10 ~12:30 (person): fast high-price SKUs held deeper
        from depts import deep_high
        lines.append("15. " + deep_high.line(t0, t1))
    except Exception as e:
        lines.append(f"15. 고가 깊게 쌓기 계산 실패 {type(e).__name__}")
    try:                                          # 11/10 ~14:15 (person): normal goods recovery after the 4 lines
        import sales_trend
        lines.append("16. " + sales_trend.recovery_line(t1, 12))
    except Exception as e:
        lines.append(f"16. 일반 상품 회복 추이 계산 실패 {type(e).__name__}")
    try:                                          # 11/10 ~15:00 (person, auto mode): warnings / info held from Telegram
        import auto_0900
        H = auto_0900.held_lines(auto_0900.alerts_in(t0, t1))
        lines += ["17. " + H[0][3:]] + H[1:]
    except Exception as e:
        lines.append(f"17. 경고·정보 모음 실패 {type(e).__name__}")
    try:                                          # 11/11 ~10:40 (person): the goal is the buyer ALL rank
        import board_speed
        lines.append("18. " + all_line(board_speed.board()))
    except Exception as e:
        lines.append(f"18. buyer ALL 순위 계산 실패 {type(e).__name__}")
    try:                                          # 11/11 ~21:00 (person): avoided shops, why, orders missed
        from depts import shop_avoid
        lines.append("19. " + shop_avoid.summary_line(t1, t0))
    except Exception as e:
        lines.append(f"19. 회피 가게 계산 실패 {type(e).__name__}")
    try:                                          # 11/11 ~22:00 (person): the night's additions
        lines += night_lines(t0, t1)
    except Exception as e:
        lines.append(f"20. 야간 점검 요약 실패 {type(e).__name__}: {e}")
    try:                                          # 11/12 ~09:30 (person): for the HIGH_MAX call on 11/13 morning
        import check_top15
        lines.append("27. " + check_top15.last24_line(t1))
    except Exception as e:
        lines.append(f"27. 150만 초과 고가 24시간 누적 계산 실패 {type(e).__name__}: {e}")
    try:                                          # 11/12 ~09:50 (person): for the call on 11/13 morning
        lines.append("28. " + aged_high_trend(t1))
    except Exception as e:
        lines.append(f"28. 12시간+ 미판매 고가 추이 계산 실패 {type(e).__name__}: {e}")
    try:                                          # 11/13 ~00:05 (person): the 11/13 08:00 additions
        lines += night_1113(t0, t1)
    except Exception as e:
        lines.append(f"29. 11/13 추가 항목 실패 {type(e).__name__}: {e}")
    return "\n".join(lines)


C_FROM = datetime(2026, 11, 12, 23, 20).timestamp()       # grade C first offer 0.55 from here
GRADE_FROM = datetime(2026, 11, 12, 18, 16).timestamp()   # the grade rule (C 0.65) from here
REV_1112 = ("11/12 매출 2.59억 vs 11/11 3.77억 (-1.18억) 중 -1.10억이 고가: 고가 매입 520개 3.0억 -> 373개 1.8억 "
            "(high_max 100에 보유 88~100으로 붙어 자리 부족, 12시간+ 미판매가 자리 차지)으로 고가 판매 498 -> 357개; "
            "시장 전체 거래액도 -11%")


def rank_parse(text):
    """(rank, total, 9th, gap to 9th) from a student_rank Telegram text, or None."""
    import re
    m = re.search(r"(\d+)위/\d+ .*?합계 ([\d.]+); 9위 ([\d.]+) \(([-+][\d.]+)\)", text or "")
    return (int(m[1]), float(m[2]), float(m[3]), m[4]) if m else None


def night_1113(t0, t1, db_path=None, board=None, alerts_path=None):
    """11/13 ~00:05 (person): student rank at 03 / 06 / now with the 9th gap, the high 1 -> 2 3 h check, cash, C 0.55
    effect by grade, saved vs 11im, deal rate and deals per hour, the 11/12 revenue cause, the decisions needed."""
    import student_rank_watch as SR
    L = []
    pts = []
    path = alerts_path or os.path.join(HERE, "logs", "alerts.jsonl")
    with open(path, encoding="utf-8") as f:
        for ln in f:
            try:
                a = json.loads(ln)
            except ValueError:
                continue
            if a.get("key", "").startswith("student_rank:") and t0 <= a["ts"] < t1:
                r = rank_parse(a.get("text"))
                if r:
                    pts.append(f"{datetime.fromtimestamp(a['ts']):%H:%M} {r[0]}위 합계 {r[1]:.3f} (9위와 {r[3]})")
    x = None
    try:
        x = SR.numbers(board if board is not None else SR.fetch())
        g9 = "-" if x["ninth"] is None else f"{x['total'] - x['ninth']:+.3f}"
        pts.append(f"지금 {x['rank']}위 합계 {x['total']:.3f} (9위와 {g9})")
    except Exception as e:
        pts.append(f"지금: 보드 읽기 실패 {type(e).__name__}")
    L.append("29. 학생 순위 추이: " + " -> ".join(pts))
    a = last_alert("high_two_report:", t0, t1, path=path)
    L.append("30. 고가 1→2 3시간 점검: " + (a["text"].replace("\n", " / ")[:900] if a else "결과 없음 (아직 안 옴)"))
    db = db_path or os.path.join(HERE, "market.db")
    d = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=30)
    try:
        cs = []
        for t in (t0, t0 + 3 * 3600, t0 + 6 * 3600, t0 + 9 * 3600, t1):
            r = d.execute("SELECT ts, balance, stock_cost FROM cash WHERE ts<=? ORDER BY ts DESC LIMIT 1", (t,)).fetchone()
            if r:
                cs.append(f"{datetime.fromtimestamp(t):%H:%M} {r[1]:,} (재고 원가 {r[2]:,})")
    finally:
        d.close()
    L.append("31. 현금: " + " -> ".join(cs) + " | 12시간+ 미판매 고가 추이는 28번")
    h_new, h_old = max((t1 - C_FROM) / 3600, 0.01), (C_FROM - GRADE_FROM) / 3600
    now_g, old_g = SR.grade_stats(t1, h_new, db), SR.grade_stats(C_FROM, h_old, db)
    f = lambda gs, h: ", ".join(f"{k} {n}건({n / h:.1f}/h) {v:.3f}" for k, (n, v) in gs[0].items()) or "없음"
    L.append(f"32. C 0.55 효과 (등급별 거래, 새 saved 추정 점수): 23:20 이후 {f(now_g, h_new)} vs 18:16~23:20 {f(old_g, h_old)}")
    rate = SR.deal_rate(t0, t1, db)
    dph = SR.grade_stats(t1, (t1 - t0) / 3600, db)[1]
    sv = (f"saved {x['saved']} vs 학생 1위 11im {x['rival']} ({x['saved'] - x['rival']:+.4f}), 학생 saved {x['saved_rank']}위"
          if x and x.get("rival") is not None and x.get("saved") is not None else "saved 읽기 실패")
    L.append(f"33. {sv}; 성사율 {rate[0]}/{rate[1]} ({rate[0] / rate[1]:.0%}); 시간당 deals {dph:.1f}" if rate[1]
             else f"33. {sv}; 주문 없음")
    L.append("34. " + REV_1112)
    need = needs_items(t0, t1)
    try:
        from depts import wide_trial
        r = wide_trial.rules()
        if r.get("off_ts"):
            need.append(f"고가 안전장치로 B·C·1→2 꺼짐 ({datetime.fromtimestamp(r['off_ts']):%m-%d %H:%M}, {r.get('off_why')}): "
                        "다시 켤지")
    except Exception:
        pass
    L.append("35. 결정 필요 목록: " + ("; ".join(need) if need else "없음"))
    return L


CHECK_KEYS = (("check_top15", "23:35 150만 초과 고가 점검"), ("check_release", "00:31 차단 해제 가게 점검"))


def last_alert(prefix, t0, t1, path=None):
    """The newest alert whose key starts with prefix in [t0, t1) (a retry's text wins), or None."""
    best = None
    try:
        with open(path or os.path.join(HERE, "logs", "alerts.jsonl"), encoding="utf-8") as f:
            for ln in f:
                if prefix not in ln:
                    continue
                a = json.loads(ln)
                if a["key"].startswith(prefix) and t0 <= a["ts"] < t1:
                    best = a
    except (OSError, ValueError):
        pass
    return best


def needs_items(t0, t1, now=None):
    """The decisions needed: logs/needs.jsonl rows in [t0, t1) plus the checks of the person's 11/11 settings."""
    out = []
    try:
        with open(os.path.join(HERE, "logs", "needs.jsonl"), encoding="utf-8") as f:
            for ln in f:
                e = json.loads(ln)
                if t0 <= e["ts"] < t1:
                    out.append(e["item"])
    except (OSError, ValueError):
        pass
    now = now or t1
    from datetime import datetime as _dt
    end = _dt(2026, 11, 13, 12, 0).timestamp()
    try:                                          # 11/15 (person): only until the pin's end (11/13 12:00), then gone
        pins = json.load(open(os.path.join(HERE, "policy_pins.json"), encoding="utf-8")) if now < end else {}
        pol = json.load(open(os.path.join(HERE, "policy.json"), encoding="utf-8")).get("purchasing") or {}
        for k, want in ((("high_share", 0.7), ("high_max", 200)) if now < end else ()):   # 11/13 ~00:00: 100 -> 200
            if float(pins.get(f"purchasing.{k}") or 0) < end or pol.get(k) != want:
                out.append(f"{k} {want} 고정 11/13 12:00까지가 적용 안 됨 (고정 {pins.get(f'purchasing.{k}')}, 값 {pol.get(k)})")
    except (OSError, ValueError) as e:
        out.append(f"고가 비중·high_max 고정 확인 실패 {type(e).__name__}")
    try:
        nm = json.load(open(os.path.join(HERE, "normal_min.json"), encoding="utf-8"))
        if not nm.get("enabled"):
            out.append("일반 상품 run당 최소 4줄 보장이 꺼져 있음")
    except (OSError, ValueError):
        out.append("일반 상품 run당 최소 4줄 보장 설정(normal_min.json) 없음")
    return out


def normal_runs(t0, t1):
    """(runs with the normal-lines rule logged, runs under 4 in a sent chunk) from the kit log's restock lines."""
    import stall_watch
    n, short = 0, 0
    for t, x in stall_watch.kit_tail(t1, size=60_000_000):
        if t0 <= t < t1 and "[normal min] normal lines per run:" in x:
            n += 1
            short += any(int(v) < 4 for v in re.findall(r"\d+", x.split("per run:", 1)[1]))
    return n, short


def trend_line(t1):
    """Shop Book / revenue per hour and rank, buyer ALL / saved / deals per hour, in 3 h steps over 12 h (board_read)."""
    import board_read as BR
    pts = [BR.at(t1 - h * 3600) for h in (12, 9, 6, 3)] + [BR.now()]
    segs = []
    for a, b in zip(pts, pts[1:]):
        if not (a.ok and b.ok):
            segs.append(f"{datetime.fromtimestamp(b.ts or t1):%H}시 {BR.FAIL}")
            continue
        segs.append(f"~{datetime.fromtimestamp(b.ts):%H:%M} Book/h {BR.show(BR.per_h(a, b, 's', 'book'), ',.0f')} "
                    f"매출/h {BR.show(BR.per_h(a, b, 's', 'revenue'), ',.0f')} 가게 {BR.show(b.get('s', 'rank'))}위 | "
                    f"buyer ALL {BR.show(b.get('b', 'all'))} ({BR.show(b.get('b', 'rank'))}위) saved "
                    f"{BR.show(b.get('b', 'saved'))} deals/h {BR.show(BR.per_h(a, b, 'b', 'deals'), '.1f')}")
    return "; ".join(segs)


def night_lines(t0, t1):
    L = []
    for i, (key, label) in enumerate(CHECK_KEYS):
        a = last_alert(key + ":", t0, t1)
        L.append(f"{20 + i}. {label}: " + (a["text"].replace("\n", " / ")[:900] if a else "결과 없음 (아직 안 옴)"))
    try:
        import check_release as CR
        rel, tr = CR.released()
        if rel:
            names = sorted(rel)
            tries = CR.tries(names, max(t0, tr), t1)
            mm = CR.mismatches(names, max(t0, tr), t1)
            L.append(f"22. 차단 해제 가게({len(names)}곳, {datetime.fromtimestamp(tr):%m-%d %H:%M} 해제) 거래: 시도 "
                     f"{sum(v['tries'] for v in tries.values())}건 성사 {sum(v['deals'] for v in tries.values())}건, 품목 불일치 "
                     f"{mm['item_mismatch']}건, 금액 불일치 {mm['receipt_mismatch']}건, 수락 전 품목 확인 거절 "
                     f"{sum(v['wrong_item'] for v in tries.values())}건"
                     + (": " + ", ".join(f"{s} {v['tries']}/{v['deals']}" for s, v in sorted(
                         tries.items(), key=lambda x: -x[1]["tries"])[:8]) if tries else ""))
    except Exception as e:
        L.append(f"22. 차단 해제 가게 계산 실패 {type(e).__name__}")
    try:
        import stall_watch
        L.append("23. " + stall_watch.summary_line(t0, t1))
    except Exception as e:
        L.append(f"23. 멈춤 감지 계산 실패 {type(e).__name__}")
    try:
        import json as _j
        pins = _j.load(open(os.path.join(HERE, "policy_pins.json"), encoding="utf-8"))
        pol = _j.load(open(os.path.join(HERE, "policy.json"), encoding="utf-8")).get("purchasing") or {}
        n, short = normal_runs(t0, t1)
        L.append(f"24. 고정: 고가 비중 {pol.get('high_share')} / high_max {pol.get('high_max')}, 고정 "
                 f"{datetime.fromtimestamp(float(pins.get('purchasing.high_share') or 0)):%m-%d %H:%M} / "
                 f"{datetime.fromtimestamp(float(pins.get('purchasing.high_max') or 0)):%m-%d %H:%M}까지; 일반 상품 최소 4줄: "
                 f"run {n}번 기록, 그중 4줄 미만 묶음이 있던 run {short}번")
    except Exception as e:
        L.append(f"24. 고정·최소 4줄 확인 실패 {type(e).__name__}")
    try:
        L.append("25. 추이 (3시간 단위): " + trend_line(t1))
    except Exception as e:
        L.append(f"25. 추이 계산 실패 {type(e).__name__}")
    need = needs_items(t0, t1)
    L.append("26. 결정 필요: " + ("; ".join(need) if need else "없음"))
    return L


def all_line(j, me="choo-chu"):
    """My buyer ALL rank and the ALL gap to the buyer just above and just below (live board json)."""
    B = sorted((b for b in j["buyers"] if b.get("all") is not None), key=lambda b: -b["all"])
    i = next(k for k, b in enumerate(B) if b["name"] == me)
    m = B[i]
    up = (f"바로 위 {i}위 {B[i - 1]['name']} {B[i - 1]['all']} (차이 {B[i - 1]['all'] - m['all']:+.4f})" if i > 0
          else "바로 위 없음 (1위)")
    dn = (f"바로 아래 {i + 2}위 {B[i + 1]['name']} {B[i + 1]['all']} (차이 {B[i + 1]['all'] - m['all']:+.4f})"
          if i + 1 < len(B) else "바로 아래 없음")
    return f"buyer ALL {i + 1}위/{len(B)} ({m['all']}, saved {m['saved']}): {up}, {dn}"


STALE_H = 12


def b_refunds(db, t0, t1, cats, since=0, me="choo-chu", catalog=None):
    """My B grade sales with a warranty in `cats` (catalog leaf category) sold since `since`: (sales in [t0, t1),
    refunds in [t0, t1) (n, amount), refunds since `since` (n, amount)). A refund (broken.refunded > 0, the amount)
    belongs to my sale of that SKU to that buyer before it."""
    if catalog is None:
        with open(os.path.join(HERE, ".market", "catalog.json"), encoding="utf-8") as f:
            catalog = json.load(f)
    leaf = lambda k: ((catalog.get(k) or {}).get("category") or "").split(">")[-1]
    sales = db.execute("SELECT ts, sku, buyer FROM settles WHERE shop=? AND grade='B' AND warranty_hours>0 AND ts>=?",
                       (me, since)).fetchall()
    sales = [s for s in sales if leaf(s[1]) in cats]
    n_sales = sum(1 for s in sales if t0 <= s[0] < t1)
    keys = {}
    for ts, sku, buyer in sales:
        keys.setdefault((sku, buyer), []).append(ts)
    win, tot = [0, 0], [0, 0]
    for ts, sku, buyer, amt in db.execute("SELECT ts, sku, buyer, refunded FROM broken WHERE shop=? AND refunded>0 AND "
                                          "ts>=? AND ts<?", (me, since, t1)):
        if any(s <= ts for s in keys.get((sku, buyer), [])):
            tot[0] += 1
            tot[1] += amt
            if ts >= t0:
                win[0] += 1
                win[1] += amt
    return n_sales, win, tot


def b_refund_line(t0, t1, db_path=None):
    from depts import b_warranty
    r = b_warranty.rules()
    cats = r.get("on_cats") or []
    db = sqlite3.connect(db_path or os.path.join(HERE, "market.db"), timeout=30)
    try:
        n, win, tot = b_refunds(db, t0, t1, cats, r.get("since") or 0)
    finally:
        db.close()
    return (f"{'·'.join(cats)} B등급 보증 거래: 최근 12시간 판매 {n}건, 환불 {win[0]}건 {win[1]:,}; 보증 시작"
            f"({datetime.fromtimestamp(r.get('since') or 0):%m/%d %H:%M}) 이후 환불 {tot[0]}건 {tot[1]:,}")


def storage_paid(db, t0, t1):
    """Storage paid in [t0, t1): storage_per_day of each 5-min cash snapshot times the time to the next one."""
    rows = db.execute("SELECT ts, storage_per_day FROM cash WHERE ts>=? AND ts<? ORDER BY ts", (t0, t1)).fetchall()
    return round(sum((b[0] - a[0]) * (a[1] or 0) for a, b in zip(rows, rows[1:])) / 86400)


def stock_line(t1, inv_path=None, db_path=None):
    """Held units (live .market/inventory.json) supplied over STALE_H before t1: cost and count, high-price
    (cost > qc.HIGH_MIN) and other; storage paid in the last 12 h and since the first cash snapshot."""
    from depts.qc import HIGH_MIN
    with open(inv_path or os.path.join(HERE, ".market", "inventory.json"), encoding="utf-8") as f:
        inv = json.load(f)
    old = [u for u in inv.get("units", []) if u.get("grade") != "expired" and u.get("supplied_at")
           and u["supplied_at"] <= t1 - STALE_H * 3600]
    hi = [u for u in old if (u.get("cost") or 0) > HIGH_MIN]
    lo = [u for u in old if (u.get("cost") or 0) <= HIGH_MIN]
    c = lambda us: sum(u.get("cost") or 0 for u in us)
    db = sqlite3.connect(db_path or os.path.join(HERE, "market.db"), timeout=30)
    try:
        s12, sall = storage_paid(db, t1 - 12 * 3600, t1), storage_paid(db, 0, t1)
        per_day = (db.execute("SELECT storage_per_day FROM cash WHERE ts<? ORDER BY ts DESC LIMIT 1", (t1,)).fetchone()
                   or [0])[0]
    finally:
        db.close()
    return (f"12시간 넘게 안 팔린 재고: 원가 {c(old):,} {len(old)}개 (고가 {c(hi):,} {len(hi)}개 / 일반 {c(lo):,} {len(lo)}개); "
            f"보관비 누적 {sall:,} (최근 12시간 {s12:,}, 지금 하루 {per_day or 0:,})")


def aged_high_trend(t1, inv_path=None, db_path=None, step_h=3, span_h=12):
    """11/12 ~09:50 (person): high-price units (cost > qc.HIGH_MIN) unsold over STALE_H, every step_h over the last
    span_h: at each time T, my units supplied before T - STALE_H that were still held then (held now, or sold after T;
    my_stock + settles + the live inventory; a unit that expired / was disposed in between is not counted)."""
    from depts.qc import HIGH_MIN
    with open(inv_path or os.path.join(HERE, ".market", "inventory.json"), encoding="utf-8") as f:
        held = {u["unit"] for u in json.load(f).get("units", []) if u.get("grade") != "expired"}
    db = sqlite3.connect(f"file:{db_path or os.path.join(HERE, 'market.db')}?mode=ro", uri=True, timeout=30)
    try:
        st = db.execute("SELECT unit, cost, supplied_at FROM my_stock WHERE cost>? AND supplied_at<=?",
                        (HIGH_MIN, t1 - STALE_H * 3600)).fetchall()
        sold = dict(db.execute("SELECT unit, MIN(ts) FROM settles WHERE shop='choo-chu' AND unit IS NOT NULL GROUP BY unit"))
    finally:
        db.close()
    parts = []
    for k in range(span_h // step_h, -1, -1):
        T = t1 - k * step_h * 3600
        xs = [(c, u) for u, c, s in st if s <= T - STALE_H * 3600
              and ((u in held and (u not in sold or sold[u] > T)) or (u in sold and sold[u] > T))]
        top = sum(1 for c, _ in xs if c > 1_500_000)
        parts.append(f"{datetime.fromtimestamp(T):%H:%M} {len(xs)}개 {sum(c for c, _ in xs):,} (150만 초과 {top})")
    return "12시간+ 미판매 고가 추이 (3시간 단위): " + " -> ".join(parts)


WATCH_SKU = "G5e3784e6"                           # 우아미 소파, never bought again (person 11/09 ~11:10)


def exclude_line():
    """One line: is WATCH_SKU in the live buy_exclude.json (read by depts/purchasing.excluded every restock run)?"""
    try:
        with open(os.path.join(HERE, "buy_exclude.json"), encoding="utf-8") as f:
            skus = json.load(f).get("skus") or {}
    except (OSError, ValueError, AttributeError) as e:
        return f"매입 제외 목록(buy_exclude.json) 읽기 실패 {type(e).__name__}: 우아미 소파 {WATCH_SKU} 다시 매입될 수 있음, 확인 필요"
    if WATCH_SKU in skus:
        return f"우아미 소파 {WATCH_SKU}: 매입 제외 목록에 있음 (목록 {len(skus)}개)"
    return f"우아미 소파 {WATCH_SKU}: 매입 제외 목록에 없음, 다시 매입될 수 있음 (목록 {len(skus)}개), 확인 필요"


def daily():
    """11/09 ~19:45: every day at 08:00 (the 12 h before) until the round end."""
    import alerts
    from datetime import timedelta
    from limits import ROUND_END
    while True:
        n = datetime.now()
        cands = [n.replace(hour=h, minute=0, second=0, microsecond=0) + timedelta(days=d) for d in (0, 1)
                 for h in (8, 20)]                # 11/10 ~12:10 (person): 08:00 and 20:00
        at = min(x for x in cands if x > n)
        if at.timestamp() > ROUND_END:
            return
        while time.time() < at.timestamp():
            time.sleep(min(60, max(1, at.timestamp() - time.time())))
        t = at.timestamp()
        try:
            alerts.send("daily", f"morning_summary:{at:%Y%m%d}" + ("" if at.hour == 8 else f"_{at:%H}"), summary(t - NIGHT_H * 3600, t))
        except Exception as e:
            alerts.send("urgent", f"morning_summary_fail:{at:%Y%m%d%H}", f"12시간 요약 실패: {type(e).__name__}: {e}")
        time.sleep(120)


def main():
    if "--dry" in sys.argv:
        now = time.time()
        print(summary(now - NIGHT_H * 3600, now))
        return
    if "--daily" in sys.argv:
        daily()
        return
    while time.time() < AT:
        time.sleep(min(60, max(1, AT - time.time())))
    import alerts
    alerts.send("daily", f"morning_summary:{datetime.fromtimestamp(AT):%Y%m%d}", summary(AT - NIGHT_H * 3600, AT))


if __name__ == "__main__":
    main()
