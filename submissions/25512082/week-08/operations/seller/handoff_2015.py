"""11-17 20:15 계획된 일시 중단(노트북 종료) 인수인계 기록. 읽기 전용 수집 → HANDOFF-20261117-2015.{json,md} 새 파일만 작성."""
import json, re, subprocess, sys, time, urllib.request, datetime as dt
from pathlib import Path
K = dt.timezone(dt.timedelta(hours=9)); kst = lambda t: dt.datetime.fromtimestamp(t, K).strftime("%m-%d %H:%M:%S")
A = Path("/home/user/student/AX/ai-agent-engineering-101-week08-v4/submissions/25512082/week-08"); M = A / ".market"
O = Path("/home/user/.local/state/ax-round3-ops"); OUT = O / "HANDOFF-20261117-2015"
MK = "https://market.ourolabs.ai"; ME = "sohyunkim11"
TUNNELS = ["necessary-sciences-pension-arab", "noble-printable-ensuring-contributor", "overseas-acc-beyond-extras"]
KEYS = TUNNELS + [ME, "u-5a9bafc674c7"]
def get(p):
    for i in range(5):
        try: return json.load(urllib.request.urlopen(MK + p, timeout=60))
        except Exception as e: err = e; time.sleep(4)
    raise err
H = {"written_at": kst(time.time()), "type": "planned pause (laptop shutdown) — NOT a safety STOP"}
# 마지막 정산 (kit 로그 + 원장)
kit = (A / "logs/market-kit.log").read_text(errors="ignore").splitlines()
st = [l for l in kit if "[acp] settled" in l]
H["last_settle_kit_line"] = st[-1]; H["settled_lines_in_kit_log"] = len(st)
cur, mine, n = 7789219, [], 0
while True:
    e = get(f"/api/ledger?after={cur}"); e = e if isinstance(e, list) else e.get("entries", [])
    if not e: break
    for x in e:
        n += 1
        if any(k in json.dumps(x, ensure_ascii=False) for k in KEYS): mine.append({"seq": x["seq"], "ts": kst(x["ts"]), "kind": x["kind"], "unit": x["data"].get("unit"), "amount": x["data"].get("amount") or (x["data"].get("receipt") or {}).get("price")})
    cur = e[-1]["seq"]
H["ledger"] = {"scanned_from": 7789220, "scanned_entries": n, "cursor_last_seq": cur, "checked_at": kst(time.time()),
               "our_entries_since_7789220": mine,
               "last_settle": next(({"seq": x["seq"], "ts": x["ts"], "unit": x["unit"], "amount": x["amount"]} for x in reversed(mine) if x["kind"] == "settle"), None),
               "match_keys": KEYS}
# 재고·지갑
inv = json.loads((M / "inventory.json").read_text())
H["wallet_balance"] = inv["balance"]; H["realized_profit"] = inv["balance"] - inv["start"]
H["unsold_units"] = [{"unit": u["unit"], "sku": u["sku"], "name": u.get("name"), "grade": u.get("grade"), "list_price": u.get("list_price"), "cost": u["cost"],
                      "supplied": kst(u["supplied_at"]), "expires": kst(u["expires_at"])} for u in inv["units"]]
sys.dont_write_bytecode = True; sys.path.insert(0, str(A))
try:
    import seller
    H["current_ask_floor"] = {u["unit"]: dict(zip(("ask", "floor"), seller.terms(u, None, inv.get("storage_per_day", 0.0)))) for u in inv["units"]}
except Exception as e:
    H["current_ask_floor"] = {"error": repr(e)[:200]}
# 최근 합의·미정산
ev = [json.loads(l) for l in (M / "seller-events.jsonl").read_text().splitlines()[-500:] if l.strip()]
settled_units = {m.group(1) for l in st[-50:] for m in [re.search(r"unit (u-[0-9a-f]+)", l)] if m}
H["recent_accepts_without_settle_since_1800"] = [{"ts": kst(e["ts"]), "unit": e.get("unit"), "price": e.get("price")} for e in ev
                                                 if e.get("event") == "accept" and e["ts"] > dt.datetime(2026, 11, 17, 18, 0, tzinfo=K).timestamp() and e.get("unit") not in settled_units]
H["seller_events_since_1900"] = sum(1 for e in ev if e["ts"] > dt.datetime(2026, 11, 17, 19, 0, tzinfo=K).timestamp())
# board
b = get("/api/board?round=3")
s = next(y for y in b["shops"] if y["name"] == ME); x = next(y for y in b["buyers"] if y["name"] == ME)
stu = sorted([p for p in b["players"] if p.get("student")], key=lambda p: -p["student_total"]); i = [p["player"] for p in stu].index(ME)
H["board"] = {"measured_at": kst(time.time()),
              "shop": {k: s.get(k) for k in ("alive", "deals", "revenue", "profit", "book", "stars", "reviews", "stars_rank", "refunds", "broken", "stock_units", "stock_cost", "all")},
              "buyer": {k: x.get(k) for k in ("orders", "deals", "saved", "forum", "posts", "all")},
              "student_shop": stu[i]["student_shop"], "student_buyer": stu[i]["student_buyer"], "student_total": stu[i]["student_total"],
              "student_pct": stu[i].get("student_pct"), "student_rank": i + 1, "students": len(stu),
              "above": stu[i - 1]["student_total"], "below": stu[i + 1]["student_total"]}
# 누적 매입
s15 = json.loads((O / "ops15_state.json").read_text())
H["purchases"] = {"ops15": {"spent": s15["spent"], "units": s15["units_bought"], "ended": "11-17 18:00 normal end"},
                  "ops16": {"spent": 0, "units": 0, "note": "started 18:00:13, SIGTERM 18:05:02 during ledger backfill, no supply window used, no state file"},
                  "total": {"spent": s15["spent"], "units": s15["units_bought"], "approved_cap": 900000, "unit_cap": 100},
                  "new_purchase_after_1900": "forbidden; none made (no restock-approval file; safe_restock printed {\"buy\":[]})"}
H["processes"] = {"trading_processes_running": [l.strip() for l in subprocess.run(["ps", "-eo", "pid,args"], capture_output=True, text=True).stdout.splitlines()
                                                if re.search(r"market_kit\.py|run_market_locked|seller\.py|cloudflared|sell_guard|finalize_2026|ops1[0-9]\.py|price_exp|buyer_ops|stop_guard|saved_guard", l) and "grep" not in l and "handoff_2015" not in l],
                  "seller": "stopped 20:15:00 (planned pause; SIGTERM to run_market_locked.sh 2474; orphan cloudflared 2485 stopped 20:15:39)",
                  "buyer": "stopped since 11-17 03:23:15 (saved_guard7 STOP: 바로 아래 학생과 차이 0.0004 <= 0.0004). Do not restart.",
                  "guard_2355": "exited 20:15:32 with supervisor_exited (not a STOP)",
                  "finalizer": "finalize_20261117.py stopped 20:15:42 while idle (no snapshot yet). Relaunch on resume if before 23:58:30.",
                  "autostart": "none: no crontab, user timer only launchpadlib-cache-clean, no ~/.config/autostart, wsl.conf has no boot command, no rc launches"}
H["stop_records_today"] = {"safety_stop": "19:10:12 sell_guard_1917: stars_rank 3.0 < 3.93 (single reading right after re-registration; board showed 4.38 again). User chose keep-STOP then approved restart 19:20 with 2-consecutive-reading rule.",
                           "refunds": s.get("refunds"), "broken": s.get("broken"), "errors": "none besides above"}
H["pricing_policy"] = {"rule": "ask = max(round(list_price * share), floor); share SKU_TARGET_SHARE else GRADE_TARGET_SHARE {A:0.78,B:0.72,C:0.65}; floor = ceil((cost + carrying) * 1.05)",
                       "price_experiment": "ended 11-17 05:08 (file absent); seller.py PRICE_EXPERIMENT (G772009ce, A) expired 11-13",
                       "files": ["seller.py", "pricing-policy.json"], "change": "none"}
H["safety_conditions"] = {"guard": "sell_guard_2355.py: STOP on restock-approval file appearing, new stock units, below-cost settle, duplicate settle of same unit, refunds/broken increase, stars_rank < 3.93 twice in a row (~2 min); end at 23:55 KST with up to 120 s wait for an accepted-but-unsettled deal",
                          "trade_end": "23:55 KST 11-17; no trading after 23:55", "round3_close": "23:59 KST 11-17 (official)", "pr_due": "11-23 23:59 KST"}
H["resume"] = {"only_on_explicit_user_command": True,
               "script": str(O / "resume_seller_until_2355.sh"),
               "manual_command": f"cd {A} && MARKET_OWNER=\"$(cat /home/user/.config/ax-week07/market-owner)\" OWN_AGENT_NAME={ME} setsid nohup ./run_market_locked.sh --tunnel --shop-only >> .market/shop-only-console.out 2>&1 < /dev/null &  then: cd {O} && setsid nohup python3 -I sell_guard_2355.py <supervisor pid> >> sell_guard_2355.stdout 2>&1 &",
               "before_resume_check": f"scan /api/ledger after seq {cur} for our tunnels / {ME} / u-5a9bafc674c7; inventory must still be exactly [u-5a9bafc674c7]; no restock-approval.json",
               "conditions": ["Seller sells existing stock only", "no new purchase", "no Buyer restart", "no SKU/price change", "keep all safety STOP conditions", "end all trading at 23:55 KST", "no resume at/after 23:55"]}
OUT.with_suffix(".json").write_text(json.dumps(H, ensure_ascii=False, indent=1))
bd = H["board"]; u = H["unsold_units"]
md = f"""# Round 3 인수인계 — 계획된 일시 중단 ({H['written_at']} KST)

> 노트북 종료를 위한 **계획된 일시 중단**이다. 안전 STOP이 아니다. 재개는 사용자의 명시적 재개 명령이 있을 때만 한다.

## 현재 상태
- 거래 프로세스: {H['processes']['trading_processes_running'] or '없음'}
- Seller: {H['processes']['seller']}
- Buyer: {H['processes']['buyer']}
- 23:55 감시자: {H['processes']['guard_2355']}
- 마감 기록 스크립트: {H['processes']['finalizer']}
- 자동 시작: {H['processes']['autostart']}

## 거래
- 마지막 정상 정산: `{H['last_settle_kit_line']}`
- 원장 기준 마지막 정산: {json.dumps(H['ledger']['last_settle'], ensure_ascii=False)}
- 원장 조회: #7789220 이후 {H['ledger']['scanned_entries']}건, 마지막 seq **{H['ledger']['cursor_last_seq']}** ({H['ledger']['checked_at']} 확인). 우리 항목: {json.dumps(H['ledger']['our_entries_since_7789220'], ensure_ascii=False)}
- 합의 후 미정산(18:00 이후): {H['recent_accepts_without_settle_since_1800'] or '없음'} / 19:00 이후 seller 이벤트 {H['seller_events_since_1900']}건
- 누적 매입: ops15 {H['purchases']['ops15']['spent']:,}원 / {H['purchases']['ops15']['units']}개, ops16 0원 / 0개 → 합계 {H['purchases']['total']['spent']:,}원 / {H['purchases']['total']['units']}개 (승인 900,000원·100개). 19:00 이후 신규 매입 없음
- 남은 재고: {json.dumps(u, ensure_ascii=False)}
- 현재 호가/하한: {json.dumps(H['current_ask_floor'], ensure_ascii=False)}
- 지갑 {H['wallet_balance']:,} / 실현 이익 {H['realized_profit']:,}

## board ({bd['measured_at']})
- Book {bd['shop']['book']:,} · Revenue {bd['shop']['revenue']:,} · stars {bd['shop']['stars']} · stars_rank {bd['shop']['stars_rank']} · deals {bd['shop']['deals']} · 환불 {bd['shop']['refunds']} · 파손 {bd['shop']['broken']}
- Seller ALL {bd['shop']['all']} · Buyer ALL {bd['buyer']['all']} (buyer deals {bd['buyer']['deals']}, saved {bd['buyer']['saved']})
- student_total {bd['student_total']} (shop {bd['student_shop']} + buyer {bd['student_buyer']}), 학생 {bd['student_rank']}위 / {bd['students']}명, student_pct {bd['student_pct']} (위 {bd['above']}, 아래 {bd['below']})

## STOP·오류 기록
- {H['stop_records_today']['safety_stop']}
- 이번 20:15 중단은 계획된 일시 중단이며 STOP이 아니다 (`pause_20261117.log`).

## 가격 정책 (변경 없음)
- {H['pricing_policy']['rule']}
- {H['pricing_policy']['price_experiment']}

## 안전 조건과 종료 예약
- {H['safety_conditions']['guard']}
- 거래 종료 23:55 KST (이후 재개 금지) · Round 3 공식 종료 23:59 · PR 마감 11-23 23:59

## 재개 (사용자가 별도 재개 명령을 보낸 경우에만)
1. 현재 시각이 23:55 이상이면 재개하지 않는다. 결과 정리만 한다.
2. 꺼져 있는 동안의 시장 변화 확인: `/api/ledger?after={H['ledger']['cursor_last_seq']}`부터 우리 터널 URL·`{ME}`·`u-5a9bafc674c7` 항목 확인. inventory가 정확히 [u-5a9bafc674c7]인지, restock-approval.json이 없는지 확인. 불일치하면 재개하지 않고 보고한다.
3. 실행: `sh {O}/resume_seller_until_2355.sh` (위 확인을 스스로 다시 하고, 통과하면 Seller shop-only + 23:55 감시자 + (23:58:30 전이고 결과 파일이 없으면) 마감 기록 스크립트를 띄운다)
4. 조건: {', '.join(H['resume']['conditions'])}
5. 작업 경로: `{A}` · 운영 기록: `{O}`
"""
OUT.with_suffix(".md").write_text(md)
print(OUT.with_suffix(".md"))
